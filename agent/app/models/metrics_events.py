"""
Log event schemas — the shapes docs/KEY_METRICS.md's six metrics are
computed from.

These are schemas ONLY; this module emits nothing and aggregates nothing.
Three of the four are emitted elsewhere: ClaimVerifiedEvent by
app/verification/checker.py, RetrievalEvent by app/fhir/client.py, and
LatencyEvent by the graphs in app/graph/. AuthorizationCheckEvent is NOT
emitted by the agent, because the scope pre-check it records is not
implemented (see its docstring). Rollup lives in
scripts/compute_metrics.py, not here.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel

from app.models.fhir_resources import FhirResourceType


class UseCase(str, Enum):
    """
    The three, and only three, use cases USERS.md §3 defines — no others.
    USERS.md §4: "any capability not traceable to one of the three use
    cases above is out of scope for v1 regardless of how reasonable it
    sounds in isolation." The same discipline applies to use_case values.
    """

    PRE_VISIT_BRIEF = "pre_visit_brief"
    CHANGE_DETECTION = "change_detection"
    FOLLOW_UP_DRILLING = "follow_up_drilling"


class ClaimVerifiedEvent(BaseModel):
    """
    Emitted once per claim, after the verification step runs
    (ARCHITECTURE.md §5; app/verification/checker.py).

    Feeds KEY_METRICS.md:
      - Metric 1, Verification Pass Rate: count(passed) / count(all).
      - Metric 2, Unverified-Claim Disclosure Rate: of claims where
        passed=False, what fraction have disclosed_as_unverified=True.
    """

    source_id: str
    resource_type: FhirResourceType
    passed: bool
    disclosed_as_unverified: bool
    correlation_id: str
    use_case: UseCase
    # Required for Metrics 1, 2, 4, 5, 6 (all roll up per
    # (user, patient, session) per KEY_METRICS.md).
    patient_session_key: str


class ClaimDisclosureEvent(BaseModel):
    """
    Emitted once per FAILED claim, after the answer text has been built,
    by reading that text (app/verification/disclosure.py).

    This is what KEY_METRICS.md Metric 2 is computed from, and the reason
    it exists is findings.md F-59: `ClaimVerifiedEvent`'s
    `disclosed_as_unverified` is set by the verifier *before* rendering
    runs, and is `True` for every failed claim, so a metric computed from
    it reports 100% whatever the render step does — including when the
    render step hides failures, the one defect Metric 2 is defined to
    catch.

    `disclosed` here is observed, not intended: it says whether the
    claim's UNVERIFIED line was actually found in the rendered text. The
    two fields answer different questions and both are kept —
    `disclosed_as_unverified` is what the verifier required, `disclosed`
    is what the physician was shown.
    """

    source_id: str
    resource_type: FhirResourceType
    field: str
    disclosed: bool
    correlation_id: str
    use_case: UseCase
    patient_session_key: str


class RetrievalEvent(BaseModel):
    """
    Emitted once per FHIR resource type actually queried during an
    interaction.

    Feeds KEY_METRICS.md Metric 5, Retrieval Coverage per Query: that
    metric compares, per use case, which of USERS.md §3's required
    resource types for that use case (ARCHITECTURE.md §3's tool-scope
    table) were actually queried against the full required set. Explicitly
    a proxy, not a guarantee — see KEY_METRICS.md Metric 5's own framing:
    resource-type coverage does not prove every relevant row within that
    type was fetched.
    """

    resource_type: FhirResourceType
    correlation_id: str
    use_case: UseCase


class AuthorizationCheckEvent(BaseModel):
    """
    NOT emitted by the agent. Schema only.

    The design (ARCHITECTURE.md §2) is one event per FHIR request,
    comparing the scope that request needed against the agent's own
    decoded-JWT record of the scopes the current token holds, which would
    also tell a 401 caused by insufficient scope from one caused by an
    expired token (the platform's 401/403 ambiguity, F-08). That
    agent-side scope pre-check is not implemented. The agent forwards the
    caller's token unchanged, reads only its `sub` claim, and maps
    OpenEMR's 401 to FHIR_TOKEN_REJECTED without saying which cause it was
    (app/fhir/exceptions.py). Nothing in app/ constructs this event; the
    only producers are tests and scripts/send_langfuse_test_event.py. The
    consumers exist (app/middleware/langfuse_export.py,
    scripts/compute_metrics.py), so Metric 3 reports insufficient data
    rather than a result.

    Would feed KEY_METRICS.md Metric 3, Authorization Boundary Integrity: a
    request this event shows as out-of-scope (per held_scopes) that
    nonetheless comes back outcome="accepted" is exactly the silent
    authorization regression Metric 3 exists to catch — treated as a PHI
    exposure incident, not a KPI miss (KEY_METRICS.md Metric 3).
    """

    requested_scope: str
    held_scopes: list[str]
    # The actual FHIR response outcome for this request, e.g. "accepted"
    # or "rejected". Metric 3's computation (not implemented here) is the
    # cross-reference of this against held_scopes/requested_scope.
    outcome: str
    correlation_id: str


class LatencyEvent(BaseModel):
    """
    Emitted once per completed interaction, from trigger to rendered
    response.

    Feeds KEY_METRICS.md Metric 4, Response Latency (p50/p95) Against the
    90-Second Budget: rendered_ts - trigger_ts, segmented by use_case.
    Per ARCHITECTURE.md §5, verification runs before render and gates
    every response, so this interval already includes verification's own
    cost — it is not layered on afterward.
    """

    use_case: UseCase
    trigger_ts: float  # unix timestamp, seconds
    rendered_ts: float  # unix timestamp, seconds
    correlation_id: str
