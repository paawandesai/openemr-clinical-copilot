"""
F — The outlier patient (F-22, F-46; USERS.md §1 and §3 UC1; KEY_METRICS.md
Metric 4).

F-22: pid=20 had 530 encounters and 92 problems locally, "roughly 18x the
median"; the implication is a hard budget and "a deterministic truncation
policy that tells the user what was omitted". F-43 says that pid means
nothing on the droplet, so the patient is picked here as whoever has the
most encounters in the population being tested.

Two ways the outlier can fail silently: FHIR paging (a Bundle that holds a
page, not the chart) and the model skipping records in a long list. Either
yields a brief whose every claim verifies, because verification only checks
what is there. The oracle's independent read is what exposes both.
"""

from __future__ import annotations

from typing import Any

import pytest

from evals.harness import case, require_brief
from evals.invariants import completeness_violations, parse_brief


@case(
    "F1",
    title="Highest-encounter patient: nothing truncated, every active record accounted for, within 90 s",
    guards="silent truncation of a large chart (FHIR paging or the model skipping records) behind a brief "
    "that verifies 100%; a brief too slow for the gap between two patients",
    cites="F-22; F-46; USERS.md §1-2 (~90 s); KEY_METRICS.md Metrics 4 and 5; ARCHITECTURE.md §5",
    level="live endpoint",
)
def test_f1_outlier_complete_and_within_budget(agent: Any, panel: Any, cfg: Any, observe: Any) -> None:
    chart = panel.chart("outlier")
    counts = ", ".join(f"{rt} {chart.count(rt)} ({chart.active_count(rt)} active)" for rt in chart.searches)
    observe("patient", chart.patient_id)
    observe("chart", f"{chart.encounters} encounters; {counts}")
    observe(
        "oracle reads complete",
        {
            rt: f"total={s.total}, entries={len(s.records)}, next link={s.has_next_link}"
            for rt, s in chart.searches.items()
        },
    )

    reply = agent.brief(chart.patient_id)
    body = require_brief(reply)
    timing = body["timing"]
    parsed = parse_brief(body["brief"])
    observe("claims", body["verification"])
    observe("records counted as not included", {rt: s.omitted() for rt, s in parsed.sections.items()})
    observe(
        "latency",
        f"client {reply.elapsed_seconds:.1f} s; server total {timing['total_seconds']:.1f} s "
        f"(retrieve {timing['retrieve_seconds']:.2f}, draft {timing['draft_seconds']:.1f}, "
        f"verify {timing['verify_seconds']:.4f}, render {timing['render_seconds']:.4f})",
    )

    violations = completeness_violations(body, chart)
    budget = cfg.latency_budget_seconds
    if reply.elapsed_seconds > budget:
        violations.append(f"brief took {reply.elapsed_seconds:.1f} s end to end, over the {budget:.0f} s budget")
    if violations:
        pytest.fail("\n".join(violations))
