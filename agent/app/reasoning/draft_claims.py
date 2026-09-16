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
  every allergy record. That selection is a fixed rule, so it runs here
  in code rather than being left to the model.
- Each record as its `model_dump(mode="json")`, so a claim's `value` is a
  verbatim copy of that serialization.
- For a section left with no records, a query-level source to cite
  instead: source_id `query:<ResourceType>`, field `documentation_status`,
  value `not recorded` (the query returned nothing — USERS.md §3 Use Case
  1, F-21) or `none active` (records exist, none active). A section with
  nothing to report is therefore stated as a claim, never omitted
  (KEY_METRICS.md Metric 2). A verifier checks one of these claims by
  recomputing `documentation_status()` from the retrieved list, not by
  looking for a record with that id.
"""

from __future__ import annotations

import json
import time
from functools import cache
from typing import Any

import anthropic
from anthropic.types.beta import BetaMessage
from pydantic import BaseModel

from app.middleware.correlation import get_correlation_id
from app.middleware.logging import log_event
from app.models.claims import Claim
from app.models.fhir_resources import (
    AllergyIntolerance,
    Condition,
    FhirResourceType,
    MedicationRequest,
)
from app.models.metrics_events import UseCase

MODEL = "claude-opus-5"

# KEY_METRICS.md Metric 4 budgets the whole brief inside a ~90-second
# window, and this call copies values out of records rather than reasoning
# over them, so it runs below the API's default `high` effort.
EFFORT = "medium"

# Streamed, so a large cap doesn't risk an HTTP timeout. Thinking and the
# claims share this cap.
_MAX_TOKENS = 64000

# Server-side refusal fallback: if Opus 5's safety classifiers decline the
# request, the API re-runs it on Anthropic's recommended fallback model
# inside the same call. The model that actually served it is logged.
_FALLBACK_BETA = "server-side-fallback-2026-07-01"

# ARCHITECTURE.md §3: the resource types Use Case 1 requires, and the
# narrow model each retrieved list must hold.
_BRIEF_RESOURCE_MODELS: dict[FhirResourceType, type[BaseModel]] = {
    FhirResourceType.CONDITION: Condition,
    FhirResourceType.MEDICATION_REQUEST: MedicationRequest,
    FhirResourceType.ALLERGY_INTOLERANCE: AllergyIntolerance,
}

# USERS.md §3 Use Case 1: "active problem list", "current medications".
# AllergyIntolerance has no status field, so every allergy record counts.
_ACTIVE_STATUS: dict[FhirResourceType, tuple[str, str]] = {
    FhirResourceType.CONDITION: ("clinical_status", "active"),
    FhirResourceType.MEDICATION_REQUEST: ("status", "active"),
}

DOCUMENTATION_STATUS_FIELD = "documentation_status"
NOT_RECORDED = "not recorded"
NONE_ACTIVE = "none active"


def query_source_id(resource_type: FhirResourceType) -> str:
    """The source_id a claim cites for a section's query-level status."""
    return f"query:{resource_type.value}"


def brief_records(
    resource_type: FhirResourceType, records: list[Any]
) -> list[Any]:
    """The subset of retrieved records the Pre-Visit Brief reports on."""
    if resource_type not in _ACTIVE_STATUS:
        return records
    field, active_value = _ACTIVE_STATUS[resource_type]
    return [record for record in records if getattr(record, field) == active_value]


def documentation_status(
    resource_type: FhirResourceType, records: list[Any]
) -> str | None:
    """
    `not recorded` when the query returned nothing, `none active` when it
    returned records but none the brief reports on, otherwise None (the
    section has records, so there is no query-level claim to make).
    """
    if not records:
        return NOT_RECORDED
    if not brief_records(resource_type, records):
        return NONE_ACTIVE
    return None


class ClaimDraftingError(Exception):
    """The model call completed but did not produce a usable claim list."""


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

The data has already been narrowed to what the brief covers: active conditions, active medication requests, and every allergy record. For every record in every section, emit one claim with field "display". Emit sections in the order Condition, MedicationRequest, AllergyIntolerance, and keep records in the order given.

## Sections with nothing to report are stated, never omitted

When a section's "records" list is empty, that section includes a "no_records_claim" object with a source_id, field, and value. Emit it as a claim exactly as given. Its value says why the section is empty: "not recorded" means the query returned no records for this patient; "none active" means records exist but none is active.

