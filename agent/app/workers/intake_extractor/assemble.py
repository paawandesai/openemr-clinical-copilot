"""
Transcription + page words -> the typed extraction. D3 stage 3 decides.

Every value the model transcribed goes through `locate` against the words
of the page the model says it came from, with the value class declared for
its schema field (`fields.py`), never inferred. The locate step's
three-valued answer maps onto D13's `FieldStatus`:

    located      -> located, with a Citation carrying the bbox and extraction id
    ambiguous    -> unlocated, UnsupportedReason.not_located_on_page
    not_located  -> unlocated, UnsupportedReason.not_located_on_page

A locate miss never becomes `missing` or `illegible` — those describe the
document, and only the transcription's own `absent` / `illegible` states
produce them (D24's relationship to D13).

Three further rules, each recorded in D29:

* **A blank printed field is `missing`.** A lab flag column the model reads
  as present-but-blank carries no flag for that row: the field is `missing`
  and the cross-check reads it as `not_reported` (D20), never `normal`.
  There is nothing to locate in an empty string.
* **A value that does not parse into its schema type is `illegible`.** The
  contracts are typed (`ExtractedField[datetime]`), so a date the model
  transcribed as `3O/O8/2026` cannot be held as an `unlocated` datetime. It
  is reported `illegible` with no value and listed in
  `AssembledExtraction.parse_failures`, so the cause stays visible.
* **The page is the model's claim, and it is tested.** A value is searched
  for only on the page the model named; found only elsewhere, it is
  unlocated (eval case W2-C6).

Constructing the result goes through the contract constructors
(`ExtractedField.located/unlocated/...`, `Citation.located_document`) and
the contract validators run on every field: nothing here can mark a value
presentable that the types would not.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from app.extraction import (
    DocumentWords,
    LocateResult,
    LocateStatus,
    MatchKind,
    PageIndex,
    ValueClass,
    locate,
    squash,
)
from app.extraction.locate import EXACT_ONLY_VALUE_CLASSES, Candidate
from app.models.w2.citations import BBox, Citation
from app.models.w2.documents import DocumentPatientHeader, SourceDocument
from app.models.w2.extraction import ExtractedField, FieldStatus
from app.models.w2.factory_mapping import parse_lab_value
from app.models.w2.intake_form import (
    FamilyHistoryEntry,
    IntakeAllergy,
    IntakeFormExtraction,
    IntakeMedication,
)
from app.models.w2.lab_pdf import LabOrderHeader, LabPdfExtraction, LabResult, PerformingLab
from app.models.w2.lab_values import parse_reference_range_text, parse_reported_flag
from app.workers.intake_extractor.confidence import UNLOCATED_CONFIDENCE, extraction_confidence
from app.workers.intake_extractor.fields import (
    LocateStrategy,
    declared_class,
    declared_strategy,
)
from app.workers.intake_extractor.vision_schema import (
    RawCheckbox,
    RawField,
    RawIntakeDocument,
    RawLabDocument,
    RawPatient,
)

#: What a tick in a printed checkbox is drawn as, squashed.
CHECKBOX_MARKS = frozenset({"x", "✓", "✔", "✗", "✘", "☒", "■", "v"})

#: How far left of its label a checkbox mark may sit, in label heights.
_CHECKBOX_MAX_GAP = 3.0

_DATETIME_FORMATS = (
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%m/%d/%Y %H:%M",
    "%m/%d/%Y %I:%M %p",
)
#: Slashed dates are read month-first, the US convention of the forms this
#: deployment receives. A day-first-only date (`25/09/2026`) fails to parse
#: and is reported rather than silently re-read (eval case W2-B5).
_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y")


class FieldOutcome:
    LOCATED = "located"
    UNLOCATED = "unlocated"
    MISSING = "missing"
    ILLEGIBLE = "illegible"
    PARSE_FAILED = "parse_failed"


@dataclass(frozen=True)
class FieldRecord:
    """
    What happened to one field. Identifiers, enums and numbers only — this is
    the shape the worker aggregates into its log line (invariant 1).
    """

    #: The declaration pattern, e.g. `results[].value` (fields.py).
    schema_path: str
    #: The concrete path in the citation vocabulary, e.g. `results.cmp.glucose.value`.
    field_path: str
    value_class: ValueClass
    strategy: LocateStrategy
    outcome: str
    locate_status: LocateStatus | None = None
    match_kind: MatchKind | None = None
    score: float | None = None
    reader_confidence: float | None = None
    confidence: float | None = None
    page: int | None = None


@dataclass(frozen=True)
class RowAnchor:
    """A row label's locate result, and the one occurrence that heads the row (or None)."""

    page: int | None
    result: LocateResult | None
    candidate: Candidate | None


