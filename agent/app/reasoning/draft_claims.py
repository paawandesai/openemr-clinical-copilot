"""
Claim drafting for the Pre-Visit Brief — USERS.md §3 Use Case 1,
ARCHITECTURE.md §5.

ARCHITECTURE.md §5: "The reasoning model never renders prose claims
directly. It drafts structured claims, each bound to source_id + field +
value." This module is that drafting step for Use Case 1 only: one LLM
call that turns already-retrieved Condition / MedicationRequest /
AllergyIntolerance records into a list of Claim objects. It does no
retrieval (app/fhir/client.py), no verification, and no rendering. Change
Detection and Follow-Up Drilling are deliberately not handled here.

The output shape is forced with structured outputs
(`output_config.format` with a JSON schema), not parsed out of free text.

What the model is given:

- Records narrowed to what Use Case 1 covers — conditions with
  clinical_status "active", medication requests with status "active", and
  allergies the chart has not marked resolved or inactive (F-58; the
  asymmetry is explained at _ENDED_ALLERGY_STATUSES). That selection is a
  fixed rule, so it runs here in code rather than being left to the model.
- Each record as its `model_dump(mode="json")`, so a claim's `value` is a
  verbatim copy of that serialization.
- Only records the chart names (drafted_records). A record with no name
  (display None: no code text, coding display or usable narrative, F-57)
  is withheld, so there is no placeholder for the model to copy. The
  pipeline states such a record itself (unnamed_record_claims), and the
  verifier never passes it, so it renders as UNVERIFIED.

Empty sections are intentionally NOT this module's job. The model only
drafts claims about records that exist and is never asked to say that a
section is empty. Whether a section came back empty is a fact known from
retrieval alone, so it is counted here with len() before the model call
and returned alongside the claims as `section_counts`. The rendering step
(not built yet) owns turning those counts into the "not recorded" / "none
active" wording USERS.md §3 Use Case 1 requires (F-21), in plain code: no
LLM call, and nothing for verification to check.

Load testing (agent/load-test/README.md): AGENT_STUB_LLM_DRAFTING=true
short-circuits the one Anthropic call this module makes, so an infra load
test measures the agent's own behavior at concurrency — connection
handling, FHIR round-trips, queue depth — without paying for or being
confounded by the LLM's latency. Off by default; must never be true on a
deployment serving real briefs, since a stubbed claim is a verbatim copy of
the source record, not a model's output (see _stub_draft).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from functools import cache
from typing import Any

import anthropic
from anthropic.types.beta import BetaMessage
from pydantic import BaseModel

from app.middleware.correlation import get_correlation_id
from app.middleware.logging import log_event
from app.middleware.telemetry import anthropic_http_client, tool_call
from app.models.claims import Claim
from app.models.fhir_resources import (
    AllergyIntolerance,
    Condition,
    FhirResourceType,
    MedicationRequest,
)
from app.models.metrics_events import UseCase
from app.models.telemetry_events import Tool
from app.reasoning import provider_health

MODEL = "claude-opus-5"

# KEY_METRICS.md Metric 4 budgets the whole brief inside a ~90-second
# window, and this call copies values out of records rather than reasoning
# over them, so it runs below the API's default `high` effort.
EFFORT = "medium"

# Thinking is off, set explicitly on every drafting call rather than left
# to the model's default, which on claude-opus-5 is adaptive (D62,
# answer-step thinking). Each call copies values out of records into a
# schema, and every claim is then checked by the deterministic verifier
# (app/verification/), so thinking buys latency, not safety. Adaptive
# thinking's depth is decided per request on the provider's side, and on
# 2026-09-25 it grew the answer step's output ~15-fold with no code change.
# The API accepts "disabled" only at effort "high" or below.
BRIEF_THINKING: dict[str, str] = {"type": "disabled"}

# Streamed, so a large cap doesn't risk an HTTP timeout. Thinking and the
# claims share this cap.
_MAX_TOKENS = 64000

# Server-side refusal fallback: if Opus 5's safety classifiers decline the
# request, the API re-runs it on Anthropic's recommended fallback model
# inside the same call. The model that actually served it is logged.
_FALLBACK_BETA = "server-side-fallback-2026-07-01"

# See the module docstring's "Load testing" note. Same true/false parsing
# as FHIR_VERIFY_TLS (app/fhir/client.py): defaults to off.
_STUB_LLM_DRAFTING = os.environ.get("AGENT_STUB_LLM_DRAFTING", "false").strip().lower() not in (
    "false",
    "0",
    "",
    "no",
)

# ARCHITECTURE.md §3: the resource types Use Case 1 requires, and the
# narrow model each retrieved list must hold.
_BRIEF_RESOURCE_MODELS: dict[FhirResourceType, type[BaseModel]] = {
    FhirResourceType.CONDITION: Condition,
    FhirResourceType.MEDICATION_REQUEST: MedicationRequest,
    FhirResourceType.ALLERGY_INTOLERANCE: AllergyIntolerance,
}

# USERS.md §3 Use Case 1: "active problem list", "current medications".
_ACTIVE_STATUS: dict[FhirResourceType, tuple[str, str]] = {
    FhirResourceType.CONDITION: ("clinical_status", "active"),
    FhirResourceType.MEDICATION_REQUEST: ("status", "active"),
}

# Allergies are filtered the other way round (findings.md F-58, F-21).
# OpenEMR marks an allergy resolved or inactive once the record carries an
# end date (FhirAllergyIntoleranceService.php:117-121), and such a record
# must not reach the physician as a current allergy — that is F-58.
#
# But the test is "explicitly ended", not "explicitly active", which is the
# opposite default from conditions and medications above, and deliberately
# so. An allergy whose status the chart does not state must still be shown:
# F-21's whole point is that allergy data the record does not rule out is
# never safe to treat as absent. Filtering on == "active" would silently
# drop an allergy whose clinicalStatus OpenEMR omitted — a worse failure
# than the one F-58 describes, and the exact silent-drop shape F-57 and the
# H cases guard against. A wrongly-shown allergy is visible and checkable;
# a wrongly-hidden one is neither.
_ENDED_ALLERGY_STATUSES = frozenset({"resolved", "inactive"})


def brief_records(resource_type: FhirResourceType, records: list[Any]) -> list[Any]:
    """The subset of retrieved records the Pre-Visit Brief reports on."""
    if resource_type is FhirResourceType.ALLERGY_INTOLERANCE:
        return [
            record for record in records if record.clinical_status not in _ENDED_ALLERGY_STATUSES
        ]
    if resource_type not in _ACTIVE_STATUS:
        return records
    field, active_value = _ACTIVE_STATUS[resource_type]
    return [record for record in records if getattr(record, field) == active_value]


def drafted_records(resource_type: FhirResourceType, records: list[Any]) -> list[Any]:
    """
    The brief records the model is given to draft claims for: every one
    the chart names. A record with no name (display None, F-57) holds
    nothing the model could copy, so it is withheld; unnamed_record_claims
    states it instead.
    """
    return [
        record for record in brief_records(resource_type, records) if record.display is not None
    ]


def unnamed_record_claims(
    retrieved: dict[FhirResourceType, list[Any]],
) -> list[Claim]:
    """
    One claim per brief record the chart names nothing for: field
    "display", value None, which is exactly what the chart holds. Written
    in code, not by the model, so the record is in the brief whether or not
    the model sees it. The verifier never passes a display claim against a
    record with no name (app/verification/checker.py), so each one renders
    as UNVERIFIED and counts as unverified: an item the physician must
    check in the chart, never a verified value (F-57).
    """
    return [
        Claim(
            source_id=record.source_id,
            field="display",
            value=None,
            resource_type=resource_type,
        )
        for resource_type in _BRIEF_RESOURCE_MODELS
        for record in brief_records(resource_type, retrieved.get(resource_type, []))
        if record.display is None
    ]


@dataclass(frozen=True)
class SectionCount:
    """
    One brief section's record counts, taken with len() from `retrieved`
    before the model call. For the rendering step: `retrieved == 0` means
    "not recorded"; `retrieved > 0` with `in_brief == 0` means "none
    active". For AllergyIntolerance the two are always equal.
    """

    retrieved: int
    in_brief: int


@dataclass(frozen=True)
class PreVisitBriefDraft:
    """
    `claims`: exactly as the model drafted them, unverified.
    `section_counts`: one entry for each of Condition, MedicationRequest,
    and AllergyIntolerance, including sections with no records.
    """

    claims: list[Claim]
    section_counts: dict[FhirResourceType, SectionCount]


class ClaimDraftingError(Exception):
    """The model call completed but did not produce a usable claim list."""


class DraftTruncatedError(ClaimDraftingError):
    """
    The output hit its `max_tokens` ceiling, so the structured output is
    incomplete. A ClaimDraftingError, so every Week 1 caller is unchanged;
    the Week 2 answer step catches it first (D50). `final_message` is the
    truncated message, kept for its usage counts only — never parsed.
    """

    def __init__(self, message: str, final_message: object) -> None:
        super().__init__(message)
        self.final_message = final_message


SYSTEM_PROMPT = """\
You draft the Pre-Visit Brief for a primary care physician using a clinical co-pilot. The physician has about 90 seconds between finishing one encounter and opening the next patient's chart. The brief answers one question: "What do I need to know before I see this patient?" It covers exactly three sections: active problems (Condition), current medications (MedicationRequest), and allergies (AllergyIntolerance).

