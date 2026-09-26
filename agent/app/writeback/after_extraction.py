"""
The write-back's trigger: a lab report's extraction has finished (D54).

`plan.py` decides which rows may be written and `client.py` posts them;
until D54 nothing called either on the live path, so an uploaded lab report
was extracted, cited and answered about, and never became a chart row.
This is the one caller. `app/api/documents.py` hands `lab_write_back()` to
the upload's extraction job, which runs it once the extraction is terminal.

**What is never written**, each checked here before the planner runs:

* **Anything that is not a lab report.** D2: intake medications and
  allergies are discrepancies against the chart, never chart entries. The
  planner raises on an intake form; this never lets one reach it.
* **A failed extraction.** It has no payload, and a failure is not a
  partial success (D7).
* **A document whose patient is not confirmed.** The identity check must
  have *matched* the chart's name and found no mismatch on either name or
  date of birth. `mismatch` is D35 item 10's hard flag; `not_comparable`
  (the name was not located, or the chart could not be read) is not
  written either: the identity check exists to catch a report filed on the
  wrong chart, and a check that could not run has not ruled that out.
* **A row the planner refuses**: unlocated, uncoded, undated, a range that
  is not a located fact, an undecidable or disputed flag (`plan.py`).

The rest is written unreviewed (D29) through the endpoint, idempotent per
row on D1's ledger key, so the same file extracted twice — a re-upload
after an agent restart — writes nothing new. The endpoint also checks the
chart itself, so the same results arriving in a *different* file (a
re-scan, a re-print, a report faxed twice) are `already_in_chart` and not
written again, and a row whose value disagrees with the chart's is a
`conflict`: written nowhere, and surfaced to the clinician with both
values, the way D2 surfaces intake medications.

**What comes back** is a `WriteBackSummary`: the decision, the counts, and
the conflicts. The document store keeps it with the document, and the
extraction-status route serves it to the chart card once the write-back
has ended. The counts are logged; the conflicts, which are chart content,
never are.

**Never raises.** The extraction is already complete and the clinician can
already ask about it; a write-back that fails is logged by reason and
counts, and the next extraction of the same bytes tries again under the
same keys (invariant 6). No value, test name, unit or patient identifier
is logged (invariant 1).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Awaitable, Callable

from app.graph.intake_extractor import EXTRACTOR_VERSION
from app.middleware.logging import log_event
from app.models.w2.documents import DocumentKind
from app.models.w2.lab_pdf import LabPdfExtraction
from app.models.w2.writeback import (
    LabWriteBackRequest,
    LabWriteBackResponse,
    WriteBackConflict,
    WriteBackDecision,
    WriteBackSummary,
    WriteOutcome,
)
from app.workers.intake_extractor.identity import IdentityComparison
from app.workers.intake_extractor.report import ExtractionReport
from app.writeback.client import write_lab_results
from app.writeback.exceptions import (
    WriteBackAuthError,
    WriteBackRefused,
    WriteBackUnavailable,
)
from app.writeback.plan import plan_write_back

#: `write_lab_results`, or a test's stand-in for it.
Writer = Callable[[str, LabWriteBackRequest], Awaitable[LabWriteBackResponse]]


__all__ = [
    "INCOMPLETE",
    "WriteBackDecision",
    "WriteBackSummary",
    "Writer",
    "identity_confirmed",
    "lab_write_back",
    "write_back_after_extraction",
    "write_back_owed",
]


#: Outcomes after which the rows the report can support are not known to be
#: in the chart: the write did not go through. A re-upload retries these
#: (D60); every other outcome either wrote or had a reason not to.
INCOMPLETE = frozenset(
    {
        WriteBackDecision.TOKEN_REFUSED,
        WriteBackDecision.REQUEST_REFUSED,
        WriteBackDecision.ENDPOINT_UNAVAILABLE,
    }
)


def write_back_owed(decision: str | None) -> bool:
    """
    Whether a re-upload should run the write-back again. None — no outcome
    recorded: never finished, or saved before outcomes were — counts as owed;
    the ledger makes a repeat idempotent.
    """
    return decision is None or decision in INCOMPLETE


def identity_confirmed(report: ExtractionReport) -> bool:
    """The document names this chart's patient, and nothing on it says otherwise."""
    identity = report.identity
    return (
        identity is not None
        and not identity.hard_flag
        and identity.name is IdentityComparison.MATCH
        and identity.date_of_birth is not IdentityComparison.MISMATCH
    )