@dataclass
class AssembledExtraction:
    extraction: LabPdfExtraction | IntakeFormExtraction
    records: list[FieldRecord] = field(default_factory=list)
    parse_failures: list[str] = field(default_factory=list)

    @property
    def located_count(self) -> int:
        return sum(1 for record in self.records if record.outcome == FieldOutcome.LOCATED)

    @property
    def unlocated_count(self) -> int:
        return sum(1 for record in self.records if record.outcome == FieldOutcome.UNLOCATED)


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


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")


class _Assembler:
    """One document's worth of locate calls, with the page indexes cached."""

    def __init__(self, words: DocumentWords, document: SourceDocument, extraction_id: str) -> None:
        self.words = words
        self.document = document
        self.extraction_id = extraction_id
        self.records: list[FieldRecord] = []
        self.parse_failures: list[str] = []
        self._indexes: dict[int, PageIndex] = {}

    # -- pages --------------------------------------------------------------

    def _index(self, page: int | None) -> PageIndex | None:
        if page is None or not 1 <= page <= self.document.page_count:
            return None
        if page not in self._indexes:
            page_words = self.words.page(page)
            if page_words is None:
                return None
            self._indexes[page] = PageIndex.for_page(page_words)
        return self._indexes[page]

    # -- row anchors ----------------------------------------------------------

    def anchor(self, label: RawField, siblings: Sequence[RawField]) -> RowAnchor | None:
        """
        Where a row's label sits — the test name of a lab row, the drug of a
        medication row, the substance of an allergy row. None when the model
        read no label.

        A label printed more than once on the page (`Glucose` is also the
        last word of `Estimated Average Glucose`; a panel heading repeats
        its analyte's name; a form lists `Acetaminophen` twice at two
        strengths) is `ambiguous` to a page-wide locate. The anchor asks a
        narrower question instead of choosing: which of those occurrences
        heads a row that also carries every one of the row's other transcribed
        values, matched by the exact and numeric tiers only? Exactly one
        such occurrence is the anchor; zero or several stay ambiguous. This
        is `locate_in_row`'s own move — narrow the question, never
        tie-break — applied to the label.
        """
        if label.state != "present" or not label.text.strip():
            return None
        index = self._index(label.page)
        if index is None:
            return RowAnchor(label.page, None, None)
        result = locate(label.text, index, value_class=ValueClass.FREE_TEXT)
        if result.status is LocateStatus.LOCATED:
            return RowAnchor(label.page, result, result.best)
        if result.status is LocateStatus.AMBIGUOUS:
            texts = [s.text for s in siblings if s.state == "present" and s.text.strip()]
            confirmed = [
                candidate
                for candidate in result.candidates
                if texts
                and all(
                    locate(
                        text, self._row_view(index, candidate), value_class=ValueClass.NUMERIC
                    ).status
                    is not LocateStatus.NOT_LOCATED
                    for text in texts
                )
            ]
            if len(confirmed) == 1:
                resolved = LocateResult(
                    LocateStatus.LOCATED,
                    (confirmed[0],),
                    "one occurrence of the label heads a row carrying all the row's values",
                    result.kind,
                    ValueClass.FREE_TEXT,
                )
                return RowAnchor(label.page, resolved, confirmed[0])
        return RowAnchor(label.page, result, None)

    @staticmethod
    def _row_view(index: PageIndex, anchor: Candidate) -> PageIndex:
        """The anchor's row(s), right of the anchor, excluding the anchor's own words."""
        position = {origin: offset for offset, origin in enumerate(index.origin)}
        own = {position[origin] for origin in anchor.word_indices if origin in position}
        rows = {index.words[offset].row for offset in own}
        return index.restrict(
            offset
            for offset, word in enumerate(index.words)
            if word.row in rows and offset not in own and word.bbox.x0 >= anchor.bbox.x1 - 0.1
        )

    # -- locate -------------------------------------------------------------

    def _run_locate(
        self, schema_path: str, text: str, page: int | None, anchor: RowAnchor | None
    ) -> LocateResult | None:
        index = self._index(page)
        if index is None:
            return None
        value_class = declared_class(schema_path)
        strategy = declared_strategy(schema_path)
        if strategy is LocateStrategy.ROW and anchor is not None:
            if anchor.candidate is None or anchor.page != page:
                # The row's label could not be placed (or is on another
                # page): the question "which row?" has no answer, so the
                # value is not searched for page-wide.
                return LocateResult(
                    LocateStatus.NOT_LOCATED, (), "row label not placed", None, value_class
                )
            result = locate(text, self._row_view(index, anchor.candidate), value_class=value_class)
        else:
            result = locate(text, index, value_class=value_class)
        # D24 is enforced inside `locate`; this is the worker refusing to
        # carry on if that ever stops being true, rather than presenting a
        # near-matched number.
        if result.kind is MatchKind.FUZZY and value_class in EXACT_ONLY_VALUE_CLASSES:
            raise AssertionError(
                f"fuzzy locate on an exact-only field ({value_class.value}); D24 violated"
            )
        return result

    def _citation(self, cite_path: str, candidate: Candidate) -> Citation:
        return Citation.located_document(
            source_id=self.document.document_id,
            field_path=cite_path,
            quote=candidate.matched_text,
            bbox=BBox(**candidate.bbox.as_contract_kwargs()),
            extraction_id=self.extraction_id,
        )

    def _unlocated_citation(self, cite_path: str, quote: str, page: int | None) -> Citation | None:
        if page is None or not 1 <= page <= self.document.page_count or not quote:
            return None
        return Citation.unlocated_document(
            source_id=self.document.document_id,
            field_path=cite_path,
            quote=quote,
            page=page,
            extraction_id=self.extraction_id,
        )

    def _record(self, schema_path: str, cite_path: str, outcome: str, **kwargs: Any) -> None:
        self.records.append(
            FieldRecord(
                schema_path=schema_path,
                field_path=cite_path,
                value_class=declared_class(schema_path),
                strategy=declared_strategy(schema_path),
                outcome=outcome,
                **kwargs,
            )
        )

    def field(
        self,
        schema_path: str,
        cite_path: str,
        raw: RawField,
        *,
        parse: Any = None,
        anchor: RowAnchor | None = None,
        is_anchor: bool = False,
    ) -> ExtractedField[Any]:
        """
        One transcribed field -> one ExtractedField, via locate. `anchor` is
        the row this field sits on (row-strategy fields); `is_anchor` marks
        the row's label itself, whose locate the anchor already ran.
        """
        if raw.state == "absent" or (raw.state == "present" and not raw.text.strip()):
            self._record(schema_path, cite_path, FieldOutcome.MISSING, page=raw.page)
            return ExtractedField[Any].missing()
        if raw.state == "illegible":
            self._record(schema_path, cite_path, FieldOutcome.ILLEGIBLE, page=raw.page)
            return ExtractedField[Any].illegible()

        text = raw.text
        value = parse(text) if parse is not None else text
        if value is None:
            self.parse_failures.append(schema_path)
            self._record(schema_path, cite_path, FieldOutcome.PARSE_FAILED, page=raw.page)
            return ExtractedField[Any].illegible()

        if is_anchor and anchor is not None:
            result = anchor.result
        else:
            result = self._run_locate(schema_path, text, raw.page, anchor)
        if result is None or result.status is not LocateStatus.LOCATED:
            self._record(
                schema_path,
                cite_path,
                FieldOutcome.UNLOCATED,
                locate_status=result.status if result else None,
                match_kind=result.kind if result else None,
                confidence=UNLOCATED_CONFIDENCE,
                page=raw.page,
            )
            return ExtractedField[Any].unlocated(
                value,
                citation=self._unlocated_citation(cite_path, text, raw.page),
                confidence=UNLOCATED_CONFIDENCE,
            )

        best = result.best
        assert best is not None and result.kind is not None and result.score is not None
        confidence = extraction_confidence(best.mean_confidence, result.kind, result.score)
        self._record(
            schema_path,
            cite_path,
            FieldOutcome.LOCATED,
            locate_status=result.status,
            match_kind=result.kind,
            score=result.score,
            reader_confidence=round(best.mean_confidence, 4),
            confidence=confidence,
            page=raw.page,
        )
        return ExtractedField[Any].located(
            value, self._citation(cite_path, best), confidence=confidence
        )

    def checkbox(self, schema_path: str, cite_path: str, raw: RawCheckbox) -> ExtractedField[Any]:
        """
        A checkbox is located by its mark: find the printed label, then a
        tick drawn immediately to its left on the same row. An unticked box
        draws nothing, so there is no ink to place a `False` on — it is
        `unlocated`, disclosed, never asserted.
        """
        if raw.state == "absent":
            self._record(schema_path, cite_path, FieldOutcome.MISSING, page=raw.page)
            return ExtractedField[Any].missing()
        if raw.state == "illegible":
            self._record(schema_path, cite_path, FieldOutcome.ILLEGIBLE, page=raw.page)
            return ExtractedField[Any].illegible()

        ticked = raw.state == "ticked"
        mark = self._checkbox_mark(raw.label, raw.page) if ticked else None
        if mark is None:
            self._record(
                schema_path,
                cite_path,
                FieldOutcome.UNLOCATED,
                confidence=UNLOCATED_CONFIDENCE,
                page=raw.page,
            )
            return ExtractedField[Any].unlocated(ticked, confidence=UNLOCATED_CONFIDENCE)

        confidence = extraction_confidence(mark.mean_confidence, MatchKind.EXACT, 1.0)
        self._record(
            schema_path,
            cite_path,
            FieldOutcome.LOCATED,
            locate_status=LocateStatus.LOCATED,
            match_kind=MatchKind.EXACT,
            score=1.0,
            reader_confidence=round(mark.mean_confidence, 4),
            confidence=confidence,
            page=raw.page,
        )
        return ExtractedField[Any].located(
            True, self._citation(cite_path, mark), confidence=confidence
        )

    def _checkbox_mark(self, label: str, page: int | None) -> Candidate | None:
        index = self._index(page)
        if index is None or not label.strip():
            return None
        anchor = locate(label, index, value_class=ValueClass.FREE_TEXT)
        if anchor.status is not LocateStatus.LOCATED or anchor.best is None:
            return None
        label_box = anchor.best.bbox
        label_rows = {index.words[i].row for i in anchor.best.word_indices}
        left = [
            (position, word)
            for position, word in enumerate(index.words)
            if word.row in label_rows and word.bbox.x1 <= label_box.x0 + 0.5
        ]
        if not left:
            return None
        position, nearest = max(left, key=lambda item: item[1].bbox.x1)
        gap = label_box.x0 - nearest.bbox.x1
        if squash(nearest.text) not in CHECKBOX_MARKS:
            return None
        if gap > _CHECKBOX_MAX_GAP * max(label_box.height, 1.0):
            return None
        return Candidate(
            bbox=nearest.bbox,
            line_bboxes=(nearest.bbox,),
            matched_text=nearest.text,
            kind=MatchKind.EXACT,
            score=1.0,
            word_indices=(index.origin[position],),
            mean_confidence=nearest.confidence,
        )

    # -- shared pieces ------------------------------------------------------

    def patient(self, prefix: str, raw: RawPatient) -> DocumentPatientHeader:
        cite = {
            "name": "patient.name",
            "date_of_birth": "patient.dob",
            "sex": "patient.sex",
            "address": "patient.address",
            "city_state_zip": "patient.city_state_zip",
            "mrn": "patient.mrn",
        }
        return DocumentPatientHeader(
            **{
                name: self.field(f"{prefix}.{name}", cite_path, getattr(raw, name))
                for name, cite_path in cite.items()
            }
        )


