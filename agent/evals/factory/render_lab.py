"""
Lab report renderer.

Produces the layout a US outpatient lab prints: letterhead with CLIA number and
laboratory director, a patient block beside a specimen/accession block, then one
shaded section per ordered panel with a TEST / RESULT / FLAG / UNITS /
REFERENCE INTERVAL table, and a footer carrying "Page n of m". Long orders
continue onto further pages with the column header and a compact patient strip
repeated, which is also how the ``multi_page`` perturbation is realised.
"""

from __future__ import annotations

import pymupdf

from evals.factory.canvas import (
    BLACK,
    GREY,
    DocumentCanvas,
    PageGeometry,
    emit_field,
    text_width,
    truncate,
)
from evals.factory.content import LabContent
from evals.factory.groundtruth import FieldOrigin

GEOMETRY = PageGeometry()

_LEFT = GEOMETRY.margin_left
_RIGHT = GEOMETRY.width - GEOMETRY.margin_right

# Result-table column geometry, in points from the page's left edge.
_COL_TEST = _LEFT
_COL_TEST_W = 176.0
_COL_RESULT = _LEFT + 176.0
_COL_RESULT_W = 56.0
_COL_FLAG = _LEFT + 242.0
_COL_FLAG_W = 20.0
_COL_UNIT = _LEFT + 272.0
_COL_UNIT_W = 78.0
_COL_REF = _LEFT + 360.0
_COL_REF_W = _RIGHT - (_LEFT + 360.0)

_ROW_HEIGHT = 12.6
_ROW_SIZE = 8.6
_BAND = (0.90, 0.92, 0.95)
_PANEL_BAND = (0.82, 0.86, 0.91)


def _header(canvas: DocumentCanvas, content: LabContent) -> None:
    plan = content.plan
    emit_field(canvas, plan, "lab.name", _LEFT, 74, content.lab.name.upper(),
               value=content.lab.name, origin=FieldOrigin.SYNTHETIC, font="serif_bold", size=15)
    canvas.label(_LEFT, 87, content.lab.address, font="sans", size=8, color=GREY)
    emit_field(canvas, plan, "lab.address", _LEFT, 98,
               f"{content.lab.city_state_zip}   Tel {content.lab.phone}",
               origin=FieldOrigin.SYNTHETIC, font="sans", size=8, color=GREY)

    canvas.label(_RIGHT - text_width("CLIA ID", "sans_bold", 8), 87, "CLIA ID", font="sans_bold", size=8)
    emit_field(canvas, plan, "lab.clia", _RIGHT - 120, 98, content.lab.clia,
               origin=FieldOrigin.SYNTHETIC, font="sans", size=8, align="right", width=120)
    emit_field(canvas, plan, "lab.director", _RIGHT - 220, 109,
               f"Laboratory Director: {content.lab.director}", value=content.lab.director,
               origin=FieldOrigin.SYNTHETIC, font="sans", size=8, align="right", width=220)

    canvas.rule(_LEFT, 118, _RIGHT, width=1.4, color=BLACK)
    canvas.y = 118


