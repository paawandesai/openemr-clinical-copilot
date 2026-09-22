"""
What both document schemas share: the file, and the header identifying the
patient it is about.

Separate from lab_pdf.py and intake_form.py because the extractor, the
supervisor and the write-back ledger all need to talk about "the document"
without caring which kind it is, and because the patient header is the one
part of an intake form and a lab report that is genuinely the same shape.

**Two digests, never compared.** `DocumentChecksums` carries a SHA3-512
*and* a SHA-256 slot, because two systems that both hash our PDFs hash them
differently. OpenEMR uses SHA3-512 (`library/classes/Document.class.php`,
confirmed byte-for-byte in docs/w2/spikes/writeback.md §7) and that digest
is the document-level idempotency key D1 specifies. The doc-factory records
a SHA-256 in each ground truth (`document.sha256`). Holding them in one
field would eventually mean comparing a SHA-256 to a SHA3-512 and
concluding a document is new. They are separate fields with separate
patterns, and `document_idempotency_key()` (app/models/w2/writeback.py)
reads only the SHA3-512.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from app.models.w2.base import SHA3_512_PATTERN, SHA256_PATTERN, W2Contract
from app.models.w2.extraction import ExtractedField


class DocumentKind(StrEnum):
    """
    The two document kinds Week 2 extracts. Closed, because the supervisor
    routes on it and the write-back path accepts exactly one of them: D2
    forbids intake data reaching the chart, and the type is where that is
    enforced (`LabWriteBackRequest` takes LAB_PDF and nothing else).
    """

    LAB_PDF = "lab_pdf"
    INTAKE_FORM = "intake_form"


class ExtractionStatus(StrEnum):
    """
    Where an upload is in the D7 pipeline. D7: extraction runs
    asynchronously at upload time, and "a question asked against a document
    that has not finished extracting gets an explicit 'still processing'
    state — never a synchronous wait and never a partial extraction
    presented as complete."

    PENDING and IN_PROGRESS are that state. `IntakeExtractorOutput` forbids
    a payload in either, so a partial extraction has nowhere to be returned
    from.
    """

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"
    FAILED = "failed"


TERMINAL_EXTRACTION_STATUSES = frozenset({ExtractionStatus.COMPLETE, ExtractionStatus.FAILED})


class DocumentChecksums(W2Contract):
    """See the module docstring: two algorithms, two fields, never compared."""

    sha3_512: str | None = Field(
        default=None,
        pattern=SHA3_512_PATTERN,
        description=(
            "OpenEMR's digest of the stored file, lowercase hex. Required "
            "before anything can be written back: it is the document half "
            "of D1's idempotency key."
        ),
    )
    sha256: str | None = Field(
        default=None,
        pattern=SHA256_PATTERN,
        description=(
            "The doc-factory's digest (`document.sha256` in a ground truth "
            "file), lowercase hex. Evaluation provenance only; never used "
            "for deduplication."
        ),
    )

    @model_validator(mode="after")
    def _at_least_one(self) -> Self:
        if self.sha3_512 is None and self.sha256 is None:
            raise ValueError("a document carries at least one checksum")
        return self


class SourceDocument(W2Contract):
    """
    The file an extraction is about. Identifiers and counts only — no page
    text, no extracted values (CLAUDE.md invariant 1 governs what may be
    logged, and this model is the thing most likely to end up in a log
    line, so it holds nothing that could not).
    """

    document_id: str = Field(
        min_length=1,
        description=(
            "Our id for the stored document. Where OpenEMR holds it this is "
            "the stringified `documents.id`, which the custom write-back "
            "endpoint returns (D1) because the standard upload API returns "
            "only `true` (spike §9)."
        ),
    )
    kind: DocumentKind
    filename: str = Field(min_length=1)
    mime_type: str = Field(
        min_length=1,
        description=(
            "Validate by content before upload: OpenEMR silently rejects a "
            "type outside `files_white_list` as an empty 404 (spike §10.6)."
        ),
    )
    page_count: int = Field(ge=1)
    byte_length: int = Field(ge=1)
    checksums: DocumentChecksums
    has_text_layer: bool = Field(
        description=(
            "Whether stage 1 of D3 found a text layer (PyMuPDF) or had to "
            "OCR. Recorded because it is the single best predictor of "
            "`unlocated` fields, and a run's unlocated rate is meaningless "
            "without it."
        )
    )


class DocumentPatientHeader(W2Contract):
    """
    Who the document says it is about.

    Every field is an `ExtractedField`, including the name: a scanned intake
    form can have an illegible patient name, and the schema has to be able
    to say so rather than emit a plausible one. The doc-factory exercises
    exactly this — `intake-missing-allergy-006` omits `patient.dob`
    entirely and renders `patient.city_state_zip` as an unreadable smudge.

    **This header is not an identity assertion.** It is what the page says,
    and matching it to a chart is a discrepancy check
    (app/models/w2/intake_form.py), never a merge. Which patient a document
    belongs to is decided by the upload, not by reading the paper.
    """

    name: ExtractedField[str]
    date_of_birth: ExtractedField[str] = Field(
        description=(
            "As written on the page, not parsed. Intake forms and lab "
            "reports disagree about format (the factory emits `1959-12-28` "
            "on one and `09/10/2026` on the other) and normalising here "
            "would put a parser between the page and the citation's quote."
        )
    )
    sex: ExtractedField[str]
    address: ExtractedField[str]
    city_state_zip: ExtractedField[str]
    mrn: ExtractedField[str] = Field(
        default_factory=lambda: ExtractedField[str].missing(),
        description="Lab reports print one; intake forms generally do not.",
    )
