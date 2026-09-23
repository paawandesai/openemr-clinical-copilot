"""
What an answer may cite from an uploaded document, and how a cited value is
worded — both in code.

**The catalog is located fields only.** `document_facts()` walks a finished
extraction and keeps every field whose `as_presented_fact()` is not None: a
value the deterministic locate step placed on its page, with a bbox (D3,
D13). An unlocated, missing or illegible field is not in the catalog, so the
drafting model is never shown it as something to cite; it reaches the
clinician through `FinalAnswer.unsupported`, as unsupported (invariant 4).

**Clinical parts only.** The catalog covers lab results and specimen dates,
and the intake form's medications, allergies, chief concern, family history
and visit date. The printed patient header (name, date of birth, address,
MRN) and signature are left out: they are what the identity check compares,
not facts a clinical question is answered from, and leaving them out keeps
identifiers out of the drafting prompt.

**Wording is code's.** The model picks a `field_path` and copies its quote;
the sentence the clinician reads is built here from the *extraction's* value,
never from the model's text. That is what makes D20 a property of the code
rather than a hope about a prompt: `flag_wording()` has no path from
`not_reported` to the word "normal".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from app.models.w2.citations import Citation
from app.models.w2.documents import DocumentKind
from app.models.w2.extraction import ExtractedField
from app.models.w2.intake_form import IntakeFormExtraction
from app.models.w2.lab_pdf import LabPdfExtraction, LabResult
from app.models.w2.lab_values import (
    NumericLabValue,
    QualitativeLabValue,
    ReferenceRange,
    ReportedFlag,
)

Extraction = LabPdfExtraction | IntakeFormExtraction

#: Within `order`, the parts that are about the specimen rather than people.
_ORDER_FIELDS = frozenset({"specimen", "collected_at", "reported_at"})

_DOCUMENT_NOUN = {DocumentKind.LAB_PDF: "Lab report", DocumentKind.INTAKE_FORM: "Intake form"}

_ATTRIBUTE_LABEL = {
    "test_name": "test",
    "value": "result",
    "unit": "unit",
    "reference_range": "reference range",
    "reported_flag": "flag",
    "collected_at": "collected",
    "reported_at": "reported",
    "specimen": "specimen",
    "name": "name",
    "dose": "dose",
    "frequency": "frequency",
    "route": "route",
    "substance": "substance",
    "reaction": "reaction",
    "present": "reported",
    "relation": "relation",
    "visit_date": "visit date",
    "chief_concern": "chief concern",
    "no_known_drug_allergies": "no known drug allergies box",
}

#: D20, as wording. The laboratory's own mark is quoted; a blank column is
#: said to be blank, and says so explicitly because "no flag" is the reading
#: a hurried eye turns into "normal".
_FLAG_WORDING: dict[ReportedFlag, str] = {
    ReportedFlag.HIGH: "flagged high by the laboratory",
    ReportedFlag.LOW: "flagged low by the laboratory",
    ReportedFlag.ABNORMAL: "flagged abnormal by the laboratory",
    ReportedFlag.NORMAL: "marked normal by the laboratory",
    ReportedFlag.NOT_REPORTED: (
        "no flag printed (a blank flag column is not a statement that the value is normal)"
    ),
    ReportedFlag.UNRECOGNISED: "a flag mark the co-pilot could not read",
}


def flag_wording(flag: ReportedFlag) -> str:
    """How a printed flag is worded. Never "normal" for a blank column (D20)."""
    return _FLAG_WORDING[flag]


@dataclass(frozen=True)
class DocumentFact:
    """One located, citable value. `citation` is the locate step's own."""

    document_id: str
    kind: DocumentKind
    field_path: str
    #: What the value belongs to — a lab row's printed test name, "medication
    #: 2" — and which part of it this is ("result", "flag"). Code's words.
    group: str
    part: str
    quote: str
    value: Any
    citation: Citation

    @property
    def label(self) -> str:
        return f"{self.group} — {self.part}" if self.part else self.group

    @property
    def group_key(self) -> tuple[str, str]:
        """
        Facts sharing this key render as one statement: one lab row, one
        medication. The path prefix, not the printed name, so two rows that
        print the same test name in different panels stay apart.
        """
        prefix, _, _ = self.field_path.rpartition(".")
        return self.document_id, prefix or self.field_path

    @property
    def page(self) -> int:
        assert self.citation.bbox is not None  # located by construction
        return self.citation.bbox.page

    def wording(self) -> str:
        """This value, worded from the extraction; never from the model's text."""
        value = render_value(self.value, self.quote)
        return f"{self.part}: {value}" if self.part else value


