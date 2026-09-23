#!/usr/bin/env python3
"""
The Week 2 operational dashboard and alert evaluator, computed from the
agent's structured stdout log (docs/w2/OBSERVABILITY.md).

Same source and same rule as Week 1's `compute_metrics.py`: every
`log_event` writes one JSON line to stdout before it is exported to
Langfuse, so this stream carries every number the Langfuse dashboard shows
and does not depend on Langfuse being up, deployed, or covered by a BAA
(agent/docs/observability.md §1). Langfuse is the interactive view of the
same events; this is the one that is guaranteed to exist.

    cd agent
    python scripts/w2_dashboard.py --log-file logs/agent-stdout.jsonl
    python scripts/w2_dashboard.py --log-file - < some.jsonl
    docker logs <agent-container> 2>&1 | python scripts/w2_dashboard.py --log-file -

    # Real-time: re-read every 10 s and rewrite a self-refreshing HTML page.
    python scripts/w2_dashboard.py --log-file logs/agent-stdout.jsonl \\
        --html logs/dashboard.html --watch 10

    # Alerts only, for cron: exit 3 when any alert fires.
    python scripts/w2_dashboard.py --log-file ... --window-minutes 10 --alerts-only

**What it never prints.** Counts and numbers only — correlation ids are
counted, never listed; `patient_session_key` is never printed; and there is no
event field in the source that carries chart text to print (CLAUDE.md
invariant 1; app/models/telemetry_events.py).

Exit status: 0 no alert firing; 3 one or more alerts firing; 2 unreadable
input.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TextIO

#: Routes that are probes, not work. Counted, but kept out of the request
#: latency and error figures so a load balancer polling /ready every second
#: cannot dilute an error rate or pull a p95 down.
PROBE_ROUTES = frozenset({"/health", "/ready"})

#: The routes a clinician waits on for an answer — the ones KEY_METRICS.md
#: Metric 4's 90-second budget is about.
ANSWER_ROUTES = frozenset(
    {"/pre-visit-brief", "/pre-visit-brief/follow-up", "/pre-visit-brief/conversation"}
)


# --- reading -------------------------------------------------------------------


@dataclass(frozen=True)
class Event:
    line: int
    ts: datetime | None
    event_type: str
    data: dict[str, Any]
    correlation_id: str | None
    use_case: str | None


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def read_events(stream: Iterable[str]) -> Iterator[Event]:
    """agent_event lines only; everything else (uvicorn, prose) is skipped."""
    for number, raw in enumerate(stream, start=1):
        raw = raw.strip()
        if not raw.startswith("{"):
            continue
        try:
            line = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(line, dict) or line.get("event") != "agent_event":
            continue
        data = line.get("event_data")
        yield Event(
            line=number,
            ts=_parse_ts(line.get("timestamp")),
            event_type=str(line.get("event_type")),
            data=data if isinstance(data, dict) else {},
            correlation_id=line.get("correlation_id")
            or (data.get("correlation_id") if isinstance(data, dict) else None),
            use_case=line.get("use_case"),
        )


def within(events: Iterable[Event], since: datetime | None) -> list[Event]:
    if since is None:
        return list(events)
    return [e for e in events if e.ts is not None and e.ts >= since]


# --- arithmetic ----------------------------------------------------------------


def percentile(values: list[float], q: float) -> float | None:
    """Linear interpolation between closest ranks (numpy's default)."""
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


@dataclass
class Latency:
    count: int = 0
    errors: int = 0
    values: list[float] = field(default_factory=list)

    def add(self, milliseconds: float, error: bool) -> None:
        self.count += 1
        self.errors += int(error)
        self.values.append(milliseconds)

    def summary(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "errors": self.errors,
            "p50_ms": percentile(self.values, 0.50),
            "p95_ms": percentile(self.values, 0.95),
            "p99_ms": percentile(self.values, 0.99),
            "max_ms": max(self.values) if self.values else None,
        }


# --- the dashboard -------------------------------------------------------------


def is_dependency_failure(data: dict[str, Any]) -> bool:
    """Same rule as app.middleware.langfuse_export.is_dependency_failure."""
    if data.get("outcome") != "error":
        return False
    status = data.get("status_code")
    return status is None or status >= 500 or status == 429


def located_rate(outcomes: dict[str, int]) -> float | None:
    """Same rule as app.middleware.langfuse_export.located_rate."""
    located = int(outcomes.get("located", 0))
    read = located + int(outcomes.get("unlocated", 0))
    return located / read if read else None


def _cost(usage: dict[str, Any], prices: dict[str, dict[str, float]] | None) -> float | None:
    if prices is None:
        return None
    price = prices.get(str(usage.get("model")))
    if price is None:
        return None
    return (
        int(usage.get("input_tokens") or 0) * price["input"]
        + int(usage.get("output_tokens") or 0) * price["output"]
        + int(usage.get("cache_creation_input_tokens") or 0)
        * price.get("cache_write", price["input"])
        + int(usage.get("cache_read_input_tokens") or 0) * price.get("cache_read", price["input"])
    ) / 1_000_000


def build(events: list[Event], prices: dict[str, dict[str, float]] | None = None) -> dict:
    """Every number on the dashboard, as one JSON-serialisable dict."""
    requests_all = Latency()
    answer_requests = Latency()
    by_route: dict[str, Latency] = defaultdict(Latency)
    status_counts: Counter[str] = Counter()
    probes = 0

    by_step: dict[str, Latency] = defaultdict(Latency)
    step_outcomes: dict[str, Counter[str]] = defaultdict(Counter)

    tools: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "calls": 0,
            "dependency_failures": 0,
            "other_errors": 0,
            "retries": 0,
            "calls_with_retries": 0,
            "latency": Latency(),
        }
    )

    claims_passed = claims_failed = 0
    disclosures_total = disclosures_shown = 0

    extractions_completed = extractions_failed = 0
    pooled_outcomes: Counter[str] = Counter()
    text_layer_outcomes: Counter[str] = Counter()
    confidences: list[float] = []
    per_extraction_rates: list[float] = []

    retrievals = zero_hit = reranked = rerank_requested = 0
    hit_counts: list[int] = []

    encounters: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "llm_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cost_usd": 0.0,
            "unpriced_calls": 0,
        }
    )
    first_ts: datetime | None = None
    last_ts: datetime | None = None
    correlation_ids: set[str] = set()

    for event in events:
        if event.ts is not None:
            first_ts = event.ts if first_ts is None or event.ts < first_ts else first_ts
            last_ts = event.ts if last_ts is None or event.ts > last_ts else last_ts
        if event.correlation_id:
            correlation_ids.add(event.correlation_id)
        data = event.data

        if event.event_type == "http_request_event":
            route = str(data.get("route", "unmatched"))
            status = int(data.get("status_code", 0))
            if route in PROBE_ROUTES:
                probes += 1
                continue
            latency = float(data.get("latency_ms", 0.0))
            error = status >= 500
            requests_all.add(latency, error)
            by_route[f"{data.get('method', '?')} {route}"].add(latency, error)
            status_counts[f"{status // 100}xx"] += 1
            if route in ANSWER_ROUTES:
                answer_requests.add(latency, error)

        elif event.event_type == "step_event":
            key = f"{data.get('pipeline')}.{data.get('step')}"
            outcome = str(data.get("outcome"))
            by_step[key].add(float(data.get("latency_ms", 0.0)), outcome == "error")
            step_outcomes[key][outcome] += 1

        elif event.event_type == "tool_call_event":
            key = str(data.get("tool"))
            tool = tools[key]
            tool["calls"] += 1
            retries = int(data.get("retries", 0))
            tool["retries"] += retries
            tool["calls_with_retries"] += int(retries > 0)
            failed = is_dependency_failure(data)
            tool["dependency_failures"] += int(failed)
            tool["other_errors"] += int(data.get("outcome") == "error" and not failed)
            tool["latency"].add(float(data.get("latency_ms", 0.0)), failed)

        elif event.event_type == "claim_verified_event":
            if data.get("passed") is True:
                claims_passed += 1
            elif data.get("passed") is False:
                claims_failed += 1

        elif event.event_type == "claim_disclosure_event":
            disclosures_total += 1
            disclosures_shown += int(data.get("disclosed") is True)

        elif event.event_type == "intake_extraction_completed":
            extractions_completed += 1
            outcomes = {k: int(v) for k, v in (data.get("field_outcomes") or {}).items()}
            pooled_outcomes.update(outcomes)
            if data.get("has_text_layer") is True:
                text_layer_outcomes.update(outcomes)
            single = located_rate(outcomes)
            if single is not None:
                per_extraction_rates.append(single)
            if isinstance(data.get("mean_confidence"), (int, float)):
                confidences.append(float(data["mean_confidence"]))

        elif event.event_type in ("intake_extraction_failed", "intake_extraction_internal_error"):
            extractions_failed += 1

        elif event.event_type == "evidence_retrieval_event":
            retrievals += 1
            hits = int(data.get("hit_count", 0))
            hit_counts.append(hits)
            zero_hit += int(hits == 0)
            reranked += int(data.get("reranked") is True)
            rerank_requested += int(int(data.get("rerank_candidates", 0)) > 0)

        elif event.event_type == "llm_usage_event":
            encounter = encounters[event.correlation_id or "no-correlation-id"]
            encounter["llm_calls"] += 1
            for name in (
                "input_tokens",
                "output_tokens",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
            ):
                encounter[name] += int(data.get(name) or 0)
            cost = _cost(data, prices)
            if cost is None:
                encounter["unpriced_calls"] += 1
            else:
                encounter["cost_usd"] += cost

    tokens = [e["input_tokens"] + e["output_tokens"] for e in encounters.values()]
    costs = [e["cost_usd"] for e in encounters.values() if e["unpriced_calls"] == 0]
    span_seconds = (last_ts - first_ts).total_seconds() if first_ts and last_ts else None

    return {
        "window": {
            "first_event": first_ts.isoformat() if first_ts else None,
            "last_event": last_ts.isoformat() if last_ts else None,
            "span_seconds": span_seconds,
            "events": len(events),
            "correlation_ids": len(correlation_ids),
        },
        "requests": {
            **requests_all.summary(),
            "error_rate": rate(requests_all.errors, requests_all.count),
            "status_classes": dict(sorted(status_counts.items())),
            "probe_requests": probes,
            "throughput_rps": (requests_all.count / span_seconds if span_seconds else None),
            "answer_routes": answer_requests.summary(),
            "by_route": {k: v.summary() for k, v in sorted(by_route.items())},
        },
        "steps": {
            k: {**v.summary(), "outcomes": dict(step_outcomes[k])}
            for k, v in sorted(by_step.items())
        },
        "tools": {
            k: {
                "calls": v["calls"],
                "dependency_failures": v["dependency_failures"],
                "failure_rate": rate(v["dependency_failures"], v["calls"]),
                "other_errors": v["other_errors"],
                "retries": v["retries"],
                "calls_with_retries": v["calls_with_retries"],
                **{f"latency_{m}": v["latency"].summary()[m] for m in ("p50_ms", "p95_ms")},
            }
            for k, v in sorted(tools.items())
        },
        "verification": {
            "claims": claims_passed + claims_failed,
            "passed": claims_passed,
            "failed": claims_failed,
            "pass_rate": rate(claims_passed, claims_passed + claims_failed),
            "failed_claims_checked_in_render": disclosures_total,
            "disclosure_rate": rate(disclosures_shown, disclosures_total),
        },
        "extraction": {
            "completed": extractions_completed,
            "failed": extractions_failed,
            "field_outcomes": dict(pooled_outcomes),
            "located_rate": located_rate(pooled_outcomes),
            "text_layer_located_rate": located_rate(text_layer_outcomes),
            "text_layer_fields_read": text_layer_outcomes.get("located", 0)
            + text_layer_outcomes.get("unlocated", 0),
            "min_located_rate": min(per_extraction_rates) if per_extraction_rates else None,
            "mean_confidence": (sum(confidences) / len(confidences)) if confidences else None,
        },
        "retrieval": {
            "retrievals": retrievals,
            "mean_hits": (sum(hit_counts) / len(hit_counts)) if hit_counts else None,
            "zero_hit": zero_hit,
            "reranked": reranked,
            "reranked_rate": rate(reranked, retrievals),
            "rerank_requested": rerank_requested,
        },
        "cost": {
            "priced": prices is not None,
            "encounters_with_llm_calls": len(encounters),
            "llm_calls": sum(e["llm_calls"] for e in encounters.values()),
            "tokens_per_encounter_mean": (sum(tokens) / len(tokens)) if tokens else None,
            "tokens_per_encounter_p95": percentile([float(t) for t in tokens], 0.95),
            "cost_per_encounter_mean_usd": (sum(costs) / len(costs)) if costs else None,
            "cost_total_usd": sum(costs) if costs else None,
            "unpriced_calls": sum(e["unpriced_calls"] for e in encounters.values()),
        },
    }


