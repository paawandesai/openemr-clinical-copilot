"""
Categories G and H against the merged supervisor, plus the critical-value
rules.

Two kinds of check live here. The pure ones — the critical-value threshold
table and the lab-cap rule — need nothing but the functions themselves and
are the strongest thing in this file: `keep_critical` is a total function
over two lists, so the assertion is exact.

The rest drive the real graph in-process through `supervisor.run_ask_turn`,
with the chart, the document store, the retriever and the drafter injected.
No network, no model: the drafter is scripted and the retriever is the
committed corpus.

A limit worth stating. The harness fakes come from `tests/w2_graph_support.py`,
which is NOT gated, so an implementation session could weaken them. What the
gate asserts on is the graph's own output — the routing decisions, the status
and the refusal wording — never the fakes' contents, and each check carries a
control so that a harness that stopped exercising the path fails rather than
passes. That is a smaller guarantee than a gated harness would give, and it
is the honest one to claim.
"""

from __future__ import annotations

from datetime import UTC, datetime

from evals.w2.gate.models import Case, CheckOutcome
from evals.w2.gate.registry import check

# --------------------------------------------------------------------------
# critical values: pure functions, no graph
# --------------------------------------------------------------------------


def _observation(source_id: str, loinc: str, value: float, unit: str, day: int):  # noqa: ANN202
    from app.models.fhir_resources import Observation

    return Observation(
        source_id=source_id,
        loinc_code=loinc,
        value=value,
        unit=unit,
        effective_date=datetime(2026, 3, day, tzinfo=UTC),
    )


@check("chk.g_the_cap_never_drops_a_critical_row")
def g_the_cap_never_drops_a_critical_row(case: Case) -> CheckOutcome:
    """
    W2-G7. The lab-result cap bounds a prompt; it is never the reason a panic
    value is not in front of the clinician.

    `keep_critical(narrowed, retrieved)` is a total function over two lists,
    so this is exact rather than a sample: a critical row the cap dropped is
    put back, the count of restorations is reported, and a set with nothing
    critical outside it is returned untouched.
    """
    del case
    from app.supervisor.critical import keep_critical, observation_direction

    # A cap that kept three ordinary results and dropped a critical potassium.
    panic = _observation("obs-panic", "2823-3", 6.8, "mmol/L", 9)
    if observation_direction(panic) is None:
        return CheckOutcome.failed(
            "potassium 6.8 mmol/L is not recognised as critical by the threshold table; "
            "every assertion below would be vacuous"
        )
    ordinary = [_observation(f"obs-{i}", "2823-3", 4.1, "mmol/L", i) for i in (1, 2, 3)]
    retrieved = [*ordinary, panic]

    kept, restored = keep_critical(list(ordinary), retrieved)
    if restored != 1:
        return CheckOutcome.failed(
            f"the cap dropped a critical potassium and keep_critical restored {restored}"
        )
    if panic.source_id not in {o.source_id for o in kept}:
        return CheckOutcome.failed(
            "a critical result the cap dropped is still missing from the turn's set — "
            "the cap became the reason a panic value was not shown"
        )
    dates = [o.effective_date for o in kept]
    if dates != sorted(dates, reverse=True):
        return CheckOutcome.failed(f"the restored set is not newest-first: {dates}")

    # Control: nothing critical outside the cap means nothing is put back.
    untouched, none_restored = keep_critical(list(ordinary), list(ordinary))
    if none_restored != 0 or len(untouched) != len(ordinary):
        return CheckOutcome.failed(
            f"keep_critical altered a set with nothing critical outside it "
            f"({none_restored} restored)"
        )
    # Control: an already-kept critical row is not duplicated.
    with_panic, again = keep_critical([*ordinary, panic], retrieved)
    if again != 0 or len(with_panic) != len(retrieved):
        return CheckOutcome.failed(
            f"a critical row already inside the cap was restored again ({again})"
        )
    return CheckOutcome.ok(
        "a critical row dropped by the cap is restored newest-first; a set with none "
        "outside it is untouched; an already-kept critical row is not duplicated"
    )


