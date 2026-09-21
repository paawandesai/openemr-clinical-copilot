"""
Recent model-call outcomes, so /ready can see a provider failure the
tokenless probe cannot (findings.md F-60).

`/ready`'s LLM probe calls `models.retrieve`, which proves the API key is
accepted and the drafting model exists but spends no tokens — so it cannot
see a billing failure. On 2026-09-16 the account's credit balance ran out
mid-run: every brief returned 502 while `/ready` kept reporting
`llm_provider: healthy`.

Whether the account can still pay for a completion is only observable by
attempting one. Rather than spend tokens on a timer behind a public
endpoint, this module reads the calls the service already makes: the two
real call sites (`draft_claims.stream_claims` and
`draft_follow_up.stream_structured`) report each outcome here, and `/ready`
treats a run of consecutive failures as the provider being down.

Three parameters, each chosen against a specific failure:

- **Consecutive failures, not a rate.** A single `BadRequestError` can be
  one request's fault (a prompt that came out too long for one large
  chart). That must not make the whole service unready. A provider that is
  actually down fails every call, so successes interleave only in the
  request-specific case, and a success resets the count.
- **`_FAILURE_THRESHOLD` = 3.** Low enough that an outage is reported
  within a few requests, high enough that an isolated bad request or a
  single dropped connection never is.
- **`_RECOVERY_SECONDS` = 300.** Without it, readiness that gates traffic
  would deadlock: no traffic means no model calls, no model calls means no
  success to clear the count, so the service could never report ready
  again on its own. After five minutes with no further failure the count
  is treated as stale and readiness recovers; if the provider is still
  down the next three calls trip it again. This is the half-open step of
  an ordinary circuit breaker, and it is why this signal is a readiness
  input and not a request-path gate — it never blocks a call, it only
  reports.

**What this does and does not buy.** It is reactive, by construction: the
first few briefs after the credits run out still fail. They fail closed and
fast — 502 with no partial brief (F-60's own evidence) — and from the third
failure on, `/ready` says `not_ready`, so an orchestrator stops routing here
and an operator polling `/ready` sees the incident instead of a green light
over a dead service. The alternative, a real minimal completion on every
probe, would be proactive but bills ~$13/month at this cache TTL, adds a
real model call behind a publicly reachable endpoint, and consumes the same
rate limit it is meant to protect. The existing `models.retrieve` probe is
kept alongside this: it catches a revoked key, a missing model and an
unreachable API *before* any request fails, which this signal cannot.

Single process, single event loop (one uvicorn worker; `agent/Dockerfile`).
The counters are plain ints mutated between awaits, never across one, so no
lock is needed; if the service ever runs multiple workers each keeps its
own view, which is the correct scope — readiness is per process.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

# Consecutive failed model calls before the provider is reported down.
_FAILURE_THRESHOLD = 3

# Seconds after the last failure before the run is treated as stale.
_RECOVERY_SECONDS = 300.0


@dataclass
class _Recent:
    consecutive_failures: int = 0
    last_failure_at: float = float("-inf")


_recent = _Recent()


def record_success() -> None:
    """A model call returned. Clears the run of failures."""
    _recent.consecutive_failures = 0
    _recent.last_failure_at = float("-inf")


def record_failure() -> None:
    """A model call raised an APIError. Counted toward the threshold."""
    _recent.consecutive_failures += 1
    _recent.last_failure_at = time.monotonic()


def consecutive_failures() -> int:
    """How many model calls have failed in a row, for logging and tests."""
    return _recent.consecutive_failures


def looks_down() -> bool:
    """
    True when the last `_FAILURE_THRESHOLD` model calls all failed and the
    most recent was inside `_RECOVERY_SECONDS`.
    """
    if _recent.consecutive_failures < _FAILURE_THRESHOLD:
        return False
    return (time.monotonic() - _recent.last_failure_at) <= _RECOVERY_SECONDS


def reset() -> None:
    """Drop all recorded state. For tests and process start."""
    _recent.consecutive_failures = 0
    _recent.last_failure_at = float("-inf")
