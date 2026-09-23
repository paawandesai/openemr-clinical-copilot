"""
Category K — write-back idempotency and provenance.

A replayed 200 is not proof of idempotency. What can be proved without the
endpoint is the half that lives in the contract: the key is a pure function
of (document sha3-512, test code, collection date), the extraction id is a
pure function of the key, and a request whose key is not that composition is
refused at construction rather than sent and hoped for.

K2 (concurrent duplicate submits) and K4 (partial failure leaves no orphan
rows) are the half that needs the endpoint, and are specified and pending.
"""

from __future__ import annotations

from datetime import date

from pydantic import ValidationError

from app.models.w2 import (
    AbnormalFlag,
    BBox,
    LabWriteBackLedgerRow,
    LabWriteBackRequest,
    LabWriteBackResult,
    NumericLabValue,
    ReferenceRange,
    document_extraction_id,
    document_idempotency_key,
    lab_result_extraction_id,
    lab_result_idempotency_key,
)
from evals.w2.gate.models import Case, CheckOutcome
from evals.w2.gate.registry import check

_SHA3 = "9c2a41be" + "0" * 120
_TEST_CODE = "2345-7"
_COLLECTED = date(2026, 3, 2)
_CORRELATION_ID = "4c547101-195d-47b2-bc4b-0db614573052"
#: The write-back merge made this required, and made the request prove
#: its extraction_id is derived from it. A fixed value here keeps the
#: derived ids stable across runs.
_EXTRACTOR_VERSION = "w2-extractor/1.0.0"
_BBOX = BBox(page=1, x0=230.0, y0=396.3, x1=275.6, y1=407.2)


def _result(**overrides: object) -> LabWriteBackResult:
    fields: dict[str, object] = {
        "test_code": _TEST_CODE,
        "test_name": "Glucose",
        "value": NumericLabValue(value=168.0),
        "unit": "mg/dL",
        "reference_range": ReferenceRange(low=70.0, high=99.0, text="70 - 99", unit="mg/dL"),
        "collection_date": _COLLECTED,
        "abnormal": AbnormalFlag.HIGH,
        "page": 1,
        "bbox": _BBOX,
        "idempotency_key": lab_result_idempotency_key(
            document_sha3_512=_SHA3, test_code=_TEST_CODE, collection_date=_COLLECTED
        ),
        "extraction_id": lab_result_extraction_id(
            document_sha3_512=_SHA3, test_code=_TEST_CODE, collection_date=_COLLECTED
        ),
    }
    fields.update(overrides)
    return LabWriteBackResult(**fields)


def _request(results: list[LabWriteBackResult]) -> LabWriteBackRequest:
    return LabWriteBackRequest(
        patient_uuid="a2c373ec-57bf-41cb-8767-94410dcf663d",
        document={
            "filename": "lab-clean-001.pdf",
            "mime_type": "application/pdf",
            "sha3_512": _SHA3,
            "byte_length": 21044,
            "content_base64": "eA==",
        },
        results=results,
        extraction_id=document_extraction_id(
            document_sha3_512=_SHA3, extractor_version=_EXTRACTOR_VERSION
        ),
        extractor_version=_EXTRACTOR_VERSION,
        correlation_id=_CORRELATION_ID,
    )


