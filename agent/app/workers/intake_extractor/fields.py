"""
What kind of value each schema field holds — declared, never inferred.

The locate step (D3 stage 3) refuses the fuzzy tier for numbers, units,
dates and lab flags (D24). It can only honour that for a value whose class
it knows, and inferring the class from the characters the model emitted is
exactly the blind spot D24 measured: OCR reading the unit `ratio` as
`rtaio` stops looking like a unit, infers as `free_text`, and would have
the fuzzy tier switched back on for it. A colon-written ratio (`7.6:1.99`)
does the same.

So every leaf `ExtractedField` in both extraction schemas is listed here
with the class the extractor declares when it locates that field. The
extractor never calls `locate` without one, and
`tests/test_intake_extractor_fields.py` walks the two Pydantic schemas and
fails if a field exists that this table does not cover — so a schema field
added later cannot quietly fall back to inference.

**Identifiers are declared `numeric`.** An MRN, a CLIA number or an
accession number is not a quantity, but a near match on one is the same
kind of error as a near match on a potassium: one character is a different
patient, a different laboratory or a different specimen. `numeric` is the
declared class that means "exact tiers only"; the choice is recorded in
D29.

**Lab result values are declared `numeric` even when qualitative.** A
`Not Detected` that OCR reads as `Not Detectd` is not safe to forgive
either — `Detected` is one deletion away.
"""

from __future__ import annotations

from enum import StrEnum

from app.extraction import EXACT_ONLY_VALUE_CLASSES, ValueClass


class LocateStrategy(StrEnum):
    """How a field is placed on its page."""

    #: Search the whole page for the value.
    PAGE = "page"
    #: Search only the visual row headed by the field's row label — the
    #: lab-table and form-row case (`locate_in_row`).
    ROW = "row"
    #: A checkbox: find the printed label, then the tick mark immediately
    #: to its left on the same row (`locate_checkbox`).
    CHECKBOX = "checkbox"


#: Schema path pattern -> (declared value class, locate strategy). Patterns
#: use `[]` for a list element. Both the lab and the intake schema are here;
#: `patient.*` is shared (`patient_header` on a lab report, `demographics`
#: on an intake form).
FIELD_DECLARATIONS: dict[str, tuple[ValueClass, LocateStrategy]] = {
    # -- the patient header, both document kinds ---------------------------
    "patient.name": (ValueClass.FREE_TEXT, LocateStrategy.PAGE),
    "patient.date_of_birth": (ValueClass.DATE, LocateStrategy.PAGE),
    "patient.sex": (ValueClass.FREE_TEXT, LocateStrategy.PAGE),
    "patient.address": (ValueClass.FREE_TEXT, LocateStrategy.PAGE),
    "patient.city_state_zip": (ValueClass.FREE_TEXT, LocateStrategy.PAGE),
    "patient.mrn": (ValueClass.NUMERIC, LocateStrategy.PAGE),  # identifier
    # -- lab report ---------------------------------------------------------
    "performing_lab.name": (ValueClass.FREE_TEXT, LocateStrategy.PAGE),
    "performing_lab.address": (ValueClass.FREE_TEXT, LocateStrategy.PAGE),
    "performing_lab.clia": (ValueClass.NUMERIC, LocateStrategy.PAGE),  # identifier
    "performing_lab.director": (ValueClass.FREE_TEXT, LocateStrategy.PAGE),
    "order.accession": (ValueClass.NUMERIC, LocateStrategy.PAGE),  # identifier
    "order.specimen": (ValueClass.FREE_TEXT, LocateStrategy.PAGE),
    "order.collected_at": (ValueClass.DATE, LocateStrategy.PAGE),
    "order.received_at": (ValueClass.DATE, LocateStrategy.PAGE),
    "order.reported_at": (ValueClass.DATE, LocateStrategy.PAGE),
    "order.ordering_provider": (ValueClass.FREE_TEXT, LocateStrategy.PAGE),
    "results[].test_name": (ValueClass.FREE_TEXT, LocateStrategy.PAGE),
    "results[].value": (ValueClass.NUMERIC, LocateStrategy.ROW),
    "results[].unit": (ValueClass.UNIT, LocateStrategy.ROW),
    "results[].reference_range": (ValueClass.NUMERIC, LocateStrategy.ROW),
    "results[].reported_flag": (ValueClass.FLAG, LocateStrategy.ROW),
    # Not located on its own: copied from order.collected_at, with that
    # field's citation, exactly as the ground-truth adapter does.
    "results[].collection_date": (ValueClass.DATE, LocateStrategy.PAGE),
    # -- intake form --------------------------------------------------------
    "visit_date": (ValueClass.DATE, LocateStrategy.PAGE),
    "chief_concern": (ValueClass.FREE_TEXT, LocateStrategy.PAGE),
    "medications[].name": (ValueClass.FREE_TEXT, LocateStrategy.PAGE),
    "medications[].dose": (ValueClass.NUMERIC, LocateStrategy.ROW),
    "medications[].frequency": (ValueClass.FREE_TEXT, LocateStrategy.ROW),
    "medications[].route": (ValueClass.FREE_TEXT, LocateStrategy.ROW),
    "no_known_drug_allergies": (ValueClass.FLAG, LocateStrategy.CHECKBOX),
    "allergies[].substance": (ValueClass.FREE_TEXT, LocateStrategy.PAGE),
    "allergies[].reaction": (ValueClass.FREE_TEXT, LocateStrategy.ROW),
    "family_history[].present": (ValueClass.FLAG, LocateStrategy.CHECKBOX),
    "family_history[].relation": (ValueClass.FREE_TEXT, LocateStrategy.ROW),
    "signature_name": (ValueClass.FREE_TEXT, LocateStrategy.PAGE),
    "signature_date": (ValueClass.DATE, LocateStrategy.PAGE),
}

#: `loinc_code` is assigned, not extracted (lab_pdf.LabResult), so it has no
#: declaration and is never located.
NOT_EXTRACTED_FIELDS = frozenset({"results[].loinc_code"})


def pattern_for(field_path: str) -> str:
    """`results[3].value` -> `results[].value`; `demographics.name` -> `patient.name`."""
    parts = []
    for part in field_path.split("."):
        head, bracket, _ = part.partition("[")
        parts.append(f"{head}[]" if bracket else head)
    pattern = ".".join(parts)
    for prefix in ("patient_header.", "demographics."):
        if pattern.startswith(prefix):
            return "patient." + pattern[len(prefix) :]
    return pattern


def declared_class(field_path: str) -> ValueClass:
    """The declared class for a concrete field path. Raises on an undeclared one."""
    return FIELD_DECLARATIONS[pattern_for(field_path)][0]


def declared_strategy(field_path: str) -> LocateStrategy:
    return FIELD_DECLARATIONS[pattern_for(field_path)][1]


def is_exact_only(field_path: str) -> bool:
    """Whether D24 forbids the fuzzy tier for this field."""
    return declared_class(field_path) in EXACT_ONLY_VALUE_CLASSES
