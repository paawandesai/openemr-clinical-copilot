"""
Write-back contracts for the custom OpenEMR endpoint — D1 (superseding).

D1 settled what Week 2 builds against, on the evidence in
docs/w2/spikes/writeback.md:

* documents go in through `POST /api/patient/:pid/document` and come back
  out through `GET /fhir/Binary/:id`;
* **lab values have no API write path at all** — every FHIR write 404s and
  no `Observation` write scope exists to grant — so a custom endpoint in
  the fork writes the `procedure_order -> procedure_order_code ->
  procedure_report -> procedure_result` chain in one transaction through
  OpenEMR's own DB layer, **and returns real ids**;
* idempotency is a ledger table in the fork with a UNIQUE key over
  (document sha3-512, test code, collection date);
* `procedure_result.comments` carries **an extraction id only**, which
  surfaces as `Observation.note[0].text`; the ledger maps that id to
  document, page and bbox, because `derivedFrom` is not emitted and cannot
  carry the link;
* `Observation` only this week.

This module is the request, the response, the ledger row, and the two key
functions — nothing that talks to a socket. The endpoint itself lives in
the PHP fork (`docs/openemr-dev-guide.md`), and this contract is what both
sides are written against.

---

**Decision: the extraction id is a pure function of the idempotency key.**

D1 gives the ledger a UNIQUE key over three things and gives
`procedure_result.comments` an extraction id. Left independent, those are
two identifiers for one row and a class of bug where they disagree. So
`lab_result_extraction_id()` is `sha3-256` of the canonical key string,
truncated to 128 bits and prefixed `w2x-`. Consequences, all wanted:

* replaying the same extraction produces the same id, so the duplicate the
  ledger refuses is also the duplicate already visible in `note[0].text`;
* duplicate detection works client-side, which is the only place it can:
  the FHIR Observation search has no `note`, `_text` or `identifier`
  parameter, so a caller narrows on `patient` + `code` server-side and
  matches this string in the results (spike §7);
* the id is short and opaque. Spike §10.3 is explicit that
  `procedure_result.comments` is clinician-visible text in a chart, so it
  has to be "parseable but not alarming" — `w2x-` plus hex is both, where
  the raw 128-hex-character document digest would be neither.

The truncation is to 128 bits of a collision-resistant hash over a key that
is already unique per (document, test, date). The ledger's UNIQUE
constraint, not this id, is what actually enforces idempotency; the id is
how the write is found again from the chart side.

---

**Decision: there is no intake write-back contract, and this module rejects
intake documents.** D2 forbids intake data reaching the chart. A missing
type is easy to add later by someone who does not know why it is missing,
so `LabWriteBackRequest` validates `document.kind is LAB_PDF` and says so.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from app.models.w2.base import (
    CORRELATION_ID_PATTERN,
    EXTRACTION_ID_PATTERN,
    PATIENT_UUID_PATTERN,
    SHA3_512_PATTERN,
    W2Contract,
)
from app.models.w2.citations import BBox
from app.models.w2.documents import DocumentKind
from app.models.w2.lab_values import AbnormalFlag, LabValue, ReferenceRange, openemr_abnormal_code

#: The separator in the canonical idempotency key. A colon, matching the
#: `w2key=<hash>:<code>:<date>` form the spike wrote and read back intact
#: from `procedure_result.comments` (§7).
_KEY_SEPARATOR = ":"

#: Names of the two published JSON Schema documents for this endpoint
#: (`docs/w2/schemas/`, exported by `agent/scripts/export_w2_schemas.py`).
#: Unlike `LabPdfExtraction`, neither model carries a `schema_version`
#: *field*: an extraction file outlives the extractor that wrote it and has
#: to say which schema it is in, whereas a request is read the moment it is
#: sent, by one endpoint at one route. Versioning lives in the route.
#:
#: D22 applies to both documents: they describe shape. The cross-field rules
#: — that an idempotency key is the composition of its own components, that
#: a run id is this document under this extractor — live in the validators
#: below and in the PHP endpoint, and a consumer that checks only the schema
#: has not checked them.
LAB_WRITEBACK_REQUEST_SCHEMA_VERSION = "w2-contracts/lab_writeback_request/1.0.0"
LAB_WRITEBACK_RESPONSE_SCHEMA_VERSION = "w2-contracts/lab_writeback_response/1.0.0"


def document_idempotency_key(sha3_512: str) -> str:
    """
    The document-level key: OpenEMR's own digest of the file.

    D1: "Document-level dedup uses OpenEMR's own stored hash, checked before
    insert." OpenEMR hashes with SHA3-512 and returns the digest from the
    document listing endpoint, but does **not** deduplicate — the spike
    uploaded an identical file twice and got two rows with the same hash and
    no unique constraint (§7). So dedup is ours: list the category, compare
    this key, skip if present.

    Normalises case and rejects anything that is not a SHA3-512 digest,
    because the one thing that must never happen here is a SHA-256 being
    compared against a SHA3-512 and concluding the document is new.
    """
    normalised = sha3_512.strip().lower()
    if len(normalised) != 128 or any(
        character not in "0123456789abcdef" for character in normalised
    ):
        raise ValueError(
            "document idempotency key is a SHA3-512 digest (128 hex characters); "
            f"got {len(normalised)} characters"
        )
    return normalised


def lab_result_idempotency_key(
    *, document_sha3_512: str, test_code: str, collection_date: date
) -> str:
    """
    D1's UNIQUE key over (document sha3-512, test code, collection date).

    Canonical form: `<digest>:<test code>:<YYYY-MM-DD>`, digest lowercased,
    test code upper-cased and stripped, date ISO. Canonicalising here rather
    than at each call site is the point — the same result submitted with
    `2345-7` and ` 2345-7 ` is one row, not two.
    """
    if not test_code.strip():
        raise ValueError("a lab result idempotency key needs a test code")
    return _KEY_SEPARATOR.join(
        (
            document_idempotency_key(document_sha3_512),
            test_code.strip().upper(),
            collection_date.isoformat(),
        )
    )


def lab_result_extraction_id(
    *, document_sha3_512: str, test_code: str, collection_date: date
) -> str:
    """
    The id that goes into `procedure_result.comments`. See the module
    docstring for why it is derived from the idempotency key rather than
    generated.
    """
    key = lab_result_idempotency_key(
        document_sha3_512=document_sha3_512,
        test_code=test_code,
        collection_date=collection_date,
    )
    return "w2x-" + hashlib.sha3_256(key.encode("utf-8")).hexdigest()[:32]


def document_extraction_id(*, document_sha3_512: str, extractor_version: str) -> str:
    """
    The id for an extraction *run* over a whole document — what a document
    citation's `extraction_id` carries.

    Includes the extractor version so that re-extracting the same file with
    a changed extractor produces a different id. Two runs of the same
    extractor over the same bytes produce the same id, which is what makes
    a re-upload of an identical document cite the values it already has.
    """
    if not extractor_version.strip():
        raise ValueError("an extraction run records which extractor produced it")
    material = _KEY_SEPARATOR.join(
        (document_idempotency_key(document_sha3_512), extractor_version.strip())
    )
    return "w2x-" + hashlib.sha3_256(material.encode("utf-8")).hexdigest()[:32]


class WriteOutcome(StrEnum):
    """What the endpoint did with one result."""

    CREATED = "created"
    #: The ledger already had this key. The original ids are returned
    #: unchanged, which is what makes a retry safe (invariant 6).
    DUPLICATE_IGNORED = "duplicate_ignored"
    #: The chart already holds a result for this test and collection
    #: date/time with the same value and unit, from any source (this
    #: endpoint under another file, a lab interface, a hand entry). Nothing
    #: is written; the ids are the existing row's. The ledger is per file,
    #: so it cannot see this; the chart can.
    ALREADY_IN_CHART = "already_in_chart"
    #: The chart holds a result for this test and collection date/time with
    #: a different value or unit. Nothing is written and nothing is
    #: overwritten: the two values are returned side by side for a human to
    #: resolve, the way D2 surfaces an intake medication that disagrees
    #: with the chart.
    CONFLICT = "conflict"


class WriteBackDocument(W2Contract):
    """
    The document half of the request.

    Exactly one of `content_base64` and `existing_document_id`: a first
    attempt sends the bytes, a retry after a partial failure names the
    document OpenEMR already stored. `sha3_512` is required either way — it
    is the document idempotency key and the first component of every
    result's key, so a request without it could not be made idempotent at
    all (invariant 6).
    """

    filename: str = Field(min_length=1)
    mime_type: str = Field(
        min_length=1,
        description=(
            "Check by content before sending: a type outside OpenEMR's "
            "`files_white_list` is rejected as an empty 404 with no error "
            "(spike §10.6)."
        ),
    )
    sha3_512: str = Field(pattern=SHA3_512_PATTERN)
    byte_length: int = Field(ge=1)
    content_base64: str | None = Field(
        default=None, description="The file, base64-encoded. Omit when retrying against an id."
    )
    existing_document_id: int | None = Field(
        default=None,
        ge=1,
        description="`documents.id` of a file already stored for this patient.",
    )
    category_path: str = Field(
        default="/Categories/Lab_Report",
        min_length=1,
        description="OpenEMR document category the file is filed under.",
    )

    @model_validator(mode="after")
    def _exactly_one_source(self) -> Self:
        has_content = self.content_base64 is not None
        has_id = self.existing_document_id is not None
        if has_content == has_id:
            raise ValueError(
                "send the bytes (content_base64) or name an already-stored "
                "document (existing_document_id) — exactly one"
            )
        return self


class LabWriteBackResult(W2Contract):
    """
    One lab value to write, with everything the four-table insert needs and
    everything the ledger needs to trace it back to a page.

    `test_code` and `test_name` are both required because the spike found
    `procedure_result.result_code` and `result_text` are both required or
    "the code is dropped and replaced with a null-flavor" (§9).

    `abnormal` may not be `indeterminate`: `openemr_abnormal_code()` refuses
    to map it, because `procedure_result.abnormal` has no value meaning "we
    could not tell" and writing `no` would put a false normal in a chart.
    """

    test_code: str = Field(min_length=1, description="LOINC code -> procedure_order_code.")
    test_name: str = Field(min_length=1, description="-> procedure_result.result_text.")
    value: LabValue
    unit: str | None = None
    reference_range: ReferenceRange | None = None
    collection_date: date
    collected_at: datetime | None = Field(
        default=None,
        description=(
            "The collection time, when the report prints one, on the same day "
            "as `collection_date`. -> procedure_report.date_collected. The "
            "endpoint matches an existing chart result on this exact time, or "
            "on the date alone when the report gives no time."
        ),
    )
    reported_at: datetime | None = Field(
        default=None,
        description=(
            "-> procedure_report.date_report, which becomes both "
            "`effectiveDateTime` and `meta.lastUpdated` on the Observation "
            "(spike §9)."
        ),
    )
    abnormal: AbnormalFlag
    page: int = Field(ge=1, description="Page of the source document this value came from.")
    bbox: BBox = Field(description="Where on that page — D2's provenance requirement.")
    idempotency_key: str = Field(
        min_length=1,
        description=(
            "D1's UNIQUE key. Required, with no default: invariant 6 has no "
            "exceptions, and a default would be one."
        ),
    )
    extraction_id: str = Field(
        pattern=EXTRACTION_ID_PATTERN,
        description="-> procedure_result.comments -> Observation.note[0].text.",
    )

    @model_validator(mode="after")
    def _writable(self) -> Self:
        if self.abnormal is AbnormalFlag.INDETERMINATE:
            raise ValueError(
                "an indeterminate flag is not written back: "
                "procedure_result.abnormal has no value for it"
            )
        # Raises for anything the column cannot hold, here rather than at
        # INSERT time in PHP.
        openemr_abnormal_code(self.abnormal)
        if self.bbox.page != self.page:
            raise ValueError(f"page is {self.page} but bbox is on page {self.bbox.page}")
        if self.collected_at is not None and self.collected_at.date() != self.collection_date:
            raise ValueError(
                "collected_at is a time on the collection date; "
                f"{self.collected_at.date()} is not {self.collection_date}"
            )
        return self


class LabWriteBackRequest(W2Contract):
    """
    Document plus lab results in; real ids out (D1).

    The endpoint is transactional: the document and the whole
    `procedure_order -> ... -> procedure_result` chain are written together,
    so a partial write cannot leave results pointing at a document that was
    never stored.
    """

    patient_uuid: str = Field(
        pattern=PATIENT_UUID_PATTERN,
        description="FHIR Patient id. The endpoint resolves it to a pid; callers never send one.",
    )
    document_kind: DocumentKind = Field(
        default=DocumentKind.LAB_PDF,
        description="Lab reports only. See the module docstring on D2.",
    )
    document: WriteBackDocument
    results: list[LabWriteBackResult] = Field(min_length=1)
    extraction_id: str = Field(
        pattern=EXTRACTION_ID_PATTERN,
        description="The extraction run these results came from.",
    )
    extractor_version: str = Field(
        min_length=1,
        description=(
            "Which extractor produced this run. Required, with no default: "
            "the ledger row records it, and a default would make every "
            "unstated run look like the same one."
        ),
    )
    correlation_id: str = Field(pattern=CORRELATION_ID_PATTERN)

    @model_validator(mode="after")
    def _keys_and_kind(self) -> Self:
        if self.document_kind is not DocumentKind.LAB_PDF:
            raise ValueError(
                "only lab values are written back. D2: intake medications and "
                "allergies are document-derived facts surfaced as "
                "discrepancies, never merged into the chart"
            )
        expected_run = document_extraction_id(
            document_sha3_512=self.document.sha3_512,
            extractor_version=self.extractor_version,
        )
        if self.extraction_id != expected_run:
            raise ValueError(
                "extraction_id is not this document extracted by this "
                "extractor_version; see document_extraction_id"
            )
        seen: set[str] = set()
        for result in self.results:
            expected_key = lab_result_idempotency_key(
                document_sha3_512=self.document.sha3_512,
                test_code=result.test_code,
                collection_date=result.collection_date,
            )
            if result.idempotency_key != expected_key:
                raise ValueError(
                    f"idempotency_key for {result.test_code} is not the "
                    "composition of (document sha3-512, test code, collection "
                    "date); see lab_result_idempotency_key"
                )
            expected_extraction = lab_result_extraction_id(
                document_sha3_512=self.document.sha3_512,
                test_code=result.test_code,
                collection_date=result.collection_date,
            )
            if result.extraction_id != expected_extraction:
                raise ValueError(
                    f"extraction_id for {result.test_code} is not derived from "
                    "its idempotency key; see lab_result_extraction_id"
                )
            if expected_key in seen:
                raise ValueError(
                    f"two results share the idempotency key for {result.test_code}; "
                    "the ledger's UNIQUE constraint would reject the second"
                )
            seen.add(expected_key)
        return self

    def document_key(self) -> str:
        """The document-level dedup key for this request."""
        return document_idempotency_key(self.document.sha3_512)


class LabWriteBackResultOutcome(W2Contract):
    """
    What happened to one result, with the ids D1 requires the endpoint to
    return. Real ids, not `true`: the standard document endpoint's
    returns-no-identifier behaviour is the race D1 exists to fix.

    The ids are the row this write made (`created`), the row an earlier
    write of the same file made (`duplicate_ignored`), or the row already
    in the chart from any source (`already_in_chart`). A `conflict` wrote
    nothing and has no ids of its own; `matched_procedure_result_id`,
    `chart_value` and `chart_unit` say what it disagreed with.
    """

    test_code: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    extraction_id: str = Field(pattern=EXTRACTION_ID_PATTERN)
    outcome: WriteOutcome
    procedure_report_id: int | None = Field(default=None, ge=1)
    procedure_result_id: int | None = Field(default=None, ge=1)
    matched_procedure_result_id: int | None = Field(
        default=None,
        ge=1,
        description="The chart row this result matched (already_in_chart) or disagrees with (conflict).",
    )
    chart_value: str | None = Field(
        default=None, description="The chart row's value as stored, for a conflict."
    )
    chart_unit: str | None = Field(
        default=None, description="The chart row's unit as stored, for a conflict."
    )

    @model_validator(mode="after")
    def _ids_match_the_outcome(self) -> Self:
        has_ids = self.procedure_report_id is not None and self.procedure_result_id is not None
        if self.outcome is WriteOutcome.CONFLICT:
            if (
                has_ids
                or self.procedure_report_id is not None
                or self.procedure_result_id is not None
            ):
                raise ValueError("a conflict wrote nothing and has no ids of its own")
            if self.matched_procedure_result_id is None or self.chart_value is None:
                raise ValueError("a conflict names the chart row and value it disagrees with")
            return self
        if not has_ids:
            raise ValueError(f"a {self.outcome.value} result carries the ids of its chart row")
        if self.outcome is WriteOutcome.ALREADY_IN_CHART:
            if self.matched_procedure_result_id != self.procedure_result_id:
                raise ValueError("an already_in_chart result's ids are the matched chart row's")
        elif self.matched_procedure_result_id is not None or self.chart_value is not None:
            raise ValueError(f"a {self.outcome.value} result matched nothing in the chart")
        return self


class WriteBackCounts(W2Contract):
    """How many results a write-back ended in each outcome. Counts only."""

    created: int = Field(default=0, ge=0)
    duplicate_ignored: int = Field(default=0, ge=0)
    already_in_chart: int = Field(default=0, ge=0)
    conflict: int = Field(default=0, ge=0)


class LabWriteBackResponse(W2Contract):
    """
    The ids the endpoint wrote, found already written, or found already in
    the chart — and, for a conflict, what it declined to overwrite.
    """

    patient_uuid: str = Field(pattern=PATIENT_UUID_PATTERN)
    document_id: int = Field(ge=1, description="`documents.id` of the stored file.")
    document_outcome: WriteOutcome
    procedure_order_id: int | None = Field(
        default=None,
        ge=1,
        description=(
            "The order this call created; when it created nothing, the order "
            "recorded for its first result; None when no result has an order "
            "(every result a conflict)."
        ),
    )
    results: list[LabWriteBackResultOutcome] = Field(min_length=1)
    correlation_id: str = Field(pattern=CORRELATION_ID_PATTERN)
    counts: WriteBackCounts | None = Field(
        default=None,
        description="Per-outcome counts as the endpoint reports them; `outcome_counts()` derives the same from `results`.",
    )

    def outcome_counts(self) -> WriteBackCounts:
        """Counts derived from the per-result outcomes, whatever the endpoint sent."""
        return WriteBackCounts(
            **{
                outcome.value: sum(1 for r in self.results if r.outcome is outcome)
                for outcome in WriteOutcome
            }
        )


class WriteBackDecision(StrEnum):
    """
    What happened after one extraction (D54). Logged; never shown as a
    value. `written` covers a call that reached the endpoint and was
    answered, including one that wrote no row because every row was
    already in the chart or conflicts with it.
    """

    WRITTEN = "written"
    NOT_LAB_REPORT = "not_lab_report"
    EXTRACTION_NOT_COMPLETE = "extraction_not_complete"
    IDENTITY_NOT_CONFIRMED = "identity_not_confirmed"
    NOTHING_WRITABLE = "nothing_writable"
    TOKEN_REFUSED = "token_refused"
    REQUEST_REFUSED = "request_refused"
    ENDPOINT_UNAVAILABLE = "endpoint_unavailable"


class WriteBackConflict(W2Contract):
    """
    One result the chart already holds with a different value or unit.

    Shown to the clinician on the chart card as "chart has X, this document
    has Y", never written (D2's rule for intake data, applied to a lab
    value). Both sides carry their provenance: the document value its page
    and region (invariant 2), the chart value its `procedure_result` id.
    Never logged: this model is chart content.
    """

    test_code: str = Field(min_length=1)
    test_name: str = Field(min_length=1)
    collection_date: date
    collected_at: datetime | None = None
    page: int = Field(ge=1)
    bbox: BBox
    document_value: str = Field(min_length=1, description="The value as the report writes it.")
    document_unit: str | None = None
    chart_value: str = Field(min_length=1, description="The value as the chart stores it.")
    chart_unit: str | None = None
    chart_procedure_result_id: int = Field(ge=1)


class WriteBackSummary(W2Contract):
    """
    A lab upload's write-back, as the chart card reports it: the decision,
    the counts, and the conflicts. Saved with the document (D60) and served
    by the extraction-status route once the write-back has ended.

    `skipped` is the rows the planner refused (unlocated, uncoded, undated,
    an undecidable or disputed flag); `results_total` is every row the
    extraction read, so the counts add up to it.
    """

    decision: WriteBackDecision
    results_total: int = Field(default=0, ge=0)
    skipped: int = Field(default=0, ge=0)
    created: int = Field(default=0, ge=0)
    duplicate_ignored: int = Field(default=0, ge=0)
    already_in_chart: int = Field(default=0, ge=0)
    conflict: int = Field(default=0, ge=0)
    conflicts: list[WriteBackConflict] = Field(default_factory=list)

    @model_validator(mode="after")
    def _conflicts_are_counted(self) -> Self:
        if len(self.conflicts) != self.conflict:
            raise ValueError("`conflict` counts the entries of `conflicts`")
        return self


class LabWriteBackLedgerRow(W2Contract):
    """
    One row of the ledger table in the fork.

    The UNIQUE constraint is on `idempotency_key` — that is the enforcement,
    and every other column is what makes the write traceable afterwards.
    D1: "`procedure_result.comments` carries an extraction id only. The
    ledger maps that extraction id to document id, page and bbox." This is
    that map.

    Identifiers and coordinates, no chart content: no test value, no patient
    name. The one clinical-ish column is `test_code`, which is a LOINC code
    and is part of the key. That keeps the table safe to dump while
    debugging, which is the same rule CLAUDE.md invariant 1 applies to logs.
    """

    idempotency_key: str = Field(min_length=1, description="UNIQUE.")
    extraction_id: str = Field(pattern=EXTRACTION_ID_PATTERN)
    document_sha3_512: str = Field(pattern=SHA3_512_PATTERN)
    document_id: int = Field(ge=1)
    patient_uuid: str = Field(pattern=PATIENT_UUID_PATTERN)
    test_code: str = Field(min_length=1)
    collection_date: date
    page: int = Field(ge=1)
    bbox: BBox
    procedure_order_id: int = Field(ge=1)
    procedure_report_id: int = Field(ge=1)
    procedure_result_id: int = Field(ge=1)
    written_at: datetime
    extractor_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def _self_consistent(self) -> Self:
        expected_key = lab_result_idempotency_key(
            document_sha3_512=self.document_sha3_512,
            test_code=self.test_code,
            collection_date=self.collection_date,
        )
        if self.idempotency_key != expected_key:
            raise ValueError("ledger row's idempotency_key disagrees with its own components")
        if self.bbox.page != self.page:
            raise ValueError("ledger row's page disagrees with its bbox")
        return self
