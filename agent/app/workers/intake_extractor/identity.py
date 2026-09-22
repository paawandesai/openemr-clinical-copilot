"""
Is this document about this chart's patient? A hard flag, not a discrepancy.

The factory's `wrong_patient_name` perturbation heads a document with
another person's name: "the document does not belong to this chart". That
is not a difference between two accounts of one patient — which is what a
`Discrepancy` describes — it is a document filed against the wrong chart.
So it is reported as its own `IdentityCheck` with `hard_flag=True`, and the
worker does not run the medication/allergy comparison at all when it fires:
comparing another person's medication list against this chart would produce
a screenful of `added`/`omitted` noise that reads as clinical findings.

Only located header values are compared (invariant 4): an unlocated or
illegible name is `not_comparable`, never a match and never a mismatch.

Name rule: the chart's family name must appear among the document name's
words, and at least one of the chart's given names must too (case and
punctuation ignored, word order free, so `Mueller846, Merlyn225` matches).
Birth date: compared only when the document's DOB parses as a date.
Nothing here is logged; the check returns enums and a citation.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from enum import StrEnum

from app.models.w2.base import W2Contract
from app.models.w2.citations import Citation
from app.models.w2.documents import DocumentPatientHeader
from app.workers.intake_extractor.chart import PatientIdentity

_WORD = re.compile(r"[a-z0-9]+")
_DOB_FORMATS = ("%Y-%m-%d", "%m/%d/%Y")


class IdentityComparison(StrEnum):
    MATCH = "match"
    MISMATCH = "mismatch"
    NOT_COMPARABLE = "not_comparable"


class IdentityCheck(W2Contract):
    name: IdentityComparison
    date_of_birth: IdentityComparison
    #: True when either comparison is a mismatch: the document is flagged as
    #: possibly not this patient's, and no discrepancy check runs.
    hard_flag: bool
    #: Where the document states the name that was compared, when it was.
    document_name_citation: Citation | None = None


def _words(text: str) -> set[str]:
    return set(_WORD.findall(text.casefold()))


def _parse_dob(text: str) -> date | None:
    for fmt in _DOB_FORMATS:
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            continue
    return None


def check_identity(header: DocumentPatientHeader, chart: PatientIdentity | None) -> IdentityCheck:
    name_fact = header.name.as_presented_fact()
    dob_fact = header.date_of_birth.as_presented_fact()

    name = IdentityComparison.NOT_COMPARABLE
    if name_fact is not None and chart is not None and chart.family and chart.given:
        written = _words(name_fact.value)
        family_ok = _words(chart.family) <= written
        given_ok = any(_words(part) and _words(part) <= written for part in chart.given)
        name = IdentityComparison.MATCH if family_ok and given_ok else IdentityComparison.MISMATCH

    dob = IdentityComparison.NOT_COMPARABLE
    if dob_fact is not None and chart is not None and chart.birth_date is not None:
        parsed = _parse_dob(dob_fact.value)
        if parsed is not None:
            dob = (
                IdentityComparison.MATCH
                if parsed == chart.birth_date
                else IdentityComparison.MISMATCH
            )

    return IdentityCheck(
        name=name,
        date_of_birth=dob,
        hard_flag=IdentityComparison.MISMATCH in (name, dob),
        document_name_citation=name_fact.citation if name_fact else None,
    )
