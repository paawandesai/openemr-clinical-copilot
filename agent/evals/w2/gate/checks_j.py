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
from evals.w2.gate import phi_scanner, samples
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


@check("chk.j_real_retrieval_log_line_has_no_identifier")
def j_real_retrieval_log_line_has_no_identifier(case: Case) -> CheckOutcome:
    """
    J3 against the line the middleware would actually ship.

    Captured from a real retrieval with `log_event` intercepted, not copied
    into a fixture here — a copy would go on passing for ever after somebody
    added a field to the real one. Nineteen fields: counts, scores,
    milliseconds, ids. No query text, no chunk text, no patient anything.
    """
    del case
    event = samples.retrieval_log_event()

    expected = {
        "correlation_id",
        "method",
        "concept_count",
        "hit_count",
        "retrieval_scores",
        "rerank_scores",
        "reranked",
        "rerank_candidates",
        "rerank_over_budget",
        "rerank_unavailable",
        # Added by feat/w2-observability's reranker budget work and admitted
        # here on the doctrine above, not waved through: app/retrieval/worker.py
        # emits it as bool(rerank and rerank.busy), and a bool cannot carry
        # content whatever is on the right of it. Same shape as the three
        # rerank_* flags above it.
        "rerank_busy",
        # Added by chore/w2-deploy-rehearsal's circuit-breaker fix (D41) and
        # admitted on the same doctrine: app/retrieval/worker.py emits it as
        # bool(rerank and rerank.skipped_circuit_open), and the retriever sets
        # that to a literal True only when the circuit's own open-time and
        # skip counter say to skip. No query, chunk or patient input reaches it.
        "rerank_skipped_circuit_open",
        "lexical_ms",
        "dense_ms",
        "fusion_ms",
        "rerank_ms",
        "total_ms",
        "guideline_ids",
        "dated_hit_count",
    }
    added = set(event) - expected
    if added:
        return CheckOutcome.failed(
            f"the retrieval log line has grown {sorted(added)}. Every field in it is "
            "a count, a score, a duration or an id; a new one has to be shown to be "
            "one of those before this case is updated."
        )
    removed = expected - set(event)
    if removed:
        return CheckOutcome.failed(f"the retrieval log line lost {sorted(removed)}")

    hits = phi_scanner.scan(event, _canaries())
    if hits:
        return CheckOutcome.failed(
            f"{len(hits)} hit(s) in the real retrieval log line: "
            + "; ".join(str(h) for h in hits[:5])
        )

    # The query itself must not be in the line, under any field name.
    chunks = samples.retrieved_chunks()
    flat = json.dumps(event, default=str)
    for concept in samples._RETRIEVAL_CONCEPTS:
        if concept in flat:
            return CheckOutcome.failed(
                f"the log line carries the concept text {concept!r}; concepts are what "
                "a query is made of and the line is meant to carry counts of them"
            )
    for chunk in chunks:
        if chunk.text[:60] in flat:
            return CheckOutcome.failed(
                f"{chunk.chunk_id}: guideline chunk text reached the log line"
            )

    non_scalar = {
        key: type(value).__name__
        for key, value in event.items()
        if not isinstance(value, (str, int, float, bool, list))
    }
    if non_scalar:
        return CheckOutcome.failed(f"the log line carries nested structures {non_scalar}")
    return CheckOutcome.ok(
        f"{len(event)} fields, all counts/scores/durations/ids; zero canary and zero "
        "pattern hits; neither the concept text nor any chunk text is present"
    )


# --------------------------------------------------------------------------
# The root-run trace gap (docs/w2/root-run-trace-gap.md).
# --------------------------------------------------------------------------

#: Input keys a graph run may carry. Bookkeeping only.
SAFE_INPUT_KEYS = frozenset({"node_seconds"})
RUN_METHODS = frozenset(
    {"ainvoke", "invoke", "astream", "stream", "astream_events", "abatch", "batch"}
)


