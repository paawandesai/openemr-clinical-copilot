"""
The eval suite's own reading of the chart: raw FHIR JSON from OpenEMR, parsed
here without importing anything from app/.

Independence is the point. The agent's FHIR client (app/fhir/client.py)
decides what a record's name is and which records are active; if that code
is wrong, the verifier checks claims against the same wrong values and
passes them (ARCHITECTURE.md §5, "Retrieval completeness" and "Source data
accuracy"). Expectations built from the agent's own parser could never
catch that. This module re-derives them from the raw resources instead.

What counts as chart content for a record (`ChartRecord.names`):
  - the CodeableConcept's `text`
  - every coding `display`, except codings from the data-absent-reason
    system, whose "Unknown" is a placeholder, not a value
  - the resource narrative `text.div`, tags stripped. OpenEMR puts an
    allergy's title there (src/Services/FHIR/FhirAllergyIntoleranceService.php:219-221),
    and for an uncoded allergy it is the only place the allergen is named.

Active, per USERS.md §3 Use Case 1 ("active problem list", "current
medications", "documented allergies"):
  - Condition: clinicalStatus has code "active"
  - MedicationRequest: status == "active"
  - AllergyIntolerance: clinicalStatus has code "active". OpenEMR emits
    "resolved" or "inactive" when the allergy has an end date
    (FhirAllergyIntoleranceService.php:117-121).

Matching is case- and whitespace-insensitive, nothing looser: the same rule
the verifier documents (app/verification/checker.py `_normalize_text`), so a
disagreement between the two is about content, not formatting.
"""

from __future__ import annotations

import asyncio
import html
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

DATA_ABSENT_SYSTEM = "http://terminology.hl7.org/CodeSystem/data-absent-reason"

BRIEF_TYPES = ("Condition", "MedicationRequest", "AllergyIntolerance")


def normalize(value: str) -> str:
    return " ".join(value.split()).casefold()


def short(source_id: str) -> str:
    """Record ids in messages: enough to find the record, not a dump of ids."""
    return source_id[:13]


@dataclass(frozen=True)
class ChartRecord:
    resource_type: str
    source_id: str
    active: bool
    status: str | None
    # Human-readable names in chart order, first is the best label.
    labels: tuple[str, ...]
    # True when the record's code carries no real value (data-absent only).
    uncoded: bool
    # True when the code itself names the record (text or a coding display),
    # not only the narrative.
    coded_name: bool = False

    @property
    def names(self) -> frozenset[str]:
        return frozenset(normalize(label) for label in self.labels)

    @property
    def label(self) -> str:
        return self.labels[0] if self.labels else "(no name anywhere in the record)"


@dataclass(frozen=True)
class Search:
    """One `<type>?patient=<id>` search, with enough to tell a partial read."""

    resource_type: str
    status: int
    total: int | None
    has_next_link: bool
    records: tuple[ChartRecord, ...]

    @property
    def complete(self) -> bool:
        return self.status == 200 and not self.has_next_link and (self.total is None or self.total == len(self.records))

    @property
    def ids(self) -> set[str]:
        return {r.source_id for r in self.records}

    @property
    def active(self) -> list[ChartRecord]:
        return [r for r in self.records if r.active]


@dataclass(frozen=True)
class PatientChart:
    patient_id: str
    searches: dict[str, Search]
    encounters: int | None = None

    def record(self, resource_type: str, source_id: str) -> ChartRecord | None:
        search = self.searches.get(resource_type)
        if search is None:
            return None
        return next((r for r in search.records if r.source_id == source_id), None)

    def record_of_any_type(self, source_id: str) -> ChartRecord | None:
        for search in self.searches.values():
            for record in search.records:
                if record.source_id == source_id:
                    return record
        return None

    @property
    def all_ids(self) -> set[str]:
        return set().union(*(s.ids for s in self.searches.values()))

    @property
    def all_names(self) -> set[str]:
        return {n for s in self.searches.values() for r in s.records for n in r.names}

    def count(self, resource_type: str) -> int:
        return len(self.searches[resource_type].records)

    def active_count(self, resource_type: str) -> int:
        return len(self.searches[resource_type].active)


