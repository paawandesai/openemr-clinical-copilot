"""
Document extraction — the deterministic halves of D3.

D3 makes extraction three stages: a text layer with word boxes, a vision
LLM into a strict schema, and a deterministic locate step. This package is
stages 1 and 3. **There is no model call anywhere under it**, and there is
no network access: `words` reads a PDF, `locate` compares strings and
rectangles, and that is the whole dependency surface. Stage 2 belongs to
another work order and will import from here, not the other way round.

    from app.extraction import extract_words, locate, locate_in_row

    document = extract_words(pdf_bytes)
    result = locate("5.4", document.page(1))
    if result.located:
        citation_bbox = result.bbox          # the only source of a bbox
    else:
        ...                                   # `unsupported` (invariant 4)

`tests/locate_measurement.py` measures this package against the
doc-factory's six committed samples and prints the located-rate, IoU,
false-locate and timing table that the extraction-confidence metric is
built from.
"""

from app.extraction.geometry import BBox, union_all
from app.extraction.locate import (
    AMBIGUITY_MARGIN,
    DEFAULT_FUZZY_THRESHOLD,
    EXACT_ONLY_VALUE_CLASSES,
    Candidate,
    FuzzyMode,
    LocateResult,
    LocateStatus,
    MatchKind,
    PageIndex,
    RowLocateResult,
    locate,
    locate_in_row,
)
from app.extraction.normalize import (
    ValueClass,
    classify_value,
    is_unit,
    normalize,
    parse_number,
    same_number,
    split_number_and_unit,
    squash,
)
from app.extraction.words import (
    DocumentWords,
    OcrPolicy,
    OcrSettings,
    OcrUnavailableError,
    PageWords,
    TextSource,
    Word,
    build_page_words,
    extract_page_words,
    extract_words,
    tesseract_available,
)

__all__ = [
    "AMBIGUITY_MARGIN",
    "DEFAULT_FUZZY_THRESHOLD",
    "EXACT_ONLY_VALUE_CLASSES",
    "BBox",
    "Candidate",
    "DocumentWords",
    "FuzzyMode",
    "LocateResult",
    "LocateStatus",
    "MatchKind",
    "OcrPolicy",
    "OcrSettings",
    "OcrUnavailableError",
    "PageIndex",
    "PageWords",
    "RowLocateResult",
    "TextSource",
    "ValueClass",
    "Word",
    "build_page_words",
    "classify_value",
    "extract_page_words",
    "extract_words",
    "is_unit",
    "locate",
    "locate_in_row",
    "normalize",
    "parse_number",
    "same_number",
    "split_number_and_unit",
    "squash",
    "tesseract_available",
    "union_all",
]