# --- alerts --------------------------------------------------------------------


@dataclass(frozen=True)
class Alert:
    """
    One alert rule. Meaning, on-call response and each threshold's basis are
    in docs/w2/ALERTS.md; the numbers are here, once.
    """

    name: str
    severity: str
    description: str
    threshold: float
    min_sample: int

    def evaluate(self, dashboard: dict) -> dict[str, Any]:
        value, sample, fired = self._measure(dashboard)
        if sample < self.min_sample:
            state = "insufficient_data"
        else:
            state = "firing" if fired else "ok"
        return {
            "name": self.name,
            "severity": self.severity,
            "state": state,
            "value": value,
            "threshold": self.threshold,
            "sample": sample,
            "min_sample": self.min_sample,
            "description": self.description,
        }

    def _measure(self, d: dict) -> tuple[Any, int, bool]:  # noqa: PLR0911
        if self.name == "answer_p95_latency":
            answer = d["requests"]["answer_routes"]
            p95 = answer["p95_ms"]
            return p95, answer["count"], p95 is not None and p95 > self.threshold
        if self.name == "request_error_rate":
            r = d["requests"]
            return r["error_rate"], r["count"], (r["error_rate"] or 0) > self.threshold
        if self.name == "tool_failure_rate":
            worst_name, worst_rate, worst_calls = None, None, 0
            for name, tool in d["tools"].items():
                if tool["calls"] < self.min_sample:
                    continue
                if worst_rate is None or (tool["failure_rate"] or 0) > worst_rate:
                    worst_name, worst_rate, worst_calls = (
                        name,
                        tool["failure_rate"] or 0,
                        tool["calls"],
                    )
            value = {"tool": worst_name, "rate": worst_rate}
            sample = (
                worst_calls
                if worst_name
                else max((t["calls"] for t in d["tools"].values()), default=0)
            )
            if worst_name is None:
                return value, min(sample, self.min_sample - 1), False
            return value, sample, worst_rate > self.threshold
        if self.name == "text_layer_located_rate_collapse":
            e = d["extraction"]
            value = e["text_layer_located_rate"]
            return value, e["text_layer_fields_read"], value is not None and value < self.threshold
        if self.name == "verification_failure_spike":
            v = d["verification"]
            failure = None if v["pass_rate"] is None else 1 - v["pass_rate"]
            return failure, v["claims"], failure is not None and failure > self.threshold
        raise ValueError(f"no measurement for alert {self.name}")