def group_statement(facts: list[DocumentFact]) -> str:
    """One sentence for facts sharing a `group_key`, in the order given."""
    first = facts[0]
    pages = sorted({fact.page for fact in facts})
    where = f"page {pages[0]}" if len(pages) == 1 else "pages " + ", ".join(map(str, pages))
    noun = _DOCUMENT_NOUN[first.kind]
    # A row's printed name is already its heading; saying it again as
    # "test: Glucose" adds nothing. Its citation is still on the claim.
    parts = [
        fact.wording() for fact in facts if not (fact.part == "test" and fact.quote == first.group)
    ]
    return f"{noun}, {where}: {first.group}: " + "; ".join(parts or [facts[0].wording()])


def render_value(value: Any, quote: str) -> str:
    """A located value, worded from its typed form; the quote where that is clearer."""
    if isinstance(value, ReportedFlag):
        return flag_wording(value)
    if isinstance(value, bool):
        return "ticked" if value else "not ticked"
    if isinstance(value, NumericLabValue | QualitativeLabValue | ReferenceRange):
        # The page's own characters: "<5", "0.00 - 0.04", "Positive".
        return quote
    if isinstance(value, datetime | date):
        return quote
    return str(value)


def _row_label(result: LabResult) -> str:
    name = result.test_name.as_presented_fact()
    return name.value if name is not None else result.test_key


def document_facts(extraction: Extraction) -> list[DocumentFact]:
    """Every located value in the clinical parts of one extraction, in document order."""
    kind = extraction.document.kind
    found: list[DocumentFact] = []

    def add(field: ExtractedField[Any], group: str, part: str = "") -> None:
        fact = field.as_presented_fact()
        if fact is None:
            return
        citation = fact.citation
        found.append(
            DocumentFact(
                document_id=extraction.document.document_id,
                kind=kind,
                field_path=citation.field_or_chunk_id,
                group=group,
                part=part,
                quote="" if citation.quote_or_value is None else str(citation.quote_or_value),
                value=fact.value,
                citation=citation,
            )
        )

    def attribute(name: str) -> str:
        return _ATTRIBUTE_LABEL.get(name, name.replace("_", " "))

    if isinstance(extraction, LabPdfExtraction):
        for name in sorted(_ORDER_FIELDS):
            # "specimen: type: Serum", not "specimen: specimen: Serum".
            part = "type" if name == "specimen" else attribute(name)
            add(getattr(extraction.order, name), "specimen", part)
        for result in extraction.results:
            row = _row_label(result)
            for name in ("test_name", "value", "unit", "reference_range", "reported_flag"):
                add(getattr(result, name), row, attribute(name))
        return found

    add(extraction.visit_date, attribute("visit_date"))
    add(extraction.chief_concern, attribute("chief_concern"))
    for medication in extraction.medications:
        for name in ("name", "dose", "frequency", "route"):
            add(
                getattr(medication, name), f"medication {medication.row_index + 1}", attribute(name)
            )
    add(extraction.no_known_drug_allergies, attribute("no_known_drug_allergies"))
    for allergy in extraction.allergies:
        for name in ("substance", "reaction"):
            add(getattr(allergy, name), f"allergy {allergy.row_index + 1}", attribute(name))
    for entry in extraction.family_history:
        add(entry.present, f"family history of {entry.condition}", attribute("present"))
        add(entry.relation, f"family history of {entry.condition}", attribute("relation"))
    return found


def flagged_test_names(extraction: Extraction) -> list[str]:
    """Printed names of lab rows the laboratory flagged high, low or abnormal."""
    if not isinstance(extraction, LabPdfExtraction):
        return []
    names: list[str] = []
    for result in extraction.results:
        flag = result.reported_flag.as_presented_fact()
        name = result.test_name.as_presented_fact()
        if flag is None or name is None:
            continue
        if flag.value in (ReportedFlag.HIGH, ReportedFlag.LOW, ReportedFlag.ABNORMAL):
            names.append(name.value)
    return names
