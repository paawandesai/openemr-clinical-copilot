"""
When too few of a document's values could be placed on its page, the answer
says so near the top (D60): directly below the critical-value block, which
still leads every answer (D40 item 9), and before everything else.

    quality = document_quality(document_id, report)
    if quality.degraded:
        line = degraded_notice(quality)

The measure is the **located rate** the extractor has been measured by since
D24 (`tests/intake_extractor_measurement.py`, W2_ARCHITECTURE.md §1.4):
located / (located + unlocated), over the values the model read. `missing`
and `illegible` describe the document, not how well it was read, and are in
neither count. A located value is the only kind the verifier accepts (D40
item 7), so the rate is also the share of what was read that *can* be stated.

THE THRESHOLD, AND WHY 75%
--------------------------
Measured (replay; real vision where marked), a document either reads well or
does not, with nothing in between:

  text layer   91.8% - 99.5%   (92% and 98% with real vision)
  light scan   87.5%
  heavy scan   33.6%           (34% with real vision)

The two clusters are 54 points apart, so any threshold between them separates
the samples we have. Where in the gap is decided by the cost of each error: a
notice on a document that read reasonably costs one sentence that is still
true — it states the counts — while a missing notice leaves a clinician
reading a short list of values as if it were the document. So the threshold
sits as high as it can without firing on a document measured as readable:
75%, 12.5 points under the light scan (which has no real-vision measurement,
and real vision has differed from replay by under a point on the two samples
measured both ways), and 41 points over the heavy scan. Below it, more than
one value in four that the model read could not be placed on the page.

The notice states counts and nothing read from the document, so it can never
state an unverified value. When the critical block above it holds values from
the same document, the notice says that each one stated was located on the
page and verified — the degradation is about the rest of the page, not about
them — and that a *possible* critical value was not, so its number is absent.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.w2.documents import DocumentKind
from app.workers.intake_extractor.report import ExtractionReport

#: A document whose located rate is strictly below this is reported as
#: degraded. See the module docstring for the measurements behind it.
DEGRADED_BELOW = 0.75

_KIND_NAMES = {DocumentKind.LAB_PDF: "lab report", DocumentKind.INTAKE_FORM: "intake form"}


@dataclass(frozen=True)
class DocumentQuality:
    document_id: str
    kind: DocumentKind
    located: int
    #: Values the model read: located + unlocated.
    read: int
    #: No text layer on every page: the words came from OCR.
    scanned: bool

    @property
    def located_rate(self) -> float | None:
        return self.located / self.read if self.read else None

    @property
    def degraded(self) -> bool:
        rate = self.located_rate
        return rate is not None and rate < DEGRADED_BELOW


def document_quality(document_id: str, report: ExtractionReport) -> DocumentQuality:
    outcomes = [field.outcome for field in report.fields]
    located = outcomes.count("located")
    return DocumentQuality(
        document_id=document_id,
        kind=report.output.kind,
        located=located,
        read=located + outcomes.count("unlocated"),
        scanned=report.has_text_layer is False,
    )


def degraded_notice(
    quality: DocumentQuality,
    *,
    name_document: bool = False,
    stated_criticals: int = 0,
    possible_criticals: int = 0,
) -> str:
    """
    Counts only. `name_document` prefixes which upload it is about, for a
    turn that reads more than one. `stated_criticals` / `possible_criticals`
    are this document's entries in the critical block above the notice.
    """
    noun = "scan" if quality.scanned else "document"
    unplaced = quality.read - quality.located
    sentence = (
        f"This {noun} is heavily degraded: {quality.located} of {quality.read} values read "
        f"from it could be located on the page and verified; the other {unplaced} are listed "
        "as unsupported and are not stated."
    )
    if stated_criticals == 1:
        sentence += (
            f" The critical value above from this {noun} was located on the page and verified."
        )
    elif stated_criticals > 1:
        sentence += (
            f" The {stated_criticals} critical values above from this {noun} were each "
            "located on the page and verified."
        )
    if possible_criticals == 1:
        sentence += (
            " The possible critical value above could not be located, so its number is not stated."
        )
    elif possible_criticals > 1:
        sentence += (
            f" The {possible_criticals} possible critical values above could not be located, "
            "so their numbers are not stated."
        )
    if name_document:
        return f"Uploaded {_KIND_NAMES[quality.kind]} ({quality.document_id[:12]}…): {sentence}"
    return sentence