ALERTS: tuple[Alert, ...] = (
    Alert(
        "answer_p95_latency",
        "page",
        "p95 of answer-route latency (brief, follow-up, conversation turn) in ms",
        threshold=45_000,
        min_sample=10,
    ),
    Alert(
        "request_error_rate",
        "page",
        "share of non-probe requests answered 5xx",
        threshold=0.05,
        min_sample=20,
    ),
    Alert(
        "tool_failure_rate",
        "page",
        "worst per-tool dependency-failure rate (no response, 5xx or 429)",
        threshold=0.05,
        min_sample=20,
    ),
    Alert(
        "text_layer_located_rate_collapse",
        "ticket",
        "located / (located + unlocated) over fields read from text-layer PDFs",
        threshold=0.85,
        min_sample=50,
    ),
    Alert(
        "verification_failure_spike",
        "ticket",
        "share of drafted claims failing deterministic verification",
        threshold=0.10,
        min_sample=20,
    ),
)


def evaluate_alerts(dashboard: dict) -> list[dict[str, Any]]:
    return [alert.evaluate(dashboard) for alert in ALERTS]


# --- rendering -----------------------------------------------------------------


def _fmt(value: Any, unit: str = "") -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        if unit == "%":
            return f"{value * 100:.1f}%"
        if unit == "ms":
            return f"{value / 1000:.2f} s" if value >= 1000 else f"{value:.0f} ms"
        if unit == "$":
            return f"${value:.4f}"
        return f"{value:.3g}"
    return f"{value}{unit}"