@check("chk.k_key_is_the_composition_of_its_components")
def k_key_is_the_composition_of_its_components(case: Case) -> CheckOutcome:
    """
    CRITICAL. The same result submitted twice composes the same key and the
    same extraction id — which is what makes the duplicate the ledger refuses
    also the duplicate already visible in `note[0].text`.

    Canonicalisation is part of the composition, not a courtesy at the call
    site: ` 2345-7 ` and `2345-7` are one row, and an upper-cased digest and a
    lower-cased one are the same document.
    """
    del case
    first = lab_result_idempotency_key(
        document_sha3_512=_SHA3, test_code=_TEST_CODE, collection_date=_COLLECTED
    )
    replay = lab_result_idempotency_key(
        document_sha3_512=_SHA3.upper(),
        test_code=f"  {_TEST_CODE.lower()}  ",
        collection_date=_COLLECTED,
    )
    if first != replay:
        return CheckOutcome.failed(
            "the same result submitted with a differently-cased digest and a padded test "
            "code composed a different key; the ledger would write two rows for one "
            "result (invariant 6)"
        )

    # A LOINC code is all digits and a hyphen, so the line above exercises
    # whitespace and digest case but says nothing about test-code case. Local
    # and lab-specific codes are not numeric, and `test_code` is a free string
    # on the contract. An earlier version of this check stopped here and passed
    # while `.upper()` had been deleted from the composition; the
    # planted-regression harness is what found that.
    for alphabetic in ("HbA1c", "hba1c", " HBA1C "):
        cased = lab_result_idempotency_key(
            document_sha3_512=_SHA3, test_code=alphabetic, collection_date=_COLLECTED
        )
        expected = f"{_SHA3}:HBA1C:{_COLLECTED.isoformat()}"
        if cased != expected:
            return CheckOutcome.failed(
                f"test code {alphabetic!r} composed {cased!r}, not {expected!r}; the key "
                "is no longer canonicalised, so the same result submitted with a "
                "differently-cased code writes a second row (invariant 6)"
            )
    if first != f"{_SHA3}:{_TEST_CODE.upper()}:{_COLLECTED.isoformat()}":
        return CheckOutcome.failed(
            f"the canonical key form has changed: {first!r}. Every extraction id already "
            "written into a chart was derived from the old form, and they no longer match"
        )
    if lab_result_extraction_id(
        document_sha3_512=_SHA3, test_code=_TEST_CODE, collection_date=_COLLECTED
    ) != lab_result_extraction_id(
        document_sha3_512=_SHA3.upper(), test_code=f" {_TEST_CODE} ", collection_date=_COLLECTED
    ):
        return CheckOutcome.failed("the extraction id is no longer a pure function of the key")

    other_day = lab_result_idempotency_key(
        document_sha3_512=_SHA3, test_code=_TEST_CODE, collection_date=date(2026, 3, 3)
    )
    other_test = lab_result_idempotency_key(
        document_sha3_512=_SHA3, test_code="1742-6", collection_date=_COLLECTED
    )
    if len({first, other_day, other_test}) != 3:
        return CheckOutcome.failed(
            "two different results compose the same key; the ledger's UNIQUE constraint "
            "would drop one of them"
        )

    duplicate = _result()
    try:
        _request([duplicate, _result()])
    except ValidationError:
        pass
    else:
        return CheckOutcome.failed(
            "a request carried two results sharing one idempotency key; the ledger's "
            "UNIQUE constraint would reject the second after the first had committed"
        )

    if document_idempotency_key(_SHA3.upper()) != _SHA3:
        return CheckOutcome.failed("the document key is not normalised")
    try:
        document_idempotency_key("a" * 64)
    except ValueError:
        return CheckOutcome.ok(
            "the key is the canonical composition of its three components, the "
            "extraction id is derived from it, and a SHA-256 is refused where a "
            "SHA3-512 belongs (D16)"
        )
    return CheckOutcome.failed(
        "a 64-character digest was accepted as a document key; a SHA-256 compared "
        "against a SHA3-512 concludes every document is new (D16)"
    )


@check("chk.k_provenance_survives_the_write")
def k_provenance_survives_the_write(case: Case) -> CheckOutcome:
    """
    D2's provenance requirement on the write path: a written value carries the
    page and the box it came from, they agree with each other, and the ledger
    row maps the extraction id back to them.
    """
    del case
    result = _result()
    if result.bbox.page != result.page:
        return CheckOutcome.failed("page and bbox disagree on a constructed result")
    try:
        _result(page=2)
    except ValidationError:
        pass
    else:
        return CheckOutcome.failed("a result was accepted with a page that its bbox contradicts")

    row = LabWriteBackLedgerRow.model_fields
    for required in ("idempotency_key", "extraction_id"):
        if required not in row:
            return CheckOutcome.failed(f"the ledger row no longer carries {required}")

    request = _request([result])
    if request.document_key() != _SHA3:
        return CheckOutcome.failed("the request's document key is not the document digest")
    if request.results[0].extraction_id != result.extraction_id:
        return CheckOutcome.failed("the extraction id did not survive into the request")

    try:
        _result(abnormal=AbnormalFlag.INDETERMINATE)
    except ValidationError:
        return CheckOutcome.ok(
            "page, bbox and extraction id survive into the request, and an "
            "undecidable flag is refused rather than written back as normal"
        )
    return CheckOutcome.failed(
        "an indeterminate flag was accepted for write-back; procedure_result.abnormal "
        "has no value meaning 'we could not tell', and writing `no` puts a false normal "
        "in a chart"
    )


