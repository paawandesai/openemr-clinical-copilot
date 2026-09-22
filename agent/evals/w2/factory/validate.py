"""
``python -m evals.w2.factory validate <output_dir>``

Re-reads what was written and checks it against itself, independently of the
code that produced it:

* every file's sha256 still matches what the ground truth recorded;
* every ``present`` field's bbox, on the CLEAN pdf, really does contain its
  ``rendered_text`` — via PyMuPDF text extraction, not via the render-time
  metrics that produced the bbox;
* every blank cell and every ``illegible`` region contains no extractable text;
* every ``absent`` field has no page and no bbox;
* every bbox lies inside its page;
* a scanned document has no text layer on any page, and a clean one does.

Comparison is whitespace-insensitive. The handwriting variant draws one glyph
at a time with a jittered advance, so PyMuPDF splits a written answer into more
"words" than were drawn; collapsing whitespace absorbs that while still
catching a wrong value, a wrong position or a wrong page.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from evals.w2.factory.canvas import words_in
from evals.w2.factory.groundtruth import GROUND_TRUTH_SCHEMA_VERSION


def _normalise(text: str) -> str:
    return "".join(text.split())


@dataclass
class ScenarioReport:
    scenario_id: str
    ground_truth_path: Path
    checks: int = 0
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


@dataclass
class ValidationReport:
    scenarios: list[ScenarioReport] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(s.ok for s in self.scenarios)

    @property
    def checks(self) -> int:
        return sum(s.checks for s in self.scenarios)

    @property
    def failures(self) -> int:
        return sum(len(s.failures) for s in self.scenarios)


def find_ground_truth_files(target: Path) -> list[Path]:
    if target.is_file():
        return [target]
    return sorted(target.rglob("*.ground_truth.json"))


def validate_file(ground_truth_path: Path) -> ScenarioReport:
    payload = json.loads(ground_truth_path.read_text(encoding="utf-8"))
    scenario_id = payload.get("scenario", {}).get("scenario_id", ground_truth_path.stem)
    report = ScenarioReport(scenario_id=scenario_id, ground_truth_path=ground_truth_path)
    base = ground_truth_path.parent

    def check(condition: bool, message: str) -> bool:
        report.checks += 1
        if not condition:
            report.failures.append(message)
        return condition

    if not check(
        payload.get("schema_version") == GROUND_TRUTH_SCHEMA_VERSION,
        f"schema_version {payload.get('schema_version')!r} != {GROUND_TRUTH_SCHEMA_VERSION!r}",
    ):
        return report

    document = payload["document"]
    clean = payload.get("clean_reference") or document
    document_path = base / document["path"]
    clean_path = base / clean["path"]

    if not check(document_path.is_file(), f"missing document file {document['path']}"):
        return report
    if not check(clean_path.is_file(), f"missing clean reference {clean['path']}"):
        return report

    document_bytes = document_path.read_bytes()
    check(
        hashlib.sha256(document_bytes).hexdigest() == document["sha256"],
        f"{document['path']}: sha256 does not match the recorded digest",
    )
    clean_bytes = clean_path.read_bytes()
    check(
        hashlib.sha256(clean_bytes).hexdigest() == clean["sha256"],
        f"{clean['path']}: sha256 does not match the recorded digest",
    )

    quality = payload["scenario"]["quality"]
    scanned = quality != "clean"

    with pymupdf.open(stream=document_bytes, filetype="pdf") as opened:
        text_pages = [opened[i].get_text("text").strip() for i in range(opened.page_count)]
    if scanned:
        offenders = [i + 1 for i, t in enumerate(text_pages) if t]
        check(
            not offenders,
            f"{document['path']}: quality={quality} but pages {offenders} have a text layer",
        )
        check(
            payload.get("transform") is not None,
            f"{scenario_id}: scanned document has no transform block",
        )
        if payload.get("transform"):
            check(
                len(payload["transform"]["pages"]) == len(text_pages),
                f"{scenario_id}: transform covers {len(payload['transform']['pages'])} pages, "
                f"document has {len(text_pages)}",
            )
    else:
        check(any(text_pages), f"{document['path']}: quality=clean but no page has a text layer")

    clean_doc = pymupdf.open(stream=clean_bytes, filetype="pdf")
    page_rects = [clean_doc[i].rect for i in range(clean_doc.page_count)]
    check(
        clean_doc.page_count == len(payload["pages"]),
        f"{scenario_id}: ground truth lists {len(payload['pages'])} pages, clean pdf has {clean_doc.page_count}",
    )

    for record in payload["fields"]:
        path = record["field_path"]
        status = record["status"]
        bbox = record["bbox"]
        page_number = record["page"]
        rendered = record["rendered_text"]

        if status == "absent":
            check(
                page_number is None and bbox is None,
                f"{path}: status=absent but carries page/bbox {page_number}/{bbox}",
            )
            check(rendered == "", f"{path}: status=absent but rendered_text is {rendered!r}")
            continue

        if not check(
            isinstance(page_number, int) and 1 <= page_number <= clean_doc.page_count,
            f"{path}: page {page_number!r} is out of range 1..{clean_doc.page_count}",
        ):
            continue
        if not check(bbox is not None, f"{path}: status={status} but bbox is null"):
            continue

        page_rect = page_rects[page_number - 1]
        inside = (
            bbox[0] >= page_rect.x0 - 0.5
            and bbox[1] >= page_rect.y0 - 0.5
            and bbox[2] <= page_rect.x1 + 0.5
            and bbox[3] <= page_rect.y1 + 0.5
        )
        check(inside, f"{path}: bbox {bbox} falls outside page {page_number} {tuple(page_rect)}")

        page = clean_doc[page_number - 1]
        # A wrapped field's union bbox spans several lines, and words_in orders
        # by x alone (a single drawn line is its unit), so the lines would
        # interleave. Read each line's own box instead and join them in order.
        boxes = record.get("line_bboxes") or [bbox]
        extracted = " ".join(
            " ".join(word[4] for word in words_in(page, box)) for box in boxes
        ).strip()

        if status == "illegible":
            check(
                not extracted, f"{path}: status=illegible but the bbox extracts text {extracted!r}"
            )
            continue

        if rendered == "":
            check(not extracted, f"{path}: blank field but the bbox extracts text {extracted!r}")
            continue

        check(
            _normalise(extracted) == _normalise(rendered),
            f"{path}: bbox on page {page_number} extracts {extracted!r}, expected {rendered!r}",
        )

        rendering = record.get("rendering")
        if rendering == "wrapped":
            check(
                len(record.get("line_bboxes") or []) > 1,
                f"{path}: rendering=wrapped but line_bboxes does not hold more than one box",
            )
        if rendering == "truncated":
            check(
                rendered.endswith("..."),
                f"{path}: rendering=truncated but rendered_text does not end in an ellipsis",
            )

    clean_doc.close()
    return report


def validate(target: Path) -> ValidationReport:
    report = ValidationReport()
    for path in find_ground_truth_files(target):
        report.scenarios.append(validate_file(path))
    return report
