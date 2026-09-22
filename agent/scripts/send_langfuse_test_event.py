#!/usr/bin/env python3
"""
End-to-end proof that log_event reaches self-hosted Langfuse — not a mock.

Emits one synthetic Pre-Visit Brief interaction through the real
`configure_logging()` / `log_event()` path (every event_data built from the
real pydantic models in app/models/metrics_events.py), flushes, then reads
the trace BACK from Langfuse's v4 public APIs (Observations API v2, and the
Metrics API v2 that backs the dashboards) and exits non-zero unless every
expected observation, score, tag, session and cost actually landed — and
the scores resolve per use case (traceName), which is what KEY_METRICS.md
needs. A printed "sent" is not treated as proof of arrival
(docs/AUDIT.md F-15).

All identifiers are synthetic (patient "synthetic-0001"); no FHIR server,
no LLM, and no real data are involved.

    agent/observability/generate-env.sh          # once
    docker compose -f agent/observability/docker-compose.langfuse.yml up -d --wait
    cd agent
    set -a; . observability/agent-langfuse.env; set +a
    python scripts/send_langfuse_test_event.py
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langfuse import Langfuse  # noqa: E402

from app.middleware.correlation import bind_correlation_id  # noqa: E402
from app.middleware.langfuse_export import flush_export  # noqa: E402
from app.middleware.logging import configure_logging, log_event  # noqa: E402
from app.models.fhir_resources import FhirResourceType  # noqa: E402
from app.models.metrics_events import (  # noqa: E402
    AuthorizationCheckEvent,
    ClaimVerifiedEvent,
    LatencyEvent,
    RetrievalEvent,
    UseCase,
)

_POLL_TIMEOUT_SECONDS = 120


def _metrics(query: dict[str, Any]) -> list[dict[str, Any]]:
    """Langfuse Metrics API v2 — the query engine behind the dashboards."""
    result = _api_get(
        "/api/public/v2/metrics?" + urllib.parse.urlencode({"query": json.dumps(query)})
    )
    return (result or {}).get("data", [])


def _api_get(path: str) -> dict[str, Any] | None:
    base = os.environ["LANGFUSE_BASE_URL"].rstrip("/")
    token = base64.b64encode(
        f"{os.environ['LANGFUSE_PUBLIC_KEY']}:{os.environ['LANGFUSE_SECRET_KEY']}".encode()
    ).decode()
    request = urllib.request.Request(f"{base}{path}", headers={"Authorization": f"Basic {token}"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def main() -> int:
    for name in ("LANGFUSE_BASE_URL", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        if not os.environ.get(name):
            print(
                f"ERROR: {name} not set — source observability/agent-langfuse.env", file=sys.stderr
            )
            return 2

    configure_logging()
    correlation_id = f"langfuse-smoke-{uuid.uuid4()}"
    bind_correlation_id(correlation_id)
    use_case = UseCase.PRE_VISIT_BRIEF
    session = f"user:synthetic-physician|patient:synthetic-0001|session:{uuid.uuid4()}"
    trigger_ts = time.time()

    # Metric 5 — the three resource types ARCHITECTURE.md §3 requires for Use Case 1.
    for resource_type in (
        FhirResourceType.CONDITION,
        FhirResourceType.MEDICATION_REQUEST,
        FhirResourceType.ALLERGY_INTOLERANCE,
    ):
        event = RetrievalEvent(
            resource_type=resource_type, correlation_id=correlation_id, use_case=use_case
        )
        log_event(
            "retrieval_event",
            event.model_dump(mode="json"),
            use_case=use_case,
            patient_session_key=session,
        )

    # Metric 3 — an out-of-scope request that was correctly rejected.
    auth = AuthorizationCheckEvent(
        requested_scope="user/Observation.read",
        held_scopes=[
            "user/Patient.read",
            "user/Condition.read",
            "user/MedicationRequest.read",
            "user/AllergyIntolerance.read",
        ],
        outcome="rejected",
        correlation_id=correlation_id,
    )
    log_event("authorization_check_event", auth.model_dump(mode="json"))

    # Metric 6 — token counts only; synthetic numbers, real model id Langfuse prices.
    log_event(
        "llm_usage_event",
        {
            "model": "claude-sonnet-5",
            "input_tokens": 2400,
            "output_tokens": 350,
            "cache_read_input_tokens": 1800,
        },
        use_case=use_case,
        patient_session_key=session,
    )

    # Metrics 1 and 2 — one claim passes; one fails and is disclosed as unverified.
    for source_id, passed in (("synthetic-condition-1", True), ("synthetic-allergy-1", False)):
        claim = ClaimVerifiedEvent(
            source_id=source_id,
            resource_type=FhirResourceType.CONDITION
            if passed
            else FhirResourceType.ALLERGY_INTOLERANCE,
            passed=passed,
            disclosed_as_unverified=not passed,
            correlation_id=correlation_id,
            use_case=use_case,
            patient_session_key=session,
        )
        log_event(
            "claim_verified_event",
            claim.model_dump(mode="json"),
            use_case=use_case,
            patient_session_key=session,
        )

    # Metric 4 — trigger to render.
    latency = LatencyEvent(
        use_case=use_case,
        trigger_ts=trigger_ts,
        rendered_ts=trigger_ts + 41.7,
        correlation_id=correlation_id,
    )
    log_event(
        "latency_event",
        latency.model_dump(mode="json"),
        use_case=use_case,
        patient_session_key=session,
    )

    flush_export()

    trace_id = Langfuse.create_trace_id(seed=correlation_id)
    expected_observations = {
        "retrieval_event": 3,
        "authorization_check_event": 1,
        "llm_usage_event": 1,
        "claim_verified_event": 2,
        "latency_event": 1,
    }
    # (score name, trace name it resolves to, avg value, count) — read back through
    # the same Metrics API v2 the Langfuse dashboards use, grouped by traceName,
    # which is exactly the per-use-case segmentation KEY_METRICS.md requires.
    expected_boolean_scores = sorted(
        [
            ("out_of_scope_request_rejected", use_case.value, 1.0, 1),
            ("unverified_claim_disclosed", use_case.value, 1.0, 1),
            ("verification_passed", use_case.value, 0.5, 2),
        ]
    )
    expected_tags = {
        "use_case:pre_visit_brief",
        "retrieved:Condition",
        "retrieved:MedicationRequest",
        "retrieved:AllergyIntolerance",
    }

    now = datetime.now(UTC)
    window = {
        "fromTimestamp": (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        "toTimestamp": (now + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
    }
    trace_filter = [{"column": "traceId", "operator": "=", "value": trace_id, "type": "string"}]

    print(
        f"\nPolling Langfuse for trace {trace_id} (correlation_id {correlation_id}) ...",
        file=sys.stderr,
    )
    deadline = time.monotonic() + _POLL_TIMEOUT_SECONDS
    problems: list[str] = ["nothing read back yet"]
    observations: list[dict[str, Any]] = []
    boolean_scores: list[tuple[str, str, float, int]] = []
    latency_rows: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        observations = (
            _api_get(
                f"/api/public/v2/observations?traceId={trace_id}&limit=100"
                "&fields=core,basic,io,metadata,model,usage,metrics,trace_context"
            )
            or {}
        ).get("data", [])
        boolean_scores = sorted(
            (
                row["name"],
                row["traceName"],
                round(float(row["avg_value"]), 3),
                int(row["count_count"]),
            )
            for row in _metrics(
                {
                    "view": "scores-boolean",
                    "metrics": [
                        {"measure": "value", "aggregation": "avg"},
                        {"measure": "count", "aggregation": "count"},
                    ],
                    "dimensions": [{"field": "name"}, {"field": "traceName"}],
                    "filters": trace_filter,
                    **window,
                }
            )
        )
        latency_rows = _metrics(
            {
                "view": "scores-numeric",
                "metrics": [
                    {"measure": "value", "aggregation": "p50"},
                    {"measure": "value", "aggregation": "p95"},
                ],
                "dimensions": [{"field": "traceName"}],
                "filters": trace_filter
                + [
                    {
                        "column": "name",
                        "operator": "=",
                        "value": "response_latency_seconds",
                        "type": "string",
                    }
                ],
                **window,
            }
        )

        names: dict[str, int] = {}
        for observation in observations:
            names[observation["name"]] = names.get(observation["name"], 0) + 1
        tags = {tag for observation in observations for tag in (observation.get("tags") or [])}
        generation = next((o for o in observations if o["name"] == "llm_usage_event"), {})
        problems = []
        if names != expected_observations:
            problems.append(f"observations {names} != {expected_observations}")
        if tags != expected_tags:
            problems.append(f"tags {sorted(tags)} != {sorted(expected_tags)}")
        if {
            o.get("traceName") for o in observations if o["name"] != "authorization_check_event"
        } != {use_case.value}:
            problems.append("traceName not pre_visit_brief on every use-case-tagged observation")
        if {
            o.get("sessionId") for o in observations if o["name"] != "authorization_check_event"
        } != {session}:
            problems.append("sessionId not set on every session-scoped observation")
        if not generation.get("totalCost"):
            problems.append(
                f"llm_usage_event totalCost {generation.get('totalCost')!r} not computed"
            )
        if any(o.get("input") is not None or o.get("output") is not None for o in observations):
            problems.append("an observation carries input/output — must never happen")
        if boolean_scores != expected_boolean_scores:
            problems.append(
                f"boolean scores by traceName {boolean_scores} != {expected_boolean_scores}"
            )
        if [
            (r.get("traceName"), round(float(r.get("p50_value", -1)), 1)) for r in latency_rows
        ] != [(use_case.value, 41.7)]:
            problems.append(
                f"latency score by traceName {latency_rows} != p50 41.7 for {use_case.value}"
            )
        if not problems:
            break
        time.sleep(3)

    if problems:
        print("FAILED — trace did not arrive as expected:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    project_id = ((_api_get("/api/public/projects") or {}).get("data") or [{}])[0].get("id")
    base = os.environ["LANGFUSE_BASE_URL"].rstrip("/")
    summary = {
        "trace_url": f"{base}/project/{project_id}/traces/{trace_id}",
        "trace_id": trace_id,
        "correlation_id": correlation_id,
        "session_id": session,
        "observations": sorted(
            (
                o["name"],
                o["type"],
                o.get("level"),
                o.get("traceName") or None,
                o.get("model") or None,
                o.get("totalCost"),
            )
            for o in observations
        ),
        "tags_across_trace": sorted({t for o in observations for t in (o.get("tags") or [])}),
        "environment": sorted({o.get("environment") for o in observations}),
        "boolean_scores_by_trace_name": boolean_scores,
        "latency_seconds_by_trace_name": latency_rows,
        "input_or_output_present": any(
            o.get("input") is not None or o.get("output") is not None for o in observations
        ),
    }
    print(json.dumps(summary, indent=2, default=str))
    print(
        "VERIFIED: every expected observation, score, tag, session and cost is in Langfuse.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