You output structured claims only, never prose. Each claim names one source_id from the provided data, one field on that source, and the value that source holds in that field. Nothing you write is shown to the physician as written: code checks every claim against the retrieved records before rendering it, and a claim that does not match a provided source exactly is shown to the physician flagged as unverified.

## Source constraint

The FHIR data in the user message is the only source of information you may use for this task. Treat it as your only medical knowledge: for this task you know nothing about this patient, or about medicine, beyond what that data states.

- Cite only source_ids that appear in the provided data, character for character. Never invent, guess, complete, or alter a source_id.
- Copy each value exactly as it appears in the provided data: same spelling, capitalization, punctuation, and abbreviations, even when it looks misspelled or unclear. Do not paraphrase, expand, normalize, or correct it.
- Add nothing the data does not state: no inferred diagnoses, drug classes, indications, doses, interactions, contraindications, severity, or clinical interpretation.
- If a fact is not in the provided data, it does not appear in any claim.

## What to include

The data has already been narrowed to what the brief covers: active conditions, active medication requests, and every allergy record. For every record in every section, emit one claim with field "display". Emit sections in the order Condition, MedicationRequest, AllergyIntolerance, and keep records in the order given. A section whose records list is empty gets no claims; empty sections are reported to the physician separately, not by you.
"""

_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "resource_type": {
                        "type": "string",
                        "enum": [rt.value for rt in _BRIEF_RESOURCE_MODELS],
                    },
                    "source_id": {"type": "string"},
                    "field": {"type": "string", "enum": ["display"]},
                    "value": {"type": "string"},
                },
                "required": ["resource_type", "source_id", "field", "value"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["claims"],
    "additionalProperties": False,
}


@cache
def _client() -> anthropic.AsyncAnthropic:
    # Built on first use rather than at import, so importing this module
    # doesn't require Anthropic credentials to be configured.
    # The http client counts the SDK's own silent retries into the open
    # tool call (app/middleware/telemetry.py); it reads one header, never a body.
    return anthropic.AsyncAnthropic(http_client=anthropic_http_client())


def _serialize_retrieved(retrieved: dict[FhirResourceType, list[Any]]) -> str:
    """
    Parses the caller's retrieval result into the JSON the model sees.
    Raises rather than guessing when a required resource type is missing:
    a type that was never queried must not be counted as having zero
    records.
    """
    sections: dict[str, dict[str, Any]] = {}
    for resource_type, model in _BRIEF_RESOURCE_MODELS.items():
        if resource_type not in retrieved:
            raise ValueError(
                f"Pre-Visit Brief requires {resource_type.value} retrieval; "
                "it is missing from `retrieved`, which is not the same as "
                "the query returning no records."
            )
        records = retrieved[resource_type]
        for record in records:
            if not isinstance(record, model):
                raise TypeError(
                    f"retrieved[{resource_type.value}] must hold "
                    f"{model.__name__} instances, got {type(record).__name__}"
                )
        sections[resource_type.value] = {
            "records": [
                record.model_dump(mode="json") for record in drafted_records(resource_type, records)
            ]
        }
    return json.dumps(sections, indent=2)


def build_user_content(retrieved: dict[FhirResourceType, list[Any]]) -> str:
    """The user message. Pinned by W2-L1 (evals/w2/gate/prompt_pins.py)."""
    return (
        "<retrieved_fhir_data>\n"
        f"{_serialize_retrieved(retrieved)}\n"
        "</retrieved_fhir_data>\n\n"
        "Draft the Pre-Visit Brief claims for this patient."
    )


def _final_text(message: BetaMessage) -> str:
    """
    The structured-output JSON text. After a mid-stream refusal fallback,
    the declined model's partial text stays in `content` ahead of a
    `fallback` block, so only text after the last such block is the answer.
    """
    text_blocks: list[str] = []
    for block in message.content:
        if block.type == "fallback":
            text_blocks = []
        elif block.type == "text":
            text_blocks.append(block.text)
    if not text_blocks:
        raise ClaimDraftingError("Model response contained no text block")
    return text_blocks[-1]


def _stub_draft(
    retrieved: dict[FhirResourceType, list[Any]],
    *,
    section_counts: dict[FhirResourceType, SectionCount],
    correlation_id: str,
    patient_session_key: str | None,
) -> PreVisitBriefDraft:
    """
    AGENT_STUB_LLM_DRAFTING=true path — load testing only (module
    docstring). No Anthropic call: drafts one claim per record the model
    would be given (drafted_records), field "display", value copied
    verbatim from that record. That is exactly the claim shape
    SYSTEM_PROMPT asks the real model for on the golden path, so every
    claim verifies, but with no LLM latency or cost.

    Logs claims_drafted exactly as the real path does (section_counts is
    unaffected by stubbing), but logs llm_call_stubbed in place of
    llm_usage_event, so compute_metrics.py's cost metric (6) can never
    mistake a stubbed run's zero-cost claims for a real, billed call.
    """
    claims = [
        Claim(
            source_id=record.source_id,
            field="display",
            value=record.display,
            resource_type=resource_type,
        )
        for resource_type in _BRIEF_RESOURCE_MODELS
        for record in drafted_records(resource_type, retrieved[resource_type])
    ]
    log_event(
        "llm_call_stubbed",
        event_data={
            "correlation_id": correlation_id,
            "requested_model": MODEL,
            "claim_count": len(claims),
        },
        use_case=UseCase.PRE_VISIT_BRIEF,
        patient_session_key=patient_session_key,
    )
    log_event(
        "claims_drafted",
        event_data={
            "correlation_id": correlation_id,
            "claim_count": len(claims),
            "section_counts": {
                resource_type.value: asdict(count)
                for resource_type, count in section_counts.items()
            },
        },
        use_case=UseCase.PRE_VISIT_BRIEF,
        patient_session_key=patient_session_key,
    )
    return PreVisitBriefDraft(claims=claims, section_counts=section_counts)


async def draft_pre_visit_brief(
    retrieved: dict[FhirResourceType, list[Any]],
    *,
    patient_session_key: str | None = None,
) -> PreVisitBriefDraft:
    """
    Drafts Pre-Visit Brief claims from one patient's retrieved Condition,
    MedicationRequest, and AllergyIntolerance lists. Other resource types
    in `retrieved` are ignored; Use Case 1 doesn't use them (§3).

    Claims come back unverified. A claim citing a source_id that wasn't
    provided is returned too, not dropped here, so the verification step
    can report it (§5). `section_counts` is computed from `retrieved` in
    code, not by the model; see the module docstring.

    `patient_session_key` goes on every event this call logs; Metric 6
    groups cost per (user, patient, session) by it (ARCHITECTURE.md §4).
    None is only for standalone callers with no session context, such as
    scripts/smoke_test_claims.py; app/graph/pre_visit_brief.py always
    passes one.

    AGENT_STUB_LLM_DRAFTING=true (module docstring, "Load testing")
    short-circuits to _stub_draft before any of the above is built, other
    than the validation `_serialize_retrieved` already performs — kept so
    a caller missing a required resource type fails identically whether
    stubbed or not.
    """
    user_content = build_user_content(retrieved)
    section_counts = {
        resource_type: SectionCount(
            retrieved=len(retrieved[resource_type]),
            in_brief=len(brief_records(resource_type, retrieved[resource_type])),
        )
        for resource_type in _BRIEF_RESOURCE_MODELS
    }
    correlation_id = get_correlation_id() or "no-request-context"

    if _STUB_LLM_DRAFTING:
        return _stub_draft(
            retrieved,
            section_counts=section_counts,
            correlation_id=correlation_id,
            patient_session_key=patient_session_key,
        )

    started = time.perf_counter()
    try:
        with tool_call(
            Tool.LLM,
            MODEL,
            use_case=UseCase.PRE_VISIT_BRIEF,
            patient_session_key=patient_session_key,
        ):
            async with _client().beta.messages.stream(
                model=MODEL,
                max_tokens=_MAX_TOKENS,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_content}],
                thinking=BRIEF_THINKING,
                output_config={
                    "effort": EFFORT,
                    "format": {"type": "json_schema", "schema": _OUTPUT_SCHEMA},
                },
                betas=[_FALLBACK_BETA],
                fallbacks="default",
            ) as stream:
                message = await stream.get_final_message()
                request_id = stream.request_id
    except anthropic.APIError as exc:
        # Logged so a failed call still counts toward Metrics 4 and 6, and
        # recorded so /ready can see a provider outage a tokenless probe
        # cannot (F-60). Then re-raised: the caller decides how to recover.
        provider_health.record_failure()
        log_event(
            "llm_call_failed",
            event_data={
                "correlation_id": correlation_id,
                "requested_model": MODEL,
                "latency_ms": round((time.perf_counter() - started) * 1000),
                "error_type": type(exc).__name__,
                "consecutive_failures": provider_health.consecutive_failures(),
            },
            use_case=UseCase.PRE_VISIT_BRIEF,
            patient_session_key=patient_session_key,
        )
        raise
    provider_health.record_success()
    latency_ms = round((time.perf_counter() - started) * 1000)

    usage = message.usage
    fallback_ran = any(iteration.type == "fallback_message" for iteration in usage.iterations or [])
    # KEY_METRICS.md Metric 6 (cost per interaction) is computed from the
    # token counts and served model. latency_ms is this call's share of
    # Metric 4's trigger-to-render interval, not the whole interval.
    # `model` is the field the shared llm_usage_event schema reads (the
    # observability branch's Langfuse exporter and compute_metrics.py).
    log_event(
        "llm_usage_event",
        event_data={
            "correlation_id": correlation_id,
            "model": message.model,
            "requested_model": MODEL,
            "served_model": message.model,
            "fallback_ran": fallback_ran,
            "effort": EFFORT,
            "thinking": BRIEF_THINKING["type"],
            "request_id": request_id,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_creation_input_tokens": usage.cache_creation_input_tokens,
            "cache_read_input_tokens": usage.cache_read_input_tokens,
            "latency_ms": latency_ms,
            "stop_reason": message.stop_reason,
        },
        use_case=UseCase.PRE_VISIT_BRIEF,
        patient_session_key=patient_session_key,
    )

    if message.stop_reason == "refusal":
        category = message.stop_details.category if message.stop_details else None
        raise ClaimDraftingError(f"Model declined to draft claims (refusal category: {category})")
    if message.stop_reason == "max_tokens":
        raise ClaimDraftingError(f"Claim output truncated at max_tokens={_MAX_TOKENS}")

    try:
        payload = json.loads(_final_text(message))
        claims = [
            Claim(
                source_id=item["source_id"],
                field=item["field"],
                value=item["value"],
                resource_type=FhirResourceType(item["resource_type"]),
            )
            for item in payload["claims"]
        ]
    except (ValueError, KeyError, TypeError) as exc:
        raise ClaimDraftingError("Model output did not match the claim schema") from exc

    log_event(
        "claims_drafted",
        event_data={
            "correlation_id": correlation_id,
            "claim_count": len(claims),
            "section_counts": {
                resource_type.value: asdict(count)
                for resource_type, count in section_counts.items()
            },
        },
        use_case=UseCase.PRE_VISIT_BRIEF,
        patient_session_key=patient_session_key,
    )
    return PreVisitBriefDraft(claims=claims, section_counts=section_counts)
