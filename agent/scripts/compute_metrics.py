#!/usr/bin/env python3
"""
Compute docs/KEY_METRICS.md's six metrics from the agent's structured stdout
log — no Langfuse, no dashboard, no deployment.

Every `log_event(...)` call writes one JSON line to stdout
(app/middleware/logging.py) before the same call exports to Langfuse, so
this stream carries every event the dashboards are built from. The output is
deliberately plain: each number names the event type and field it came from
and the log line numbers that contributed, so a reviewer can re-derive it by
hand from the same file.

Capturing the log (configure_logging() writes to stdout only; this script's
default path is the convention for where that stdout is redirected):

    cd agent
    uvicorn app.main:app --host 127.0.0.1 --port 8000 >> logs/agent-stdout.jsonl
    python scripts/send_langfuse_test_event.py > logs/agent-stdout.jsonl

    python scripts/compute_metrics.py                        # logs/agent-stdout.jsonl
    python scripts/compute_metrics.py --log-file other.jsonl
    docker logs <agent-container> | python scripts/compute_metrics.py --log-file -

Lines that are not one JSON object (uvicorn access logs, pretty-printed
script output) are skipped and counted; JSON log lines other than
agent_event (e.g. observability_export_enabled) are counted separately. logs/ is gitignored: these lines carry
patient_session_key and FHIR source ids.

Cost (Metric 6): no pricing constant exists anywhere in this codebase —
app/reasoning/draft_claims.py on agent/claim-drafting logs token counts
only, and the only USD figure computed so far was by Langfuse's server-side
model price table. So token counts are always reported, and USD only when a
price file is supplied explicitly with --prices (never a built-in default):

    {"<model id as logged>": {"input": <USD>, "output": <USD>,
                              "cache_read": <USD>, "cache_write": <USD>}}
    # USD per 1M tokens; take them from the provider's published prices

Exit status: 0 normally; 1 if an absolute from KEY_METRICS.md is broken
(Metric 2 below 100%, or Metric 3's invariant violated); 2 on unreadable
input.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, TextIO

AGENT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENT_DIR))

from pydantic import BaseModel, NonNegativeInt, ValidationError  # noqa: E402

from app.middleware.langfuse_export import (  # noqa: E402
    AUTHORIZATION_CHECK_EVENT,
    CLAIM_VERIFIED_EVENT,
    LATENCY_EVENT,
    LLM_USAGE_EVENT,
    RETRIEVAL_EVENT,
    RETRIEVAL_EVENT_SKIPPED,
)
from app.models.fhir_resources import FhirResourceType as R  # noqa: E402
from app.models.metrics_events import (  # noqa: E402
    AuthorizationCheckEvent,
    ClaimVerifiedEvent,
    LatencyEvent,
    RetrievalEvent,
    UseCase,
)

DEFAULT_LOG_FILE = AGENT_DIR / "logs" / "agent-stdout.jsonl"

# The claim-drafting branch's committed call site logs its model call as
# "llm_call" with served_model/requested_model instead of "llm_usage_event"
# with model. Both are read so Metric 6 isn't silently empty after merge.
LLM_CALL_EVENT = "llm_call"

LATENCY_BUDGET_SECONDS = 90.0  # USERS.md §1–2, KEY_METRICS.md Metric 4

# USERS.md §3 "Data required" for each use case, as tabulated in
# ARCHITECTURE.md §3's Tool Scope table. Follow-Up Drilling has no fixed
# set by definition (USERS.md §3, Use case 3), so it has no entry.
REQUIRED_RESOURCES: dict[UseCase, frozenset[R]] = {
    UseCase.PRE_VISIT_BRIEF: frozenset({R.CONDITION, R.MEDICATION_REQUEST, R.ALLERGY_INTOLERANCE}),
    UseCase.CHANGE_DETECTION: frozenset({R.ENCOUNTER, R.CONDITION, R.MEDICATION_REQUEST, R.OBSERVATION}),
}


class LlmUsage(BaseModel):
    """Token counts from llm_usage_event (model) or llm_call (served_model)."""

    model: str
    input_tokens: NonNegativeInt
    output_tokens: NonNegativeInt
    cache_creation_input_tokens: NonNegativeInt | None = None
    cache_read_input_tokens: NonNegativeInt | None = None


@dataclass
class Event:
    line: int
    event_type: str
    data: dict[str, Any]
    use_case: str | None
    patient_session_key: str | None
    correlation_id: str | None


@dataclass
class Log:
    path: str
    lines: int = 0
    non_json: int = 0
    other_json: int = 0
    events: list[Event] = field(default_factory=list)

    def of_type(self, *event_types: str) -> list[Event]:
        return [e for e in self.events if e.event_type in event_types]


def read_log(stream: TextIO, path: str) -> Log:
    log = Log(path=path)
    for number, raw in enumerate(stream, start=1):
        log.lines = number
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            record = None
        if not isinstance(record, dict):
            log.non_json += 1
            continue
        if record.get("event") != "agent_event":
            log.other_json += 1
            continue
        data = record.get("event_data")
        log.events.append(
            Event(
                line=number,
                event_type=str(record.get("event_type")),
                data=data if isinstance(data, dict) else {},
                use_case=record.get("use_case"),
                patient_session_key=record.get("patient_session_key"),
                correlation_id=record.get("correlation_id"),
            )
        )
    return log


# -- output helpers -----------------------------------------------------------


def lines_ref(lines: Iterable[int], limit: int = 25) -> str:
    ordered = sorted(lines)
    if not ordered:
        return "no lines"
    shown = ", ".join(map(str, ordered[:limit]))
    more = f", … (+{len(ordered) - limit} more)" if len(ordered) > limit else ""
    return f"log line{'s' if len(ordered) > 1 else ''} {shown}{more}"


def pct(numerator: int, denominator: int) -> str:
    return f"{100 * numerator / denominator:.1f}%"


def insufficient(event_type: str, qualifier: str = "") -> str:
    return f"insufficient data — 0 events of type {event_type}{qualifier} found"


def parse_all(events: list[Event], model: type[BaseModel]) -> tuple[list[tuple[Event, Any]], list[Event]]:
    parsed, invalid = [], []
    for event in events:
        try:
            parsed.append((event, model.model_validate(event.data)))
        except ValidationError:
            invalid.append(event)
    return parsed, invalid


def report_invalid(invalid: list[Event], model: type[BaseModel]) -> None:
    if invalid:
        print(f"    excluded: {len(invalid)} event(s) whose event_data failed {model.__name__} validation — {lines_ref(e.line for e in invalid)}")


def nearest_rank(sorted_values: list[float], percentile: float) -> float:
    return sorted_values[max(0, math.ceil(percentile / 100 * len(sorted_values)) - 1)]


# -- the six metrics ----------------------------------------------------------


def metric_1(log: Log) -> None:
    print("[1] Verification Pass Rate")
    print(f"    source: event_type \"{CLAIM_VERIFIED_EVENT}\" (ClaimVerifiedEvent), field event_data.passed")
    print("    rule:   count(passed == true) / count(all claims)")
    parsed, invalid = parse_all(log.of_type(CLAIM_VERIFIED_EVENT), ClaimVerifiedEvent)
    report_invalid(invalid, ClaimVerifiedEvent)
    if not parsed:
        print(f"    result: {insufficient(CLAIM_VERIFIED_EVENT)}")
        return
    passed = [e for e, c in parsed if c.passed]
    print(f"    result: {pct(len(passed), len(parsed))}  ({len(passed)} passed of {len(parsed)} claims) — {lines_ref(e.line for e, _ in parsed)}")
    by_use_case: dict[str, list[bool]] = defaultdict(list)
    by_interaction: dict[str, list[bool]] = defaultdict(list)
    for _, claim in parsed:
        by_use_case[claim.use_case.value].append(claim.passed)
        by_interaction[claim.patient_session_key].append(claim.passed)
    for use_case, results in sorted(by_use_case.items()):
        print(f"      use_case {use_case}: {pct(sum(results), len(results))} ({sum(results)} of {len(results)})")
    print(f"    per interaction (event_data.patient_session_key), {len(by_interaction)} interaction(s):")
    for key, results in sorted(by_interaction.items()):
        print(f"      {key}: {sum(results)} of {len(results)} passed")


def metric_2(log: Log) -> bool:
    """Returns False when the 100% absolute is broken."""
    print("[2] Unverified-Claim Disclosure Rate")
    print(f"    source: event_type \"{CLAIM_VERIFIED_EVENT}\" (ClaimVerifiedEvent), fields event_data.passed, event_data.disclosed_as_unverified")
    print("    rule:   among passed == false: count(disclosed_as_unverified == true) / count(passed == false); absolute target 100%")
    parsed, invalid = parse_all(log.of_type(CLAIM_VERIFIED_EVENT), ClaimVerifiedEvent)
    report_invalid(invalid, ClaimVerifiedEvent)
    if not parsed:
        print(f"    result: {insufficient(CLAIM_VERIFIED_EVENT)}")
        return True
    failed = [(e, c) for e, c in parsed if not c.passed]
    if not failed:
        print(f"    result: {insufficient(CLAIM_VERIFIED_EVENT, ' with passed == false')} (among {len(parsed)} claims)")
        return True
    disclosed = [e for e, c in failed if c.disclosed_as_unverified]
    undisclosed = [(e, c) for e, c in failed if not c.disclosed_as_unverified]
    verdict = "meets the 100% absolute" if not undisclosed else "BELOW the 100% absolute — render-step defect"
    print(f"    result: {pct(len(disclosed), len(failed))}  ({len(disclosed)} disclosed of {len(failed)} failed claims) — {verdict} — {lines_ref(e.line for e, _ in failed)}")
    for event, claim in undisclosed:
        print(f"      UNDISCLOSED: line {event.line}, source_id {claim.source_id}, correlation_id {claim.correlation_id}")
    return not undisclosed


def metric_3(log: Log) -> bool:
    """Returns False when the invariant is violated."""
    print("[3] Authorization Boundary Integrity (hard invariant)")
    print(f"    source: event_type \"{AUTHORIZATION_CHECK_EVENT}\" (AuthorizationCheckEvent), fields event_data.requested_scope, event_data.held_scopes, event_data.outcome")
    print("    rule:   out-of-scope = requested_scope not in held_scopes; every out-of-scope request must have outcome == \"rejected\"; one \"accepted\" breaks the invariant")
    parsed, invalid = parse_all(log.of_type(AUTHORIZATION_CHECK_EVENT), AuthorizationCheckEvent)
    report_invalid(invalid, AuthorizationCheckEvent)
    if not parsed:
        print(f"    result: {insufficient(AUTHORIZATION_CHECK_EVENT)}")
        return True
    out_of_scope = [(e, a) for e, a in parsed if a.requested_scope not in a.held_scopes]
    in_scope = len(parsed) - len(out_of_scope)
    if not out_of_scope:
        print(f"    result: {insufficient(AUTHORIZATION_CHECK_EVENT, ' with requested_scope not in held_scopes')} ({in_scope} in-scope request(s) present, not part of this metric)")
        return True
    rejected = [e for e, a in out_of_scope if a.outcome == "rejected"]
    accepted = [(e, a) for e, a in out_of_scope if a.outcome == "accepted"]
    unrecognized = [(e, a) for e, a in out_of_scope if a.outcome not in ("rejected", "accepted")]
    holds = not accepted and not unrecognized
    state = "INVARIANT HOLDS" if holds else "INVARIANT VIOLATED — page someone today (KEY_METRICS.md Metric 3)"
    print(f"    result: {state} — {len(rejected)} of {len(out_of_scope)} out-of-scope requests rejected ({pct(len(rejected), len(out_of_scope))}) — {lines_ref(e.line for e, _ in out_of_scope)}")
    print(f"      in-scope requests (not counted): {in_scope}")
    for event, auth in accepted:
        print(f"      ACCEPTED OUT-OF-SCOPE: line {event.line}, requested {auth.requested_scope}, correlation_id {auth.correlation_id}")
    for event, auth in unrecognized:
        print(f"      UNRECOGNIZED OUTCOME {auth.outcome!r} (cannot confirm rejection): line {event.line}, correlation_id {auth.correlation_id}")
    return holds


def metric_4(log: Log) -> None:
    print(f"[4] Response Latency p50 / p95 per use_case vs the {LATENCY_BUDGET_SECONDS:.0f} s budget")
    print(f"    source: event_type \"{LATENCY_EVENT}\" (LatencyEvent), fields event_data.rendered_ts − event_data.trigger_ts (seconds), grouped by event_data.use_case")
    print("    rule:   nearest-rank percentiles, computed separately per use case, never blended")
    parsed, invalid = parse_all(log.of_type(LATENCY_EVENT), LatencyEvent)
    report_invalid(invalid, LatencyEvent)
    negative = [e for e, lat in parsed if lat.rendered_ts < lat.trigger_ts]
    if negative:
        print(f"    excluded: {len(negative)} event(s) with rendered_ts < trigger_ts — {lines_ref(e.line for e in negative)}")
    by_use_case: dict[UseCase, list[tuple[float, int]]] = defaultdict(list)
    for event, latency in parsed:
        if latency.rendered_ts >= latency.trigger_ts:
            by_use_case[latency.use_case].append((latency.rendered_ts - latency.trigger_ts, event.line))
    for use_case in UseCase:
        samples = by_use_case.get(use_case)
        if not samples:
            print(f"      {use_case.value}: {insufficient(LATENCY_EVENT, f' with use_case={use_case.value}')}")
            continue
        values = sorted(s for s, _ in samples)
        over = sum(1 for v in values if v > LATENCY_BUDGET_SECONDS)
        print(
            f"      {use_case.value}: n={len(values)}  p50={nearest_rank(values, 50):.1f}s  p95={nearest_rank(values, 95):.1f}s"
            f"  over budget: {over} — {lines_ref(line for _, line in samples)}"
        )


def metric_5(log: Log) -> None:
    print("[5] Retrieval Coverage per Query (proxy — type-level, not row-level)")
    print(f"    source: event_type \"{RETRIEVAL_EVENT}\" (RetrievalEvent), field event_data.resource_type, per interaction = event_data.correlation_id + event_data.use_case")
    print("    rule:   |queried ∩ required| / |required|, required set per USERS.md §3 \"Data required\" as tabulated in ARCHITECTURE.md §3 Tool Scope:")
    for use_case in UseCase:
        required = REQUIRED_RESOURCES.get(use_case)
        listed = ", ".join(sorted(r.value for r in required)) if required else "no fixed set (USERS.md §3, Use case 3) — not computable against a fixed requirement"
        print(f"      {use_case.value}: {listed}")
    skipped = log.of_type(RETRIEVAL_EVENT_SKIPPED)
    print(f"    {RETRIEVAL_EVENT_SKIPPED} (no use_case, cannot be attributed): {len(skipped)}" + (f" — {lines_ref(e.line for e in skipped)}" if skipped else ""))
    parsed, invalid = parse_all(log.of_type(RETRIEVAL_EVENT), RetrievalEvent)
    report_invalid(invalid, RetrievalEvent)
    orphaned = [e for e, r in parsed if r.correlation_id == "no-request-context"]
    if orphaned:
        print(f"    excluded: {len(orphaned)} event(s) with correlation_id \"no-request-context\" (not attributable to one interaction) — {lines_ref(e.line for e in orphaned)}")
    interactions: dict[tuple[UseCase, str], dict[R, list[int]]] = defaultdict(lambda: defaultdict(list))
    for event, retrieval in parsed:
        if retrieval.correlation_id != "no-request-context":
            interactions[(retrieval.use_case, retrieval.correlation_id)][retrieval.resource_type].append(event.line)
    for use_case in UseCase:
        required = REQUIRED_RESOURCES.get(use_case)
        mine = {cid: types for (uc, cid), types in interactions.items() if uc == use_case}
        if not mine:
            print(f"      {use_case.value}: {insufficient(RETRIEVAL_EVENT, f' with use_case={use_case.value}')}")
            continue
        if required is None:
            print(f"      {use_case.value}: {len(mine)} interaction(s) — coverage not computed (no fixed required set)")
            continue
        fractions = []
        missed: Counter[str] = Counter()
        print(f"      {use_case.value}: {len(mine)} interaction(s)")
        for cid, types in sorted(mine.items()):
            queried = set(types)
            covered = queried & required
            missing = sorted(r.value for r in required - queried)
            missed.update(missing)
            fractions.append(len(covered) / len(required))
            print(
                f"        correlation_id {cid}: {len(covered)}/{len(required)} required types queried"
                f" [{', '.join(sorted(r.value for r in queried))}]; missing: {', '.join(missing) or 'none'}"
                f" — {lines_ref(line for lines in types.values() for line in lines)}"
            )
        full = sum(1 for f in fractions if f == 1)
        print(f"        mean coverage {100 * sum(fractions) / len(fractions):.1f}%, fully covered {full} of {len(fractions)}; misses by type: {dict(missed) or 'none'}")


def metric_6(log: Log, prices: dict[str, dict[str, float]] | None) -> None:
    print("[6] Cost per Interaction per use_case")
    print(
        f"    source: event_type \"{LLM_USAGE_EVENT}\" (field event_data.model) or \"{LLM_CALL_EVENT}\" (field event_data.served_model);"
        " fields event_data.input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens;"
        " per interaction = patient_session_key (ARCHITECTURE.md §4 key), grouped by use_case"
    )
    if prices is None:
        print("    pricing: none — no pricing constant exists in this codebase; USD not computed (pass --prices FILE, USD per 1M tokens)")
    else:
        print(f"    pricing: --prices file, models: {', '.join(sorted(prices))}")
    events = log.of_type(LLM_USAGE_EVENT, LLM_CALL_EVENT)
    if not events:
        print(f"    result: {insufficient(LLM_USAGE_EVENT)} (nor {LLM_CALL_EVENT})")
        return
    usages: list[tuple[Event, LlmUsage]] = []
    invalid = []
    for event in events:
        data = dict(event.data)
        if event.event_type == LLM_CALL_EVENT:
            data.setdefault("model", data.get("served_model") or data.get("requested_model"))
        try:
            usages.append((event, LlmUsage.model_validate(data)))
        except ValidationError:
            invalid.append(event)
    if invalid:
        print(f"    excluded: {len(invalid)} event(s) without valid model/token fields — {lines_ref(e.line for e in invalid)}")
    by_use_case: dict[str, dict[str, list[tuple[Event, LlmUsage]]]] = defaultdict(lambda: defaultdict(list))
    no_use_case = [e for e, _ in usages if not e.use_case]
    for event, usage in usages:
        if event.use_case:
            key = event.patient_session_key or f"(no patient_session_key) correlation_id {event.correlation_id}"
            by_use_case[event.use_case][key].append((event, usage))
    if no_use_case:
        print(f"    excluded: {len(no_use_case)} call(s) with no use_case — {lines_ref(e.line for e in no_use_case)}")
    for use_case in UseCase:
        interactions = by_use_case.get(use_case.value)
        if not interactions:
            print(f"      {use_case.value}: {insufficient(LLM_USAGE_EVENT, f' with use_case={use_case.value}')}")
            continue
        costs: list[float] = []
        totals: list[int] = []
        print(f"      {use_case.value}: {len(interactions)} interaction(s)")
        for key, calls in sorted(interactions.items()):
            t_in = sum(u.input_tokens for _, u in calls)
            t_out = sum(u.output_tokens for _, u in calls)
            t_cw = sum(u.cache_creation_input_tokens or 0 for _, u in calls)
            t_cr = sum(u.cache_read_input_tokens or 0 for _, u in calls)
            totals.append(t_in + t_out + t_cw + t_cr)
            cost_text = "USD not computed"
            if prices is not None:
                unpriced = sorted({u.model for _, u in calls if u.model not in prices})
                if unpriced:
                    cost_text = f"USD not computed — no price for {', '.join(unpriced)}"
                else:
                    cost = sum(
                        (u.input_tokens * prices[u.model]["input"]
                         + u.output_tokens * prices[u.model]["output"]
                         + (u.cache_creation_input_tokens or 0) * prices[u.model].get("cache_write", prices[u.model]["input"])
                         + (u.cache_read_input_tokens or 0) * prices[u.model].get("cache_read", prices[u.model]["input"]))
                        / 1_000_000
                        for _, u in calls
                    )
                    costs.append(cost)
                    cost_text = f"${cost:.6f}"
            models = ", ".join(sorted({u.model for _, u in calls}))
            print(
                f"        {key}: {len(calls)} call(s) [{models}] input {t_in}, cache_write {t_cw}, cache_read {t_cr}, output {t_out}"
                f" → {cost_text} — {lines_ref(e.line for e, _ in calls)}"
            )
        summary = f"mean tokens/interaction {sum(totals) / len(totals):.0f}"
        if costs and len(costs) == len(interactions):
            summary += f", mean cost/interaction ${sum(costs) / len(costs):.6f}"
        print(f"        {summary}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--log-file", default=str(DEFAULT_LOG_FILE), help=f"structured stdout log to read, or - for stdin (default: {DEFAULT_LOG_FILE})")
    parser.add_argument("--prices", help="JSON file: {model: {input, output, cache_read, cache_write}} in USD per 1M tokens; no default")
    args = parser.parse_args()

    prices = None
    if args.prices:
        try:
            prices = json.loads(Path(args.prices).read_text())
        except (OSError, json.JSONDecodeError) as exc:
            print(f"ERROR: cannot read --prices file: {exc}", file=sys.stderr)
            return 2

    if args.log_file == "-":
        log = read_log(sys.stdin, "<stdin>")
    else:
        try:
            with open(args.log_file, encoding="utf-8") as stream:
                log = read_log(stream, str(Path(args.log_file).resolve()))
        except OSError as exc:
            print(f"ERROR: cannot read --log-file: {exc}", file=sys.stderr)
            return 2

    print("KEY_METRICS.md — computed from the agent's structured stdout log")
    print(f"log file: {log.path}")
    print(
        f"lines: {log.lines} | agent_event lines: {len(log.events)} | other structured log lines: {log.other_json}"
        f" | non-log lines skipped: {log.non_json}"
    )
    counts = Counter(e.event_type for e in log.events)
    print(f"agent_event types: {', '.join(f'{k}={v}' for k, v in sorted(counts.items())) or 'none'}")
    print()
    metric_1(log)
    print()
    disclosure_ok = metric_2(log)
    print()
    invariant_ok = metric_3(log)
    print()
    metric_4(log)
    print()
    metric_5(log)
    print()
    metric_6(log, prices)
    return 0 if disclosure_ok and invariant_ok else 1


if __name__ == "__main__":
    sys.exit(main())
