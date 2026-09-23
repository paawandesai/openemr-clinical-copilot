"""
The chart side of the discrepancy check, read through the Week 1 FHIR client.

Read-only by construction: this module calls three `app.fhir.client` read
functions and nothing that writes. D2 — intake medications and allergies
are never merged into the chart — is also enforced by the absence of any
write path here, and `tests/test_intake_extractor_discrepancies.py`
asserts the worker package imports no write-capable client.

**Which chart records are compared.** The Week 1 brief's own notion of
"current" (`app/reasoning/draft_claims.py:brief_records`): medications
whose status is `active`, and allergies not explicitly ended (resolved or
inactive). An intake form lists what the patient takes and reacts to now,
so comparing it against stopped prescriptions would manufacture `omitted`
discrepancies for medications nobody expects on the form.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date

from app.fhir import client as fhir_client
from app.models.fhir_resources import AllergyIntolerance, FhirResourceType, MedicationRequest
from app.reasoning.draft_claims import brief_records


@dataclass(frozen=True)
class PatientIdentity:
    """The chart's name and birth date. Compared, never logged."""

    source_id: str
    given: tuple[str, ...]
    family: str | None
    birth_date: date | None


@dataclass(frozen=True)
class ChartSnapshot:
    identity: PatientIdentity | None
    medications: list[MedicationRequest]
    allergies: list[AllergyIntolerance]

    @property
    def current_medications(self) -> list[MedicationRequest]:
        return brief_records(FhirResourceType.MEDICATION_REQUEST, self.medications)

    @property
    def current_allergies(self) -> list[AllergyIntolerance]:
        return brief_records(FhirResourceType.ALLERGY_INTOLERANCE, self.allergies)


#: The seam the worker reads the chart through: patient uuid -> snapshot.
ChartReader = Callable[[str], Awaitable[ChartSnapshot]]


def fhir_chart_reader(token: str, *, patient_session_key: str | None = None) -> ChartReader:
    """A `ChartReader` over the Week 1 FHIR client, under the clinician's own token."""

    async def read(patient_uuid: str) -> ChartSnapshot:
        identity = await fhir_client.get_patient_identity(token, patient_uuid)
        medications = await fhir_client.get_medication_requests(
            token, patient_uuid, patient_session_key=patient_session_key
        )
        allergies = await fhir_client.get_allergy_intolerances(
            token, patient_uuid, patient_session_key=patient_session_key
        )
        birth = identity.get("birth_date")
        try:
            birth_date = date.fromisoformat(birth) if birth else None
        except ValueError:
            birth_date = None
        return ChartSnapshot(
            identity=PatientIdentity(
                source_id=patient_uuid,
                given=tuple(identity.get("given") or ()),
                family=identity.get("family"),
                birth_date=birth_date,
            ),
            medications=medications,
            allergies=allergies,
        )

    return read
