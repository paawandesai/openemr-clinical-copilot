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
import unicodedata
from datetime import datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote

import httpx

from app.fhir.exceptions import (
    FhirAuthError,
    FhirNotFoundError,
    FhirServerError,
    PatientNotFoundError,
)
from app.middleware.correlation import get_correlation_id
from app.middleware.telemetry import tool_call
from app.models.fhir_resources import (
    AllergyIntolerance,
    Condition,
    Encounter,
    FhirResourceType,
    MedicationRequest,
    Observation,
)
from app.models.metrics_events import RetrievalEvent, UseCase
from app.models.telemetry_events import Tool

# Confirmed live: https://<droplet>/apis/default/fhir/<ResourceType>?patient=<id>
DEFAULT_BASE_URL = "https://64.177.42.210:9300/apis/default/fhir"
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
        raise FhirAuthError(f"FHIR request rejected with {response.status_code}: {response.text}")
    if response.status_code == 404:
        raise FhirNotFoundError(f"FHIR resource not found: {response.text}")
    if response.status_code >= 400:
        raise FhirServerError(f"FHIR server returned {response.status_code}: {response.text}")


async def _get_bundle(token: str, resource_type: str, params: dict[str, Any]) -> dict[str, Any]:
    """
    One authenticated GET against the FHIR search endpoint for
    `resource_type`, returning the parsed Bundle JSON. `token` is supplied
    by the caller on every call (§2) — never read from configuration, an
    environment variable, or any client-held state.
    """
    headers = {"Authorization": f"Bearer {token}"}
    with tool_call(Tool.FHIR_READ, resource_type) as call:
        async with httpx.AsyncClient(verify=_VERIFY_TLS, timeout=_TIMEOUT_SECONDS) as http:
            response = await http.get(
                f"{FHIR_BASE_URL}/{resource_type}", headers=headers, params=params
            )
        call.status_code = response.status_code
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

# Characters that never belong in a name shown to a physician: control
# characters (other than whitespace, which is collapsed instead), format
# characters (zero-width and bidirectional overrides, which can make one
# name display as another) and lone surrogates.
_INVISIBLE_CATEGORIES = frozenset({"Cc", "Cf", "Cs"})

# The elements a FHIR narrative may contain: basic HTML formatting, links
# and images only (FHIR R4 Narrative, constraint txt-1). Any other element
# — script, style, iframe, object, form, svg, a misspelled or unknown tag —
# means the narrative is not plain formatted text, and no name is taken
# from it.
_NARRATIVE_ELEMENTS = frozenset(
    {
        "a",
        "abbr",
        "acronym",
        "address",
        "area",
        "b",
        "bdo",
        "big",
        "blockquote",
        "br",
        "caption",
        "cite",
        "code",
        "col",
        "colgroup",
        "dd",
        "dfn",
        "div",
        "dl",
        "dt",
        "em",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "hr",
        "i",
        "img",
        "kbd",
        "li",
        "map",
        "ol",
        "p",
        "pre",
        "q",
        "samp",
        "small",
        "span",
        "strong",
        "sub",
        "sup",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
        "tt",
        "ul",
        "var",
    }
)

# Elements whose boundary separates words when rendered. Inline formatting
# (<b>, <span>) does not: "<b>peni</b>cillin" reads "penicillin".
_NARRATIVE_WORD_BREAKS = frozenset(
    {
        "address",
        "blockquote",
        "br",
        "caption",
        "dd",
        "div",
        "dl",
        "dt",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "hr",
        "li",
        "ol",
        "p",
        "pre",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
        "ul",
    }
)

# OpenEMR's narrative is `<div xmlns=…>` + the record's title
# (UtilsService::createNarrative), and `lists.title` is varchar(255)
# (sql/database.sql). A longer text is not a record title, and a much
# longer div is not worth parsing to find out.
_NARRATIVE_NAME_MAX_CHARS = 255
_NARRATIVE_DIV_MAX_CHARS = 4096


def _plain_text(value: str) -> str:
    """One line of visible text: invisible characters dropped, whitespace
    (including newlines and Unicode line separators) collapsed to single
    spaces, so a name can never start a new line of the rendered brief."""
    visible = "".join(
        char
        for char in value
        if char.isspace() or unicodedata.category(char) not in _INVISIBLE_CATEGORIES
    )
    return " ".join(visible.split())


def _display_text(codeable_concept: dict[str, Any] | None) -> str | None:
    """
    CodeableConcept -> a human-readable display string, or None. Tries, in
    order: `.text`, then the first non-placeholder coding's `.display`.
    None means the concept names nothing; the caller decides what else to
    try (_record_name). Never a placeholder string (F-57).
    """
    if not codeable_concept:
        return None
    text = codeable_concept.get("text")
    if isinstance(text, str) and (name := _plain_text(text)):
        return name
    for coding in codeable_concept.get("coding", []):
        if coding.get("system") == _DATA_ABSENT_REASON_SYSTEM:
            continue
        display = coding.get("display")
        if isinstance(display, str) and (name := _plain_text(display)):
            return name
    return None