def _graph_names() -> set[str]:
    """Every module-level name in `app` bound to a graph object."""
    import importlib
    import pkgutil

    from langgraph.pregel import Pregel

    import app
    from app.graph.pre_visit_brief import UntracedGraph

    found: set[str] = set()
    for info in pkgutil.walk_packages(app.__path__, "app."):
        module = importlib.import_module(info.name)
        for name, value in vars(module).items():
            if isinstance(value, UntracedGraph | Pregel):
                found.add(name)
    return found


def _empty_literal(node: Any) -> bool:
    import ast

    if isinstance(node, ast.Dict | ast.List | ast.Tuple | ast.Set):
        return not getattr(node, "keys", None) and not getattr(node, "elts", None)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return node.func.id in {"dict", "list", "tuple", "set"} and not node.args
    return isinstance(node, ast.Constant) and node.value in (None, 0, 0.0, "")


def _receiver(node: Any) -> str | None:
    import ast

    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def scan_graph_inputs(source: str, where: str, graph_names: set[str]) -> list[str]:
    """Every way `source` runs a graph with something worth leaking in its input."""
    import ast

    problems: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assign):
            value = node.value
            if value is not None and _receiver(value) in graph_names:
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id not in graph_names:
                        problems.append(
                            f"{where}:{node.lineno}: graph aliased as {target.id!r}; "
                            "calls through it would go unchecked"
                        )
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr not in RUN_METHODS or _receiver(node.func.value) not in graph_names:
            continue
        arg = (
            node.args[0]
            if node.args
            else next((kw.value for kw in node.keywords if kw.arg in ("input", "inputs")), None)
        )
        if not isinstance(arg, ast.Dict):
            problems.append(f"{where}:{node.lineno}: graph input is not a dict literal")
            continue
        for key, value in zip(arg.keys, arg.values, strict=True):
            if not (isinstance(key, ast.Constant) and key.value in SAFE_INPUT_KEYS):
                label = key.value if isinstance(key, ast.Constant) else "**expansion"
                problems.append(f"{where}:{node.lineno}: input key {label!r} is not bookkeeping")
            elif not _empty_literal(value):
                problems.append(f"{where}:{node.lineno}: input {key.value!r} is not empty")
    return problems


@check("chk.j_graph_input_carries_nothing_worth_leaking")
def j_graph_input_carries_nothing_worth_leaking(case: Case) -> CheckOutcome:
    """
    The root-run trace gap, which the extractor branch found.

    Every node in a Week 2 graph runs inside Week 1's `_untraced` guard and
    refuses to execute with a LangSmith tracer attached (findings.md F-56).
    But a tracer attached to the compiled graph posts the ROOT RUN — carrying
    the caller's input dict verbatim — *before* any node gets the chance to
    refuse. `UntracedGraph` stops that on its own entry point; it cannot stop
    a caller that reaches the compiled graph another way, and the node guard
    cannot either.

    So the input has to hold nothing worth leaking, whatever path runs the
    graph. Per-run data — tokens, patient ids, document bytes, questions —
    belongs in LangGraph runtime *context*, which a tracer does not receive.

    `agent/tests/test_graph_inputs_carry_nothing.py` is the branch's own
    version with five controls. This is the gated copy, for the same reason
    W2-C8 duplicates the locate measurement: `agent/tests/` is not gated and
    the gate must hold a copy an implementation session cannot weaken. Its
    two controls below are what stop it passing because it stopped looking.
    """
    del case
    from pathlib import Path

    import app

    names = _graph_names()
    if not names:
        return CheckOutcome.failed(
            "no compiled graph found anywhere in app/. Either the graphs moved or "
            "discovery is broken — and a scanner that finds nothing reports nothing."
        )

    root = Path(app.__file__).resolve().parent
    problems: list[str] = []
    scanned = 0
    for path in sorted(root.rglob("*.py")):
        scanned += 1
        problems += scan_graph_inputs(
            path.read_text(encoding="utf-8"), path.relative_to(root).as_posix(), names
        )
    if problems:
        return CheckOutcome.failed(
            f"{len(problems)} graph run(s) in app/ carry something in their input: "
            + "; ".join(problems[:4])
        )

    # Controls: the scanner sees what it claims to.
    control_names = {"GRAPH"}
    caught = scan_graph_inputs(
        "async def run(pdf):\n    await GRAPH.ainvoke({'pdf_bytes': pdf})\n",
        "<control>",
        control_names,
    )
    if not caught:
        return CheckOutcome.failed(
            "the scanner did not catch document bytes in a graph input; it is not "
            "looking, and its silence on app/ means nothing"
        )
    aliased = scan_graph_inputs(
        "def run():\n    g = GRAPH\n    return g.invoke({'node_seconds': {}})\n",
        "<control>",
        control_names,
    )
    if not aliased:
        return CheckOutcome.failed("the scanner did not catch a graph rebound to another name")
    quiet = scan_graph_inputs(
        "async def run():\n    await GRAPH.ainvoke({'node_seconds': {}})\n",
        "<control>",
        control_names,
    )
    if quiet:
        return CheckOutcome.failed(
            f"the scanner fired on the safe shape: {quiet}. A check that rejects the "
            "correct call is one somebody deletes."
        )
    return CheckOutcome.ok(
        f"{len(names)} graph name(s), {scanned} modules scanned, zero runs carrying "
        "anything but empty bookkeeping; scanner controls all fire"
    )