def _unique(keys: Sequence[str], fallback: str) -> list[str]:
    seen: dict[str, int] = {}
    out: list[str] = []
    for position, key in enumerate(keys):
        base = _slug(key) or f"{fallback}_{position}"
        count = seen.get(base, 0) + 1
        seen[base] = count
        out.append(base if count == 1 else f"{base}_{count}")
    return out


def assemble_lab(
    raw: RawLabDocument, words: DocumentWords, document: SourceDocument, extraction_id: str
) -> AssembledExtraction:
    a = _Assembler(words, document, extraction_id)
    header = a.patient("patient_header", raw.patient)

    lab_cite = {"name": "lab.name", "address": "lab.address", "clia": "lab.clia"}
    lab_cite["director"] = "lab.director"
    lab_fields = {
        name: a.field(f"performing_lab.{name}", cite, getattr(raw.performing_lab, name))
        for name, cite in lab_cite.items()
    }
    performing_lab = (
        PerformingLab(**lab_fields)
        if any(f.status is not FieldStatus.MISSING for f in lab_fields.values())
        else None
    )

    order_cite = {
        "accession": ("order.accession", None),
        "specimen": ("order.specimen", None),
        "collected_at": ("order.collected", _parse_datetime),
        "received_at": ("order.received", _parse_datetime),
        "reported_at": ("order.reported", _parse_datetime),
        "ordering_provider": ("order.ordering_provider", None),
    }
    order_fields = {
        name: a.field(f"order.{name}", cite, getattr(raw.order, name), parse=parse)
        for name, (cite, parse) in order_cite.items()
    }
    order = LabOrderHeader(**order_fields)

    collected = order.collected_at
    collection_date: ExtractedField[Any] = ExtractedField[Any].missing()
    if collected.is_presentable and collected.value is not None and collected.citation:
        collection_date = ExtractedField[Any].located(
            collected.value.date(), collected.citation, confidence=collected.confidence
        )

    keys = _unique([row.test_key for row in raw.results], "row")
    results: list[LabResult] = []
    for key, row in zip(keys, raw.results, strict=True):
        panel = _slug(row.panel) if row.panel else None
        base = f"results.{panel or 'ungrouped'}.{key}"
        anchor = a.anchor(row.test_name, [row.value, row.unit, row.reference_range, row.flag])
        unit_text = row.unit.text.strip() if row.unit.state == "present" else None
        results.append(
            LabResult(
                panel=panel,
                test_key=key,
                test_name=a.field(
                    "results[].test_name",
                    f"{base}.name",
                    row.test_name,
                    anchor=anchor,
                    is_anchor=True,
                ),
                value=a.field(
                    "results[].value",
                    f"{base}.value",
                    row.value,
                    parse=parse_lab_value,
                    anchor=anchor,
                ),
                unit=a.field("results[].unit", f"{base}.unit", row.unit, anchor=anchor),
                reference_range=a.field(
                    "results[].reference_range",
                    f"{base}.reference_range",
                    row.reference_range,
                    parse=lambda text, _unit=unit_text: parse_reference_range_text(
                        text, unit=_unit or None
                    ),
                    anchor=anchor,
                ),
                collection_date=collection_date,
                reported_flag=a.field(
                    "results[].reported_flag",
                    f"{base}.flag",
                    row.flag,
                    parse=parse_reported_flag,
                    anchor=anchor,
                ),
            )
        )

    extraction = LabPdfExtraction(
        extraction_id=extraction_id,
        document=document,
        patient_header=header,
        performing_lab=performing_lab,
        order=order,
        results=results,
    )
    return AssembledExtraction(extraction, a.records, a.parse_failures)


