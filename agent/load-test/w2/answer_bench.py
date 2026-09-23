#!/usr/bin/env python3
"""
The `/w2/ask` answer step over one uploaded lab report, N runs in sequence —
work order 19's before/after measurement. LIVE: real OpenEMR FHIR reads under
the eval physician's token, and one real Anthropic call per run.

    cd agent && python load-test/w2/answer_bench.py \
        --sample lab-clean-001 --runs 3 --out ../docs/w2/evidence/load/answer-<label>.json

Adds no instrumentation. Every number is read back from events the ask path
already emits (feat/w2-observability):

  step_event            pipeline=supervisor, step=answer — the answer node's
                        wall-clock, which is the chart read, the drafting call,
                        verification and rendering.
  tool_call_event       tool=llm — the drafting call alone.
  llm_usage_event       input/output tokens and stop_reason of that call.
  w2_ask_turn_completed total_ms, cost, claim counts by source type.

Vision is replayed (the recorded transcription), or rebuilt from the
sample's ground truth with `--vision ground-truth` for a staged sample with
no recording (lab-critical-potassium-007). Either way extraction costs
nothing here; the text layer and the locate step run live on the PDF.

Prints JSON: one row per run, then p50 and worst. Chart content is never
printed; the answer text is written only with `--answer-out`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import uuid
import warnings
from pathlib import Path
from typing import Any

AGENT_ROOT = Path(__file__).resolve().parents[2]
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))

from evals.harness import EvalConfig, jwt_claims, load_dotenv  # noqa: E402

load_dotenv(AGENT_ROOT / ".env")
warnings.filterwarnings("ignore", message="Unverified HTTPS request")

from structlog.testing import capture_logs  # noqa: E402

from app.graph import supervisor  # noqa: E402
from app.workers.document_store import DocumentStore  # noqa: E402
from app.workers.intake_extractor.chart import fhir_chart_reader  # noqa: E402
from scripts.w2_e2e_ask import mint_token  # noqa: E402
from tests.intake_extractor_support import (  # noqa: E402
    doc_kind,
    ground_truth_vision,
    patient_uuid,
    pdf_bytes,
    recorded_vision,
)

DEFAULT_QUESTION = "What were the results on the uploaded lab report?"


def _event(logs: list[dict[str, Any]], event_type: str, **match: Any) -> dict[str, Any] | None:
    for entry in logs:
        if entry.get("event_type") != event_type:
            continue
        data = entry.get("event_data") or {}
        if all(data.get(k) == v for k, v in match.items()):
            return data
    return None


async def one_run(
    token: str, user: str, sample: str, question: str, vision: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    extractor = ground_truth_vision(sample) if vision == "ground-truth" else recorded_vision(sample)
    documents = DocumentStore(vision=extractor)
    patient = patient_uuid(sample)
    with capture_logs():  # the upload's own events are not this measurement
        stored, _, _ = documents.accept(
            patient, pdf_bytes(sample), doc_kind(sample), chart_reader=fhir_chart_reader(token)
        )
        report = await documents.wait(stored.document.document_id)
    assert report is not None and report.output.payload is not None
    with capture_logs() as logs:
        response = await supervisor.run_ask_turn(
            token,
            user,
            patient,
            f"answer-bench-{uuid.uuid4().hex[:12]}",
            question,
            [stored.document.document_id],
            documents=documents,
            retriever=lambda: None,
        )
    body = response.model_dump(mode="json")
    step = _event(logs, "step_event", pipeline="supervisor", step="answer") or {}
    llm = _event(logs, "tool_call_event", tool="llm") or {}
    usage = _event(logs, "llm_usage_event") or {}
    turn = _event(logs, supervisor.TURN_EVENT) or {}
    answer = (body.get("answer_view") or {}).get("answer") or {}
    row = {
        "status": body["status"],
        "rows_on_report": len(report.output.payload.results),
        "answer_step_ms": step.get("latency_ms"),
        "llm_call_ms": llm.get("latency_ms"),
        "total_ms": turn.get("total_ms"),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "stop_reason": usage.get("stop_reason"),
        "cost_usd": turn.get("llm_cost_usd"),
        "claims": turn.get("claims"),
        "scope": turn.get("answer_scope"),
        "patient_fact_lines": len(answer.get("patient_facts", [])),
        "critical_values": [
            (c["analyte"], c["direction"], c["stated"]) for c in body["critical_values"]
        ],
        "unverified": len(body["unverified"]),
        "unsupported_disclosed": len(answer.get("unsupported", [])),
    }
    return row, body


def _summary(rows: list[dict[str, Any]], key: str) -> dict[str, float | None]:
    values = [row[key] for row in rows if isinstance(row.get(key), int | float)]
    if not values:
        return {"p50": None, "worst": None}
    return {"p50": statistics.median(values), "worst": max(values)}


async def main_async(args: argparse.Namespace) -> dict[str, Any]:
    cfg = EvalConfig.from_env()
    if not (cfg.client_id and cfg.client_secret):
        raise SystemExit("FHIR_CLIENT_ID / FHIR_CLIENT_SECRET not set; this run is live")
    token = await mint_token(cfg)
    user = jwt_claims(token)["sub"]
    rows = []
    for index in range(args.runs):
        row, body = await one_run(token, user, args.sample, args.question, args.vision)
        rows.append(row)
        print(f"run {index + 1}/{args.runs}: {json.dumps(row)}", file=sys.stderr)
        if args.answer_out and index == 0:
            args.answer_out.write_text(json.dumps(body, indent=2))
    return {
        "sample": args.sample,
        "question": args.question,
        "runs": rows,
        "summary": {
            key: _summary(rows, key)
            for key in (
                "answer_step_ms",
                "llm_call_ms",
                "total_ms",
                "input_tokens",
                "output_tokens",
                "cost_usd",
            )
        },
        "total_cost_usd": round(sum(row["cost_usd"] or 0 for row in rows), 4),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--sample", default="lab-clean-001")
    parser.add_argument("--question", default=DEFAULT_QUESTION)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--vision", choices=("recorded", "ground-truth"), default="recorded")
    parser.add_argument("--answer-out", type=Path, help="first run's full AskResponse (has PHI)")
    parser.add_argument("--out", type=Path, help="write the JSON here as well as to stdout")
    args = parser.parse_args()
    text = json.dumps(asyncio.run(main_async(args)), indent=2)
    if args.out:
        args.out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
