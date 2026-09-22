"""
`SourceType` on its own, so that CLAUDE.md invariant 3 has one definition.

It lives in its own module rather than in citations.py because the
write-back contracts, the worker contracts and the final-answer contract
all switch on it, and a shared enum imported from the module that happens
to have needed it first is how two subtly different copies eventually
appear.
"""

from __future__ import annotations

from enum import StrEnum


class SourceType(StrEnum):
    """
    The three kinds of source a Week 2 claim can cite. Disjoint by
    construction — CLAUDE.md invariant 3: "Patient-record facts and
    guideline evidence are distinct source_types. Never let a guideline
    citation stand in for a chart fact, or the reverse."
    """

    #: A fact read from this patient's FHIR record under the clinician's own
    #: token. Week 1's entire world; `Claim.source_id` is its source_id.
    CHART = "chart"

    #: A fact extracted from a document uploaded for this patient — a lab
    #: PDF or an intake form. About the patient, but not *in* the chart:
    #: D2 keeps intake medications and allergies out of the chart entirely,
    #: and a document-derived value is only ever presented with the page and
    #: region it was located on.
    DOCUMENT = "document"

    #: A passage from guideline evidence. Not about this patient at all, and
    #: never usable as a statement about them.
    GUIDELINE = "guideline"


#: The source types that describe *this patient*. `PatientFactClaim`
#: accepts exactly these; `GuidelineClaim` accepts exactly GUIDELINE.
PATIENT_SOURCE_TYPES = frozenset({SourceType.CHART, SourceType.DOCUMENT})
