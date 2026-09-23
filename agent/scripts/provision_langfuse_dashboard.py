#!/usr/bin/env python3
"""
Provision the Week 2 operations dashboard into a LOCAL self-hosted Langfuse.

    cd agent
    python scripts/provision_langfuse_dashboard.py --print-sql > dashboard.sql   # inspect
    python scripts/provision_langfuse_dashboard.py --apply                       # local only

Langfuse 4.37 has no public API for dashboards: they are rows in its
Postgres (`dashboard_widgets`, `dashboards`), written by the UI through
tRPC. The widget shapes below are the ones Langfuse's own seeded dashboards
use (read from those rows), and every query was first run through the
public Metrics API v2 — the engine the widgets render with — to check it
returns data (docs/w2/OBSERVABILITY.md §4).

`--apply` pipes the SQL into the local Langfuse Postgres container. It is
for this laptop's dev instance: the IDs are fixed, so re-running replaces
the same dashboard rather than adding another. On any other instance, build
the widgets in the UI from `WIDGETS` (the table in OBSERVABILITY.md lists
them) — never point `--apply` at a shared database.

Every widget reads an event or score this branch's exporter produces
(app/middleware/langfuse_export.py); none reads chart content, because no
exported event carries any.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

PROJECT_ID = "clinical-copilot-agent"
DASHBOARD_ID = "w2-operations-dashboard"
POSTGRES_CONTAINER = "clinical-copilot-langfuse-postgres-1"


def _names(*names: str) -> dict:
    return {"type": "stringOptions", "column": "name", "operator": "any of", "value": list(names)}


def _number(name: str, view: str, measure: str, agg: str, filters: list[dict]) -> dict:
    return {
        "name": name,
        "view": view,
        "dimensions": [],
        "metrics": [{"measure": measure, "agg": agg}],
        "filters": filters,
        "chart_type": "NUMBER",
        "chart_config": {"type": "NUMBER"},
    }


_STEP_SCORES = {
    "type": "string",
    "column": "name",
    "operator": "starts with",
    "value": "step_latency_ms:",
}

#: (widget, x, y, width, height) on Langfuse's 12-column grid.
WIDGETS: list[tuple[dict, int, int, int, int]] = [
    (
        _number("Requests", "OBSERVATIONS", "count", "count", [_names("http_request_event")]),
        0,
        0,
        2,
        2,
    ),
    (
        _number(
            "Server errors (5xx)",
            "OBSERVATIONS",
            "count",
            "count",
            [
                _names("http_request_event"),
                {
                    "type": "stringOptions",
                    "column": "level",
                    "operator": "any of",
                    "value": ["ERROR"],
                },
            ],
        ),
        2,
        0,
        2,
        2,
    ),
    (
        _number(
            "Error rate (5xx share)",
            "SCORES_NUMERIC",
            "value",
            "avg",
            [_names("request_server_error")],
        ),
        4,
        0,
        2,
        2,
    ),
    (
        _number(
            "Request latency p50 (ms)",
            "SCORES_NUMERIC",
            "value",
            "p50",
            [_names("request_latency_ms")],
        ),
        6,
        0,
        2,
        2,
    ),
    (
        _number(
            "Request latency p95 (ms)",
            "SCORES_NUMERIC",
            "value",
            "p95",
            [_names("request_latency_ms")],
        ),
        8,
        0,
        2,
        2,
    ),
    (
        _number(
            "Verification pass rate",
            "SCORES_NUMERIC",
            "value",
            "avg",
            [_names("verification_passed")],
        ),
        10,
        0,
        2,
        2,
    ),
    (
        {
            "name": "p95 latency by step (ms)",
            "view": "SCORES_NUMERIC",
            "dimensions": [{"field": "name"}],
            "metrics": [{"measure": "value", "agg": "p95"}],
            "filters": [_STEP_SCORES],
            "chart_type": "HORIZONTAL_BAR",
            "chart_config": {"type": "HORIZONTAL_BAR", "row_limit": 50, "show_value_labels": True},
        },
        0,
        2,
        6,
        6,
    ),
    (
        {
            "name": "p50 latency by step (ms)",
            "view": "SCORES_NUMERIC",
            "dimensions": [{"field": "name"}],
            "metrics": [{"measure": "value", "agg": "p50"}],
            "filters": [_STEP_SCORES],
            "chart_type": "HORIZONTAL_BAR",
            "chart_config": {"type": "HORIZONTAL_BAR", "row_limit": 50, "show_value_labels": True},
        },
        6,
        2,
        6,
        6,
    ),
    (
        {
            "name": "Tool calls by tool",
            "view": "OBSERVATIONS",
            "dimensions": [{"field": "tags"}],
            "metrics": [{"measure": "count", "agg": "count"}],
            "filters": [_names("tool_call_event")],
            "chart_type": "VERTICAL_BAR",
            "chart_config": {"type": "VERTICAL_BAR", "row_limit": 20},
        },
        0,
        8,
        4,
        5,
    ),
    (
        _number(
            "Tool dependency-failure rate",
            "SCORES_NUMERIC",
            "value",
            "avg",
            [_names("tool_call_failed")],
        ),
        4,
        8,
        2,
        2,
    ),
    (
        _number(
            "Tool retries (sum)", "SCORES_NUMERIC", "value", "sum", [_names("tool_call_retries")]
        ),
        6,
        8,
        2,
        2,
    ),
    (
        _number(
            "Retrieval hits (mean)",
            "SCORES_NUMERIC",
            "value",
            "avg",
            [_names("retrieval_hit_count")],
        ),
        8,
        8,
        2,
        2,
    ),
    (
        _number(
            "Reranked-true rate", "SCORES_NUMERIC", "value", "avg", [_names("retrieval_reranked")]
        ),
        10,
        8,
        2,
        2,
    ),
    (
        _number(
            "Extraction located rate",
            "SCORES_NUMERIC",
            "value",
            "avg",
            [_names("extraction_located_rate")],
        ),
        4,
        10,
        2,
        3,
    ),
    (
        _number(
            "Extraction mean confidence",
            "SCORES_NUMERIC",
            "value",
            "avg",
            [_names("extraction_mean_confidence")],
        ),
        6,
        10,
        2,
        3,
    ),
    (
        {
            "name": "LLM tokens and cost per encounter (trace)",
            "view": "OBSERVATIONS",
            "dimensions": [{"field": "traceId"}],
            "metrics": [
                {"measure": "totalCost", "agg": "sum"},
                {"measure": "totalTokens", "agg": "sum"},
            ],
            "filters": [
                {
                    "type": "stringOptions",
                    "column": "type",
                    "operator": "any of",
                    "value": ["GENERATION"],
                }
            ],
            "chart_type": "PIVOT_TABLE",
            "chart_config": {
                "type": "PIVOT_TABLE",
                "row_limit": 20,
                "defaultSort": {"column": "sum_totalCost", "order": "DESC"},
            },
        },
        8,
        10,
        4,
        5,
    ),
]


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def build_sql() -> str:
    statements = ["BEGIN;"]
    placements = []
    for index, (widget, x, y, width, height) in enumerate(WIDGETS):
        widget_id = f"w2-ops-widget-{index:02d}"
        statements.append(f"DELETE FROM dashboard_widgets WHERE id = {_sql_literal(widget_id)};")
        statements.append(
            "INSERT INTO dashboard_widgets (id, project_id, name, description, view, "
            "dimensions, metrics, filters, chart_type, chart_config, min_version) VALUES ("
            + ", ".join(
                [
                    _sql_literal(widget_id),
                    _sql_literal(PROJECT_ID),
                    _sql_literal(widget["name"]),
                    _sql_literal("Week 2 operations (docs/w2/OBSERVABILITY.md)"),
                    _sql_literal(widget["view"]) + '::"DashboardWidgetViews"',
                    _sql_literal(json.dumps(widget["dimensions"])) + "::jsonb",
                    _sql_literal(json.dumps(widget["metrics"])) + "::jsonb",
                    _sql_literal(json.dumps(widget["filters"])) + "::jsonb",
                    _sql_literal(widget["chart_type"]) + '::"DashboardWidgetChartType"',
                    _sql_literal(json.dumps(widget["chart_config"])) + "::jsonb",
                    "1",
                ]
            )
            + ");"
        )
        placements.append(
            {
                "type": "widget",
                "id": f"w2-ops-place-{index:02d}",
                "widgetId": widget_id,
                "x": x,
                "y": y,
                "x_size": width,
                "y_size": height,
            }
        )
    statements.append(f"DELETE FROM dashboards WHERE id = {_sql_literal(DASHBOARD_ID)};")
    statements.append(
        "INSERT INTO dashboards (id, project_id, name, description, definition, filters) VALUES ("
        + ", ".join(
            [
                _sql_literal(DASHBOARD_ID),
                _sql_literal(PROJECT_ID),
                _sql_literal("Clinical Co-pilot: Week 2 operations"),
                _sql_literal(
                    "Requests, errors, latency overall and by step, tool calls and retries, "
                    "verification, extraction, retrieval, cost per encounter. "
                    "docs/w2/OBSERVABILITY.md"
                ),
                _sql_literal(json.dumps({"widgets": placements})) + "::jsonb",
                "'[]'::jsonb",
            ]
        )
        + ");"
    )
    statements.append("COMMIT;")
    return "\n".join(statements) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--print-sql", action="store_true")
    mode.add_argument("--apply", action="store_true", help="local dev Langfuse only")
    parser.add_argument("--container", default=POSTGRES_CONTAINER)
    args = parser.parse_args()

    sql = build_sql()
    if args.print_sql:
        sys.stdout.write(sql)
        return 0
    result = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            args.container,
            "psql",
            "-U",
            "postgres",
            "-v",
            "ON_ERROR_STOP=1",
            "-q",
        ],
        input=sql,
        text=True,
        capture_output=True,
        check=False,
    )
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    if result.returncode == 0:
        print(f"dashboard {DASHBOARD_ID!r}: {len(WIDGETS)} widgets in project {PROJECT_ID!r}")
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
