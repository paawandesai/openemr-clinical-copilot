"""
Async FHIR client — retrieval only. ARCHITECTURE.md §2, §3.

§2's core constraint: "The agent has no identity of its own with respect
to patient data. Every FHIR read it performs runs under the OAuth2 token
of the clinician who is using it." Every function below therefore takes a
bearer `token` as a parameter on every call. This module never
authenticates itself and never holds, caches, or manages a credential of
its own — there is no client_id/client_secret anywhere in this file, and
no function here mints or refreshes a token.

One function per resource type in ARCHITECTURE.md §3's tool-scope table.
Each calls the real FHIR endpoint, parses the response into the narrow
models in app/models/fhir_resources.py, and returns a list of those
models — never raw FHIR JSON. This wires retrieval only: no claim
drafting, no verification (the next task).

Every endpoint path, search parameter, and error shape below was
confirmed live against the deployed droplet while building this module —
see /agent/scripts/smoke_test_fhir.py for the reproducible proof.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import httpx

from app.fhir.exceptions import FhirAuthError, FhirNotFoundError, FhirServerError
from app.middleware.correlation import get_correlation_id
from app.models.fhir_resources import (
    AllergyIntolerance,
    Condition,
    Encounter,
    FhirResourceType,
    MedicationRequest,
    Observation,
)
from app.models.metrics_events import RetrievalEvent, UseCase

# Confirmed live: https://<droplet>/apis/default/fhir/<ResourceType>?patient=<id>
DEFAULT_BASE_URL = "https://201.79.25.22:9300/apis/default/fhir"
FHIR_BASE_URL = os.environ.get("FHIR_BASE_URL", DEFAULT_BASE_URL)

# findings.md F-36: the droplet serves a self-signed certificate.
# ARCHITECTURE.md §5's own citation of that finding is explicit that the
# right fix is to "pin an explicit CA, never disable verification
# globally" — no CA-pinning mechanism exists yet anywhere in this
# codebase, so this is a stated, temporary exception for the current
# self-signed deployment, not an endorsed production default. Defaults to
# verifying; must be explicitly turned off via env var.
_VERIFY_TLS = os.environ.get("FHIR_VERIFY_TLS", "true").strip().lower() not in (
    "false",
    "0",
    "no",
)

_TIMEOUT_SECONDS = 30.0


def _raise_for_status(response: httpx.Response) -> None:
    """
    Maps the real, observed response shapes to the typed exceptions in
    app/fhir/exceptions.py. Confirmed live: a forged/expired bearer token
    returns 401 with body
    {"error":"...","message":"...","code":0} — not a FHIR OperationOutcome.
    """
    if response.status_code in (401, 403):
        raise FhirAuthError(
            f"FHIR request rejected with {response.status_code}: "
            f"{response.text}"
        )
    if response.status_code == 404:
        raise FhirNotFoundError(f"FHIR resource not found: {response.text}")
    if response.status_code >= 400:
        raise FhirServerError(
            f"FHIR server returned {response.status_code}: {response.text}"
        )


async def _get_bundle(
    token: str, resource_type: str, params: dict[str, Any]
) -> dict[str, Any]:
    """
    One authenticated GET against the FHIR search endpoint for
    `resource_type`, returning the parsed Bundle JSON. `token` is supplied
    by the caller on every call (§2) — never read from configuration, an
    environment variable, or any client-held state.
    """
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(verify=_VERIFY_TLS, timeout=_TIMEOUT_SECONDS) as http:
        response = await http.get(
            f"{FHIR_BASE_URL}/{resource_type}", headers=headers, params=params
        )
    _raise_for_status(response)
    return response.json()


def _resources_from_bundle(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    return [entry["resource"] for entry in bundle.get("entry", [])]


def _first_coding(
    codeable_concept: dict[str, Any] | None, system_substring: str | None = None
) -> dict[str, Any] | None:
    """
    Returns the first coding in a FHIR CodeableConcept's `coding` array,
    optionally filtered to one whose `system` contains `system_substring`
    (e.g. "loinc.org", "rxnorm"). Confirmed live that OpenEMR's FHIR output
    sometimes places multiple code systems in one coding array (e.g. an
    AllergyIntolerance can carry an RxNorm-system coding for a finding
    rather than an actual drug), so filtering by system, not just taking
    coding[0], is what makes loinc_code/rxnorm_code extraction reliable.
    """
    if not codeable_concept:
        return None
    for coding in codeable_concept.get("coding", []):
        if system_substring is None or system_substring in (coding.get("system") or ""):
            return coding
    return None


# Confirmed live: OpenEMR uses this system to mean "no real code available"
# (e.g. AllergyIntolerance.code when the underlying record has no coded
# allergen) — a display of "Unknown" from this system is a placeholder,
# not a clinically meaningful value, so display-text extraction below
# skips it rather than surfacing "Unknown" as if it were real data.
_DATA_ABSENT_REASON_SYSTEM = "http://terminology.hl7.org/CodeSystem/data-absent-reason"


def _display_text(codeable_concept: dict[str, Any] | None, fallback: str) -> str:
    """
    CodeableConcept -> a human-readable display string. Tries, in order:
    `.text`, then the first non-placeholder coding's `.display`. Falls
    back to `fallback` rather than raising — a claim's source_id already
    identifies the resource precisely (ARCHITECTURE.md §5); this is only
    the narrated label, and a narrow FHIR client should degrade instead of
    dropping a resource on a display-string gap alone.
    """
    if not codeable_concept:
        return fallback
    text = codeable_concept.get("text")
    if text:
        return text
    for coding in codeable_concept.get("coding", []):
        if coding.get("system") == _DATA_ABSENT_REASON_SYSTEM:
            continue
        if coding.get("display"):
            return coding["display"]
    return fallback


def _emit_retrieval_event(
    resource_type: FhirResourceType, use_case: UseCase | None
) -> None:
    """
    KEY_METRICS.md Metric 5 (Retrieval Coverage per Query) is computed by
    comparing, per use case, which of a use case's required resource
    types (ARCHITECTURE.md §3) were actually queried. RetrievalEvent
    requires a real `use_case` (app/models/metrics_events.py) — when the
    caller doesn't supply one (no orchestration layer exists yet; that's
    a later task), this function deliberately does NOT emit a fabricated
    event with a guessed use_case, which would corrupt Metric 5's rollup
    with a false label. It logs that the event was skipped instead.
    """
    from app.middleware.logging import log_event  # local import: avoids a

    # module-load-order dependency between fhir/client.py and
    # middleware/logging.py for what is otherwise a leaf usage.

    if use_case is None:
        log_event(
            "retrieval_event_skipped",
            event_data={
                "resource_type": resource_type.value,
                "reason": "no use_case supplied by caller",
            },
        )
        return

    event = RetrievalEvent(
        resource_type=resource_type,
        correlation_id=get_correlation_id() or "no-request-context",
        use_case=use_case,
    )
    log_event(
        "retrieval_event",
        event_data=event.model_dump(mode="json"),
        use_case=use_case,
    )


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


async def get_conditions(
    token: str,
    patient_id: str,
    *,
    use_case: UseCase | None = None,
) -> list[Condition]:
    """
    ARCHITECTURE.md §3: Condition is required by Use Case 1 (active
    problem list) and Use Case 2 (problems added/resolved). Endpoint:
    GET {FHIR_BASE_URL}/Condition?patient=<patient_id>
    """
    bundle = await _get_bundle(token, "Condition", {"patient": patient_id})
    conditions = []
    for r in _resources_from_bundle(bundle):
        clinical_status = _first_coding(r.get("clinicalStatus"))
        conditions.append(
            Condition(
                source_id=r["id"],
                display=_display_text(r.get("code"), fallback="(unspecified problem)"),
                clinical_status=(clinical_status or {}).get("code", "unknown"),
                onset_date=_parse_datetime(
                    r.get("onsetDateTime") or r.get("recordedDate")
                ),
            )
        )
    _emit_retrieval_event(FhirResourceType.CONDITION, use_case)
    return conditions


async def get_medication_requests(
    token: str,
    patient_id: str,
    *,
    use_case: UseCase | None = None,
) -> list[MedicationRequest]:
    """
    ARCHITECTURE.md §3: MedicationRequest is required by Use Case 1
    (current medications) and Use Case 2 (started/stopped/changed).
    Endpoint: GET {FHIR_BASE_URL}/MedicationRequest?patient=<patient_id>
    """
    bundle = await _get_bundle(token, "MedicationRequest", {"patient": patient_id})
    requests = []
    for r in _resources_from_bundle(bundle):
        med = r.get("medicationCodeableConcept")
        rxnorm_coding = _first_coding(med, system_substring="rxnorm")
        requests.append(
            MedicationRequest(
                source_id=r["id"],
                display=_display_text(med, fallback="(unspecified medication)"),
                status=r.get("status", "unknown"),
                rxnorm_code=(rxnorm_coding or {}).get("code"),
                authored_date=_parse_datetime(r.get("authoredOn"))
                or datetime.min,
            )
        )
    _emit_retrieval_event(FhirResourceType.MEDICATION_REQUEST, use_case)
    return requests


async def get_allergy_intolerances(
    token: str,
    patient_id: str,
    *,
    use_case: UseCase | None = None,
) -> list[AllergyIntolerance]:
    """
    ARCHITECTURE.md §3: AllergyIntolerance is required by Use Case 1 only.
    Endpoint: GET {FHIR_BASE_URL}/AllergyIntolerance?patient=<patient_id>

    An empty list here is meaningful, not an error (USERS.md §1/F-21): it
    means "no allergy resources exist for this patient," which the caller
    must render as "not recorded," never as "no known allergies" — that
    disclosure decision belongs in the not-yet-built claim-drafting layer,
    not here, but this client must not obscure the distinction by, e.g.,
    treating an empty list as an exceptional case.
    """
    bundle = await _get_bundle(token, "AllergyIntolerance", {"patient": patient_id})
    allergies = []
    for r in _resources_from_bundle(bundle):
        allergies.append(
            AllergyIntolerance(
                source_id=r["id"],
                display=_display_text(r.get("code"), fallback="(unspecified allergen)"),
                recorded_date=_parse_datetime(r.get("recordedDate")),
            )
        )
    _emit_retrieval_event(FhirResourceType.ALLERGY_INTOLERANCE, use_case)
    return allergies


async def get_encounters(
    token: str,
    patient_id: str,
    count: int = 2,
    *,
    use_case: UseCase | None = None,
) -> list[Encounter]:
    """
    ARCHITECTURE.md §3: Encounter is required by Use Case 2 (current visit
    and most recent prior — hence `count=2` as the default) and Use Case 3
    (the visit where something was noted or tried). Endpoint:
    GET {FHIR_BASE_URL}/Encounter?patient=<patient_id>&_sort=-date&_count=<count>

    `_sort=-date` (confirmed live) is what makes `count=2` actually mean
    "current visit + most recent prior," not an arbitrary two encounters —
    without it, `_count` alone gives no ordering guarantee.
    """
    bundle = await _get_bundle(
        token,
        "Encounter",
        {"patient": patient_id, "_sort": "-date", "_count": count},
    )
    encounters = []
    for r in _resources_from_bundle(bundle):
        period = r.get("period") or {}
        start = period.get("start")
        if start is None:
            # ARCHITECTURE.md §3 uses Encounter purely as a dated anchor;
            # an encounter with no start date can't serve that purpose, so
            # it's skipped rather than modeled with a fabricated date.
            continue
        encounters.append(Encounter(source_id=r["id"], date=_parse_datetime(start)))
    _emit_retrieval_event(FhirResourceType.ENCOUNTER, use_case)
    return encounters


async def get_observations(
    token: str,
    patient_id: str,
    since: datetime | None = None,
    *,
    use_case: UseCase | None = None,
) -> list[Observation]:
    """
    ARCHITECTURE.md §3: Observation is required by Use Case 2 (new lab
    results in an interval) and Use Case 3 (a specific past value).
    Scoped to lab results only in both cases — vitals are excluded from
    tool scope entirely (§3: recorded once per patient, never per
    encounter, F-20) — so this always filters `category=laboratory`;
    there is no unscoped/"all observations" mode. `since`, when given,
    narrows to the Change Detection interval; omitted, it's unscoped in
    time (Use Case 3 may reference a value from any point in the record).

    Endpoint:
    GET {FHIR_BASE_URL}/Observation?patient=<patient_id>&category=laboratory[&date=ge<since>]

    Confirmed live: `category=laboratory` search correctly excludes vital-
    sign panel Observations (which carry no direct `valueQuantity` at all,
    only `hasMember` references to child Observations) — but this function
    still guards against an entry lacking a usable value defensively,
    rather than assuming the filter is exhaustive for every record shape.
    """
    params: dict[str, Any] = {"patient": patient_id, "category": "laboratory"}
    if since is not None:
        params["date"] = f"ge{since.strftime('%Y-%m-%d')}"

    bundle = await _get_bundle(token, "Observation", params)
    observations = []
    for r in _resources_from_bundle(bundle):
        value_quantity = r.get("valueQuantity")
        if value_quantity is None or "value" not in value_quantity:
            # No single-value result to cite (e.g. a panel container) —
            # ARCHITECTURE.md §5 explicitly defers multi-record synthesis;
            # this client only models single-value lab results.
            continue
        loinc_coding = _first_coding(r.get("code"), system_substring="loinc.org")
        if loinc_coding is None:
            # ARCHITECTURE.md §5: lab claims bind to a LOINC code; a
            # result with none can't be modeled per that contract.
            continue
        observations.append(
            Observation(
                source_id=r["id"],
                value=value_quantity["value"],
                unit=value_quantity.get("unit"),
                loinc_code=loinc_coding["code"],
                effective_date=_parse_datetime(r.get("effectiveDateTime"))
                or datetime.min,
            )
        )
    _emit_retrieval_event(FhirResourceType.OBSERVATION, use_case)
    return observations
