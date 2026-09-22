"""
doc-factory ground truth -> Week 2 schema instances, plus the mapping table.

The doc-factory (`agent/evals/w2/factory/`, branch `feat/doc-factory`)
emits an interim ground-truth format and says so in its own docstring: "NOT
the final Phase 2 schema ... `schema_version` exists so a later adapter can
convert it to the Pydantic `lab_pdf` / `intake_form` models without
guessing which shape a file on disk is in." This is that adapter, and the
mapping table below is the contract between the two.

The schema adopts the factory's `field_path` vocabulary wherever the
factory has one, so most of the table is an identity mapping with a
different spelling. `FIELD_PATH_RULES` is the machine-readable form;
`docs/w2/factory-field-mapping.md` is generated from it by
`scripts/export_w2_schemas.py`, so the documentation cannot drift from the
code.

---

**The one thing this adapter must not do, and the reason it is a module
rather than four lines in a test.**

A ground-truth `FieldRecord` has both a `value` and a `rendered_text`, and
they are not the same thing:

* `rendered_text` is "exactly the characters drawn on the page".
* `value` is "the semantic value ... for absent/illegible it is the chart
  truth the document fails to convey".

So for an `absent` field, `value` holds what the *chart* says while the
document says nothing at all — `intake-missing-allergy-006` records
`patient.dob` as absent with `value: "2014-01-24"`, and its own expected
discrepancy is "An extractor must report it as absent, not infer it." For
an `illegible` field, `value` holds the chart truth behind a smudge.

Copying `value` into an `ExtractedField` would therefore present chart
truth as document content: CLAUDE.md invariant 4 broken by the adapter,
silently, on exactly the cases the factory built to catch it. So the rule
here is absolute — **`ExtractedField.value` is derived from
`rendered_text`, never from `value`** — and the chart truth is returned
separately, in `GroundTruthConversion.chart_truth`, where it is available
for discrepancy checking and cannot be mistaken for something the document
said.

`rendered_text` and `value` also diverge on three `present` fields, where
the page legitimately carries less than the chart does. The factory flags
each with its own `note`:

* `lab.name` is drawn upper-cased;
* `lab.director` is drawn with its "Laboratory Director: " label;
* `medications[i].name` is drawn as the common name while `value` is the
  full chart product name ("the form carries the common name").

All three are the page being the page. The adapter takes `rendered_text`
and records the difference in `chart_truth`.

---

**The extraction id is synthetic here.** A ground-truth PDF was never
uploaded to OpenEMR, so it has no SHA3-512 and no real extraction run. The
adapter derives a stable `w2x-` id from the scenario id and the factory's
SHA-256 so that citations validate and stay stable across runs. Real
extractions use `writeback.document_extraction_id` over OpenEMR's digest.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime
from typing import Any, Literal

from pydantic import Field

from app.models.w2.base import W2Contract
from app.models.w2.citations import BBox, Citation
from app.models.w2.documents import (
    DocumentChecksums,
    DocumentKind,
    DocumentPatientHeader,
    SourceDocument,
)
from app.models.w2.extraction import ExtractedField
from app.models.w2.intake_form import (
    FamilyHistoryEntry,
    IntakeAllergy,
    IntakeFormExtraction,
    IntakeMedication,
)
from app.models.w2.lab_pdf import LabOrderHeader, LabPdfExtraction, LabResult, PerformingLab
from app.models.w2.lab_values import (
    AbnormalFlag,
    Comparator,
    NumericLabValue,
    QualitativeLabValue,
    ReferenceRange,
    parse_reference_range_text,
    parse_reported_flag,
)

#: The ground-truth format this adapter understands. The factory pins it in
#: every file it writes; a different version fails loudly rather than being
#: read under the wrong meanings.
SUPPORTED_GROUND_TRUTH_SCHEMA_VERSION = "doc-factory-groundtruth/0.1.0"


class FieldPathRule(W2Contract):
    """One row of the mapping table."""

    factory_path: str = Field(description="The factory's field_path, or a pattern for a family.")
    schema_path: str = Field(description="Where it lands in the Week 2 schema.")
    doc_kind: Literal["lab_pdf", "intake_form", "both"]
    note: str = ""


#: The mapping table. Patterns use `<panel>`, `<test>`, `<i>` and
#: `<condition>` as placeholders for the part of the path that varies.
FIELD_PATH_RULES: tuple[FieldPathRule, ...] = (
    # -- lab report ------------------------------------------------------
    FieldPathRule(
        factory_path="lab.name",
        schema_path="performing_lab.name",
        doc_kind="lab_pdf",
        note="Drawn upper-cased; the adapter takes the drawn form.",
    ),
    FieldPathRule(
        factory_path="lab.address", schema_path="performing_lab.address", doc_kind="lab_pdf"
    ),
    FieldPathRule(factory_path="lab.clia", schema_path="performing_lab.clia", doc_kind="lab_pdf"),
    FieldPathRule(
        factory_path="lab.director",
        schema_path="performing_lab.director",
        doc_kind="lab_pdf",
        note="Drawn with its 'Laboratory Director: ' label; the adapter takes the drawn form.",
    ),
    FieldPathRule(
        factory_path="order.accession", schema_path="order.accession", doc_kind="lab_pdf"
    ),
    FieldPathRule(factory_path="order.specimen", schema_path="order.specimen", doc_kind="lab_pdf"),
    FieldPathRule(
        factory_path="order.collected",
        schema_path="order.collected_at",
        doc_kind="lab_pdf",
        note="Parsed to datetime; also supplies every result's collection_date.",
    ),
    FieldPathRule(
        factory_path="order.received", schema_path="order.received_at", doc_kind="lab_pdf"
    ),
    FieldPathRule(
        factory_path="order.reported", schema_path="order.reported_at", doc_kind="lab_pdf"
    ),
    FieldPathRule(
        factory_path="order.ordering_provider",
        schema_path="order.ordering_provider",
        doc_kind="lab_pdf",
    ),
    FieldPathRule(
        factory_path="results.<panel>.<test>.name",
        schema_path="results[test_key=<test>].test_name",
        doc_kind="lab_pdf",
    ),
    FieldPathRule(
        factory_path="results.<panel>.<test>.value",
        schema_path="results[test_key=<test>].value",
        doc_kind="lab_pdf",
        note="Parsed from the drawn text into NumericLabValue or QualitativeLabValue.",
    ),
    FieldPathRule(
        factory_path="results.<panel>.<test>.unit",
        schema_path="results[test_key=<test>].unit",
        doc_kind="lab_pdf",
    ),
    FieldPathRule(
        factory_path="results.<panel>.<test>.reference_range",
        schema_path="results[test_key=<test>].reference_range",
        doc_kind="lab_pdf",
        note="Parsed by lab_values.parse_reference_range_text; the drawn text is kept.",
    ),
    FieldPathRule(
        factory_path="results.<panel>.<test>.flag",
        schema_path="results[test_key=<test>].reported_flag",
        doc_kind="lab_pdf",
        note="Empty flag column means `normal` — see lab_values' module docstring.",
    ),
    # -- intake form -----------------------------------------------------
    FieldPathRule(factory_path="visit.date", schema_path="visit_date", doc_kind="intake_form"),
    FieldPathRule(
        factory_path="chief_concern", schema_path="chief_concern", doc_kind="intake_form"
    ),
    FieldPathRule(
        factory_path="medications[<i>].name",
        schema_path="medications[<i>].name",
        doc_kind="intake_form",
        note="Drawn as the common name; the chart product name goes to chart_truth.",
    ),
    FieldPathRule(
        factory_path="medications[<i>].dose",
        schema_path="medications[<i>].dose",
        doc_kind="intake_form",
    ),
    FieldPathRule(
        factory_path="medications[<i>].frequency",
        schema_path="medications[<i>].frequency",
        doc_kind="intake_form",
    ),
    FieldPathRule(
        factory_path="allergies.none_known",
        schema_path="no_known_drug_allergies",
        doc_kind="intake_form",
        note="Checkbox; drawn 'X' is True, drawn empty is a located False.",
    ),
    FieldPathRule(
        factory_path="allergies[<i>].allergen",
        schema_path="allergies[<i>].substance",
        doc_kind="intake_form",
    ),
    FieldPathRule(
        factory_path="allergies[<i>].reaction",
        schema_path="allergies[<i>].reaction",
        doc_kind="intake_form",
    ),
    FieldPathRule(
        factory_path="family_history.<condition>",
        schema_path="family_history[condition=<condition>].present",
        doc_kind="intake_form",
    ),
    FieldPathRule(
        factory_path="family_history.<condition>.relation",
        schema_path="family_history[condition=<condition>].relation",
        doc_kind="intake_form",
    ),
    FieldPathRule(
        factory_path="signature.name", schema_path="signature_name", doc_kind="intake_form"
    ),
    FieldPathRule(
        factory_path="signature.date", schema_path="signature_date", doc_kind="intake_form"
    ),
    # -- both ------------------------------------------------------------
    FieldPathRule(
        factory_path="patient.name",
        schema_path="patient_header.name / demographics.name",
        doc_kind="both",
    ),
    FieldPathRule(
        factory_path="patient.dob",
        schema_path="patient_header.date_of_birth / demographics.date_of_birth",
        doc_kind="both",
        note="Kept as written; formats differ between the two document kinds.",
    ),
    FieldPathRule(
        factory_path="patient.sex",
        schema_path="patient_header.sex / demographics.sex",
        doc_kind="both",
    ),
    FieldPathRule(
        factory_path="patient.address",
        schema_path="patient_header.address / demographics.address",
        doc_kind="both",
    ),
    FieldPathRule(
        factory_path="patient.city_state_zip",
        schema_path="patient_header.city_state_zip / demographics.city_state_zip",
        doc_kind="both",
    ),
    FieldPathRule(
        factory_path="patient.mrn",
        schema_path="patient_header.mrn / demographics.mrn",
        doc_kind="both",
        note="Lab reports print one; the factory's intake forms do not.",
    ),
)


#: Factory paths with no home in the schema. Empty: every `field_path` the
#: six committed samples emit maps. `convert_ground_truth` reports anything
#: it cannot place in `GroundTruthConversion.unmapped_field_paths`, and
#: tests/test_w2_factory_ground_truth.py asserts that list is empty for all
#: six — so a new factory path shows up as a test failure, not a silent drop.
UNMAPPED_FACTORY_PATHS: frozenset[str] = frozenset()


#: Schema fields the factory has no path for, with why. The other half of
#: requirement 8: a mapping table is only honest if it lists both
#: directions.
SCHEMA_FIELDS_WITHOUT_FACTORY_PATH: dict[str, str] = {
    "lab_pdf.results[].loinc_code": (
        "Lab PDFs rarely print a LOINC code and the factory draws none. It is "
        "assigned by a coding step outside extraction, and the write-back path "
        "requires it (procedure_order_code.procedure_code)."
    ),
    "intake_form.medications[].route": (
        "The factory's intake form has no route column. Real forms ask for it, "
        "so the field exists and stays `missing` on factory documents."
    ),
    "*.confidence": (
        "The factory records no extractor confidence — ground truth is not a "
        "prediction. Stays None on converted fields."
    ),
    "*.checksums.sha3_512": (
        "The factory records a SHA-256; OpenEMR hashes with SHA3-512. Separate "
        "fields, never compared (see documents.DocumentChecksums)."
    ),
    "*.citation.extraction_id": (
        "No extraction run produced a ground truth. The adapter derives a "
        "stable synthetic id; see this module's docstring."
    ),
    "ExtractedField.status == unlocated": (
        "Not a document property and so not a ground-truth status. It is what "
        "the D3 locate step concludes at runtime when it cannot place a value "
        "the model read."
    ),
}


# --------------------------------------------------------------------------
# path parsing
# --------------------------------------------------------------------------

_RESULT_PATH = re.compile(r"^results\.(?P<panel>[^.]+)\.(?P<test>[^.]+)\.(?P<attr>[^.]+)$")
_INDEXED_PATH = re.compile(r"^(?P<group>medications|allergies)\[(?P<index>\d+)\]\.(?P<attr>.+)$")
_FAMILY_RELATION = re.compile(r"^family_history\.(?P<condition>[^.]+)\.relation$")
_FAMILY_CHECKBOX = re.compile(r"^family_history\.(?P<condition>[^.]+)$")

_PATIENT_HEADER_ATTR = {
    "patient.name": "name",
    "patient.dob": "date_of_birth",
    "patient.sex": "sex",
    "patient.address": "address",
    "patient.city_state_zip": "city_state_zip",
    "patient.mrn": "mrn",
}

_LAB_HEADER_ATTR = {
    "lab.name": "name",
    "lab.address": "address",
    "lab.clia": "clia",
    "lab.director": "director",
}

_ORDER_ATTR = {
    "order.accession": "accession",
    "order.specimen": "specimen",
    "order.collected": "collected_at",
    "order.received": "received_at",
    "order.reported": "reported_at",
    "order.ordering_provider": "ordering_provider",
}

_RESULT_ATTR = {
    "name": "test_name",
    "value": "value",
    "unit": "unit",
    "reference_range": "reference_range",
    "flag": "reported_flag",
}


def map_field_path(field_path: str, doc_kind: DocumentKind) -> str | None:
    """
    The schema path a factory `field_path` lands at, or None if there is
    none. Used by the generated mapping document and by the conversion's
    own unmapped-path reporting.
    """
    if field_path in _PATIENT_HEADER_ATTR:
        attr = _PATIENT_HEADER_ATTR[field_path]
        prefix = "patient_header" if doc_kind is DocumentKind.LAB_PDF else "demographics"
        return f"{prefix}.{attr}"
    if doc_kind is DocumentKind.LAB_PDF:
        if field_path in _LAB_HEADER_ATTR:
            return f"performing_lab.{_LAB_HEADER_ATTR[field_path]}"
        if field_path in _ORDER_ATTR:
            return f"order.{_ORDER_ATTR[field_path]}"
        match = _RESULT_PATH.match(field_path)
        if match and match.group("attr") in _RESULT_ATTR:
            return f"results[test_key={match.group('test')}].{_RESULT_ATTR[match.group('attr')]}"
        return None

    if field_path in ("visit.date", "chief_concern"):
        return "visit_date" if field_path == "visit.date" else "chief_concern"
    if field_path == "allergies.none_known":
        return "no_known_drug_allergies"
    if field_path == "signature.name":
        return "signature_name"
    if field_path == "signature.date":
        return "signature_date"
    indexed = _INDEXED_PATH.match(field_path)
    if indexed:
        group, index, attr = indexed.group("group", "index", "attr")
        if group == "medications" and attr in ("name", "dose", "frequency", "route"):
            return f"medications[{index}].{attr}"
        if group == "allergies" and attr in ("allergen", "reaction"):
            target = "substance" if attr == "allergen" else "reaction"
            return f"allergies[{index}].{target}"
        return None
    relation = _FAMILY_RELATION.match(field_path)
    if relation:
        return f"family_history[condition={relation.group('condition')}].relation"
    checkbox = _FAMILY_CHECKBOX.match(field_path)
    if checkbox:
        return f"family_history[condition={checkbox.group('condition')}].present"
    return None


# --------------------------------------------------------------------------
# value parsing
# --------------------------------------------------------------------------

_NUMERIC = re.compile(r"^(?P<op><=|>=|<|>)?\s*(?P<value>-?\d+(?:\.\d+)?)$")
_DATETIME_FORMATS = ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S")
_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y")


def parse_lab_value(text: str) -> NumericLabValue | QualitativeLabValue:
    """
    What is drawn in a result's value column -> a `LabValue`.

    A bare or censored number becomes `NumericLabValue`; anything else
    becomes `QualitativeLabValue`. Nothing is guessed: `positive` stays
    `positive` rather than becoming a 1.
    """
    stripped = text.strip()
    match = _NUMERIC.match(stripped)
    if match:
        return NumericLabValue(
            value=float(match.group("value")),
            comparator=Comparator(match.group("op")) if match.group("op") else None,
        )
    return QualitativeLabValue(text=stripped)


def _parse_datetime(text: str) -> datetime | None:
    for fmt in _DATETIME_FORMATS:
        try:
            return datetime.strptime(text.strip(), fmt)
        except ValueError:
            continue
    return None


def _parse_date(text: str) -> date | None:
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _parse_checkbox(text: str) -> bool:
    """A drawn checkbox: any ink means ticked, nothing drawn means unticked."""
    return bool(text.strip())


# --------------------------------------------------------------------------
# conversion
# --------------------------------------------------------------------------


class ChartTruthNote(W2Contract):
    """
    What the chart says where the document does not say it, or says
    something narrower. Kept out of the schema instance on purpose — see the
    module docstring.
    """

    field_path: str
    ground_truth_status: str
    chart_value: str
    rendered_text: str
    note: str | None = None


class GroundTruthConversion(W2Contract):
    """The result of converting one ground-truth file."""

    scenario_id: str
    extraction: LabPdfExtraction | IntakeFormExtraction
    chart_truth: list[ChartTruthNote] = Field(default_factory=list)
    expected_discrepancies: list[str] = Field(default_factory=list)
    unmapped_field_paths: list[str] = Field(default_factory=list)


def ground_truth_extraction_id(ground_truth: dict[str, Any]) -> str:
    """
    A stable synthetic extraction id for a ground-truth document. See the
    module docstring: ground truth has no OpenEMR digest and no real run.
    """
    scenario_id = ground_truth["scenario"]["scenario_id"]
    digest = ground_truth["document"].get("sha256", "")
    material = f"{SUPPORTED_GROUND_TRUTH_SCHEMA_VERSION}:{scenario_id}:{digest}"
    return "w2x-" + hashlib.sha3_256(material.encode("utf-8")).hexdigest()[:32]


def _citation(
    record: dict[str, Any], *, source_id: str, extraction_id: str, quote: Any
) -> Citation | None:
    page, box = record.get("page"), record.get("bbox")
    if page is None or box is None:
        return None
    return Citation.located_document(
        source_id=source_id,
        field_path=record["field_path"],
        quote=quote,
        bbox=BBox(page=page, x0=box[0], y0=box[1], x1=box[2], y1=box[3]),
        extraction_id=extraction_id,
    )


def _extracted(
    record: dict[str, Any],
    *,
    source_id: str,
    extraction_id: str,
    parse: Any = None,
) -> ExtractedField[Any]:
    """
    One ground-truth `FieldRecord` -> one `ExtractedField`.

    `present` becomes `located` when the record carries a page and a bbox,
    and `unlocated` when it does not — the latter cannot happen in the
    committed samples, but making the adapter total means a future factory
    change surfaces as a status rather than a crash.
    """
    status = record["status"]
    drawn = record.get("rendered_text", "")

    if status == "absent":
        return ExtractedField[Any].missing()

    if status == "illegible":
        # The quote of an unreadable region is the empty string: something
        # is there, and none of it could be read. The bbox is the ink.
        citation = _citation(record, source_id=source_id, extraction_id=extraction_id, quote="")
        return ExtractedField[Any].illegible(citation=citation)

    value = parse(drawn) if parse is not None else drawn
    if value is None:
        # The page drew something this adapter could not parse. That is an
        # unlocated value, not a missing one: the text is there.
        return ExtractedField[Any].unlocated(drawn)

    citation = _citation(record, source_id=source_id, extraction_id=extraction_id, quote=drawn)
    if citation is None:
        return ExtractedField[Any].unlocated(value)
    return ExtractedField[Any].located(value, citation)


def _same_meaning(extracted: Any, ground_truth_value: Any) -> bool:
    """
    Does the parsed field say the same thing as the ground truth's semantic
    value? Compared on meaning, not on spelling — `89` drawn on the page and
    a ground-truth `89.0` are the same reading, and recording that as a
    chart/document divergence would bury the three real ones under sixteen
    formatting artefacts.
    """
    if extracted is None or ground_truth_value is None:
        return extracted is ground_truth_value
    if isinstance(extracted, NumericLabValue):
        return (
            isinstance(ground_truth_value, (int, float))
            and float(ground_truth_value) == extracted.value
        )
    if isinstance(extracted, QualitativeLabValue):
        return str(ground_truth_value).strip() == extracted.text
    if isinstance(extracted, AbnormalFlag):
        return parse_reported_flag(str(ground_truth_value)) is extracted
    if isinstance(extracted, ReferenceRange):
        return str(ground_truth_value).strip() == (extracted.text or "")
    if isinstance(extracted, bool) or isinstance(ground_truth_value, bool):
        return bool(extracted) is bool(ground_truth_value)
    if isinstance(extracted, datetime):
        return _parse_datetime(str(ground_truth_value)) == extracted
    if isinstance(extracted, date):
        return _parse_date(str(ground_truth_value)) == extracted
    if isinstance(extracted, (int, float)) and isinstance(ground_truth_value, (int, float)):
        return float(extracted) == float(ground_truth_value)
    return str(extracted) == str(ground_truth_value)


def _chart_truth_note(
    record: dict[str, Any], extracted: ExtractedField[Any]
) -> ChartTruthNote | None:
    """
    Record the chart truth wherever the document does not convey it.

    Two cases produce a note. An `absent` or `illegible` field: the document
    says nothing and the chart value is what it failed to convey. And a
    `present` field the page draws more narrowly than the chart holds — a
    medication's common name against the chart's full product name.
    """
    status = record["status"]
    value = record.get("value")
    drawn = record.get("rendered_text", "")
    if value is None:
        return None
    if status == "present" and _same_meaning(extracted.value, value):
        return None
    return ChartTruthNote(
        field_path=record["field_path"],
        ground_truth_status=status,
        chart_value=str(value),
        rendered_text=drawn,
        note=record.get("note"),
    )


def _source_document(ground_truth: dict[str, Any], kind: DocumentKind) -> SourceDocument:
    document = ground_truth["document"]
    scenario_id = ground_truth["scenario"]["scenario_id"]
    return SourceDocument(
        document_id=scenario_id,
        kind=kind,
        filename=document["path"],
        mime_type="application/pdf",
        page_count=document["page_count"],
        byte_length=document["bytes"],
        checksums=DocumentChecksums(sha256=document["sha256"]),
        has_text_layer=document["has_text_layer"],
    )


def convert_ground_truth(ground_truth: dict[str, Any]) -> GroundTruthConversion:
    """
    A doc-factory ground-truth document -> a valid Week 2 schema instance.

    Total over the six committed samples and honest about anything it
    cannot place: unrecognised field paths come back in
    `unmapped_field_paths` rather than being dropped.
    """
    version = ground_truth.get("schema_version")
    if version != SUPPORTED_GROUND_TRUTH_SCHEMA_VERSION:
        raise ValueError(
            f"ground truth is {version!r}; this adapter reads "
            f"{SUPPORTED_GROUND_TRUTH_SCHEMA_VERSION!r}"
        )
    doc_type = ground_truth["scenario"]["doc_type"]
    kind = DocumentKind(doc_type)
    if kind is DocumentKind.LAB_PDF:
        return _convert_lab(ground_truth)
    return _convert_intake(ground_truth)


def _common(ground_truth: dict[str, Any], kind: DocumentKind) -> tuple[str, str, list[str]]:
    scenario_id = ground_truth["scenario"]["scenario_id"]
    extraction_id = ground_truth_extraction_id(ground_truth)
    discrepancies = [
        perturbation["expected_discrepancy"]
        for perturbation in ground_truth.get("perturbations", [])
        if perturbation.get("expected_discrepancy")
    ]
    _ = kind
    return scenario_id, extraction_id, discrepancies


def _convert_lab(ground_truth: dict[str, Any]) -> GroundTruthConversion:
    scenario_id, extraction_id, discrepancies = _common(ground_truth, DocumentKind.LAB_PDF)
    source_id = scenario_id

    header: dict[str, ExtractedField[Any]] = {}
    lab: dict[str, ExtractedField[Any]] = {}
    order: dict[str, ExtractedField[Any]] = {}
    rows: dict[str, dict[str, Any]] = {}
    chart_truth: list[ChartTruthNote] = []
    unmapped: list[str] = []

    for record in ground_truth["fields"]:
        path = record["field_path"]

        def build(parse: Any = None, _record: dict[str, Any] = record) -> ExtractedField[Any]:
            field = _extracted(
                _record, source_id=source_id, extraction_id=extraction_id, parse=parse
            )
            note = _chart_truth_note(_record, field)
            if note is not None:
                chart_truth.append(note)
            return field

        if path in _PATIENT_HEADER_ATTR:
            header[_PATIENT_HEADER_ATTR[path]] = build()
            continue
        if path in _LAB_HEADER_ATTR:
            lab[_LAB_HEADER_ATTR[path]] = build()
            continue
        if path in _ORDER_ATTR:
            attr = _ORDER_ATTR[path]
            order[attr] = build(_parse_datetime) if attr.endswith("_at") else build()
            continue
        result_match = _RESULT_PATH.match(path)
        if result_match and result_match.group("attr") in _RESULT_ATTR:
            panel, test, attr = result_match.group("panel", "test", "attr")
            row = rows.setdefault(test, {"panel": panel, "test_key": test})
            match attr:
                case "value":
                    row["value"] = build(parse_lab_value)
                case "reference_range":
                    unit = record.get("unit")
                    row["reference_range"] = build(
                        lambda text, _unit=unit: (
                            parse_reference_range_text(text, unit=_unit) if text.strip() else None
                        )
                    )
                case "flag":
                    row["reported_flag"] = build(parse_reported_flag)
                case _:
                    row[_RESULT_ATTR[attr]] = build()
            continue
        unmapped.append(path)

    collected = order.get("collected_at")
    collection_date = ExtractedField[Any].missing()
    if collected is not None and collected.is_presentable and collected.citation is not None:
        collection_date = ExtractedField[Any].located(collected.value.date(), collected.citation)

    results = [
        LabResult(
            panel=row["panel"],
            test_key=row["test_key"],
            test_name=row.get("test_name", ExtractedField[Any].missing()),
            value=row.get("value", ExtractedField[Any].missing()),
            unit=row.get("unit", ExtractedField[Any].missing()),
            reference_range=row.get("reference_range", ExtractedField[Any].missing()),
            collection_date=collection_date,
            reported_flag=row.get("reported_flag", ExtractedField[Any].missing()),
        )
        for row in rows.values()
    ]

    return GroundTruthConversion(
        scenario_id=scenario_id,
        extraction=LabPdfExtraction(
            extraction_id=extraction_id,
            document=_source_document(ground_truth, DocumentKind.LAB_PDF),
            patient_header=DocumentPatientHeader(**_with_missing(header, DocumentPatientHeader)),
            performing_lab=PerformingLab(**_with_missing(lab, PerformingLab)) if lab else None,
            order=LabOrderHeader(**_with_missing(order, LabOrderHeader)),
            results=results,
        ),
        chart_truth=chart_truth,
        expected_discrepancies=discrepancies,
        unmapped_field_paths=unmapped,
    )


def _convert_intake(ground_truth: dict[str, Any]) -> GroundTruthConversion:
    scenario_id, extraction_id, discrepancies = _common(ground_truth, DocumentKind.INTAKE_FORM)
    source_id = scenario_id

    demographics: dict[str, ExtractedField[Any]] = {}
    top: dict[str, ExtractedField[Any]] = {}
    medications: dict[int, dict[str, Any]] = {}
    allergies: dict[int, dict[str, Any]] = {}
    family: dict[str, dict[str, Any]] = {}
    chart_truth: list[ChartTruthNote] = []
    unmapped: list[str] = []

    for record in ground_truth["fields"]:
        path = record["field_path"]

        def build(parse: Any = None, _record: dict[str, Any] = record) -> ExtractedField[Any]:
            field = _extracted(
                _record, source_id=source_id, extraction_id=extraction_id, parse=parse
            )
            note = _chart_truth_note(_record, field)
            if note is not None:
                chart_truth.append(note)
            return field

        if path in _PATIENT_HEADER_ATTR:
            demographics[_PATIENT_HEADER_ATTR[path]] = build()
            continue
        if path == "visit.date":
            top["visit_date"] = build(_parse_date)
            continue
        if path == "chief_concern":
            top["chief_concern"] = build()
            continue
        if path == "allergies.none_known":
            top["no_known_drug_allergies"] = build(_parse_checkbox)
            continue
        if path == "signature.name":
            top["signature_name"] = build()
            continue
        if path == "signature.date":
            top["signature_date"] = build(_parse_date)
            continue
        indexed = _INDEXED_PATH.match(path)
        if indexed:
            group, index, attr = indexed.group("group", "index", "attr")
            position = int(index)
            if group == "medications" and attr in ("name", "dose", "frequency", "route"):
                medications.setdefault(position, {"row_index": position})[attr] = build()
                continue
            if group == "allergies" and attr in ("allergen", "reaction"):
                target = "substance" if attr == "allergen" else "reaction"
                allergies.setdefault(position, {"row_index": position})[target] = build()
                continue
            unmapped.append(path)
            continue
        relation = _FAMILY_RELATION.match(path)
        if relation:
            condition = relation.group("condition")
            family.setdefault(condition, {"condition": condition})["relation"] = build()
            continue
        checkbox = _FAMILY_CHECKBOX.match(path)
        if checkbox:
            condition = checkbox.group("condition")
            family.setdefault(condition, {"condition": condition})["present"] = build(
                _parse_checkbox
            )
            continue
        unmapped.append(path)

    return GroundTruthConversion(
        scenario_id=scenario_id,
        extraction=IntakeFormExtraction(
            extraction_id=extraction_id,
            document=_source_document(ground_truth, DocumentKind.INTAKE_FORM),
            demographics=DocumentPatientHeader(
                **_with_missing(demographics, DocumentPatientHeader)
            ),
            medications=[
                IntakeMedication(**_with_missing(row, IntakeMedication))
                for _, row in sorted(medications.items())
            ],
            allergies=[
                IntakeAllergy(**_with_missing(row, IntakeAllergy))
                for _, row in sorted(allergies.items())
            ],
            family_history=[
                FamilyHistoryEntry(**_with_missing(row, FamilyHistoryEntry))
                for _, row in sorted(family.items())
            ],
            **top,
        ),
        chart_truth=chart_truth,
        expected_discrepancies=discrepancies,
        unmapped_field_paths=unmapped,
    )


def _with_missing(values: dict[str, Any], model: type[W2Contract]) -> dict[str, Any]:
    """
    Fill every required `ExtractedField` the factory did not draw with an
    explicit `missing()`, rather than leaving it to a default. A field the
    document does not carry is a statement, and the schema should carry it
    as one.
    """
    filled = dict(values)
    for name, field in model.model_fields.items():
        if name in filled:
            continue
        if field.is_required() and "ExtractedField" in str(field.annotation):
            filled[name] = ExtractedField[Any].missing()
    return filled
