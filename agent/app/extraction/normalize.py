"""
Normalisation for the deterministic locate step (D3).

The locate step is the verifier: it decides whether a value a vision model
emitted is actually on the page it claims. So every rule in this module is
a decision about what counts as "the same text", and each one is a
trade-off between two failure modes:

* too strict — a value that *is* on the page is flagged `unsupported`
  (invariant 4), and a true finding is withheld from the clinician;
* too loose — a value that is *not* on the page gets a bbox and renders as
  fact, which is the failure D3 exists to prevent.

The second is far worse, so the rules here fold only differences that are
provably presentational: how the same characters can be drawn, spaced,
hyphenated, or written as the same number. Nothing here folds two
different readings of the page into one. In particular there is no
synonym list, no stemming and no spelling correction — "Sodium" does not
match "Na", and it should not.

Unit spellings are the one place a table is unavoidable, because a lab
report writes `mg/dL` and a model may emit `mg per dL` or `MG/DL` for the
same unit. `UNIT_ALIASES` is deliberately short and holds only strings
that are unit notation and nothing else. `per` is folded to `/` only
between two atoms that are already known units, so the ordinary English
word in "one tablet per day" survives untouched. `sec`/`seconds` is
excluded on the same ground and is left unfolded: it is an ordinary word,
and a coagulation result in seconds is not worth garbling prose for.

Numbers are compared as `Decimal`, not as text and not as `float`:
`Decimal("5") == Decimal("5.00")` is True, which is exactly the "5 vs 5.0
vs 5.00" equivalence the work order asks for, while `0.1 + 0.2` never
enters the picture.
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation

#: Characters that are the same mark drawn differently. Folded before
#: casefolding so the result is plain ASCII punctuation.
_CHARACTER_FOLDS = {
    "‐": "-",  # hyphen
    "‑": "-",  # non-breaking hyphen
    "‒": "-",  # figure dash
    "–": "-",  # en dash
    "—": "-",  # em dash
    "―": "-",  # horizontal bar
    "−": "-",  # minus sign
    "­": "",  # soft hyphen: a line-break hint, never ink
    "‘": "'",
    "’": "'",
    "‚": "'",
    "“": '"',
    "”": '"',
    "µ": "u",  # micro sign
    "μ": "u",  # greek small mu
    "°": "",  # degree sign: "°C" and "C" are the same unit
    "⁄": "/",  # fraction slash
    " ": " ",  # non-breaking space
}

#: Unit atoms: what may appear on either side of a `/` in a unit. Used to
#: decide whether the word "per" is unit notation (`_fold_per`), and
#: whether a string is a unit at all (`is_unit`). Adding an atom here
#: widens what `locate`'s number-only fallback will fire on, so add only
#: notation that cannot also be an ordinary word or an identifier.
_UNIT_ATOMS = frozenset(
    {
        "g",
        "mg",
        "ug",
        "ng",
        "pg",
        "kg",
        "l",
        "dl",
        "ml",
        "ul",
        "fl",
        "mol",
        "mmol",
        "umol",
        "nmol",
        "pmol",
        "meq",
        "iu",
        "miu",
        "uiu",
        "niu",
        "u",
        "k",
        "m",
        "min",
        "h",
        "hr",
        "mm3",
        "cm",
        "mm",
        "kpa",
        "mmhg",
        "cells",
        "ratio",
        "%",
    }
)

#: Whole-token rewrites. Every key is unit notation and is not an ordinary
#: English word, so rewriting it cannot change prose. Keep it that way.
UNIT_ALIASES = {
    "mcg": "ug",
    "mcl": "ul",
    "mcmol": "umol",
    "pct": "%",
    "percent": "%",
    "cumm": "mm3",
    "cmm": "mm3",
    "mm^3": "mm3",
    "mm³": "mm3",
    "10^3/ul": "k/ul",
    "10*3/ul": "k/ul",
    "x10e3/ul": "k/ul",
    "thou/ul": "k/ul",
    "10^6/ul": "m/ul",
    "10*6/ul": "m/ul",
    "x10e6/ul": "m/ul",
    "mil/ul": "m/ul",
    "mm hg": "mmhg",
    "mm/hg": "mmhg",
}

_WHITESPACE = re.compile(r"\s+")
_SLASH_SPACING = re.compile(r"\s*/\s*")
_PER = re.compile(r"(?<![^\s/(\[])([a-z%0-9^*]+)\s+per\s+([a-z%0-9^*]+)(?![^\s/)\],.;])")
_NUMBER = re.compile(r"^[+-]?(?:\d{1,3}(?:,\d{3})+|\d*)(?:\.\d+)?$")
_LEADING_NUMBER = re.compile(r"^\s*([+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|[+-]?\.\d+)\s*(.*)$")


def fold_characters(text: str) -> str:
    """NFKC, then the presentational character folds above. No casefolding."""
    folded = unicodedata.normalize("NFKC", text)
    return "".join(_CHARACTER_FOLDS.get(character, character) for character in folded)


def _fold_per(text: str) -> str:
    """`mg per dL` -> `mg/dl`, but only when both sides are known units."""

    def replace(match: re.Match[str]) -> str:
        left, right = match.group(1), match.group(2)
        if left in _UNIT_ATOMS and right in _UNIT_ATOMS:
            return f"{left}/{right}"
        return match.group(0)

    return _PER.sub(replace, text)


def normalize_units(text: str) -> str:
    """
    Apply `UNIT_ALIASES` and the notation folds, on an already-casefolded
    string.

    Multi-word aliases are applied before the string is split, so
    `mm hg` reaches `mmhg` rather than surviving as two tokens, and each
    token is then rewritten atom by atom so `mcg/dL` reaches `ug/dl`.
    """
    for alias, canonical in UNIT_ALIASES.items():
        if " " in alias and alias in text:
            text = text.replace(alias, canonical)
    text = _fold_per(text)
    text = _SLASH_SPACING.sub("/", text)
    return " ".join(_alias_token(token) for token in text.split(" "))


def _alias_token(token: str) -> str:
    """
    Rewrite one token through `UNIT_ALIASES`, whole first and then atom by
    atom, so `mcg/dL` reaches `ug/dl` and not only a bare `mcg` does. The
    whole-token pass runs first because some aliases contain a `/`
    themselves (`10^3/ul`).
    """
    if token in UNIT_ALIASES:
        return UNIT_ALIASES[token]
    if "/" not in token:
        return token
    parts = token.split("/")
    mapped = [UNIT_ALIASES.get(part, part) for part in parts]
    return "/".join(mapped)


def normalize(text: str) -> str:
    """
    The canonical form two strings are compared in.

    Character folds, casefold, unit notation, collapsed whitespace. Order
    matters: casefolding before the alias table is what makes the table
    lowercase-only, and collapsing whitespace last keeps `mm hg` visible to
    the multi-word aliases.
    """
    folded = fold_characters(text).casefold()
    folded = _WHITESPACE.sub(" ", folded).strip()
    return normalize_units(folded)


def squash(text: str) -> str:
    """
    `normalize`, with every space removed.

    Whitespace is the least reliable thing on a page. PyMuPDF splits a
    handwritten answer into one "word" per glyph run, a lab report puts a
    column gap where a model emits a single space, and OCR invents and
    loses spaces freely. Matching on the squashed stream means none of that
    decides whether a value is found; word boundaries are re-imposed
    separately, by `locate`, which only accepts a span that begins where a
    word begins and ends where a word ends.
    """
    return normalize(text).replace(" ", "")


def parse_number(text: str) -> Decimal | None:
    """
    The `Decimal` a token denotes, or None if it is not purely a number.

    Thousands separators (`1,234`) are accepted; a bare `5,0` is not. A
    decimal comma is a real convention, but telling it from a thousands
    separator needs a locale this module does not have, and guessing wrong
    turns `1,500` into one and a half. Rejecting it means such a value is
    reported `not_located` rather than located at the wrong number.
    """
    candidate = fold_characters(text).strip()
    if not candidate or not _NUMBER.match(candidate):
        return None
    candidate = candidate.replace(",", "")
    if candidate in {"", "+", "-", "."}:
        return None
    try:
        return Decimal(candidate)
    except InvalidOperation:  # pragma: no cover - guarded by the regex above
        return None


def is_unit(text: str) -> bool:
    """
    Whether a normalised string is unit notation, as opposed to more value.

    This is what keeps `locate`'s number-only fallback honest. `22D2118740`
    and `566 Labadie Station Suite 85` both start with a number, and if
    whatever follows counted as a unit, the fallback would locate a CLIA
    number on an unrelated `22` and a street address on an unrelated `566`.
    Requiring the remainder to be *built out of known unit atoms* rejects
    both, and rejects `70-99` and `2026-08-30` too, because a leading
    `-99` is a range and not a unit.

    A unit is atoms joined by `/`, with a bare number allowed only after
    the first one — `mL/min/1.73` is a unit, `-99` is not.
    """
    if not text or " " in text:
        return False
    first, *rest = text.split("/")
    if first not in _UNIT_ATOMS:
        return False
    return all(part in _UNIT_ATOMS or parse_number(part) is not None for part in rest)


def same_number(left: str, right: str) -> bool:
    """True when both tokens are numbers of equal value (`5` == `5.00`)."""
    left_value, right_value = parse_number(left), parse_number(right)
    if left_value is None or right_value is None:
        return False
    return left_value == right_value


def split_number_and_unit(text: str) -> tuple[Decimal, str] | None:
    """
    `"89 mg/dL"` -> `(Decimal("89"), "mg/dl")`; `"Serum"` -> None.

    The unit half is normalised; the empty string means the value carried
    no unit. Used by `locate` to try a number-and-unit match before falling
    back to the number alone, so `5` is preferred over `5 mg/dL` only when
    the stricter form is not on the page.
    """
    match = _LEADING_NUMBER.match(fold_characters(text).casefold())
    if match is None:
        return None
    number = parse_number(match.group(1))
    if number is None:
        return None
    return number, normalize_units(_WHITESPACE.sub(" ", match.group(2)).strip())