def render_text(d: dict, alerts: list[dict]) -> str:
    out: list[str] = []
    w = d["window"]
    out.append(
        f"Window: {w['first_event']} .. {w['last_event']}  "
        f"({w['events']} events, {w['correlation_ids']} correlation ids)"
    )
    r = d["requests"]
    out.append(
        f"\nRequests: {r['count']}  errors(5xx): {r['errors']}  error rate: "
        f"{_fmt(r['error_rate'], '%')}  p50 {_fmt(r['p50_ms'], 'ms')}  p95 "
        f"{_fmt(r['p95_ms'], 'ms')}  p99 {_fmt(r['p99_ms'], 'ms')}  "
        f"throughput {_fmt(r['throughput_rps'])} req/s  "
        f"(status {r['status_classes']}, {r['probe_requests']} probe requests excluded)"
    )
    out.append("\nBy step (pipeline.step): count, errors, p50, p95")
    for name, s in d["steps"].items():
        out.append(
            f"  {name:42} {s['count']:5} {s['errors']:4}  "
            f"{_fmt(s['p50_ms'], 'ms'):>9}  {_fmt(s['p95_ms'], 'ms'):>9}"
        )
    out.append("\nTool calls: calls, dependency failures, retries, calls with retries")
    for name, t in d["tools"].items():
        out.append(
            f"  {name:22} {t['calls']:5} {t['dependency_failures']:4} "
            f"{t['retries']:4} {t['calls_with_retries']:4}  "
            f"fail {_fmt(t['failure_rate'], '%')}"
        )
    v = d["verification"]
    out.append(
        f"\nVerification: {v['passed']}/{v['claims']} passed "
        f"({_fmt(v['pass_rate'], '%')}); disclosure {_fmt(v['disclosure_rate'], '%')}"
    )
    e = d["extraction"]
    out.append(
        f"Extraction: {e['completed']} completed, {e['failed']} failed; located rate "
        f"{_fmt(e['located_rate'], '%')} (text layer {_fmt(e['text_layer_located_rate'], '%')}); "
        f"mean confidence {_fmt(e['mean_confidence'])}"
    )
    rt = d["retrieval"]
    out.append(
        f"Retrieval: {rt['retrievals']} runs, mean hits {_fmt(rt['mean_hits'])}, "
        f"zero-hit {rt['zero_hit']}, reranked {_fmt(rt['reranked_rate'], '%')}"
    )
    c = d["cost"]
    out.append(
        f"Tokens/cost: {c['llm_calls']} LLM calls over {c['encounters_with_llm_calls']} "
        f"encounters; tokens/encounter mean {_fmt(c['tokens_per_encounter_mean'])}; "
        f"cost/encounter {_fmt(c['cost_per_encounter_mean_usd'], '$')}"
        + ("" if c["priced"] else " (no --prices file: USD not computed)")
    )
    out.append("\nAlerts:")
    for a in alerts:
        out.append(
            f"  [{a['state'].upper():17}] {a['name']:34} value={a['value']} "
            f"threshold={a['threshold']} n={a['sample']}/{a['min_sample']}"
        )
    return "\n".join(out)


