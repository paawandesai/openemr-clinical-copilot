"""
The agent's half of the write-back: what it refuses to send, and how it
sends the rest.

The refusals carry the weight here. Every one of them is a rule that
already exists somewhere else — invariant 4, D1's key, D3's cross-check —
applied at the last point before a value becomes a row in someone's chart.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from app.models.w2.citations import BBox, Citation
from app.models.w2.documents import (
    DocumentChecksums,
    DocumentKind,
    DocumentPatientHeader,
    SourceDocument,
)
from app.models.w2.extraction import ExtractedField
from app.models.w2.lab_pdf import LabOrderHeader, LabPdfExtraction, LabResult
from app.models.w2.lab_values import (
    AbnormalFlag,
    NumericLabValue,
    ReferenceRange,
    ReportedFlag,
    parse_reference_range_text,
)
from app.models.w2.writeback import (
    WriteOutcome,
    document_extraction_id,
    lab_result_extraction_id,
    lab_result_idempotency_key,
)
from app.writeback.client import write_lab_results, writeback_url
from app.writeback.exceptions import (
    WriteBackAuthError,
    WriteBackRefused,
    WriteBackUnavailable,
)
from app.writeback.plan import SkipReason, plan_write_back

DIGEST = "d" * 128
EXTRACTION_ID = "w2x-" + "2" * 32
PATIENT = "a2c36e54-af42-4c38-b86e-ade616ed5494"
CORRELATION_ID = "11111111-2222-4333-8444-555555555555"
EXTRACTOR = "vlm-1.0"
COLLECTED = date(2026, 8, 30)


def citation(quote: str, *, bbox: BBox | None = None) -> Citation:
    return Citation.located_document(
        source_id="lab-clean-001",
        field_path="results.cmp.glucose.value",
        quote=quote,
        bbox=bbox or BBox(page=1, x0=270.0, y0=256.0, x1=280.0, y1=268.0),
        extraction_id=EXTRACTION_ID,
    )


def glucose(
    *,
    value: float = 89.0,
    loinc: str | None = "2345-7",
    flag: ReportedFlag = ReportedFlag.NOT_REPORTED,
    reference: str | None = "70 - 99",
    located_value: bool = True,
    located_date: bool = True,
    located_name: bool = True,
    test_key: str = "glucose",
) -> LabResult:
    reference_field: ExtractedField[ReferenceRange] = (
        ExtractedField[ReferenceRange].located(
            parse_reference_range_text(reference, unit="mg/dL"), citation(reference)
        )
        if reference is not None
        else ExtractedField[ReferenceRange].missing()
    )
    return LabResult(
        panel="cmp",
        test_key=test_key,
        test_name=(
            ExtractedField[str].located("Glucose", citation("Glucose"))
            if located_name
            else ExtractedField[str].missing()
        ),
        value=(
            ExtractedField[NumericLabValue].located(
                NumericLabValue(value=value), citation(str(value))
            )
            if located_value
            else ExtractedField[NumericLabValue].missing()
        ),
        unit=ExtractedField[str].located("mg/dL", citation("mg/dL")),
        reference_range=reference_field,
        collection_date=(
            ExtractedField[date].located(COLLECTED, citation("2026-08-30"))
            if located_date
            else ExtractedField[date].missing()
        ),
        reported_flag=ExtractedField[ReportedFlag].located(flag, citation("")),
        loinc_code=loinc,
    )


def extraction(
    results: list[LabResult] | None = None,
    *,
    kind: DocumentKind = DocumentKind.LAB_PDF,
    reported_at: datetime | None = datetime(2026, 8, 30, 9, 30, tzinfo=UTC),
) -> LabPdfExtraction:
    order = LabOrderHeader(
        reported_at=(
            ExtractedField[datetime].located(reported_at, citation("2026-08-30 09:30"))
            if reported_at is not None
            else ExtractedField[datetime].missing()
        )
    )
    return LabPdfExtraction(
        extraction_id=EXTRACTION_ID,
        document=SourceDocument(
            document_id="lab-clean-001",
            kind=kind,
            filename="lab-clean-001.pdf",
            mime_type="application/pdf",
            page_count=1,
            byte_length=31851,
            checksums=DocumentChecksums(sha3_512=DIGEST),
            has_text_layer=True,
        ),
        patient_header=DocumentPatientHeader(
            name=ExtractedField[str].missing(),
            date_of_birth=ExtractedField[str].missing(),
            sex=ExtractedField[str].missing(),
            address=ExtractedField[str].missing(),
            city_state_zip=ExtractedField[str].missing(),
        ),
        order=order,
        results=results if results is not None else [glucose()],
    )


def plan(results: list[LabResult] | None = None, **kwargs: Any):  # noqa: ANN201
    return plan_write_back(
        extraction(results, **kwargs),
        patient_uuid=PATIENT,
        document_sha3_512=DIGEST,
        extractor_version=EXTRACTOR,
        correlation_id=CORRELATION_ID,
        content=b"%PDF-1.4 ...",
    )


# -- what gets built --------------------------------------------------------


def test_a_located_coded_row_becomes_a_writable_result() -> None:
    built = plan()

    assert built.skipped == []
    assert built.request is not None
    assert built.writable_count == 1
    result = built.request.results[0]
    assert result.test_code == "2345-7"
    assert result.test_name == "Glucose"
    assert result.unit == "mg/dL"
    assert result.abnormal is AbnormalFlag.NORMAL
    assert result.reported_at == datetime(2026, 8, 30, 9, 30, tzinfo=UTC)


def test_the_keys_are_the_contracts_own_derivation() -> None:
    """Not re-implemented here: the same functions the endpoint recomputes."""
    built = plan()
    assert built.request is not None
    result = built.request.results[0]

    assert result.idempotency_key == lab_result_idempotency_key(
        document_sha3_512=DIGEST, test_code="2345-7", collection_date=COLLECTED
    )
    assert result.extraction_id == lab_result_extraction_id(
        document_sha3_512=DIGEST, test_code="2345-7", collection_date=COLLECTED
    )
    assert built.request.extraction_id == document_extraction_id(
        document_sha3_512=DIGEST, extractor_version=EXTRACTOR
    )
    assert built.request.extractor_version == EXTRACTOR


def test_the_bbox_travels_with_the_value() -> None:
    """D2: a persisted lab value points at the region of the page it came from."""
    built = plan()
    assert built.request is not None
    result = built.request.results[0]

    assert result.bbox == BBox(page=1, x0=270.0, y0=256.0, x1=280.0, y1=268.0)
    assert result.page == 1


def test_a_retry_names_the_stored_document_instead_of_resending_it() -> None:
    built = plan_write_back(
        extraction(),
        patient_uuid=PATIENT,
        document_sha3_512=DIGEST,
        extractor_version=EXTRACTOR,
        correlation_id=CORRELATION_ID,
        existing_document_id=2474,
    )

    assert built.request is not None
    assert built.request.document.content_base64 is None
    assert built.request.document.existing_document_id == 2474


# -- what gets refused ------------------------------------------------------


def test_a_value_the_locate_step_could_not_place_is_never_written() -> None:
    """CLAUDE.md invariant 4, at the last point before it becomes a chart row."""
    built = plan([glucose(located_value=False)])

    assert built.request is None
    assert built.skipped == [
        type(built.skipped[0])(test_key="glucose", reason=SkipReason.VALUE_NOT_LOCATED)
    ]


def test_an_uncoded_row_is_not_written() -> None:
    built = plan([glucose(loinc=None)])

    assert built.request is None
    assert built.skipped[0].reason is SkipReason.NO_LOINC_CODE


def test_a_row_with_no_collection_date_has_no_idempotency_key() -> None:
    built = plan([glucose(located_date=False)])

    assert built.request is None
    assert built.skipped[0].reason is SkipReason.NO_COLLECTION_DATE


def test_a_row_with_no_printed_name_is_not_written() -> None:
    # OpenEMR drops result_code unless result_text is populated too.
    built = plan([glucose(located_name=False)])

    assert built.request is None
    assert built.skipped[0].reason is SkipReason.NO_TEST_NAME


def test_a_row_with_no_reference_range_on_the_page_is_not_written() -> None:
    # No reference range means no computable flag, and
    # procedure_result.abnormal has no value meaning "we could not tell". The
    # reason names the missing range, not an undecidable flag.
    built = plan([glucose(reference=None)])

    assert built.request is None
    assert built.skipped[0].reason is SkipReason.REFERENCE_RANGE_NOT_LOCATED


def test_an_undecidable_flag_is_not_written() -> None:
    # The range is on the page but has no numeric bounds: the flag is
    # genuinely indeterminate, which is a different thing to disclose.
    built = plan([glucose(reference="See note")])

    assert built.request is None
    assert built.skipped[0].reason is SkipReason.FLAG_INDETERMINATE


def test_a_row_whose_printed_flag_contradicts_the_arithmetic_is_not_written() -> None:
    """
    D3's cross-check is a verification failure, and Week 1's rule is that a
    failed verification is disclosed, never silently resolved. A chart write
    is the strictest place that rule applies: 89 mg/dL against 70-99 is
    normal, and a page that prints H disagrees with the page's own numbers.
    """
    built = plan([glucose(value=89.0, flag=ReportedFlag.HIGH)])

    assert built.request is None
    assert built.skipped[0].reason is SkipReason.FLAG_DISAGREES_WITH_PAGE


def test_a_page_that_printed_no_flag_does_not_block_the_write() -> None:
    """D20: a blank flag column asserts nothing, so there is nothing to contradict."""
    built = plan([glucose(flag=ReportedFlag.NOT_REPORTED)])

    assert built.request is not None
    assert built.skipped == []


def test_the_writable_rows_are_sent_and_the_rest_are_reported() -> None:
    built = plan(
        [
            glucose(test_key="glucose"),
            glucose(test_key="potassium", loinc=None),
            glucose(test_key="sodium", located_value=False),
        ]
    )

    assert built.request is not None
    assert built.writable_count == 1
    assert [(s.test_key, s.reason) for s in built.skipped] == [
        ("potassium", SkipReason.NO_LOINC_CODE),
        ("sodium", SkipReason.VALUE_NOT_LOCATED),
    ]


def test_an_intake_form_is_a_routing_bug_not_a_skip() -> None:
    """
    Two layers say no. `LabPdfExtraction` refuses to describe an intake
    document at all, so this has to go around its validator with
    `model_copy` to reach the second layer — which is the point: the guard
    holds even for an object that was never validated.
    """
    lab = extraction()
    intake = lab.model_copy(
        update={"document": lab.document.model_copy(update={"kind": DocumentKind.INTAKE_FORM})}
    )

    with pytest.raises(ValueError, match="only lab documents are written back"):
        plan_write_back(
            intake,
            patient_uuid=PATIENT,
            document_sha3_512=DIGEST,
            extractor_version=EXTRACTOR,
            correlation_id=CORRELATION_ID,
            content=b"x",
        )


def test_the_extraction_contract_will_not_describe_an_intake_document_either() -> None:
    with pytest.raises(ValidationError, match="describes a lab_pdf document"):
        extraction(kind=DocumentKind.INTAKE_FORM)


def test_nothing_writable_is_an_outcome_not_an_error() -> None:
    built = plan([glucose(located_value=False)])

    assert built.request is None
    assert built.writable_count == 0


# -- posting it -------------------------------------------------------------


def _response(status: int, body: dict[str, Any] | str) -> httpx.Response:
    if isinstance(body, str):
        return httpx.Response(status, text=body)
    return httpx.Response(status, json=body)


def _ok_body(request_json: dict[str, Any]) -> dict[str, Any]:
    results = request_json["results"]
    return {
        "patient_uuid": PATIENT,
        "document_id": 2474,
        "document_outcome": "created",
        "procedure_order_id": 8198,
        "results": [
            {
                "test_code": r["test_code"],
                "idempotency_key": r["idempotency_key"],
                "extraction_id": r["extraction_id"],
                "outcome": "created",
                "procedure_report_id": 762 + i,
                "procedure_result_id": 14849 + i,
            }
            for i, r in enumerate(results)
        ],
        "correlation_id": CORRELATION_ID,
    }


def _post(status: int, body: Any, *, capture: list[httpx.Request] | None = None):  # noqa: ANN202
    built = plan()
    assert built.request is not None

    def handler(request: httpx.Request) -> httpx.Response:
        if capture is not None:
            capture.append(request)
        payload = json.loads(request.content)
        return _response(status, _ok_body(payload) if body is None else body)

    async def run() -> Any:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await write_lab_results("the-clinicians-token", built.request, client=client)

    return asyncio.run(run())


def test_a_successful_write_returns_the_ids_openemr_wrote() -> None:
    written = _post(200, None)

    assert written.document_id == 2474
    assert written.procedure_order_id == 8198
    assert written.results[0].procedure_result_id == 14849
    assert written.results[0].outcome is WriteOutcome.CREATED


def test_a_replay_is_reported_as_duplicate_ignored_not_as_an_error() -> None:
    built = plan()
    assert built.request is not None
    sent = json.loads(built.request.model_dump_json())
    body = _ok_body(sent)
    body["document_outcome"] = "duplicate_ignored"
    body["results"][0]["outcome"] = "duplicate_ignored"

    written = _post(200, body)

    assert written.document_outcome is WriteOutcome.DUPLICATE_IGNORED
    assert written.results[0].outcome is WriteOutcome.DUPLICATE_IGNORED


def test_the_clinicians_token_is_what_is_sent() -> None:
    captured: list[httpx.Request] = []
    _post(200, None, capture=captured)

    assert captured[0].headers["Authorization"] == "Bearer the-clinicians-token"
    assert captured[0].url == httpx.URL(writeback_url())
    # The body is the contract's own JSON, not a hand-rolled dict.
    assert json.loads(captured[0].content)["extractor_version"] == EXTRACTOR


@pytest.mark.parametrize("status", [401, 403])
def test_a_rejected_token_names_the_scopes_that_are_missing(status: int) -> None:
    with pytest.raises(WriteBackAuthError, match="api:oemr"):
        _post(status, {"message": "Unauthorized"})


def test_a_refusal_carries_the_field_the_endpoint_named() -> None:
    with pytest.raises(WriteBackRefused) as raised:
        _post(
            400,
            {
                "error": "lab_writeback_refused",
                "message": "is not the composition of (document sha3-512, test code, collection date)",
                "field": "results[0].idempotency_key",
            },
        )

    assert raised.value.field == "results[0].idempotency_key"
    assert raised.value.status_code == 400


def test_an_unknown_patient_is_a_refusal_not_an_outage() -> None:
    with pytest.raises(WriteBackRefused) as raised:
        _post(404, {"error": "lab_writeback_refused", "message": "no patient with that uuid"})

    assert raised.value.status_code == 404


def test_a_server_error_is_retryable_and_does_not_echo_the_body() -> None:
    with pytest.raises(WriteBackUnavailable) as raised:
        _post(500, "Fatal error: INSERT INTO procedure_result ... 142 mg/dL")

    assert "142" not in str(raised.value)
    assert "INSERT" not in str(raised.value)


def test_a_transport_failure_is_retryable_and_says_nothing_about_the_body() -> None:
    built = plan()
    assert built.request is not None

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await write_lab_results("token", built.request, client=client)

    with pytest.raises(WriteBackUnavailable) as raised:
        asyncio.run(run())

    assert "ConnectError" in str(raised.value)
    assert "89" not in str(raised.value)
