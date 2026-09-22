"""
Write-back contracts — D1 (superseding) and CLAUDE.md invariant 6.

The idempotency key is the part with real consequences: D1 makes it a
UNIQUE constraint in the fork, and a key that is composed differently on
two call sites means the constraint stops catching anything. So it is
tested as a function — canonicalisation, stability, and the three ways it
can differ.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime

import pytest
from pydantic import ValidationError

from app.models.w2.base import EXTRACTION_ID_PATTERN
from app.models.w2.citations import BBox
from app.models.w2.documents import DocumentKind
from app.models.w2.lab_values import AbnormalFlag, NumericLabValue, QualitativeLabValue
from app.models.w2.writeback import (
    LabWriteBackLedgerRow,
    LabWriteBackRequest,
    LabWriteBackResponse,
    LabWriteBackResult,
    LabWriteBackResultOutcome,
    WriteBackDocument,
    WriteOutcome,
    document_extraction_id,
    document_idempotency_key,
    lab_result_extraction_id,
    lab_result_idempotency_key,
)

DIGEST = "a27ac9417c2ce8a3" + "4" * 112
OTHER_DIGEST = "b27ac9417c2ce8a3" + "4" * 112
COLLECTED = date(2026, 8, 30)
PATIENT = "a2c373ec-57bf-41cb-8767-94410dcf663d"
CORRELATION_ID = "00000000-0000-4000-8000-000000000000"
EXTRACTOR_VERSION = "vlm-1.0"
BBOX = BBox(page=1, x0=270.437, y0=256.355, x1=280.0, y1=268.171)


def key(test_code: str = "2345-7", collected: date = COLLECTED, digest: str = DIGEST) -> str:
    return lab_result_idempotency_key(
        document_sha3_512=digest, test_code=test_code, collection_date=collected
    )


def extraction(test_code: str = "2345-7", collected: date = COLLECTED, digest: str = DIGEST) -> str:
    return lab_result_extraction_id(
        document_sha3_512=digest, test_code=test_code, collection_date=collected
    )


def result(test_code: str = "2345-7", collected: date = COLLECTED) -> LabWriteBackResult:
    return LabWriteBackResult(
        test_code=test_code,
        test_name="Glucose",
        value=NumericLabValue(value=89.0),
        unit="mg/dL",
        collection_date=collected,
        abnormal=AbnormalFlag.NORMAL,
        page=1,
        bbox=BBOX,
        idempotency_key=key(test_code, collected),
        extraction_id=extraction(test_code, collected),
    )


def request(results: list[LabWriteBackResult] | None = None) -> LabWriteBackRequest:
    return LabWriteBackRequest(
        patient_uuid=PATIENT,
        document=WriteBackDocument(
            filename="lab-clean-001.pdf",
            mime_type="application/pdf",
            sha3_512=DIGEST,
            byte_length=31851,
            content_base64="JVBERi0=",
        ),
        results=results if results is not None else [result()],
        extraction_id=document_extraction_id(
            document_sha3_512=DIGEST, extractor_version=EXTRACTOR_VERSION
        ),
        extractor_version=EXTRACTOR_VERSION,
        correlation_id=CORRELATION_ID,
    )


# -- document key -----------------------------------------------------------


def test_the_document_key_is_openemrs_own_digest() -> None:
    assert document_idempotency_key(DIGEST.upper()) == DIGEST


def test_a_sha256_is_not_accepted_as_a_document_key() -> None:
    """The one thing that must never happen: comparing a SHA-256 against a
    SHA3-512 and concluding the document is new."""
    with pytest.raises(ValueError, match="128 hex characters"):
        document_idempotency_key("c" * 64)


def test_a_non_hex_digest_is_rejected() -> None:
    with pytest.raises(ValueError, match="128 hex characters"):
        document_idempotency_key("z" * 128)


# -- result key -------------------------------------------------------------


def test_the_result_key_is_d1s_three_components() -> None:
    assert key() == f"{DIGEST}:2345-7:2026-08-30"


def test_the_key_is_canonical_across_call_sites() -> None:
    """A UNIQUE constraint only works if both writers spell the key the same
    way. Case and whitespace are normalised here, once."""
    assert key() == lab_result_idempotency_key(
        document_sha3_512=DIGEST.upper(), test_code="  2345-7  ", collection_date=COLLECTED
    )


@pytest.mark.parametrize(
    "different",
    [
        {"digest": OTHER_DIGEST},
        {"test_code": "2339-0"},
        {"collected": date(2026, 8, 31)},
    ],
    ids=["document", "test code", "collection date"],
)
def test_each_component_changes_the_key(different: dict[str, object]) -> None:
    assert key(**different) != key()  # type: ignore[arg-type]


def test_the_key_needs_a_test_code() -> None:
    with pytest.raises(ValueError, match="needs a test code"):
        key(test_code="   ")


# -- extraction id ----------------------------------------------------------


def test_the_extraction_id_is_derived_from_the_key() -> None:
    """One identity for one row: the duplicate the ledger refuses is the
    same one already visible in note[0].text."""
    assert re.match(EXTRACTION_ID_PATTERN, extraction())
    assert extraction() == extraction()


@pytest.mark.parametrize(
    "different",
    [{"digest": OTHER_DIGEST}, {"test_code": "2339-0"}, {"collected": date(2026, 8, 31)}],
    ids=["document", "test code", "collection date"],
)
def test_a_different_key_gives_a_different_extraction_id(different: dict[str, object]) -> None:
    assert extraction(**different) != extraction()  # type: ignore[arg-type]


def test_the_extraction_id_is_short_and_greppable() -> None:
    """Spike §10.3: `procedure_result.comments` is clinician-visible chart
    text, so the id has to be parseable but not alarming — which the raw
    128-character digest would not be."""
    value = extraction()
    assert value.startswith("w2x-")
    assert len(value) == 36


def test_a_document_extraction_id_changes_with_the_extractor() -> None:
    first = document_extraction_id(document_sha3_512=DIGEST, extractor_version="vlm-1.0")
    second = document_extraction_id(document_sha3_512=DIGEST, extractor_version="vlm-1.1")
    assert first != second
    assert first == document_extraction_id(document_sha3_512=DIGEST, extractor_version="vlm-1.0")


def test_an_extraction_run_records_its_extractor() -> None:
    with pytest.raises(ValueError, match="records which extractor"):
        document_extraction_id(document_sha3_512=DIGEST, extractor_version=" ")


# -- the request ------------------------------------------------------------


def test_a_well_formed_request_validates() -> None:
    assert request().document_key() == DIGEST


def test_every_result_carries_a_key_with_no_default() -> None:
    """Invariant 6 has no exceptions, and a default would be one."""
    assert LabWriteBackResult.model_fields["idempotency_key"].is_required()
    with pytest.raises(ValidationError, match="Field required"):
        LabWriteBackResult(
            test_code="2345-7",
            test_name="Glucose",
            value=NumericLabValue(value=89.0),
            collection_date=COLLECTED,
            abnormal=AbnormalFlag.NORMAL,
            page=1,
            bbox=BBOX,
            extraction_id=extraction(),
        )


def test_a_hand_written_key_that_is_not_the_composition_is_rejected() -> None:
    forged = result().model_copy(update={"idempotency_key": "whatever-i-like"})
    with pytest.raises(ValidationError, match="is not the composition"):
        request([forged])


def test_a_key_composed_against_the_wrong_document_is_rejected() -> None:
    mismatched = LabWriteBackResult(
        test_code="2345-7",
        test_name="Glucose",
        value=NumericLabValue(value=89.0),
        collection_date=COLLECTED,
        abnormal=AbnormalFlag.NORMAL,
        page=1,
        bbox=BBOX,
        idempotency_key=key(digest=OTHER_DIGEST),
        extraction_id=extraction(digest=OTHER_DIGEST),
    )
    with pytest.raises(ValidationError, match="is not the composition"):
        request([mismatched])


def test_an_extraction_id_not_derived_from_the_key_is_rejected() -> None:
    forged = result().model_copy(update={"extraction_id": "w2x-" + "9" * 32})
    with pytest.raises(ValidationError, match="is not derived from its idempotency key"):
        request([forged])


def test_two_results_cannot_share_a_key() -> None:
    """The ledger's UNIQUE constraint would reject the second; better to
    fail before the transaction than half-way through it."""
    with pytest.raises(ValidationError, match="share the idempotency key"):
        request([result(), result()])


def test_two_results_for_different_tests_are_fine() -> None:
    assert len(request([result(), result("2339-0")]).results) == 2


def test_a_request_needs_at_least_one_result() -> None:
    with pytest.raises(ValidationError, match="at least 1 item"):
        request([])


def test_the_document_is_sent_or_named_but_not_both() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        WriteBackDocument(
            filename="lab.pdf",
            mime_type="application/pdf",
            sha3_512=DIGEST,
            byte_length=10,
            content_base64="eA==",
            existing_document_id=2820,
        )
    with pytest.raises(ValidationError, match="exactly one"):
        WriteBackDocument(
            filename="lab.pdf", mime_type="application/pdf", sha3_512=DIGEST, byte_length=10
        )


def test_a_retry_may_name_an_already_stored_document() -> None:
    retry = WriteBackDocument(
        filename="lab.pdf",
        mime_type="application/pdf",
        sha3_512=DIGEST,
        byte_length=10,
        existing_document_id=2820,
    )
    assert retry.content_base64 is None


def test_only_lab_documents_are_written_back() -> None:
    assert request().document_kind is DocumentKind.LAB_PDF


# -- the extractor version --------------------------------------------------


def test_a_request_states_which_extractor_produced_it() -> None:
    """
    `LabWriteBackLedgerRow` has always required `extractor_version`, and
    until now nothing on the request could supply it — the endpoint had to
    invent one or leave the column empty. Required here, with no default,
    for the same reason the ledger requires it: an unstated extractor makes
    every run look like the same run when a regression has to be traced back
    to one.
    """
    payload = request().model_dump(mode="json")
    del payload["extractor_version"]

    with pytest.raises(ValidationError, match="extractor_version"):
        LabWriteBackRequest.model_validate_json(json.dumps(payload))


def test_an_empty_extractor_version_is_not_a_version() -> None:
    payload = request().model_dump(mode="json")
    payload["extractor_version"] = ""

    with pytest.raises(ValidationError):
        LabWriteBackRequest.model_validate_json(json.dumps(payload))


def test_the_run_id_must_be_this_document_extracted_by_this_extractor() -> None:
    """
    With the extractor version on the request, `extraction_id` stops being
    an assertion and becomes checkable: it is `document_extraction_id` of
    the two things the request already carries. A caller that changes the
    extractor without re-deriving the id is caught here rather than writing
    rows the ledger attributes to the wrong run.
    """
    payload = request().model_dump(mode="json")
    payload["extractor_version"] = "vlm-2.0"

    with pytest.raises(ValidationError, match="not this document extracted by this"):
        LabWriteBackRequest.model_validate_json(json.dumps(payload))


def test_re_deriving_the_run_id_makes_the_new_extractor_valid() -> None:
    payload = request().model_dump(mode="json")
    payload["extractor_version"] = "vlm-2.0"
    payload["extraction_id"] = document_extraction_id(
        document_sha3_512=DIGEST, extractor_version="vlm-2.0"
    )

    parsed = LabWriteBackRequest.model_validate_json(json.dumps(payload))
    assert parsed.extractor_version == "vlm-2.0"


def test_the_request_can_fill_the_ledger_rows_extractor_version() -> None:
    """The gap this closes: every ledger column now has a request field behind it."""
    sent = request()
    row = LabWriteBackLedgerRow(
        idempotency_key=sent.results[0].idempotency_key,
        extraction_id=sent.results[0].extraction_id,
        document_sha3_512=sent.document.sha3_512,
        document_id=2474,
        patient_uuid=sent.patient_uuid,
        test_code=sent.results[0].test_code,
        collection_date=sent.results[0].collection_date,
        page=sent.results[0].page,
        bbox=sent.results[0].bbox,
        procedure_order_id=8198,
        procedure_report_id=762,
        procedure_result_id=14849,
        written_at=datetime(2026, 9, 22, 16, 12, 58),
        extractor_version=sent.extractor_version,
    )
    assert row.extractor_version == EXTRACTOR_VERSION


# -- what the column can hold -----------------------------------------------


def test_an_indeterminate_flag_is_never_written_back() -> None:
    with pytest.raises(ValidationError, match="indeterminate flag is not written back"):
        LabWriteBackResult(
            test_code="2345-7",
            test_name="Glucose",
            value=NumericLabValue(value=89.0),
            collection_date=COLLECTED,
            abnormal=AbnormalFlag.INDETERMINATE,
            page=1,
            bbox=BBOX,
            idempotency_key=key(),
            extraction_id=extraction(),
        )


def test_the_page_and_the_bbox_must_agree() -> None:
    with pytest.raises(ValidationError, match="page is 2 but bbox is on page 1"):
        LabWriteBackResult(
            test_code="2345-7",
            test_name="Glucose",
            value=NumericLabValue(value=89.0),
            collection_date=COLLECTED,
            abnormal=AbnormalFlag.NORMAL,
            page=2,
            bbox=BBOX,
            idempotency_key=key(),
            extraction_id=extraction(),
        )


def test_a_qualitative_value_is_writable() -> None:
    qualitative = LabWriteBackResult(
        test_code="5195-3",
        test_name="Hepatitis B Surface Ag",
        value=QualitativeLabValue(text="Negative"),
        collection_date=COLLECTED,
        abnormal=AbnormalFlag.NORMAL,
        page=1,
        bbox=BBOX,
        idempotency_key=key("5195-3"),
        extraction_id=extraction("5195-3"),
    )
    assert qualitative.value.as_text() == "Negative"


def test_both_a_code_and_a_name_are_required() -> None:
    """Spike §9: `result_code` and `result_text` are both required or the
    code is dropped and replaced with a null-flavor."""
    assert LabWriteBackResult.model_fields["test_code"].is_required()
    assert LabWriteBackResult.model_fields["test_name"].is_required()


# -- the response -----------------------------------------------------------


def test_the_response_returns_real_ids() -> None:
    """D1: "Returning ids is part of the decision, not a nicety" — it is
    what fixes the upload-returns-`true`, no-id race."""
    response = LabWriteBackResponse(
        patient_uuid=PATIENT,
        document_id=2820,
        document_outcome=WriteOutcome.CREATED,
        procedure_order_id=41,
        results=[
            LabWriteBackResultOutcome(
                test_code="2345-7",
                idempotency_key=key(),
                extraction_id=extraction(),
                outcome=WriteOutcome.CREATED,
                procedure_report_id=17,
                procedure_result_id=93,
            )
        ],
        correlation_id=CORRELATION_ID,
    )
    assert response.results[0].procedure_result_id == 93


def test_a_replay_is_reported_as_a_duplicate_not_an_error() -> None:
    outcome = LabWriteBackResultOutcome(
        test_code="2345-7",
        idempotency_key=key(),
        extraction_id=extraction(),
        outcome=WriteOutcome.DUPLICATE_IGNORED,
        procedure_report_id=17,
        procedure_result_id=93,
    )
    assert outcome.outcome is WriteOutcome.DUPLICATE_IGNORED


# -- the ledger row ---------------------------------------------------------


def ledger(**overrides: object) -> LabWriteBackLedgerRow:
    fields: dict[str, object] = {
        "idempotency_key": key(),
        "extraction_id": extraction(),
        "document_sha3_512": DIGEST,
        "document_id": 2820,
        "patient_uuid": PATIENT,
        "test_code": "2345-7",
        "collection_date": COLLECTED,
        "page": 1,
        "bbox": BBOX,
        "procedure_order_id": 41,
        "procedure_report_id": 17,
        "procedure_result_id": 93,
        "written_at": datetime(2026, 9, 21, 14, 0, 0),
        "extractor_version": "vlm-1.0",
    }
    fields.update(overrides)
    return LabWriteBackLedgerRow(**fields)  # type: ignore[arg-type]


def test_a_ledger_row_maps_an_extraction_id_to_a_page_and_a_region() -> None:
    """D1: `procedure_result.comments` carries an extraction id only; the
    ledger is what maps it to document id, page and bbox."""
    row = ledger()
    assert row.extraction_id == extraction()
    assert row.document_id == 2820
    assert (row.page, row.bbox.x0) == (1, 270.437)


def test_a_ledger_row_cannot_disagree_with_its_own_key() -> None:
    with pytest.raises(ValidationError, match="disagrees with its own components"):
        ledger(test_code="2339-0")


def test_a_ledger_row_cannot_disagree_with_its_own_bbox() -> None:
    with pytest.raises(ValidationError, match="disagrees with its bbox"):
        ledger(page=2)


def test_the_ledger_holds_no_chart_content() -> None:
    """Identifiers, codes and coordinates only — the same rule invariant 1
    applies to logs, so the table is safe to dump while debugging."""
    fields = set(LabWriteBackLedgerRow.model_fields)
    assert not fields & {
        "value",
        "result",
        "patient_name",
        "test_name",
        "comments",
        "note",
        "quote",
    }