This matters most for allergies. A brief that says nothing about allergies can be misread by the physician as "no known allergies". "not recorded" means no allergy has been documented in this chart. It does not mean the patient has no allergies, so for an empty allergy section the provided no_records_claim is the only allergy claim you make, and it never says "no known allergies", "NKA", "NKDA", or "none".
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
                    "field": {
                        "type": "string",
                        "enum": ["display", DOCUMENTATION_STATUS_FIELD],
                    },
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
    return anthropic.AsyncAnthropic()


def _serialize_retrieved(retrieved: dict[FhirResourceType, list[Any]]) -> str:
    """
    Parses the caller's retrieval result into the JSON the model sees.
    Raises rather than guessing when a required resource type is missing:
    a type that was never queried must not be narrated as "not recorded".
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
        section: dict[str, Any] = {
            "records": [
                record.model_dump(mode="json")
                for record in brief_records(resource_type, records)
            ]
        }
        status = documentation_status(resource_type, records)
        if status is not None:
            section["no_records_claim"] = {
                "source_id": query_source_id(resource_type),
                "field": DOCUMENTATION_STATUS_FIELD,
                "value": status,
            }
        sections[resource_type.value] = section
    return json.dumps(sections, indent=2)


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


async def draft_pre_visit_brief(
    retrieved: dict[FhirResourceType, list[Any]],
) -> list[Claim]:
    """
    Drafts Pre-Visit Brief claims from one patient's retrieved Condition,
    MedicationRequest, and AllergyIntolerance lists. Other resource types
    in `retrieved` are ignored; Use Case 1 doesn't use them (§3).

    Returns claims exactly as the model drafted them, unverified. A claim
    citing a source_id that wasn't provided is returned too, not dropped
    here, so the verification step can report it (§5).
    """
    user_content = (
        "<retrieved_fhir_data>\n"
        f"{_serialize_retrieved(retrieved)}\n"
        "</retrieved_fhir_data>\n\n"
        "Draft the Pre-Visit Brief claims for this patient."
    )
    correlation_id = get_correlation_id() or "no-request-context"

    started = time.perf_counter()
    try:
        async with _client().beta.messages.stream(
            model=MODEL,
            max_tokens=_MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
            thinking={"type": "adaptive"},
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
        # Logged so a failed call still counts toward Metrics 4 and 6,
        # then re-raised: the caller decides how to recover.
        log_event(
            "llm_call_failed",
            event_data={
                "correlation_id": correlation_id,
                "requested_model": MODEL,
                "latency_ms": round((time.perf_counter() - started) * 1000),
                "error_type": type(exc).__name__,
            },
            use_case=UseCase.PRE_VISIT_BRIEF,
        )
        raise
    latency_ms = round((time.perf_counter() - started) * 1000)

    usage = message.usage
    fallback_ran = any(
        iteration.type == "fallback_message" for iteration in usage.iterations or []
    )
    # KEY_METRICS.md Metric 6 (cost per interaction) is computed from the
    # token counts and served model. latency_ms is this call's share of
    # Metric 4's trigger-to-render interval, not the whole interval.
    log_event(
        "llm_call",
        event_data={
            "correlation_id": correlation_id,
            "requested_model": MODEL,
            "served_model": message.model,
            "fallback_ran": fallback_ran,
            "effort": EFFORT,
            "request_id": request_id,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_creation_input_tokens": usage.cache_creation_input_tokens,
            "cache_read_input_tokens": usage.cache_read_input_tokens,
            "latency_ms": latency_ms,
            "stop_reason": message.stop_reason,
        },
        use_case=UseCase.PRE_VISIT_BRIEF,
    )

    if message.stop_reason == "refusal":
        category = message.stop_details.category if message.stop_details else None
        raise ClaimDraftingError(
            f"Model declined to draft claims (refusal category: {category})"
        )
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

    # Sections whose query-level claim the model did not emit. Not
    # verification (§5 runs separately): a nonempty list here is the
    # specific Use Case 1 failure of a section silently missing from the
    # brief instead of reading "not recorded" / "none active".
    cited = {claim.source_id for claim in claims}
    no_records_sections = {
        rt.value: status
        for rt in _BRIEF_RESOURCE_MODELS
        if (status := documentation_status(rt, retrieved[rt])) is not None
    }
    log_event(
        "claims_drafted",
        event_data={
            "correlation_id": correlation_id,
            "claim_count": len(claims),
            "no_records_sections": no_records_sections,
            "no_records_sections_not_stated": [
                rt
                for rt in no_records_sections
                if query_source_id(FhirResourceType(rt)) not in cited
            ],
        },
        use_case=UseCase.PRE_VISIT_BRIEF,
    )
    return claims
