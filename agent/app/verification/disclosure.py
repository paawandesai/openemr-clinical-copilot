"""
Did the failed claims actually reach the physician? (findings.md F-59)

ARCHITECTURE.md §5 promises a failed claim is "stated as explicitly
unverified — never silently dropped, never softened into vaguer language
that hides the failure," and KEY_METRICS.md Metric 2 is the production
monitor for that promise: "anything under 100% here is a defect in the
render step."

Metric 2 was computed from `ClaimVerifiedEvent.disclosed_as_unverified`,
which the verifier sets to `True` for every failed claim *before* the
render step runs (`app/verification/checker.py` `_failed`, which has no
parameter to set it otherwise, on purpose). Nothing after rendering
recorded what was shown, so the metric read 100% whatever the renderer
did — including through exactly the regression it exists to catch. Eval
case C5 demonstrates it: with the renderer replaced by one that drops
failed claims, the suite's own check of the text found 5 violations while
Metric 2 reported "100.0% (4 disclosed of 4 failed claims)".

**What makes this check independent of the bug it looks for.** It reads
the rendered text — the artifact the physician is shown — and not the
renderer's intentions. It is called by the graph node *after* the render
function returns, never from inside it, so replacing or breaking the
renderer does not remove the check: the substituted renderer's output is
what gets audited. That is the whole design. A check that re-derived
"did I write this line?" from the same loop that writes the lines would
share any bug in that loop and prove nothing.

It does use each path's own line builder to know what to look for, which
is deliberate: the question is whether the line is *present*, so a shared
change to the line's wording moves both sides together and the audit stays
correct. What it catches is a line that should be in the text and is not.

**The boundary, stated plainly.** This runs in the same process as the
renderer and audits the string that process produced. It catches a render
step that drops, hides or softens a failed claim, which is F-59's stated
risk. It is not an end-to-end check of what a browser finally paints: if
the chart embed's own JavaScript hid an UNVERIFIED line, this would still
report it disclosed. Verifying that would need a check at the display
surface, which is a different mechanism outside this service.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable

from app.middleware.correlation import get_correlation_id
from app.middleware.logging import log_event
from app.models.claims import VerificationResult
from app.models.metrics_events import ClaimDisclosureEvent, UseCase

CLAIM_DISCLOSURE_EVENT = "claim_disclosure_event"


def audit_disclosure(
    results: list[VerificationResult],
    text: str,
    line_for: Callable[[VerificationResult], str],
    *,
    use_case: UseCase,
    patient_session_key: str,
) -> list[tuple[VerificationResult, bool]]:
    """
    One ClaimDisclosureEvent per failed claim, saying whether its
    UNVERIFIED line is present in `text`.

    `line_for` is the calling renderer's own line builder, used to know
    what to look for. Presence is counted, not just tested: two failed
    claims can render the same line (same value, same reason), and if the
    renderer wrote that line once, exactly one of them was disclosed.

    Returns the per-claim outcome so a caller can act on it; emitting the
    events is the point, and callers that only need the log ignore it.
    """
    failed = [result for result in results if not result.passed]
    if not failed:
        return []

    available = Counter(line.strip() for line in text.splitlines())
    correlation_id = get_correlation_id() or "no-request-context"

    outcomes: list[tuple[VerificationResult, bool]] = []
    for result in failed:
        line = line_for(result).strip()
        disclosed = available[line] > 0
        if disclosed:
            available[line] -= 1
        outcomes.append((result, disclosed))
        log_event(
            CLAIM_DISCLOSURE_EVENT,
            event_data=ClaimDisclosureEvent(
                source_id=result.claim.source_id,
                resource_type=result.claim.resource_type,
                field=result.claim.field,
                disclosed=disclosed,
                correlation_id=correlation_id,
                use_case=use_case,
                patient_session_key=patient_session_key,
            ).model_dump(mode="json"),
            use_case=use_case,
            patient_session_key=patient_session_key,
        )
    return outcomes