class _NarrativeText(HTMLParser):
    """
    The text nodes of a FHIR narrative `div`, via the standard library's
    HTML tokenizer. Only character data is collected. Tag names are checked
    against _NARRATIVE_ELEMENTS, and attributes are never read, so no
    attribute value (event handler, URL, style) can reach the output.
    Comments are dropped. Anything the tokenizer does not report as an
    element or text — CDATA sections, processing instructions, doctype
    declarations — marks the narrative unusable rather than being guessed
    around.
    """

    def __init__(self) -> None:
        # convert_charrefs: entity and character references in text arrive
        # decoded, so "&amp;" is "&", and "&lt;b&gt;" is the text "<b>" —
        # which _narrative_name then refuses.
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.usable = True

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._element(tag)

    def handle_endtag(self, tag: str) -> None:
        self._element(tag)

    def _element(self, tag: str) -> None:
        if tag not in _NARRATIVE_ELEMENTS:
            self.usable = False
        elif tag in _NARRATIVE_WORD_BREAKS:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_decl(self, decl: str) -> None:
        self.usable = False

    def unknown_decl(self, data: str) -> None:
        self.usable = False

    def handle_pi(self, data: str) -> None:
        self.usable = False


def _narrative_name(resource: dict[str, Any]) -> str | None:
    """
    The resource narrative (`text.div`) as a name, or None when it cannot
    safely serve as one. OpenEMR puts an uncoded allergy's title only here
    (FhirAllergyIntoleranceService.php:217-221, F-57), and puts it there
    unescaped (UtilsService::createNarrative concatenates the title into
    the div), so the div is untrusted HTML from the chart.

    It is parsed, never pattern-matched: _NarrativeText tokenizes it and
    keeps text nodes only. The result is then refused, not repaired, if:
      - the narrative holds any element outside FHIR's formatting set, a
        CDATA section, a processing instruction or a doctype;
      - the extracted text contains "<" or ">". Text can only carry them
        from escaped markup ("&lt;script&gt;") or a stray bracket the
        tokenizer passed through as text; either way it is not a clean
        name, and refusing it means the returned string cannot form a tag
        in any HTML context downstream, however it is inserted there;
      - it is empty after _plain_text, or longer than a record title.
    Refused narratives yield None: the record renders as visibly unnamed
    rather than under a name this function had to guess at.
    """
    narrative = resource.get("text")
    if not isinstance(narrative, dict) or narrative.get("status") == "empty":
        return None
    div = narrative.get("div")
    if not isinstance(div, str) or len(div) > _NARRATIVE_DIV_MAX_CHARS:
        return None
    parser = _NarrativeText()
    parser.feed(div)
    parser.close()
    if not parser.usable:
        return None
    name = _plain_text("".join(parser.parts))
    if not name or "<" in name or ">" in name or len(name) > _NARRATIVE_NAME_MAX_CHARS:
        return None
    return name


def _record_name(resource: dict[str, Any], codeable_concept: dict[str, Any] | None) -> str | None:
    """
    A record's name for the brief: its coded display if it has one, else
    its narrative (F-57), else None. None is the record's honest state —
    the chart names nothing usable — and is never replaced with a
    placeholder string, because a placeholder is a value the model copies
    and the verifier then confirms against this same parse.
    """
    return _display_text(codeable_concept) or _narrative_name(resource)


def _emit_retrieval_event(
    resource_type: FhirResourceType,
    use_case: UseCase | None,
    patient_session_key: str | None,
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

    `patient_session_key` is passed through to the log line as given.
    KEY_METRICS.md rolls Metric 5 up per (user, patient, session)
    (ARCHITECTURE.md §4); None is only for standalone callers with no
    session context, such as scripts/smoke_test_fhir.py.
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
            patient_session_key=patient_session_key,
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
        patient_session_key=patient_session_key,
    )


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


async def check_capability_statement(timeout_seconds: float = 5.0) -> None:
    """
    Unauthenticated reachability probe for /ready: GET {FHIR_BASE_URL}/metadata.
    Confirmed live: OpenEMR serves its CapabilityStatement (FHIR 4.0.1) there
    without a token. This proves the FHIR API is up and answering; it cannot
    prove any clinician's token is valid, since the agent holds none (§2).
    Raises FhirServerError on a wrong response; httpx.TransportError on no
    response at all.
    """
    async with httpx.AsyncClient(verify=_VERIFY_TLS, timeout=timeout_seconds) as http:
        response = await http.get(f"{FHIR_BASE_URL}/metadata")
    _raise_for_status(response)
    try:
        resource_type = response.json().get("resourceType")
    except ValueError as exc:
        raise FhirServerError("FHIR metadata response was not JSON") from exc
    if resource_type != "CapabilityStatement":
        raise FhirServerError("FHIR metadata response was not a CapabilityStatement")