def _concept_labels(concept: dict[str, Any] | None) -> tuple[list[str], bool]:
    """(labels, uncoded): uncoded when the concept holds nothing but placeholders."""
    if not concept:
        return [], True
    labels: list[str] = []
    real_value = False
    if concept.get("text"):
        labels.append(concept["text"])
        real_value = True
    for coding in concept.get("coding", []):
        if coding.get("system") == DATA_ABSENT_SYSTEM:
            continue
        real_value = True
        if coding.get("display"):
            labels.append(coding["display"])
    return labels, not real_value


def _narrative(resource: dict[str, Any]) -> str | None:
    div = (resource.get("text") or {}).get("div")
    if not div:
        return None
    text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", div)).split())
    return text or None


def _has_code(concept: dict[str, Any] | None, code: str) -> bool:
    return any(c.get("code") == code for c in (concept or {}).get("coding", []))


def _status_code(concept: dict[str, Any] | None) -> str | None:
    codings = (concept or {}).get("coding", [])
    return codings[0].get("code") if codings else None


def parse_record(resource_type: str, resource: dict[str, Any]) -> ChartRecord:
    if resource_type == "MedicationRequest":
        labels, uncoded = _concept_labels(resource.get("medicationCodeableConcept"))
        status = resource.get("status")
        active = status == "active"
    else:
        labels, uncoded = _concept_labels(resource.get("code"))
        status = _status_code(resource.get("clinicalStatus"))
        if resource_type == "AllergyIntolerance" and "clinicalStatus" not in resource:
            active = True  # FHIR allows omitting it; do not count such a record as resolved
        else:
            active = _has_code(resource.get("clinicalStatus"), "active")
    coded_name = bool(labels)
    narrative = _narrative(resource)
    if narrative and normalize(narrative) not in {normalize(label) for label in labels}:
        labels.append(narrative)
    return ChartRecord(
        resource_type=resource_type,
        source_id=resource["id"],
        active=active,
        status=status,
        labels=tuple(labels),
        uncoded=uncoded,
        coded_name=coded_name,
    )


@dataclass
class FhirOracle:
    http: httpx.AsyncClient
    base_url: str
    token: str
    _concurrency: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(6))

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    async def get(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        async with self._concurrency:
            return await self.http.get(f"{self.base_url}/{path}", headers=self._headers, params=params)

    async def patient_ids(self) -> list[str]:
        response = await self.get("Patient")
        response.raise_for_status()
        bundle = response.json()
        if any(link.get("relation") == "next" for link in bundle.get("link", [])):
            raise RuntimeError("Patient search is paged; the oracle does not follow paging")
        return sorted(entry["resource"]["id"] for entry in bundle.get("entry", []))

    async def search(self, resource_type: str, patient_id: str) -> Search:
        response = await self.get(resource_type, {"patient": patient_id})
        if response.status_code != 200:
            return Search(resource_type, response.status_code, None, False, ())
        bundle = response.json()
        return Search(
            resource_type=resource_type,
            status=200,
            total=bundle.get("total"),
            has_next_link=any(link.get("relation") == "next" for link in bundle.get("link", [])),
            records=tuple(parse_record(resource_type, entry["resource"]) for entry in bundle.get("entry", [])),
        )

    async def encounter_count(self, patient_id: str) -> int:
        response = await self.get("Encounter", {"patient": patient_id})
        response.raise_for_status()
        return len(response.json().get("entry", []))

    async def chart(self, patient_id: str, *, encounters: bool = False) -> PatientChart:
        searches = await asyncio.gather(*(self.search(rt, patient_id) for rt in BRIEF_TYPES))
        count = await self.encounter_count(patient_id) if encounters else None
        return PatientChart(patient_id, {s.resource_type: s for s in searches}, count)