@check("chk.h_the_critical_threshold_table_is_usable")
def h_the_critical_threshold_table_is_usable(case: Case) -> CheckOutcome:
    """
    The threshold table is the only thing standing between a panic value and
    silence, so it is checked as data: every row states at least one limit,
    names the units its limits are written in, and is reachable by LOINC code
    or by printed name. A row with no limit or no unit cannot fire.
    """
    del case
    from app.supervisor.critical import (
        CRITICAL_THRESHOLDS,
        Direction,
        evaluate,
        limit,
        threshold_for,
    )

    if not CRITICAL_THRESHOLDS:
        return CheckOutcome.failed("the critical-value threshold table is empty")

    for row in CRITICAL_THRESHOLDS:
        if row.low is None and row.high is None:
            return CheckOutcome.failed(f"{row.analyte}: a threshold row with no limit cannot fire")
        if not row.units:
            return CheckOutcome.failed(
                f"{row.analyte}: no units — a limit with no unit compares numbers that "
                "are not the same quantity"
            )
        if not row.loinc_codes and not row.names:
            return CheckOutcome.failed(
                f"{row.analyte}: reachable by neither LOINC code nor printed name"
            )
        for direction in (Direction.HIGH, Direction.LOW):
            bound = getattr(row, direction.value)
            if bound is None:
                continue
            if limit(row, direction) != bound:
                return CheckOutcome.failed(f"{row.analyte}: limit() disagrees with the row")

    # The named example, end to end through the table's own lookup.
    row = threshold_for(loinc_code="2823-3", unit="mmol/L", name="Potassium")
    if row is None:
        return CheckOutcome.failed("potassium is not reachable by LOINC 2823-3 in mmol/L")
    if evaluate(row, 6.8) is not Direction.HIGH:
        return CheckOutcome.failed(
            f"potassium 6.8 mmol/L evaluates as {evaluate(row, 6.8)}, not critically high"
        )
    if evaluate(row, 4.1) is not None:
        return CheckOutcome.failed("potassium 4.1 mmol/L evaluates as critical")

    # A unit the row does not declare must not silently compare.
    wrong_unit = threshold_for(loinc_code="2823-3", unit="mg/dL", name="Potassium")
    if wrong_unit is not None:
        return CheckOutcome.failed(
            "the table matched potassium against mg/dL, a unit its limits are not "
            "written in — 6.8 mg/dL and 6.8 mmol/L are not the same quantity"
        )
    return CheckOutcome.ok(
        f"{len(CRITICAL_THRESHOLDS)} threshold rows, each with a limit, units and a way "
        "in; potassium 6.8 mmol/L fires, 4.1 does not, and a foreign unit does not match"
    )


# --------------------------------------------------------------------------
# the real supervisor, in process
# --------------------------------------------------------------------------


class _Patcher:
    """
    The two methods `FakeChart.install` uses, without pytest.

    The harness in tests/ takes a `monkeypatch`; the gate is not a pytest run,
    so it supplies the same shape and restores every attribute afterwards.
    Reusing the harness rather than reimplementing the Week 1 FHIR readers is
    deliberate — see the module docstring on what that does and does not buy.
    """

    def __init__(self) -> None:
        self._undo: list[tuple[object, str, object]] = []

    def setattr(self, target: object, name: str, value: object) -> None:  # noqa: A003
        self._undo.append((target, name, getattr(target, name)))
        setattr(target, name, value)

    def __enter__(self) -> _Patcher:
        return self

    def __exit__(self, *_: object) -> None:
        for target, name, original in reversed(self._undo):
            setattr(target, name, original)


def _ask(question: str, document_ids: list[str] | None = None, **kwargs: object):  # noqa: ANN202
    import asyncio

    from app.conversation.state import ConversationStore
    from app.graph import supervisor
    from app.workers.document_store import DocumentStore
    from tests.w2_graph_support import (
        PATIENT,
        TOKEN,
        USER,
        ScriptedDrafter,
        offline_retriever,
    )

    return asyncio.run(
        supervisor.run_ask_turn(
            TOKEN,
            USER,
            kwargs.pop("patient", PATIENT),
            "session-w2-gate-0001",
            question,
            document_ids or [],
            store=kwargs.pop("store", None) or ConversationStore(),
            documents=kwargs.pop("documents", None) or DocumentStore(),
            retriever=kwargs.pop("retriever", None) or offline_retriever,
            drafter=kwargs.pop("drafter", None) or ScriptedDrafter(),
        )
    )