_CSS = """
:root{--bg:#f7f7f5;--panel:#fff;--ink:#1d1d1b;--muted:#6b6b66;--line:#e2e2dc;
--ok:#1f7a4d;--warn:#a15c00;--bad:#b3261e;--bar:#3b6ea8}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--panel:#1d1d1b;--ink:#ecebe6;
--muted:#9a9a93;--line:#34342f;--ok:#5cc28f;--warn:#e0a44a;--bad:#f07167;--bar:#7aa7dc}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.45 system-ui,-apple-system,Segoe UI,sans-serif;padding:16px}
main{max-width:1100px;margin:0 auto}h1{font-size:20px;margin:0 0 4px}
.sub{color:var(--muted);margin-bottom:16px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:16px}
.tile{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:10px 12px}
.tile .k{color:var(--muted);font-size:12px}.tile .v{font-size:22px;font-weight:600;font-variant-numeric:tabular-nums}
section{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px 14px;margin-bottom:14px;overflow-x:auto}
h2{font-size:15px;margin:0 0 8px}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{text-align:left;padding:4px 8px;border-bottom:1px solid var(--line);white-space:nowrap}
th{color:var(--muted);font-weight:500;font-size:12px}td.n{text-align:right}
.bar{display:inline-block;height:8px;background:var(--bar);border-radius:2px;vertical-align:middle}
.firing{color:var(--bad);font-weight:600}.ok{color:var(--ok)}.insufficient_data{color:var(--muted)}
"""


