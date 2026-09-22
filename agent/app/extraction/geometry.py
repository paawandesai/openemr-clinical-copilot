"""
Page rectangles, in the one coordinate convention this repository uses.

Every box here is `[x0, y0, x1, y1]` in **PDF points, origin top-left, y
increasing downward** — PyMuPDF page space — on the page of the document
that was actually handed to the extractor. That is the same convention the
doc-factory records (`agent/evals/w2/factory/groundtruth.py`
COORDINATE_CONVENTION) and the same one the Week 2 citation contract
declares (`app/models/w2/citations.py:BBox`, on `feat/w2-contracts`).

This module deliberately does not import that contract. The Pydantic
contracts are a different branch's work order, and the locate engine has
to run without them; `BBox` here carries the identical five fields in the
identical order, so `citations.BBox(**box.as_contract_kwargs())` is the
whole of the adapter when the two branches meet.

**"The page it was handed" matters for scans.** A scanned page is a
skewed photograph of a layout, wrapped as a full-bleed image on a page of
the original point size. A box found by OCR is in the *scan's* page space,
which is the layout rotated by the scan skew. That is the correct answer:
the scan is the document of record, the box points at the ink the
clinician will see when the PDF is opened. The doc-factory's ground truth
is recorded on the undegraded `clean.pdf` instead, so comparing the two
means mapping through the factory's recorded `transform` — which is a
measurement concern (`tests/locate_measurement.py`), not something
production code knows about.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class BBox:
    """
    A rectangle on one page.

    `x1 >= x0` and `y1 >= y0`, not `>`: a zero-width box is real data —
    the doc-factory emits one for every unflagged lab result — so an empty
    rectangle is representable rather than rejected.
    """

    page: int
    x0: float
    y0: float
    x1: float
    y1: float

    def __post_init__(self) -> None:
        if self.page < 1:
            raise ValueError(f"page is 1-based; got {self.page}")
        if self.x1 < self.x0:
            raise ValueError(f"bbox x1 ({self.x1}) is left of x0 ({self.x0})")
        if self.y1 < self.y0:
            raise ValueError(f"bbox y1 ({self.y1}) is above y0 ({self.y0})")

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def y_centre(self) -> float:
        return (self.y0 + self.y1) / 2.0

    def as_tuple(self) -> tuple[float, float, float, float]:
        """`[x0, y0, x1, y1]`, the doc-factory's ground-truth shape."""
        return (self.x0, self.y0, self.x1, self.y1)

    def as_contract_kwargs(self) -> dict[str, Any]:
        """The exact kwargs `app.models.w2.citations.BBox` takes."""
        return {"page": self.page, "x0": self.x0, "y0": self.y0, "x1": self.x1, "y1": self.y1}

    def union(self, other: BBox) -> BBox:
        if other.page != self.page:
            raise ValueError(
                f"cannot union boxes on different pages ({self.page} and {other.page}); "
                "a located span never crosses a page break"
            )
        return BBox(
            page=self.page,
            x0=min(self.x0, other.x0),
            y0=min(self.y0, other.y0),
            x1=max(self.x1, other.x1),
            y1=max(self.y1, other.y1),
        )

    def intersection_area(self, other: BBox) -> float:
        if other.page != self.page:
            return 0.0
        dx = min(self.x1, other.x1) - max(self.x0, other.x0)
        dy = min(self.y1, other.y1) - max(self.y0, other.y0)
        if dx <= 0 or dy <= 0:
            return 0.0
        return dx * dy

    def iou(self, other: BBox) -> float:
        """
        Intersection over union, 0.0 when the boxes are on different pages.

        Two zero-area boxes score 1.0 if they are identical and 0.0
        otherwise, rather than dividing by zero: an empty lab flag column
        still has a right and a wrong place to be.
        """
        intersection = self.intersection_area(other)
        union = self.area + other.area - intersection
        if union <= 0:
            same = (self.page, self.as_tuple()) == (other.page, other.as_tuple())
            return 1.0 if same else 0.0
        return intersection / union

    def vertical_overlap(self, other: BBox) -> float:
        """
        How much of the shorter box's height the two share, 0.0 to 1.0.

        This is the row test: two words sit on the same visual row when
        their vertical extents overlap substantially, which survives the
        baseline wobble of handwriting and the small per-word jitter OCR
        reports.
        """
        if other.page != self.page:
            return 0.0
        overlap = min(self.y1, other.y1) - max(self.y0, other.y0)
        if overlap <= 0:
            return 0.0
        shorter = min(self.height, other.height)
        if shorter <= 0:
            return 1.0
        return min(1.0, overlap / shorter)


def union_all(boxes: Iterable[BBox]) -> BBox:
    """Union of a non-empty iterable of boxes, all on the same page."""
    iterator = iter(boxes)
    try:
        merged = next(iterator)
    except StopIteration:
        raise ValueError("union_all needs at least one box") from None
    for box in iterator:
        merged = merged.union(box)
    return merged