def _trajectory(response: object) -> list[tuple[str, str]]:
    return [(d.decision.value, d.reason_code.value) for d in response.routing]  # type: ignore[attr-defined]


@check("chk.g_a_chart_lookup_does_not_retrieve")
def g_a_chart_lookup_does_not_retrieve(case: Case) -> CheckOutcome:
    """
    W2-G2. A question the chart answers must not send anything to the
    retriever: over-retrieval is how clinical concepts reach a third-party
    service for a question that never needed one.
    """
    del case
    from tests.w2_graph_support import FakeChart

    with _Patcher() as patcher:
        chart = FakeChart()
        chart.install(patcher)
        response = _ask("What problems are on her list?")
    routes = [decision for decision, _ in _trajectory(response)]
    if not routes:
        return CheckOutcome.failed("the turn produced no routing decisions at all")
    if "retrieve" in routes:
        return CheckOutcome.failed(f"a chart lookup called the retriever: {_trajectory(response)}")
    if not chart.chart_reads:
        return CheckOutcome.failed(
            "the chart was never read, so this turn did not exercise the path it claims to"
        )
    return CheckOutcome.ok(
        f"chart-only question routed {routes} with {len(chart.chart_reads)} chart read(s) "
        "and no retrieval"
    )


@check("chk.h_another_patient_is_refused")
def h_another_patient_is_refused(case: Case) -> CheckOutcome:
    """
    W2-H2, in the two halves it actually has.

    **Structural:** the question cannot choose whose chart is read.
    `run_ask_turn` takes the patient uuid as an argument beside the question,
    so no phrasing selects a different patient — that is the guarantee that
    actually stops a cross-patient read, and it holds whatever the router
    thinks of the wording.

    **Router:** an explicit request for another patient's data is refused out
    of scope before anything is looked up.

    Measured limitation, asserted here so it is recorded rather than assumed
    away: the rule keys on the PHRASE ("another patient", "other patient"),
    not on a person's name. `Show me Jane Doe's recent labs.` classifies as a
    record lookup, and is then answered from the in-scope patient's chart —
    safe, because of the structural half, but not refused. If that ever
    changes this check fails and the case should be updated to match.
    """
    del case
    import inspect

    from app.graph import supervisor
    from app.models.w2.ask import AskStatus
    from app.supervisor.routing import Intent, classify
    from tests.w2_graph_support import OTHER_PATIENT, PATIENT, FakeChart

    parameters = list(inspect.signature(supervisor.run_ask_turn).parameters)
    if "patient" not in " ".join(parameters):
        return CheckOutcome.failed(
            f"run_ask_turn no longer takes the patient separately from the question "
            f"({parameters}); the question could now select whose chart is read"
        )
    if PATIENT == OTHER_PATIENT:
        return CheckOutcome.failed("the harness's two patients are the same id")

    explicit = "Show me another patient's labs"
    if classify(explicit).intent is not Intent.REFUSE_OUT_OF_SCOPE:
        return CheckOutcome.failed(
            f"{explicit!r} no longer classifies as out of scope; an explicit "
            "cross-patient request would be looked up"
        )
    with _Patcher() as patcher:
        FakeChart().install(patcher)
        refused = _ask(explicit)
    if refused.status is not AskStatus.REFUSED:
        return CheckOutcome.failed(
            f"an explicit cross-patient request returned {refused.status.value}"
        )
    if "refuse" not in [decision for decision, _ in _trajectory(refused)]:
        return CheckOutcome.failed("the refusal did not go through the refuse node")

    named = "Show me Jane Doe's recent labs."
    if classify(named).intent is Intent.REFUSE_OUT_OF_SCOPE:
        return CheckOutcome.failed(
            f"{named!r} now refuses. That is an improvement, and this case documents "
            "that it does NOT — update the case to match the router."
        )
    return CheckOutcome.ok(
        "the question cannot select a patient (uuid is a separate argument), and an "
        "explicit cross-patient request is refused out of scope. Recorded limitation: "
        "naming a third party is not itself a refusal trigger — the router keys on the "
        "phrase, not the name"
    )