def _capture_turn_event() -> dict[str, Any]:
    """
    The real `w2_ask_turn_completed` payload, captured rather than rebuilt.

    Same reasoning as `samples.retrieval_log_event`: a copy written here would
    pass for ever after somebody added a field to the real one.
    """
    from unittest.mock import patch

    from app.graph import supervisor
    from evals.w2.gate.checks_h import _ask_with_document, _Patcher
    from tests.w2_graph_support import LAB_SAMPLE, FakeChart

    captured: list[tuple[str, dict[str, Any]]] = []

    def _capture(event_type: str, event_data: dict[str, Any] | None = None, **_: Any) -> None:
        captured.append((event_type, dict(event_data or {})))

    # FakeChart keeps the turn offline; without it the chart read goes to a real
    # FHIR server and the deterministic tier stops being deterministic.
    with _Patcher() as patcher:
        FakeChart().install(patcher)
        with patch.object(supervisor, "log_event", _capture):
            _ask_with_document("What was her glucose on this report?", sample=LAB_SAMPLE)

    for event_type, event_data in captured:
        if event_type == supervisor.TURN_EVENT:
            return event_data
    raise GateError(
        f"an ask turn emitted {[e for e, _ in captured]} and no {supervisor.TURN_EVENT}; "
        "the line that records what a turn did is gone"
    )


@check("chk.j_turn_event_scope_fields_carry_no_content")
def j_turn_event_scope_fields_carry_no_content(case: Case) -> CheckOutcome:
    """
    D50's two new turn-event fields say how much was answered, never what.

    `answer_scope` is the interesting one. It is a dict, and a dict whose KEYS
    come from the data is how a count field turns into a content field without
    anyone editing a log line — `{"potassium": 1}` would be chart content
    reported as a tally. The keys have to be derived from `RowReason`, which is
    a closed enum, so this check computes the permitted key set from the enum
    rather than listing it: a new RowReason is fine, a key that is not one is
    not.
    """
    del case
    from app.supervisor.scope import RowReason

    event = _capture_turn_event()

    for field in ("answer_scope", "output_limited"):
        if field not in event:
            return CheckOutcome.failed(
                f"the turn event has no {field!r}. D50 added it; either it was removed "
                "or this case is watching the wrong event."
            )

    limited = event["output_limited"]
    if not isinstance(limited, bool):
        return CheckOutcome.failed(
            f"output_limited is {type(limited).__name__}, not a bool: {limited!r}. A "
            "bool cannot carry content; anything else can."
        )

    scope = event["answer_scope"]
    if not isinstance(scope, dict):
        return CheckOutcome.failed(f"answer_scope is {type(scope).__name__}, not a dict")

    permitted = {f"itemised_{reason.value}" for reason in RowReason} | {
        "summarised",
        "capped",
        "capped_abnormal",
    }
    stray = sorted(set(scope) - permitted)
    if stray:
        return CheckOutcome.failed(
            f"answer_scope has key(s) {stray}, which are not derived from RowReason. "
            "A key taken from the data turns this tally into chart content — "
            "'which analyte' is exactly what the critical_values counts next to it "
            "are careful not to say."
        )
    bad = {k: v for k, v in scope.items() if not isinstance(v, int) or isinstance(v, bool)}
    if bad:
        return CheckOutcome.failed(
            f"answer_scope values must be ints; these are not: {sorted(bad)}"
        )

    return CheckOutcome.ok(
        f"answer_scope is {len(scope)} int-valued key(s), every one derived from "
        f"RowReason or a fixed bucket ({sorted(scope)}); output_limited is a bool. "
        "Neither says which analyte."
    )