def _two_column_block(canvas: DocumentCanvas, content: LabContent) -> None:
    plan = content.plan
    top = canvas.y + 14
    mid = _LEFT + 262

    canvas.band(pymupdf.Rect(_LEFT, top - 10, _RIGHT, top + 2), _BAND)
    canvas.label(_LEFT + 4, top, "PATIENT", font="sans_bold", size=8)
    canvas.label(mid + 4, top, "SPECIMEN AND ORDER", font="sans_bold", size=8)

    name_origin = FieldOrigin.PERTURBED if content.patient_name_is_wrong else FieldOrigin.CHART
    left_rows: list[tuple[str, str, str, FieldOrigin, str | None]] = [
        ("Name", "patient.name", content.patient_name, name_origin,
         None if not content.patient_name_is_wrong else f"chart name is {content.chart_name!r}"),
        ("Date of birth", "patient.dob", content.dob, FieldOrigin.CHART, None),
        ("Sex", "patient.sex", content.sex, FieldOrigin.CHART, None),
        ("MRN", "patient.mrn", content.mrn, FieldOrigin.CHART, None),
        ("Address", "patient.address", content.address_line, FieldOrigin.CHART, None),
        ("", "patient.city_state_zip", content.city_state_zip, FieldOrigin.CHART, None),
    ]
    right_rows: list[tuple[str, str, str, FieldOrigin]] = [
        ("Accession", "order.accession", content.accession, FieldOrigin.SYNTHETIC),
        ("Specimen", "order.specimen", content.specimen, FieldOrigin.SYNTHETIC),
        ("Collected", "order.collected", content.collected.strftime("%Y-%m-%d %H:%M"), FieldOrigin.SYNTHETIC),
        ("Received", "order.received", content.received.strftime("%Y-%m-%d %H:%M"), FieldOrigin.SYNTHETIC),
        ("Reported", "order.reported", content.reported.strftime("%Y-%m-%d %H:%M"), FieldOrigin.SYNTHETIC),
        ("Ordered by", "order.ordering_provider", content.ordering_provider, FieldOrigin.SYNTHETIC),
    ]

    baseline = top + 15
    for (label, path, value, origin, note), (rlabel, rpath, rvalue, rorigin) in zip(left_rows, right_rows):
        if label:
            canvas.label(_LEFT + 4, baseline, label, font="sans", size=8, color=GREY)
        emit_field(canvas, plan, path, _LEFT + 76, baseline,
                   truncate(value, "sans", 8.8, 178), value=value, origin=origin,
                   font="sans", size=8.8, note=note, illegible_width=120)
        canvas.label(mid + 4, baseline, rlabel, font="sans", size=8, color=GREY)
        emit_field(canvas, plan, rpath, mid + 68, baseline,
                   truncate(rvalue, "sans", 8.8, _RIGHT - (mid + 70)), value=rvalue,
                   origin=rorigin, font="sans", size=8.8, illegible_width=110)
        baseline += 12.4

    canvas.y = baseline - 4
    canvas.rule(_LEFT, canvas.y, _RIGHT, width=0.6)


def _patient_strip(canvas: DocumentCanvas, content: LabContent) -> None:
    """Compact identification repeated at the top of a continuation page."""
    canvas.label(_LEFT, 74,
                 f"{content.patient_name}   |   DOB {content.dob}   |   MRN {content.mrn}",
                 font="sans", size=8.4, color=GREY)
    canvas.label(_RIGHT - 190, 74, f"Accession {content.accession}   (continued)",
                 font="sans", size=8.4, color=GREY)
    canvas.rule(_LEFT, 80, _RIGHT, width=0.6)
    canvas.y = 84


def _column_header(canvas: DocumentCanvas) -> None:
    top = canvas.y + 4
    canvas.band(pymupdf.Rect(_LEFT, top, _RIGHT, top + 13), _BAND)
    baseline = top + 9.4
    canvas.label(_COL_TEST + 3, baseline, "TEST", font="sans_bold", size=7.4)
    canvas.label(_COL_RESULT, baseline, "RESULT", font="sans_bold", size=7.4)
    canvas.label(_COL_FLAG, baseline, "FLAG", font="sans_bold", size=7.4)
    canvas.label(_COL_UNIT, baseline, "UNITS", font="sans_bold", size=7.4)
    canvas.label(_COL_REF, baseline, "REFERENCE INTERVAL", font="sans_bold", size=7.4)
    canvas.y = top + 13


def _panel_header(canvas: DocumentCanvas, panel_name: str, order_code: str, continued: bool) -> None:
    top = canvas.y + 8
    canvas.band(pymupdf.Rect(_LEFT, top, _RIGHT, top + 14), _PANEL_BAND)
    title = f"{panel_name.upper()}{'  (CONTINUED)' if continued else ''}"
    canvas.label(_LEFT + 4, top + 10.2, title, font="sans_bold", size=8.6)
    code = f"CPT {order_code}"
    canvas.label(_RIGHT - 4 - text_width(code, "sans", 7.8), top + 10.2, code, font="sans", size=7.8)
    canvas.y = top + 14