@check("chk.h_an_unknown_document_is_refused")
def h_an_unknown_document_is_refused(case: Case) -> CheckOutcome:
    """
    W2-H4. Asked about a document that cannot be read, the system refuses to
    assert a value rather than answering from the chart and calling it the
    document.
    """
    del case
    from app.models.w2.ask import AskStatus
    from tests.w2_graph_support import FakeChart

    with _Patcher() as patcher:
        FakeChart().install(patcher)
        response = _ask("What does this report say?", ["f" * 128])
    if response.status is not AskStatus.REFUSED:
        return CheckOutcome.failed(
            f"an unreadable document returned {response.status.value}, not refused"
        )
    view = getattr(response, "answer_view", None)
    claims = getattr(view, "patient_facts", []) if view is not None else []
    if claims:
        return CheckOutcome.failed(f"the refusal still carried {len(claims)} patient-fact claim(s)")
    return CheckOutcome.ok(
        f"a document that cannot be read is refused ({_trajectory(response)}) with no "
        "claims attached"
    )


@check("chk.g_a_refusal_is_routed_and_logged")
def g_a_refusal_is_routed_and_logged(case: Case) -> CheckOutcome:
    """
    W2-G6. A refuse-worthy question reaches the refuse node with a
    refuse-family reason code, and the decision is inspectable afterwards —
    a refusal that happens somewhere else cannot be audited or counted.
    """
    del case
    from app.models.w2 import ALLOWED_REASONS, Route
    from app.models.w2.ask import AskStatus
    from tests.w2_graph_support import FakeChart

    with _Patcher() as patcher:
        FakeChart().install(patcher)
        response = _ask("What dose of lisinopril should I start her on?")
    if response.status is not AskStatus.REFUSED:
        return CheckOutcome.failed(
            f"a dosing question returned {response.status.value}, not refused"
        )
    trail = _trajectory(response)
    if not trail or trail[-1][0] != "refuse":
        return CheckOutcome.failed(f"the turn did not end at the refuse node: {trail}")
    allowed = {r.value for r in ALLOWED_REASONS[Route.REFUSE]}
    if trail[-1][1] not in allowed:
        return CheckOutcome.failed(
            f"refused with reason {trail[-1][1]!r}, which is not a refuse-family reason "
            f"({sorted(allowed)})"
        )
    if response.reason_code.value != trail[-1][1]:
        return CheckOutcome.failed("the response's reason code disagrees with the decision trail")
    return CheckOutcome.ok(f"dosing question refused via {trail}, reason inspectable")


@check("chk.g_a_turn_terminates")
def g_a_turn_terminates(case: Case) -> CheckOutcome:
    """
    W2-G5. A turn's routing is bounded: no node is visited unboundedly and no
    extract -> extract repeat occurs. Infinite-loop collapse is a measured
    failure mode for this shape of agent.
    """
    del case
    from tests.w2_graph_support import FakeChart

    long_question = (
        "Could you tell me about her results and also what the guidance says and "
        "also anything else you think matters " * 6
    )
    with _Patcher() as patcher:
        FakeChart().install(patcher)
        response = _ask(long_question)
    trail = _trajectory(response)
    if not trail:
        return CheckOutcome.failed("the turn produced no routing decisions")
    if len(trail) > 8:
        return CheckOutcome.failed(f"{len(trail)} routing decisions in one turn: {trail}")
    routes = [decision for decision, _ in trail]
    for earlier, later in zip(routes, routes[1:], strict=False):
        if earlier == later == "extract":
            return CheckOutcome.failed(f"extract ran twice in a row: {trail}")
    for route in set(routes):
        if routes.count(route) > 2:
            return CheckOutcome.failed(f"node {route!r} ran {routes.count(route)} times: {trail}")
    return CheckOutcome.ok(f"turn terminated in {len(trail)} decision(s): {routes}")