@check("chk.k_reused_key_with_a_changed_payload_is_rejected")
def k_reused_key_with_a_changed_payload_is_rejected(case: Case) -> CheckOutcome:
    """
    The fingerprint-mismatch case. A key that does not compose from its own
    payload is refused at construction — so a second submission under a
    borrowed key cannot be silently replayed as if it were the first.
    """
    del case
    borrowed = lab_result_idempotency_key(
        document_sha3_512=_SHA3, test_code="1742-6", collection_date=_COLLECTED
    )
    try:
        _request([_result(idempotency_key=borrowed)])
    except ValidationError:
        pass
    else:
        return CheckOutcome.failed(
            "a result reused another test's idempotency key; the write would replay the "
            "other result's ids and mask a changed payload"
        )

    changed_date = _result(
        collection_date=date(2026, 3, 3),
        idempotency_key=lab_result_idempotency_key(
            document_sha3_512=_SHA3, test_code=_TEST_CODE, collection_date=_COLLECTED
        ),
        extraction_id=lab_result_extraction_id(
            document_sha3_512=_SHA3, test_code=_TEST_CODE, collection_date=_COLLECTED
        ),
    )
    try:
        _request([changed_date])
    except ValidationError:
        pass
    else:
        return CheckOutcome.failed(
            "a result whose collection date had changed kept the old key; the ledger "
            "would call it a duplicate and drop a different reading"
        )

    mismatched_extraction = _result(
        extraction_id=lab_result_extraction_id(
            document_sha3_512=_SHA3, test_code="1742-6", collection_date=_COLLECTED
        )
    )
    try:
        _request([mismatched_extraction])
    except ValidationError:
        return CheckOutcome.ok(
            "a borrowed key, a stale key and a mismatched extraction id are each refused "
            "before the request leaves the process"
        )
    return CheckOutcome.failed(
        "a result's extraction id was not derived from its own key; the id in "
        "procedure_result.comments would point at a different row"
    )


@check("chk.k_replayed_duplicate_returns_the_winners_ids")
def k_replayed_duplicate_returns_the_winners_ids(case: Case) -> CheckOutcome:
    """
    W2-K2's client-side half, and only that half — see the case file.

    What is asserted: the agent's write-back client reads a replayed
    `duplicate_ignored` as a duplicate rather than as an error, keeps the
    ids the first write produced, and counts nothing as created. Run over
    `httpx.MockTransport`, so no endpoint and no database.

    What is NOT asserted here: that two concurrent requests commit exactly
    one chain. That is the endpoint's invariant, it is proved in
    `WriteBackServiceTest::testTheChainRanOnceEvenThoughBothRequestsTried`
    and `::testTheLoserOfAnIdempotencyRaceReturnsTheWinnersIds`, and those
    are PHPUnit tests this Python gate cannot execute. The bridge is named
    in the case file.
    """
    del case
    import asyncio

    import httpx

    from app.models.w2 import WriteOutcome
    from app.writeback.client import write_lab_results

    request = _request([_result()])
    winner_ids = {"procedure_report_id": 4011, "procedure_result_id": 9022}
    body = {
        "patient_uuid": request.patient_uuid,
        "document_id": 771,
        "document_outcome": "duplicate_ignored",
        "procedure_order_id": 3001,
        "results": [
            {
                "test_code": _TEST_CODE,
                "idempotency_key": request.results[0].idempotency_key,
                "extraction_id": request.results[0].extraction_id,
                "outcome": "duplicate_ignored",
                **winner_ids,
            }
        ],
        "correlation_id": _CORRELATION_ID,
    }

    seen: list[httpx.Request] = []

    def handler(sent: httpx.Request) -> httpx.Response:
        seen.append(sent)
        return httpx.Response(200, json=body)

    async def _run() -> object:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport, base_url="http://writeback") as client:
            return await write_lab_results("test-token", request, client=client)

    written = asyncio.run(_run())

    if len(seen) != 1:
        return CheckOutcome.failed(f"the client sent {len(seen)} requests, expected 1")
    created = [r for r in written.results if r.outcome is WriteOutcome.CREATED]
    if created:
        return CheckOutcome.failed(
            f"{len(created)} result(s) counted as created on a replayed duplicate; a "
            "retry would read as a second write"
        )
    replayed = written.results[0]
    if replayed.outcome is not WriteOutcome.DUPLICATE_IGNORED:
        return CheckOutcome.failed(f"outcome was {replayed.outcome.value}")
    if (replayed.procedure_report_id, replayed.procedure_result_id) != (
        winner_ids["procedure_report_id"],
        winner_ids["procedure_result_id"],
    ):
        return CheckOutcome.failed(
            "the replay did not return the ids the winning write produced; the caller "
            "cannot find the row that actually exists"
        )
    if replayed.idempotency_key != request.results[0].idempotency_key:
        return CheckOutcome.failed("the replayed result came back under a different key")
    return CheckOutcome.ok(
        "a replayed duplicate_ignored is read as a duplicate, not an error: zero "
        "created, the winner's report and result ids preserved, one request sent "
        "[the exactly-one-chain half is the endpoint's and is pending]"
    )
