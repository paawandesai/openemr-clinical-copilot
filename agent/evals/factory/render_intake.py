"""
Patient intake form renderer.

A printed form the patient fills in by hand: ruled label/answer lines and
checkbox grids in a sans face, with every *answer* drawn in the handwriting
variant (``canvas.DocumentCanvas._draw_hand`` — Times-Italic, one glyph at a
time with a seeded baseline/advance wobble). Sections spill onto a second page
rather than truncating, so a chart with many medications still round-trips.
"""

from __future__ import annotations

import pymupdf

from evals.factory.canvas import (
    BLACK,
    GREY,
    INK,
    RULE,
    DocumentCanvas,
    PageGeometry,
    emit_field,
    text_width,
    truncate,
    wrap,
)
from evals.factory.content import IntakeContent
from evals.factory.groundtruth import FieldOrigin

GEOMETRY = PageGeometry(margin_left=56.0, margin_right=56.0, margin_top=56.0, margin_bottom=56.0)

_LEFT = GEOMETRY.margin_left
_RIGHT = GEOMETRY.width - GEOMETRY.margin_right
_HAND_SIZE = 10.5
_LABEL_SIZE = 8.2
_BAND = (0.89, 0.91, 0.94)


def _section(canvas: DocumentCanvas, number: int, title: str, hint: str = "") -> None:
    if not canvas.room_for(46):
        _continue_page(canvas)
    top = canvas.y + 12
    canvas.band(pymupdf.Rect(_LEFT, top, _RIGHT, top + 14), _BAND)
    canvas.label(_LEFT + 5, top + 10.2, f"{number}.  {title.upper()}", font="sans_bold", size=8.6)
    if hint:
        canvas.label(_RIGHT - 5 - text_width(hint, "sans_italic", 7.6), top + 10.2, hint,
                     font="sans_italic", size=7.6, color=GREY)
    canvas.y = top + 14


def _answer_line(
    canvas: DocumentCanvas,
    content: IntakeContent,
    label: str,
    field_path: str,
    value: str,
    *,
    x: float,
    width: float,
    baseline: float,
    origin: FieldOrigin = FieldOrigin.CHART,
    skip_if_blank: bool = True,
    note: str | None = None,
) -> None:
    """``Label ______`` with the answer written on the rule."""
    canvas.label(x, baseline, label, font="sans", size=_LABEL_SIZE, color=GREY)
    label_width = text_width(label, "sans", _LABEL_SIZE) + 6
    rule_x0 = x + label_width
    rule_x1 = x + width
    canvas.rule(rule_x0, baseline + 2.4, rule_x1, width=0.5, color=RULE)
    emit_field(
        canvas, content.plan, field_path, rule_x0 + 3, baseline - 0.6,
        truncate(value, "hand", _HAND_SIZE, rule_x1 - rule_x0 - 8),
        value=value, origin=origin, font="hand", size=_HAND_SIZE, color=INK,
        illegible_width=min(rule_x1 - rule_x0 - 8, 96), skip_if_blank=skip_if_blank, note=note,
    )


def _continue_page(canvas: DocumentCanvas) -> None:
    canvas.new_page()
    canvas.label(_LEFT, 68, "PATIENT INTAKE FORM  -  continued", font="sans_bold", size=9, color=GREY)
    canvas.rule(_LEFT, 74, _RIGHT, width=0.6)
    canvas.y = 78


