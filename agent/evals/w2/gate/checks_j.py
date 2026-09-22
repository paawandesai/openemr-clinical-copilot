"""
Category J — PHI in logs. Zero tolerance, every case critical.

Braintrust's rule applies here more than anywhere: a 91% aggregate can hide
an unauthorised write in the remaining 9%. There is no threshold at which a
canary in a log line is acceptable, so this category is scored at 100% and
every case in it blocks.

The shape of the category matters as much as its contents. Four arms carry a
seeded canary and must be silent; one carries no canary at all and must be
silent for a different reason; one carries a blatant plant and must not be.
Without the last two, "the scanner reported nothing" is indistinguishable
from "the scanner did nothing".
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from app.models.w2 import (
    ALLOWED_REASONS,
    ClinicalConcept,
    EvidenceRetrieverInput,
    ExtractionFailureReason,
    LabWriteBackLedgerRow,
    Route,
    RoutingDecision,
    RoutingReasonCode,
)
from app.models.w2.workers import FORBIDDEN_RETRIEVAL_FIELD_NAMES
from evals.w2.gate import phi_scanner
from evals.w2.gate.models import Case, CheckOutcome, GateError
from evals.w2.gate.paths import FIXTURES_DIR
from evals.w2.gate.registry import check


def _fixture(relative: str) -> dict[str, Any]:
    path = FIXTURES_DIR / relative
    if not path.is_file():
        raise GateError(f"fixture {path} is missing")
    return json.loads(path.read_text(encoding="utf-8"))


def _canaries() -> dict[str, str]:
    return dict(_fixture("phi/canary_patient.json")["canaries"])


def _scan_named(case: Case) -> tuple[list[phi_scanner.Hit], list[str], int]:
    """Scan every fixture the case names. Returns (hits, arms, fields scanned)."""
    canaries = _canaries()
    hits: list[phi_scanner.Hit] = []
    arms: list[str] = []
    fields = 0
    for relative in case.fixtures():
        bundle = _fixture(relative)
        captured = bundle["captured"]
        arms.append(f"{bundle['fixture_id']}({bundle['arm']})")
        fields += len(phi_scanner._walk(captured, ""))
        hits.extend(phi_scanner.scan(captured, canaries))
    if not arms:
        raise GateError(f"{case.id} names no PHI fixture to scan")
    return hits, arms, fields


@check("chk.j_no_canary_in_a_normal_run")
def j_no_canary_in_a_normal_run(case: Case) -> CheckOutcome:
    """The ordinary path: identifiers and counts, no chart content."""
    hits, arms, fields = _scan_named(case)
    if hits:
        return CheckOutcome.failed(
            f"{len(hits)} hit(s) across {arms}: " + "; ".join(str(h) for h in hits[:5])
        )
    return CheckOutcome.ok(f"{fields} captured string fields across {arms}, zero hits")


@check("chk.j_no_phi_on_the_error_path")
def j_no_phi_on_the_error_path(case: Case) -> CheckOutcome:
    """
    Error messages and stack frames. The reason a failure is loggable at all
    is that every reason it can carry is a closed enum member, so there is no
    free-text field for chart content to ride in on.
    """
    hits, arms, fields = _scan_named(case)
    if hits:
        return CheckOutcome.failed(
            f"{len(hits)} hit(s) on the error path across {arms}: "
            + "; ".join(str(h) for h in hits[:5])
        )

    if any(not isinstance(member.value, str) for member in ExtractionFailureReason):
        return CheckOutcome.failed("ExtractionFailureReason is no longer a closed string enum")
    decision = RoutingDecision(
        decision=Route.REFUSE,
        reason_code=RoutingReasonCode.EXTRACTION_FAILED,
        correlation_id="4c547101-195d-47b2-bc4b-0db614573052",
    )
    extra = set(type(decision).model_fields) - {"decision", "reason_code", "correlation_id"}
    if extra:
        return CheckOutcome.failed(
            f"RoutingDecision has grown {sorted(extra)}; D4 keeps it to three loggable "
            "fields precisely so a reason written after reading a chart cannot ride along"
        )
    if not ALLOWED_REASONS[Route.REFUSE]:
        return CheckOutcome.failed("the refuse route has no allowed reasons")
    return CheckOutcome.ok(
        f"{fields} captured fields across {arms}, zero hits; the failure and routing "
        "vocabularies are still closed enums"
    )


@check("chk.j_retrieval_query_carries_no_identifier")
def j_retrieval_query_carries_no_identifier(case: Case) -> CheckOutcome:
    """
    The query that leaves this system. D5: concepts only, no patient field to
    put an identifier in, and the concept text itself refuses a date or an
    SSN-shaped number.
    """
    hits, arms, fields = _scan_named(case)
    if hits:
        return CheckOutcome.failed(
            f"{len(hits)} hit(s) in the retrieval bundle across {arms}: "
            + "; ".join(str(h) for h in hits[:5])
        )

    declared = set(EvidenceRetrieverInput.model_fields)
    smuggled = declared & FORBIDDEN_RETRIEVAL_FIELD_NAMES
    if smuggled:
        return CheckOutcome.failed(
            f"EvidenceRetrieverInput now declares {sorted(smuggled)}; D5 says a query "
            "leaving this system carries clinical concepts and nothing else"
        )
    try:
        EvidenceRetrieverInput(
            concepts=[ClinicalConcept(text="type 2 diabetes mellitus")],
            correlation_id="4c547101-195d-47b2-bc4b-0db614573052",
            patient_id="a2c373ec-57bf-41cb-8767-94410dcf663d",
        )
    except ValidationError:
        pass
    else:
        return CheckOutcome.failed("a retrieval input accepted a patient_id")

    for text in ("dob 1971-04-02", "ssn 999-41-7772", "seen 04/02/1971"):
        try:
            ClinicalConcept(text=text)
        except ValidationError:
            continue
        return CheckOutcome.failed(f"a clinical concept accepted identifier-shaped text {text!r}")
    return CheckOutcome.ok(
        f"{fields} captured fields across {arms}, zero hits; the input type has no "
        "patient field and rejects identifier-shaped concept text"
    )


@check("chk.j_no_raw_document_content_in_logs")
def j_no_raw_document_content_in_logs(case: Case) -> CheckOutcome:
    """
    Document handling and the write-back ledger. The ledger is the interesting
    half: it is the table an engineer dumps while debugging, so it carries
    identifiers and coordinates and no test value or patient name.
    """
    hits, arms, fields = _scan_named(case)
    if hits:
        return CheckOutcome.failed(
            f"{len(hits)} hit(s) in the document bundle across {arms}: "
            + "; ".join(str(h) for h in hits[:5])
        )

    ledger_fields = set(LabWriteBackLedgerRow.model_fields)
    content_shaped = {
        "value",
        "result_value",
        "test_value",
        "patient_name",
        "name",
        "dob",
        "date_of_birth",
        "mrn",
        "quote",
        "page_text",
        "raw_text",
        "note",
        "comments",
    }
    leaked = ledger_fields & content_shaped
    if leaked:
        return CheckOutcome.failed(
            f"the write-back ledger row now carries {sorted(leaked)}; it is meant to map "
            "an extraction id to a document, a page and a box, not to hold the reading"
        )
    return CheckOutcome.ok(
        f"{fields} captured fields across {arms}, zero hits; the ledger row carries "
        f"{len(ledger_fields)} columns and none of them is chart content"
    )


@check("chk.j_control_arm_is_quiet")
def j_control_arm_is_quiet(case: Case) -> CheckOutcome:
    """
    The matched no-injection control. No canary was seeded into this run, so
    every hit here is a false positive. A detector nobody has shown to be
    quiet on clean input cannot be read as evidence when it is quiet on dirty
    input.
    """
    hits, arms, fields = _scan_named(case)
    if hits:
        return CheckOutcome.failed(
            f"the control arm produced {len(hits)} false positive(s) across {arms}: "
            + "; ".join(str(h) for h in hits[:5])
            + " — a scanner that cries wolf on clean logs gets switched off"
        )
    return CheckOutcome.ok(f"control arm: {fields} fields across {arms}, zero false positives")


@check("chk.j_positive_control_is_detected")
def j_positive_control_is_detected(case: Case) -> CheckOutcome:
    """
    The arm that fails when the scrubber is turned off.

    With the scrubber disabled the four seeded arms go quiet for the wrong
    reason — they hold no chart content to find, and a dead detector reports
    zero on them exactly as a working one does. This arm plants a name, an
    MRN, a date of birth and a free-text finding in four fields and requires
    every one of them to be reported.
    """
    canaries = _canaries()
    bundle = _fixture(case.fixtures()[0]) if case.fixtures() else {}
    if bundle.get("arm") != "positive_control":
        return CheckOutcome.failed(f"{case.id} does not name the positive-control fixture")

    hits = phi_scanner.scan(bundle["captured"], canaries)
    by_field = {hit.field_path for hit in hits}
    required = {
        "log.patient_display_name",
        "log.patient_mrn",
        "log.error_message",
        "retrieval.query_string",
    }
    missed = required - by_field
    if missed:
        return CheckOutcome.failed(
            f"the scanner missed a planted canary in {sorted(missed)}; it is not "
            "detecting, and the quiet arms prove nothing"
        )
    detectors = {(hit.detector, hit.name) for hit in hits}
    if not any(detector == "canary" for detector, _ in detectors):
        return CheckOutcome.failed("no canary detector fired on a bundle full of canaries")
    if ("pattern", "date_iso") not in detectors:
        return CheckOutcome.failed(
            "the date-of-birth in the planted error message was not caught by the "
            "pattern detector; the regex arm is dead"
        )
    return CheckOutcome.ok(
        f"{len(hits)} hits over {len(by_field)} planted fields; canary and pattern "
        "detectors both fired"
    )