async def ensure_patient_exists(token: str, patient_id: str) -> None:
    """
    ARCHITECTURE.md §3's implicit Patient read — resolving that the chart
    exists — made explicit, because the searches below cannot do it.
    Confirmed live: Condition/MedicationRequest/AllergyIntolerance searches
    for an unknown or malformed patient id all return HTTP 200 with an
    empty Bundle, exactly like a real patient with nothing recorded. Without
    this read, a mistyped id renders as "Allergies: not recorded".
    GET Patient/<id> does tell them apart: 200 exists, 404 unknown id, 400
    malformed id. The response body — demographics — is discarded unread.
    Emits no retrieval_event: Patient is a precondition, not a tool-scope
    resource (§3; app/models/fhir_resources.py).
    """
    headers = {"Authorization": f"Bearer {token}"}
    with tool_call(Tool.FHIR_READ, "Patient") as call:
        async with httpx.AsyncClient(verify=_VERIFY_TLS, timeout=_TIMEOUT_SECONDS) as http:
            response = await http.get(
                f"{FHIR_BASE_URL}/Patient/{quote(patient_id, safe='')}", headers=headers
            )
        call.status_code = response.status_code
        if response.status_code in (400, 404):
            raise PatientNotFoundError(
                f"FHIR Patient read returned {response.status_code} for the requested id"
            )
        _raise_for_status(response)


async def get_patient_identity(token: str, patient_id: str) -> dict[str, Any]:
    """
    The chart's own name and birth date, for one comparison only: whether
    an uploaded document is about this patient (the intake-extractor's
    wrong-patient flag, app/workers/intake_extractor/identity.py).

    Same read as ensure_patient_exists, but the body is kept — narrowed to
    `{"given": [str], "family": str | None, "birth_date": str | None}` from
    the first `name` entry marked `official` (else the first entry) and
    `birthDate`. Nothing else in the Patient resource is returned. Returns
    no FHIR JSON, logs nothing, and emits no retrieval_event: Patient is a
    precondition, not a tool-scope resource (§3). Callers must not log what
    it returns (CLAUDE.md invariant 1).
    """
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(verify=_VERIFY_TLS, timeout=_TIMEOUT_SECONDS) as http:
        response = await http.get(
            f"{FHIR_BASE_URL}/Patient/{quote(patient_id, safe='')}", headers=headers
        )
    if response.status_code in (400, 404):
        raise PatientNotFoundError(
            f"FHIR Patient read returned {response.status_code} for the requested id"
        )
    _raise_for_status(response)
    resource = response.json()
    names = resource.get("name") or []
    chosen = next((n for n in names if n.get("use") == "official"), names[0] if names else {})
    return {
        "given": [str(part) for part in chosen.get("given") or []],
        "family": chosen.get("family"),
        "birth_date": resource.get("birthDate"),
    }


async def get_conditions(
    token: str,
    patient_id: str,
    *,
    use_case: UseCase | None = None,
    patient_session_key: str | None = None,
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
                display=_record_name(r, r.get("code")),
                clinical_status=(clinical_status or {}).get("code", "unknown"),
                onset_date=_parse_datetime(r.get("onsetDateTime") or r.get("recordedDate")),
            )
        )
    _emit_retrieval_event(FhirResourceType.CONDITION, use_case, patient_session_key)
    return conditions


async def get_medication_requests(
    token: str,
    patient_id: str,
    *,
    use_case: UseCase | None = None,
    patient_session_key: str | None = None,
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
                display=_record_name(r, med),
                status=r.get("status", "unknown"),
                rxnorm_code=(rxnorm_coding or {}).get("code"),
                authored_date=_parse_datetime(r.get("authoredOn")) or datetime.min,
            )
        )
    _emit_retrieval_event(FhirResourceType.MEDICATION_REQUEST, use_case, patient_session_key)
    return requests


async def get_allergy_intolerances(
    token: str,
    patient_id: str,
    *,
    use_case: UseCase | None = None,
    patient_session_key: str | None = None,
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
        clinical_status = _first_coding(r.get("clinicalStatus"))
        allergies.append(
            AllergyIntolerance(
                source_id=r["id"],
                display=_record_name(r, r.get("code")),
                clinical_status=(clinical_status or {}).get("code", "unknown"),
                recorded_date=_parse_datetime(r.get("recordedDate")),
            )
        )
    _emit_retrieval_event(FhirResourceType.ALLERGY_INTOLERANCE, use_case, patient_session_key)
    return allergies


async def get_encounters(
    token: str,
    patient_id: str,
    count: int = 2,
    *,
    use_case: UseCase | None = None,
    patient_session_key: str | None = None,
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
    _emit_retrieval_event(FhirResourceType.ENCOUNTER, use_case, patient_session_key)
    return encounters


async def get_observations(
    token: str,
    patient_id: str,
    since: datetime | None = None,
    *,
    use_case: UseCase | None = None,
    patient_session_key: str | None = None,
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
                effective_date=_parse_datetime(r.get("effectiveDateTime")) or datetime.min,
            )
        )
    _emit_retrieval_event(FhirResourceType.OBSERVATION, use_case, patient_session_key)
    return observations