def render(content: IntakeContent, seed: int, paper: tuple[float, float, float] | None) -> DocumentCanvas:
    canvas = DocumentCanvas(GEOMETRY, seed=seed, paper=paper)
    plan = content.plan

    # -- letterhead
    canvas.label(_LEFT, 72, content.clinic.name.upper(), font="sans_bold", size=13)
    canvas.label(_LEFT, 84, f"{content.clinic.address}    Tel {content.clinic.phone}",
                 font="sans", size=8, color=GREY)
    title = "NEW PATIENT INTAKE FORM"
    canvas.label(_RIGHT - text_width(title, "serif_bold", 12), 72, title, font="serif_bold", size=12)
    canvas.label(_RIGHT - text_width("Please print clearly in black ink.", "sans_italic", 7.6), 84,
                 "Please print clearly in black ink.", font="sans_italic", size=7.6, color=GREY)
    canvas.rule(_LEFT, 92, _RIGHT, width=1.3, color=BLACK)
    canvas.y = 92

    _section(canvas, 1, "Patient information")
    baseline = canvas.y + 20
    _answer_line(canvas, content, "Name", "patient.name", content.patient_name,
                 x=_LEFT, width=300, baseline=baseline,
                 origin=FieldOrigin.PERTURBED if content.patient_name_is_wrong else FieldOrigin.CHART,
                 note=None if not content.patient_name_is_wrong else f"chart name is {content.chart_name!r}")
    _answer_line(canvas, content, "Date of birth", "patient.dob", content.dob,
                 x=_LEFT + 310, width=190, baseline=baseline)
    baseline += 24
    _answer_line(canvas, content, "Address", "patient.address", content.address_line,
                 x=_LEFT, width=300, baseline=baseline)
    _answer_line(canvas, content, "Sex", "patient.sex", content.sex,
                 x=_LEFT + 310, width=190, baseline=baseline)
    baseline += 24
    _answer_line(canvas, content, "City / State / ZIP", "patient.city_state_zip", content.city_state_zip,
                 x=_LEFT, width=300, baseline=baseline)
    _answer_line(canvas, content, "Phone", "patient.phone", content.phone,
                 x=_LEFT + 310, width=190, baseline=baseline)
    baseline += 24
    _answer_line(canvas, content, "Date of visit", "visit.date", content.visit_date.strftime("%m/%d/%Y"),
                 x=_LEFT, width=190, baseline=baseline, origin=FieldOrigin.SYNTHETIC)
    canvas.y = baseline + 6

    # -- chief concern
    _section(canvas, 2, "Reason for today's visit", "in your own words")
    lines = wrap(content.chief_concern, "hand", _HAND_SIZE, _RIGHT - _LEFT - 14, max_lines=2)
    baseline = canvas.y + 20
    origin = FieldOrigin.CHART_DERIVED if content.chief_concern_from_chart else FieldOrigin.SYNTHETIC
    for index in range(2):
        canvas.rule(_LEFT, baseline + 2.4, _RIGHT, width=0.5, color=RULE)
        if index < len(lines):
            path = "chief_concern" if index == 0 else f"chief_concern.line{index + 1}"
            emit_field(canvas, plan, path, _LEFT + 4, baseline - 0.6, lines[index],
                       value=content.chief_concern if index == 0 else lines[index],
                       origin=origin, font="hand", size=_HAND_SIZE, color=INK,
                       illegible_width=min(text_width(lines[index], "hand", _HAND_SIZE) + 6, 260))
        baseline += 22
    canvas.y = baseline - 12

    # -- medications
    _section(canvas, 3, "Current medications", "include over-the-counter and vitamins")
    canvas.y += 4
    _table_header(canvas, ("MEDICATION", 0, 200), ("DOSE", 206, 96), ("HOW OFTEN", 308, 140))
    for index, medication in enumerate(content.medications):
        if not canvas.room_for(24):
            _continue_page(canvas)
            _table_header(canvas, ("MEDICATION", 0, 200), ("DOSE", 206, 96), ("HOW OFTEN", 308, 140))
        baseline = canvas.y + 15
        canvas.rule(_LEFT, baseline + 2.4, _RIGHT, width=0.4, color=(0.6, 0.6, 0.62))
        med_origin = FieldOrigin.PERTURBED if medication.is_perturbed else FieldOrigin.CHART_DERIVED
        emit_field(canvas, plan, f"medications[{index}].name", _LEFT + 3, baseline,
                   truncate(medication.written_name, "hand", _HAND_SIZE, 192),
                   value=medication.chart_name or medication.written_name,
                   origin=med_origin, font="hand", size=_HAND_SIZE, color=INK,
                   illegible_width=150,
                   note=medication.note or (
                       None if medication.chart_name is None
                       else "value is the full chart product name; the form carries the common name"))
        dose_text = medication.dose or "-"
        emit_field(canvas, plan, f"medications[{index}].dose", _LEFT + 206, baseline,
                   truncate(dose_text, "hand", _HAND_SIZE, 90), value=medication.dose,
                   origin=med_origin, font="hand", size=_HAND_SIZE, color=INK, illegible_width=80,
                   note=None if medication.dose else "the chart product name carries no strength")
        emit_field(canvas, plan, f"medications[{index}].frequency", _LEFT + 308, baseline,
                   truncate(medication.frequency, "hand", _HAND_SIZE, 132), value=medication.frequency,
                   origin=FieldOrigin.SYNTHETIC, font="hand", size=_HAND_SIZE, color=INK,
                   illegible_width=110, note="frequency has no chart counterpart")
        canvas.y = baseline + 6

    # -- allergies
    _section(canvas, 4, "Allergies")
    canvas.y += 4
    baseline = canvas.y + 13
    canvas.checkbox("allergies.none_known", _LEFT + 3, baseline - 8,
                    checked=content.no_known_allergies, origin=FieldOrigin.CHART_DERIVED,
                    note="'No known drug allergies' box; checked only when the chart has no allergies")
    canvas.label(_LEFT + 18, baseline, "No known drug allergies", font="sans", size=_LABEL_SIZE)
    canvas.y = baseline + 6
    _table_header(canvas, ("ALLERGY", 0, 220), ("REACTION OR SEVERITY", 226, 222))
    for index, allergy in enumerate(content.allergies):
        if not canvas.room_for(24):
            _continue_page(canvas)
            _table_header(canvas, ("ALLERGY", 0, 220), ("REACTION OR SEVERITY", 226, 222))
        baseline = canvas.y + 15
        canvas.rule(_LEFT, baseline + 2.4, _RIGHT, width=0.4, color=(0.6, 0.6, 0.62))
        allergy_origin = FieldOrigin.PERTURBED if allergy.is_perturbed else FieldOrigin.CHART
        emit_field(canvas, plan, f"allergies[{index}].allergen", _LEFT + 3, baseline,
                   truncate(allergy.allergen, "hand", _HAND_SIZE, 212), value=allergy.allergen,
                   origin=allergy_origin, font="hand", size=_HAND_SIZE, color=INK, illegible_width=150)
        emit_field(canvas, plan, f"allergies[{index}].reaction", _LEFT + 226, baseline,
                   truncate(allergy.reaction, "hand", _HAND_SIZE, 214), value=allergy.reaction,
                   origin=allergy_origin, font="hand", size=_HAND_SIZE, color=INK, illegible_width=140,
                   skip_if_blank=True)
        canvas.y = baseline + 6

    # -- family history
    _section(canvas, 5, "Family history", "check all that apply")
    canvas.y += 4
    for row_start in range(0, len(content.family_history), 2):
        if not canvas.room_for(26):
            _continue_page(canvas)
        baseline = canvas.y + 15
        for column, entry in enumerate(content.family_history[row_start:row_start + 2]):
            x = _LEFT + column * 250
            canvas.checkbox(f"family_history.{entry.key}", x, baseline - 8,
                            checked=entry.checked, origin=FieldOrigin.SYNTHETIC,
                            note="family history has no chart counterpart")
            canvas.label(x + 15, baseline, entry.label, font="sans", size=_LABEL_SIZE)
            who_x = x + 15 + text_width(entry.label, "sans", _LABEL_SIZE) + 8
            canvas.label(who_x, baseline, "who?", font="sans", size=7.4, color=GREY)
            rule_x0 = who_x + text_width("who?", "sans", 7.4) + 4
            canvas.rule(rule_x0, baseline + 2.4, x + 236, width=0.4, color=RULE)
            if entry.checked and entry.relation:
                emit_field(canvas, plan, f"family_history.{entry.key}.relation", rule_x0 + 2, baseline - 0.6,
                           truncate(entry.relation, "hand", 9.5, x + 232 - rule_x0),
                           value=entry.relation, origin=FieldOrigin.SYNTHETIC,
                           font="hand", size=9.5, color=INK, illegible_width=60,
                           note="family history has no chart counterpart")
        canvas.y = baseline + 8

    # -- signature
    _section(canvas, 6, "Signature")
    baseline = canvas.y + 24
    _answer_line(canvas, content, "Patient signature", "signature.name", content.patient_name,
                 x=_LEFT, width=300, baseline=baseline,
                 origin=FieldOrigin.PERTURBED if content.patient_name_is_wrong else FieldOrigin.CHART)
    _answer_line(canvas, content, "Date", "signature.date", content.visit_date.strftime("%m/%d/%Y"),
                 x=_LEFT + 310, width=190, baseline=baseline, origin=FieldOrigin.SYNTHETIC)
    canvas.y = baseline + 10

    _footers(canvas, content)
    return canvas


def _table_header(canvas: DocumentCanvas, *columns: tuple[str, float, float]) -> None:
    top = canvas.y + 4
    canvas.band(pymupdf.Rect(_LEFT, top, _RIGHT, top + 12), (0.94, 0.95, 0.96))
    for title, offset, _width in columns:
        canvas.label(_LEFT + offset + 3, top + 8.6, title, font="sans_bold", size=7.2, color=(0.3, 0.3, 0.3))
    canvas.y = top + 12


def _footers(canvas: DocumentCanvas, content: IntakeContent) -> None:
    total = canvas.doc.page_count
    for index in range(total):
        page = canvas.doc[index]
        y = GEOMETRY.height - 44
        page.draw_line(pymupdf.Point(_LEFT, y), pymupdf.Point(_RIGHT, y), color=GREY, width=0.5)
        page.insert_text((_LEFT, y + 11), f"{content.clinic.name}  -  page {index + 1} of {total}",
                         fontname="helv", fontsize=7.4, color=GREY)
        note = "Synthetic document - not for clinical use."
        page.insert_text((_RIGHT - text_width(note, "sans", 7.4), y + 11), note,
                         fontname="helv", fontsize=7.4, color=GREY)
