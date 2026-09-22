"""
What the worker hands back: the contract output plus everything it verified.

`IntakeExtractorOutput` (app/models/w2/workers.py) is the contract the
supervisor reads — a status and, when complete, the typed extraction. The
work order asks the worker for more than that contract carries: per-field
confidence, the flag cross-check with its denominator, the discrepancies,
the identity flag, cost. Those ride alongside it in `ExtractionReport`
rather than being added to the contract, because they are verification
results about an extraction, not part of what the document says.

Everything here is safe to *return*; only `ExtractionReport.log_summary()`
is safe to *log*, and it is built from counts, enums and ids alone.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from pydantic import Field

from app.models.w2.base import W2Contract
from app.models.w2.documents import ExtractionStatus
from app.models.w2.extraction import ExtractedField, UnsupportedField
from app.models.w2.intake_form import IntakeFormExtraction
from app.models.w2.lab_pdf import LabPdfExtraction
from app.models.w2.workers import IntakeExtractorOutput
from app.workers.intake_extractor.checks import FlagCrossCheckSummary
from app.workers.intake_extractor.discrepancies import DiscrepancyReport
from app.workers.intake_extractor.identity import IdentityCheck


class FieldConfidence(W2Contract):
    """One field's locate outcome and confidence (see confidence.py for the definition)."""

    field_path: str
    schema_path: str
    value_class: str
    outcome: str
    match_tier: str | None = None
    score: float | None = None
    reader_confidence: float | None = None
    confidence: float | None = None


class VisionCost(W2Contract):
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    latency_ms: int = 0
    served_models: list[str] = Field(default_factory=list)
    cost_usd: float | None = None


class ExtractionReport(W2Contract):
    output: IntakeExtractorOutput
    fields: list[FieldConfidence] = Field(default_factory=list)
    unsupported: list[UnsupportedField] = Field(default_factory=list)
    parse_failures: list[str] = Field(default_factory=list)
    mean_confidence: float | None = None
    flag_cross_check: FlagCrossCheckSummary | None = None
    identity: IdentityCheck | None = None
    discrepancies: DiscrepancyReport | None = None
    vision: VisionCost = Field(default_factory=VisionCost)
    step_ms: dict[str, int] = Field(default_factory=dict)
    #: Lab reports: each row's test_key and how its LOINC code was assigned
    #: (loinc.LoincOutcome value). Rows not `mapped` are uncoded and the
    #: write-back planner skips them as `no_loinc_code`.
    loinc: list[tuple[str, str]] = Field(default_factory=list)
    page_count: int | None = None
    has_text_layer: bool | None = None

    @property
    def verification_failures(self) -> int:
        """Flag disagreements — surfaced, never resolved (checks.py)."""
        return self.flag_cross_check.disagree if self.flag_cross_check else 0

    def outcome_counts(self) -> dict[str, int]:
        return dict(Counter(field.outcome for field in self.fields))

    def log_summary(self) -> dict[str, Any]:
        """Counts, enums and ids only — CLAUDE.md invariant 1."""
        output = self.output
        located_tiers = Counter(
            field.match_tier for field in self.fields if field.outcome == "located"
        )
        summary: dict[str, Any] = {
            "document_id": output.document_id,
            "extraction_id": output.extraction_id,
            "kind": output.kind.value,
            "status": output.status.value,
            "failure_reason": output.failure_reason.value if output.failure_reason else None,
            "page_count": self.page_count,
            "has_text_layer": self.has_text_layer,
            "steps": list(self.step_ms),
            "step_ms": dict(self.step_ms),
            "vision_calls": self.vision.calls,
            "input_tokens": self.vision.input_tokens,
            "output_tokens": self.vision.output_tokens,
            "cost_usd": self.vision.cost_usd,
            "fields": len(self.fields),
            "field_outcomes": self.outcome_counts(),
            "located_by_tier": dict(located_tiers),
            "parse_failures": len(self.parse_failures),
            "mean_confidence": self.mean_confidence,
        }
        if self.loinc:
            summary["loinc_outcomes"] = dict(Counter(outcome for _, outcome in self.loinc))
        if self.flag_cross_check is not None:
            check = self.flag_cross_check
            summary["cross_check"] = {
                "rows": check.rows,
                "checked": check.checked,
                "agree": check.agree,
                "disagree": check.disagree,
                "not_reported": check.not_reported,
                "not_computable": check.not_computable,
                "comparable": check.comparable,
                "computed_abnormal_unflagged": len(check.computed_abnormal_unflagged),
            }
        if self.identity is not None:
            summary["identity"] = {
                "name": self.identity.name.value,
                "date_of_birth": self.identity.date_of_birth.value,
                "hard_flag": self.identity.hard_flag,
            }
        if self.discrepancies is not None:
            summary["discrepancies"] = {
                "status": self.discrepancies.status.value,
                **self.discrepancies.counts(),
                "unresolved": len(self.discrepancies.unresolved),
                "intake_rows_not_compared": self.discrepancies.intake_rows_not_compared,
            }
        return summary

    @property
    def is_complete(self) -> bool:
        return self.output.status is ExtractionStatus.COMPLETE


def unsupported_fields(
    extraction: LabPdfExtraction | IntakeFormExtraction,
) -> list[UnsupportedField]:
    """
    Every non-presentable field, as the disclosure record the render step
    shows (`ExtractedField.as_unsupported`). Walks the extraction so nothing
    that is not a fact can be silently neither shown nor disclosed.
    """
    found: list[UnsupportedField] = []

    def visit(value: Any, path: str) -> None:
        if isinstance(value, ExtractedField):
            record = value.as_unsupported(path)
            if record is not None:
                found.append(record)
            return
        if isinstance(value, W2Contract):
            for name in type(value).model_fields:
                if name in ("document", "collection_date"):
                    continue
                visit(getattr(value, name), f"{path}.{name}" if path else name)
        elif isinstance(value, list):
            for position, item in enumerate(value):
                visit(item, f"{path}[{position}]")

    visit(extraction, "")
    return found