@check("chk.j_rerank_circuit_events_carry_no_content")
def j_rerank_circuit_events_carry_no_content(case: Case) -> CheckOutcome:
    """
    D41's circuit breaker opens and closes on its own and logs both.

    These fire on a latency path, which is where log lines get written in a
    hurry and reviewed least. The circuit is driven here through its own clock
    rather than by waiting on a real slow rerank — the assertion is about the
    payload's shape, and a check that needed a live cross-encoder could not run
    in the deterministic tier at all.
    """
    del case
    from unittest.mock import patch

    from app.retrieval import retriever as retriever_module
    from app.retrieval.retriever import (
        RERANK_CIRCUIT_CLOSED_EVENT,
        RERANK_CIRCUIT_OPENED_EVENT,
        GuidelineRetriever,
    )

    now = [1000.0]
    # load() does not forward the clock, so it is set after construction. The
    # circuit reads self._clock on every transition, so this is the same seam
    # the constructor parameter exposes, not a way around it.
    retriever = GuidelineRetriever.load()
    retriever._clock = lambda: now[0]
    retriever.circuit_reset_seconds = 30.0

    captured: list[tuple[str, dict[str, Any]]] = []

    def _capture(event_type: str, event_data: dict[str, Any] | None = None, **_: Any) -> None:
        captured.append((event_type, dict(event_data or {})))

    with patch.object(retriever_module, "log_event", _capture):
        retriever._open_circuit(4.2)
        now[0] += 31.0
        retriever._circuit_skips_this_turn()

    events = dict(captured)
    expected = {
        RERANK_CIRCUIT_OPENED_EVENT: {
            "elapsed_ms",
            "budget_ms",
            "reset_seconds",
            "reset_turns",
        },
        RERANK_CIRCUIT_CLOSED_EVENT: {"reason", "open_seconds", "skipped_turns"},
    }
    for name, fields in expected.items():
        if name not in events:
            return CheckOutcome.failed(
                f"driving the circuit open and shut emitted {sorted(events)} and no "
                f"{name}. The breaker changed state and said nothing."
            )
        added = sorted(set(events[name]) - fields)
        if added:
            return CheckOutcome.failed(
                f"{name} has grown {added}. Every field in these two is a duration, a "
                "count or a fixed reason; a new one has to be shown to be one of those "
                "before this case is updated."
            )

    reason = events[RERANK_CIRCUIT_CLOSED_EVENT]["reason"]
    if reason not in {"manual", "timeout", "turns"}:
        return CheckOutcome.failed(
            f"the close reason is {reason!r}, which is not one of the three the code "
            "sets. A free-string reason is a content channel."
        )
    for name in expected:
        wrong = {
            k: v
            for k, v in events[name].items()
            if k != "reason" and not isinstance(v, int | float)
        }
        if wrong:
            return CheckOutcome.failed(f"{name} has non-numeric value(s): {sorted(wrong)}")

    return CheckOutcome.ok(
        f"{RERANK_CIRCUIT_OPENED_EVENT} and {RERANK_CIRCUIT_CLOSED_EVENT} both fired; "
        f"every field is a duration or a count, and the close reason is the enum-ish "
        f"{reason!r}. No query, no chunk, no patient anything."
    )
