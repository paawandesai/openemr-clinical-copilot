"""
Week 2 contracts. The source of truth every Phase 3 branch builds against.

These are contracts, not implementation: no I/O, no model calls, no
FastAPI, no database. The only executable code here is deterministic and
pure — the abnormal-flag rule (`lab_values`), the idempotency key
composition (`writeback`), and the doc-factory adapter (`factory_mapping`)
— because each of those is a place where two branches would otherwise each
invent their own version and disagree in production.

Layout, and which invariant or decision each module answers to:

| module             | what it fixes                                         |
|--------------------|-------------------------------------------------------|
| `base`             | extra="forbid", strict, frozen; shared patterns        |
| `source_types`     | invariant 3 — chart, document and guideline are distinct |
| `citations`        | invariant 2 — the citation contract, plus bbox          |
| `extraction`       | invariant 4 — only a located value is a fact            |
| `documents`        | the file and the patient header both schemas share      |
| `lab_values`       | the deterministic abnormal-flag rule and its cross-check|
| `lab_pdf`          | the `lab_pdf` extraction schema                         |
| `intake_form`      | the `intake_form` schema and `Discrepancy` (D2)         |
| `workers`          | intake-extractor, evidence-retriever (D5), supervisor (D4) |
| `writeback`        | D1's custom endpoint, ledger and idempotency (invariant 6) |
| `answer`           | the final answer: claims, citations, separated lists     |
| `factory_mapping`  | doc-factory ground truth -> schema instances             |

They live under `app/models/` because that is where Week 1's contracts
live (`claims.py`, `api.py`, `fhir_resources.py`, ...), in a `w2/`
subpackage because there are eleven of them and Week 1's six are flat in
the parent. Nothing in Week 1 was moved.
"""

from __future__ import annotations

from app.models.w2.answer import FinalAnswer, GuidelineClaim, PatientFactClaim
from app.models.w2.base import W2Contract
from app.models.w2.citations import (
    BBox,
    Citation,
    citation_from_claim,
    citation_from_claim_outcome,
    claim_from_citation,
)
from app.models.w2.documents import (
    DocumentChecksums,
    DocumentKind,
    DocumentPatientHeader,
    ExtractionStatus,
    SourceDocument,
)
from app.models.w2.extraction import (
    ExtractedField,
    FieldStatus,
    PresentedFact,
    UnsupportedField,
    UnsupportedFieldError,
    UnsupportedReason,
)
from app.models.w2.intake_form import (
    Discrepancy,
    DiscrepancyCategory,
    DiscrepancyKind,
    FamilyHistoryEntry,
    IntakeAllergy,
    IntakeFormExtraction,
    IntakeMedication,
)
from app.models.w2.lab_pdf import (
    LabOrderHeader,
    LabPdfExtraction,
    LabResult,
    PerformingLab,
)
from app.models.w2.lab_values import (
    AbnormalFlag,
    AbnormalFlagComputation,
    Comparator,
    CrossCheckOutcome,
    FlagBasis,
    FlagCrossCheck,
    LabValue,
    NumericLabValue,
    QualitativeLabValue,
    ReferenceRange,
    compute_abnormal_flag,
    cross_check_abnormal_flag,
    openemr_abnormal_code,
    parse_reference_range_text,
    parse_reported_flag,
)
from app.models.w2.source_types import PATIENT_SOURCE_TYPES, SourceType
from app.models.w2.workers import (
    ALLOWED_REASONS,
    ClinicalConcept,
    EvidenceChunk,
    EvidenceRetrieverInput,
    EvidenceRetrieverOutput,
    ExtractionFailureReason,
    GuidelineSource,
    IntakeExtractorInput,
    IntakeExtractorOutput,
    RetrievalMethod,
    Route,
    RoutingDecision,
    RoutingReasonCode,
)
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

__all__ = [
    "ALLOWED_REASONS",
    "PATIENT_SOURCE_TYPES",
    "AbnormalFlag",
    "AbnormalFlagComputation",
    "BBox",
    "Citation",
    "ClinicalConcept",
    "Comparator",
    "CrossCheckOutcome",
    "Discrepancy",
    "DiscrepancyCategory",
    "DiscrepancyKind",
    "DocumentChecksums",
    "DocumentKind",
    "DocumentPatientHeader",
    "EvidenceChunk",
    "EvidenceRetrieverInput",
    "EvidenceRetrieverOutput",
    "ExtractedField",
    "ExtractionFailureReason",
    "ExtractionStatus",
    "FamilyHistoryEntry",
    "FieldStatus",
    "FinalAnswer",
    "FlagBasis",
    "FlagCrossCheck",
    "GuidelineClaim",
    "GuidelineSource",
    "IntakeAllergy",
    "IntakeExtractorInput",
    "IntakeExtractorOutput",
    "IntakeFormExtraction",
    "IntakeMedication",
    "LabOrderHeader",
    "LabPdfExtraction",
    "LabResult",
    "LabValue",
    "LabWriteBackLedgerRow",
    "LabWriteBackRequest",
    "LabWriteBackResponse",
    "LabWriteBackResult",
    "LabWriteBackResultOutcome",
    "NumericLabValue",
    "PatientFactClaim",
    "PerformingLab",
    "PresentedFact",
    "QualitativeLabValue",
    "ReferenceRange",
    "RetrievalMethod",
    "Route",
    "RoutingDecision",
    "RoutingReasonCode",
    "SourceDocument",
    "SourceType",
    "UnsupportedField",
    "UnsupportedFieldError",
    "UnsupportedReason",
    "W2Contract",
    "WriteBackDocument",
    "WriteOutcome",
    "citation_from_claim",
    "citation_from_claim_outcome",
    "claim_from_citation",
    "compute_abnormal_flag",
    "cross_check_abnormal_flag",
    "document_extraction_id",
    "document_idempotency_key",
    "lab_result_extraction_id",
    "lab_result_idempotency_key",
    "openemr_abnormal_code",
    "parse_reference_range_text",
    "parse_reported_flag",
]