def assemble_intake(
    raw: RawIntakeDocument, words: DocumentWords, document: SourceDocument, extraction_id: str
) -> AssembledExtraction:
    a = _Assembler(words, document, extraction_id)
    demographics = a.patient("demographics", raw.patient)

    medications = []
    for position, row in enumerate(raw.medications):
        anchor = a.anchor(row.name, [row.dose, row.frequency, row.route])
        base = f"medications[{position}]"
        medications.append(
            IntakeMedication(
                row_index=position,
                name=a.field(
                    "medications[].name", f"{base}.name", row.name, anchor=anchor, is_anchor=True
                ),
                dose=a.field("medications[].dose", f"{base}.dose", row.dose, anchor=anchor),
                frequency=a.field(
                    "medications[].frequency", f"{base}.frequency", row.frequency, anchor=anchor
                ),
                route=a.field("medications[].route", f"{base}.route", row.route, anchor=anchor),
            )
        )

    allergies = []
    for position, row in enumerate(raw.allergies):
        anchor = a.anchor(row.substance, [row.reaction])
        base = f"allergies[{position}]"
        allergies.append(
            IntakeAllergy(
                row_index=position,
                substance=a.field(
                    "allergies[].substance",
                    f"{base}.allergen",
                    row.substance,
                    anchor=anchor,
                    is_anchor=True,
                ),
                reaction=a.field(
                    "allergies[].reaction", f"{base}.reaction", row.reaction, anchor=anchor
                ),
            )
        )

    conditions = _unique([row.condition.label for row in raw.family_history], "condition")
    family = [
        FamilyHistoryEntry(
            condition=condition,
            present=a.checkbox(
                "family_history[].present", f"family_history.{condition}", row.condition
            ),
            relation=a.field(
                "family_history[].relation",
                f"family_history.{condition}.relation",
                row.relation,
                anchor=a.anchor(
                    RawField(
                        state="present" if row.condition.state != "absent" else "absent",
                        text=row.condition.label,
                        page=row.condition.page,
                    ),
                    [row.relation],
                ),
            ),
        )
        for condition, row in zip(conditions, raw.family_history, strict=True)
    ]

    extraction = IntakeFormExtraction(
        extraction_id=extraction_id,
        document=document,
        demographics=demographics,
        visit_date=a.field("visit_date", "visit.date", raw.visit_date, parse=_parse_date),
        chief_concern=a.field("chief_concern", "chief_concern", raw.chief_concern),
        medications=medications,
        no_known_drug_allergies=a.checkbox(
            "no_known_drug_allergies", "allergies.none_known", raw.no_known_drug_allergies
        ),
        allergies=allergies,
        family_history=family,
        signature_name=a.field("signature_name", "signature.name", raw.signature_name),
        signature_date=a.field(
            "signature_date", "signature.date", raw.signature_date, parse=_parse_date
        ),
    )
    return AssembledExtraction(extraction, a.records, a.parse_failures)