def render(content: LabContent, seed: int, paper: tuple[float, float, float] | None) -> DocumentCanvas:
    canvas = DocumentCanvas(GEOMETRY, seed=seed, paper=paper)
    plan = content.plan
    _header(canvas, content)
    _two_column_block(canvas, content)

    for panel, results in content.panels:
        continued = False
        _panel_header(canvas, panel.name, panel.order_code, continued)
        _column_header(canvas)
        for result in results:
            if not canvas.room_for(_ROW_HEIGHT + 24):
                canvas.new_page()
                _patient_strip(canvas, content)
                continued = True
                _panel_header(canvas, panel.name, panel.order_code, continued)
                _column_header(canvas)

            baseline = canvas.y + _ROW_HEIGHT - 3.4
            test = result.test
            path = result.path
            flag_origin = FieldOrigin.PERTURBED if result.forced_abnormal else FieldOrigin.SYNTHETIC

            emit_field(canvas, plan, f"{path}.name", _COL_TEST + 3, baseline,
                       truncate(test.name, "sans", _ROW_SIZE, _COL_TEST_W - 8),
                       value=test.name, origin=FieldOrigin.SYNTHETIC, font="sans", size=_ROW_SIZE)
            emit_field(canvas, plan, f"{path}.value", _COL_RESULT, baseline, result.value_text,
                       value=result.value, unit=test.unit, origin=flag_origin,
                       font="sans_bold" if result.flag else "sans", size=_ROW_SIZE,
                       align="right", width=_COL_RESULT_W - 6, illegible_width=_COL_RESULT_W - 6)
            emit_field(canvas, plan, f"{path}.flag", _COL_FLAG, baseline, result.flag,
                       value=result.flag, origin=flag_origin, font="sans_bold", size=_ROW_SIZE,
                       align="center", width=_COL_FLAG_W, illegible_width=_COL_FLAG_W)
            emit_field(canvas, plan, f"{path}.unit", _COL_UNIT, baseline,
                       truncate(test.unit, "sans", _ROW_SIZE, _COL_UNIT_W - 4),
                       value=test.unit, origin=FieldOrigin.SYNTHETIC, font="sans", size=_ROW_SIZE)
            emit_field(canvas, plan, f"{path}.reference_range", _COL_REF, baseline,
                       result.interval_text, value=result.interval_text, unit=test.unit,
                       origin=FieldOrigin.SYNTHETIC, font="sans", size=_ROW_SIZE,
                       illegible_width=_COL_REF_W - 10)

            canvas.y += _ROW_HEIGHT
            canvas.rule(_LEFT, canvas.y, _RIGHT, width=0.25, color=(0.78, 0.78, 0.80))

        canvas.y += 4

    _footers(canvas, content)
    return canvas


def _footers(canvas: DocumentCanvas, content: LabContent) -> None:
    total = canvas.doc.page_count
    for index in range(total):
        page = canvas.doc[index]
        y = GEOMETRY.height - 46
        page.draw_line(pymupdf.Point(_LEFT, y), pymupdf.Point(_RIGHT, y), color=GREY, width=0.5)
        page.insert_text((_LEFT, y + 12), f"Page {index + 1} of {total}",
                         fontname="helv", fontsize=7.6, color=GREY)
        note = "Results apply only to the specimen tested. Synthetic document - not for clinical use."
        page.insert_text((_LEFT + 84, y + 12), note, fontname="helv", fontsize=7.6, color=GREY)
        stamp = f"Accession {content.accession}"
        page.insert_text((_RIGHT - text_width(stamp, "sans", 7.6), y + 12), stamp,
                         fontname="helv", fontsize=7.6, color=GREY)