async def write_back_after_extraction(
    report: ExtractionReport,
    *,
    token: str,
    patient_uuid: str,
    pdf: bytes,
    writer: Writer | None = None,
) -> WriteBackSummary:
    """
    Write `report`'s writable rows to the chart under `token`, or say why not.

    `writer` defaults to `write_lab_results`, looked up when called so the
    unit suite's conftest can keep every upload it drives off the network.
    """
    output = report.output
    base = {
        "document_id": output.document_id,
        "extraction_id": output.extraction_id,
        "correlation_id": output.correlation_id,
    }

    if output.kind is not DocumentKind.LAB_PDF:
        return _skipped(WriteBackDecision.NOT_LAB_REPORT, base)
    if not report.is_complete or not isinstance(output.payload, LabPdfExtraction):
        return _skipped(WriteBackDecision.EXTRACTION_NOT_COMPLETE, base)
    if not identity_confirmed(report):
        identity = report.identity
        return _skipped(
            WriteBackDecision.IDENTITY_NOT_CONFIRMED,
            {
                **base,
                "identity_name": identity.name.value if identity else None,
                "identity_date_of_birth": identity.date_of_birth.value if identity else None,
            },
        )

    extraction = output.payload
    plan = plan_write_back(
        extraction,
        patient_uuid=patient_uuid,
        document_sha3_512=extraction.document.checksums.sha3_512 or "",
        extractor_version=EXTRACTOR_VERSION,
        correlation_id=output.correlation_id,
        content=pdf,
    )
    counts = {
        **base,
        "results_total": len(extraction.results),
        "results_writable": plan.writable_count,
        "results_skipped_by_reason": dict(Counter(s.reason.value for s in plan.skipped)),
    }
    totals = {"results_total": len(extraction.results), "skipped": len(plan.skipped)}
    if plan.request is None:
        return _skipped(WriteBackDecision.NOTHING_WRITABLE, counts, totals)

    try:
        written = await (writer or write_lab_results)(token, plan.request)
    except WriteBackAuthError:
        return _skipped(WriteBackDecision.TOKEN_REFUSED, counts, totals)
    except WriteBackRefused as refused:
        # The endpoint's message names a field and a rule, never a value.
        return _skipped(
            WriteBackDecision.REQUEST_REFUSED,
            {**counts, "status_code": refused.status_code, "field": refused.field},
            totals,
        )
    except WriteBackUnavailable:
        return _skipped(WriteBackDecision.ENDPOINT_UNAVAILABLE, counts, totals)
    except Exception as error:  # noqa: BLE001 - the extraction must not be failed by its write-back
        return _skipped(
            WriteBackDecision.ENDPOINT_UNAVAILABLE,
            {**counts, "error_type": type(error).__name__},
            totals,
        )
    summary = summarise(plan.request, written, **totals)
    # `write_lab_results` has logged `lab_writeback_completed` with the ids.
    # Counts here too, so one event tells the whole story; never a conflict's
    # values.
    log_event(
        "lab_writeback_after_extraction",
        {
            **counts,
            "decision": "written",
            "results_created": summary.created,
            "results_duplicate_ignored": summary.duplicate_ignored,
            "results_already_in_chart": summary.already_in_chart,
            "results_conflict": summary.conflict,
        },
    )
    return summary


def summarise(
    request: LabWriteBackRequest,
    written: LabWriteBackResponse,
    *,
    results_total: int,
    skipped: int,
) -> WriteBackSummary:
    """
    The card's view of an answered write: counts per outcome, and each
    conflict with both values and both provenances. The request supplies
    the document side (value, unit, page, region), the response the chart
    side (value, unit, `procedure_result` id).
    """
    sent = {row.idempotency_key: row for row in request.results}
    outcome_counts = written.outcome_counts()
    conflicts: list[WriteBackConflict] = []
    for outcome in written.results:
        if outcome.outcome is not WriteOutcome.CONFLICT:
            continue
        row = sent.get(outcome.idempotency_key)
        if (
            row is None
            or outcome.matched_procedure_result_id is None
            or outcome.chart_value is None
        ):
            # The response model has already required these for a conflict;
            # a row the endpoint answered for that this request did not
            # send is not something to show as one of its values.
            continue
        conflicts.append(
            WriteBackConflict(
                test_code=row.test_code,
                test_name=row.test_name,
                collection_date=row.collection_date,
                collected_at=row.collected_at,
                page=row.page,
                bbox=row.bbox,
                document_value=row.value.as_text(),
                document_unit=row.unit,
                chart_value=outcome.chart_value,
                chart_unit=outcome.chart_unit,
                chart_procedure_result_id=outcome.matched_procedure_result_id,
            )
        )
    return WriteBackSummary(
        decision=WriteBackDecision.WRITTEN,
        results_total=results_total,
        skipped=skipped,
        created=outcome_counts.created,
        duplicate_ignored=outcome_counts.duplicate_ignored,
        already_in_chart=outcome_counts.already_in_chart,
        conflict=len(conflicts),
        conflicts=conflicts,
    )


def _skipped(
    decision: WriteBackDecision,
    fields: dict[str, object],
    totals: dict[str, int] | None = None,
) -> WriteBackSummary:
    log_event("lab_writeback_after_extraction", {**fields, "decision": decision.value})
    return WriteBackSummary(decision=decision, **(totals or {}))


def lab_write_back(
    token: str, patient_uuid: str, pdf: bytes
) -> Callable[[ExtractionReport], Awaitable[WriteBackSummary]]:
    """
    The upload's post-extraction step, bound to the uploading clinician's
    token (D1: every row is attributed to the user the token belongs to)
    and to the bytes the endpoint stores or matches by digest.
    """

    async def after(report: ExtractionReport) -> WriteBackSummary:
        return await write_back_after_extraction(
            report, token=token, patient_uuid=patient_uuid, pdf=pdf
        )

    return after
