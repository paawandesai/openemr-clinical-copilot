"""
Posting a write-back under the clinician's own token — D1's custom endpoint.

ARCHITECTURE.md §2: "The agent has no identity of its own with respect to
patient data." That was written about reads and it binds a write twice as
hard. So `write_lab_results()` takes the bearer token on every call, and
there is no client_id, client_secret or token cache anywhere in this file.

**Which token.** The clinician's, brokered by the chart embed from their
OpenEMR session (`ClinicianTokenBroker::issueFor()`), exactly as for the
Pre-Visit Brief. That is not a convenience: OpenEMR attributes every row
the endpoint writes to the user the token belongs to — `procedure_order.
provider_id`, `procedure_report.source`, `documents.owner`, and the
`authUser` on every audit row. Writing under a service account would put
a chart entry in a patient's record with nobody's name on it. The
password-grant client the tests use is a test fixture and must not be
used to write on a clinician's behalf.

**Which scopes.** The embed's client must carry `api:oemr` (OpenEMR
rejects *every* standard-API request without it, before the resource scope
is looked at) and `user/lab-writeback.c`. Scopes are immutable per client
in OpenEMR, so this means re-registering the embed client — the steps are
in `docs/w2/deploy-writeback.md`.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from app.middleware.logging import log_event
from app.middleware.telemetry import step, tool_call
from app.models.telemetry_events import Pipeline, Tool
from app.models.w2.writeback import LabWriteBackRequest, LabWriteBackResponse, WriteOutcome
from app.writeback.exceptions import (
    WriteBackAuthError,
    WriteBackRefused,
    WriteBackUnavailable,
)

#: The route the module registers (`LabWriteBackApi::ROUTE`). Derived from
#: FHIR_BASE_URL because both hang off the same `/apis/default/` prefix and
#: a deployment that moves one moves the other.
_DEFAULT_FHIR_BASE = "https://64.177.42.210:9300/apis/default/fhir"
WRITEBACK_PATH = "/api/clinical-copilot/lab-writeback"


def writeback_url() -> str:
    base = os.environ.get("FHIR_BASE_URL", _DEFAULT_FHIR_BASE).rstrip("/")
    if base.endswith("/fhir"):
        base = base[: -len("/fhir")]
    return base + WRITEBACK_PATH


_VERIFY_TLS = os.environ.get("FHIR_VERIFY_TLS", "true").strip().lower() not in (
    "false",
    "0",
    "no",
)

#: Longer than a FHIR read: the request carries a base64 PDF and the
#: endpoint does a multi-table write inside one transaction.
_TIMEOUT_SECONDS = 60.0


async def write_lab_results(
    token: str,
    request: LabWriteBackRequest,
    *,
    client: httpx.AsyncClient | None = None,
) -> LabWriteBackResponse:
    """
    POST one document and its results; return the ids OpenEMR wrote.

    Safe to retry on `WriteBackUnavailable`: the endpoint's ledger has a
    UNIQUE key over D1's (document digest, test code, collection date), so
    a repeat either creates the rows or returns the ids of the write that
    already did (CLAUDE.md invariant 6).
    """
    with step(Pipeline.WRITEBACK, "writeback") as writeback:
        writeback.counts["results_total"] = len(request.results)
        written = await _post(token, request, client)
        counts = written.outcome_counts()
        writeback.counts.update(
            results_created=counts.created,
            results_duplicate_ignored=counts.duplicate_ignored,
            results_already_in_chart=counts.already_in_chart,
            results_conflict=counts.conflict,
        )
    _log_outcome(request, written)
    return written


async def _post(
    token: str, request: LabWriteBackRequest, client: httpx.AsyncClient | None
) -> LabWriteBackResponse:
    owns_client = client is None
    http = client or httpx.AsyncClient(verify=_VERIFY_TLS, timeout=_TIMEOUT_SECONDS)
    # One logical call, one attempt: this client never retries on its own.
    # A caller that retries on WriteBackUnavailable makes a second tool call
    # under the same idempotency key, and the ledger answers it (invariant 6).
    with tool_call(Tool.LAB_WRITEBACK) as call:
        try:
            response = await http.post(
                writeback_url(),
                content=request.model_dump_json(),
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )
        except httpx.HTTPError as error:
            # Only the exception's class name: httpx puts the URL in its message,
            # and a URL with a patient uuid in it is an identifier in a log line
            # nobody asked for. The original is chained for a traceback, where it
            # still never carries the request body (CLAUDE.md invariant 1).
            raise WriteBackUnavailable(
                f"write-back request did not complete: {type(error).__name__}"
            ) from error
        finally:
            if owns_client:
                await http.aclose()

        call.status_code = response.status_code
        _raise_for_status(response, request)
    # model_validate_json, not model_validate on a parsed dict: under
    # strict=True the enums only read from JSON (D22).
    return LabWriteBackResponse.model_validate_json(response.content)


def _raise_for_status(response: httpx.Response, request: LabWriteBackRequest) -> None:
    """
    Map the endpoint's statuses onto the three exception types.

    The endpoint answers a refusal with `{"error", "message", "field"}` and
    its message names fields and states rules — it never quotes the request
    — so it is safe to carry into an exception. A 5xx body is not, and is
    replaced with a fixed string.
    """
    if response.status_code == 200:
        return
    if response.status_code in (401, 403):
        raise WriteBackAuthError(
            f"OpenEMR rejected the write-back token with {response.status_code}; the client "
            "must carry api:oemr and user/lab-writeback.c, and the user needs the "
            "patients/docs and patients/lab ACLs"
        )
    if response.status_code in (400, 404, 409, 415):
        detail = _refusal(response)
        raise WriteBackRefused(
            detail.get("message", "the write-back was refused"),
            field=detail.get("field"),
            status_code=response.status_code,
        )
    log_event(
        "lab_writeback_unavailable",
        {
            "status_code": response.status_code,
            "extraction_id": request.extraction_id,
            "results_total": len(request.results),
        },
    )
    raise WriteBackUnavailable(f"the write-back endpoint returned {response.status_code}")


def _refusal(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _log_outcome(request: LabWriteBackRequest, written: LabWriteBackResponse) -> None:
    """
    Identifiers, ids and counts. No test code, no value, no unit, no
    patient name — CLAUDE.md invariant 1. `patient_uuid` is deliberately
    absent too: the ids below identify the write without it, and a log line
    that pairs a patient with a lab write says more than it needs to.
    """
    counts = written.outcome_counts()
    log_event(
        "lab_writeback_completed",
        {
            "extraction_id": request.extraction_id,
            "extractor_version": request.extractor_version,
            "document_id": written.document_id,
            "document_outcome": written.document_outcome.value,
            "procedure_order_id": written.procedure_order_id,
            "results_total": len(written.results),
            "results_created": counts.created,
            "results_duplicate_ignored": counts.duplicate_ignored,
            "results_already_in_chart": counts.already_in_chart,
            "results_conflict": counts.conflict,
            "procedure_result_ids": [
                r.procedure_result_id
                for r in written.results
                if r.outcome is WriteOutcome.CREATED or r.outcome is WriteOutcome.DUPLICATE_IGNORED
            ],
            # The chart rows a document matched or disagrees with: ids only.
            "matched_procedure_result_ids": [
                r.matched_procedure_result_id
                for r in written.results
                if r.matched_procedure_result_id is not None
            ],
        },
    )