def render_html(d: dict, alerts: list[dict], refresh_seconds: int | None = None) -> str:
    e = html.escape
    r, v, x, rt, c = (d["requests"], d["verification"], d["extraction"], d["retrieval"], d["cost"])
    tiles = [
        ("Requests", _fmt(r["count"])),
        ("5xx errors", _fmt(r["errors"])),
        ("Error rate", _fmt(r["error_rate"], "%")),
        ("p50", _fmt(r["p50_ms"], "ms")),
        ("p95", _fmt(r["p95_ms"], "ms")),
        ("Answer p95", _fmt(r["answer_routes"]["p95_ms"], "ms")),
        ("Tool calls", _fmt(sum(t["calls"] for t in d["tools"].values()))),
        ("Retries", _fmt(sum(t["retries"] for t in d["tools"].values()))),
        ("Verification pass", _fmt(v["pass_rate"], "%")),
        ("Located rate", _fmt(x["located_rate"], "%")),
        ("Mean confidence", _fmt(x["mean_confidence"])),
        ("Reranked", _fmt(rt["reranked_rate"], "%")),
        ("Tokens / encounter", _fmt(c["tokens_per_encounter_mean"])),
        ("Cost / encounter", _fmt(c["cost_per_encounter_mean_usd"], "$")),
    ]
    tile_html = "".join(
        f'<div class="tile"><div class="k">{e(k)}</div><div class="v">{e(val)}</div></div>'
        for k, val in tiles
    )
    worst = max((s["p95_ms"] or 0 for s in d["steps"].values()), default=0) or 1
    step_rows = "".join(
        f"<tr><td>{e(name)}</td><td class=n>{s['count']}</td><td class=n>{s['errors']}</td>"
        f"<td class=n>{e(_fmt(s['p50_ms'], 'ms'))}</td><td class=n>{e(_fmt(s['p95_ms'], 'ms'))}</td>"
        f'<td><span class=bar style="width:{max(2, int(160 * (s["p95_ms"] or 0) / worst))}px"></span></td>'
        f"<td>{e(', '.join(f'{k}:{n}' for k, n in s['outcomes'].items()))}</td></tr>"
        for name, s in d["steps"].items()
    )
    route_rows = "".join(
        f"<tr><td>{e(name)}</td><td class=n>{s['count']}</td><td class=n>{s['errors']}</td>"
        f"<td class=n>{e(_fmt(s['p50_ms'], 'ms'))}</td><td class=n>{e(_fmt(s['p95_ms'], 'ms'))}</td>"
        f"<td class=n>{e(_fmt(s['p99_ms'], 'ms'))}</td></tr>"
        for name, s in r["by_route"].items()
    )
    tool_rows = "".join(
        f"<tr><td>{e(name)}</td><td class=n>{t['calls']}</td><td class=n>{t['dependency_failures']}</td>"
        f"<td class=n>{e(_fmt(t['failure_rate'], '%'))}</td><td class=n>{t['other_errors']}</td>"
        f"<td class=n>{t['retries']}</td><td class=n>{t['calls_with_retries']}</td>"
        f"<td class=n>{e(_fmt(t['latency_p50_ms'], 'ms'))}</td><td class=n>{e(_fmt(t['latency_p95_ms'], 'ms'))}</td></tr>"
        for name, t in d["tools"].items()
    )
    alert_rows = "".join(
        f'<tr><td class="{e(a["state"])}">{e(a["state"])}</td><td>{e(a["name"])}</td>'
        f"<td>{e(a['severity'])}</td><td>{e(json.dumps(a['value']))}</td><td>{e(str(a['threshold']))}</td>"
        f"<td class=n>{a['sample']}/{a['min_sample']}</td><td>{e(a['description'])}</td></tr>"
        for a in alerts
    )
    w = d["window"]
    refresh = f'<meta http-equiv="refresh" content="{refresh_seconds}">' if refresh_seconds else ""
    generated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">{refresh}
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Agent Operations Dashboard</title><style>{_CSS}</style></head><body><main>
<h1>Clinical Co-pilot agent: operations</h1>
<div class="sub">Generated {e(generated)} from the agent's stdout event log ·
window {e(str(w["first_event"]))} → {e(str(w["last_event"]))} · {w["events"]} events ·
{w["correlation_ids"]} correlation ids{" · refreshes every " + str(refresh_seconds) + " s" if refresh_seconds else ""}</div>
<div class="grid">{tile_html}</div>
<section><h2>Alerts</h2><table><tr><th>State</th><th>Alert</th><th>Severity</th><th>Value</th>
<th>Threshold</th><th>n / min n</th><th>Measures</th></tr>{alert_rows}</table></section>
<section><h2>Latency by step (p95 bar)</h2><table><tr><th>pipeline.step</th><th>n</th><th>errors</th>
<th>p50</th><th>p95</th><th></th><th>outcomes</th></tr>{step_rows}</table></section>
<section><h2>Requests by route (probes excluded)</h2><table><tr><th>route</th><th>n</th><th>5xx</th>
<th>p50</th><th>p95</th><th>p99</th></tr>{route_rows}</table></section>
<section><h2>Tool calls</h2><table><tr><th>tool</th><th>calls</th><th>dep. failures</th><th>rate</th>
<th>other errors</th><th>retries</th><th>calls w/ retries</th><th>p50</th><th>p95</th></tr>{tool_rows}</table></section>
<section><h2>Verification, extraction, retrieval, cost</h2><table>
<tr><th>Verification</th><td>{v["passed"]}/{v["claims"]} claims passed ({e(_fmt(v["pass_rate"], "%"))}); disclosure of failures in render {e(_fmt(v["disclosure_rate"], "%"))} of {v["failed_claims_checked_in_render"]}</td></tr>
<tr><th>Extraction</th><td>{x["completed"]} completed, {x["failed"]} failed; field outcomes {e(json.dumps(x["field_outcomes"]))}; located rate {e(_fmt(x["located_rate"], "%"))} (text layer {e(_fmt(x["text_layer_located_rate"], "%"))}, worst document {e(_fmt(x["min_located_rate"], "%"))}); mean confidence {e(_fmt(x["mean_confidence"]))}</td></tr>
<tr><th>Retrieval</th><td>{rt["retrievals"]} runs; mean hits {e(_fmt(rt["mean_hits"]))}; zero-hit {rt["zero_hit"]}; reranked {rt["reranked"]} of {rt["retrievals"]} ({e(_fmt(rt["reranked_rate"], "%"))})</td></tr>
<tr><th>Tokens &amp; cost</th><td>{c["llm_calls"]} LLM calls over {c["encounters_with_llm_calls"]} encounters; tokens/encounter mean {e(_fmt(c["tokens_per_encounter_mean"]))}, p95 {e(_fmt(c["tokens_per_encounter_p95"]))}; cost/encounter {e(_fmt(c["cost_per_encounter_mean_usd"], "$"))}{"" if c["priced"] else " (no price file)"}</td></tr>
</table></section>
</main></body></html>"""


# --- entry point ---------------------------------------------------------------


def _open(path: str) -> TextIO:
    return sys.stdin if path == "-" else open(path, encoding="utf-8")  # noqa: SIM115


def run_once(args: argparse.Namespace, prices: dict | None) -> int:
    try:
        stream = _open(args.log_file)
    except OSError as exc:
        print(f"ERROR: cannot read {args.log_file}: {exc}", file=sys.stderr)
        return 2
    with stream if stream is not sys.stdin else _nullcontext(stream) as handle:
        events = list(read_events(handle))
    since = (
        datetime.now(UTC) - timedelta(minutes=args.window_minutes) if args.window_minutes else None
    )
    dashboard = build(within(events, since), prices)
    alerts = evaluate_alerts(dashboard)
    if args.json:
        Path(args.json).write_text(
            json.dumps({"dashboard": dashboard, "alerts": alerts}, indent=2, default=str)
        )
    if args.html:
        Path(args.html).write_text(render_html(dashboard, alerts, args.watch))
    if args.alerts_only:
        for alert in alerts:
            print(json.dumps(alert))
    else:
        print(render_text(dashboard, alerts))
    return 3 if any(a["state"] == "firing" for a in alerts) else 0


class _nullcontext:
    def __init__(self, value: Any) -> None:
        self.value = value

    def __enter__(self) -> Any:
        return self.value

    def __exit__(self, *exc: object) -> None:
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--log-file", default="logs/agent-stdout.jsonl", help="JSONL, or - for stdin"
    )
    parser.add_argument("--prices", help="USD per 1M tokens, compute_metrics.py's format")
    parser.add_argument("--window-minutes", type=float, help="only events newer than this")
    parser.add_argument("--html", help="write a self-contained HTML dashboard here")
    parser.add_argument("--json", help="write every number and alert state here")
    parser.add_argument("--watch", type=int, help="re-read and re-render every N seconds")
    parser.add_argument("--alerts-only", action="store_true", help="one JSON line per alert")
    args = parser.parse_args(argv)

    prices = None
    if args.prices:
        prices = {
            k: v
            for k, v in json.loads(Path(args.prices).read_text()).items()
            if not k.startswith("_")
        }
    if not args.watch:
        return run_once(args, prices)
    if args.log_file == "-":
        parser.error("--watch re-reads the log, so it needs a file, not stdin")
    while True:
        run_once(args, prices)
        time.sleep(args.watch)


if __name__ == "__main__":
    sys.exit(main())
