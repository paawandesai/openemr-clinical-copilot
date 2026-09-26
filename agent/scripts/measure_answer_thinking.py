#!/usr/bin/env python3
"""
The answer step's thinking, measured: the same questions over the same
extracted documents, under each thinking configuration, interleaved round by
round so that a provider-side swing lands on every configuration alike.
LIVE: real OpenEMR FHIR, real Anthropic. Costs money (about $0.1-0.25 a run).

    cd agent && FHIR_BASE_URL=... FHIR_CLIENT_ID=... FHIR_CLIENT_SECRET=... \\
        python scripts/measure_answer_thinking.py --rounds 3 --out results.json

Cases (the two questions the work order names):
  whole-report    "What were the results on the uploaded lab report?" over
                  lab-critical-potassium-007, whose critical potassium must be
                  stated. That sample has no recorded transcription, so its
                  vision call is live, once, before any run.
  glucose-uspstf  scripts/w2_e2e_ask.py's default question over lab-clean-001,
                  with its recorded transcription.

Each document is extracted once, through `DocumentStore.accept` (the code
POST /documents runs) under a real clinician token; every run is a new
session over that one extraction, so extraction never varies between runs.

Configurations (app/reasoning/draft_answer.ANSWER_THINKING / ANSWER_EFFORT,
set per run):
  adaptive-medium   what main ran before this change
  disabled-medium   thinking off, same effort
  adaptive-low      thinking on, lower effort

Recorded per run: turn and answer-step latency, the answer call's output
tokens, verified claims and citations by source type, unverified claims,
and whether each critical value was stated. Nothing drafted is printed.

`--path brief` and `--path conversation` measure the Week 1 paths the same
way (draft_claims.BRIEF_THINKING, draft_follow_up.FOLLOW_UP_THINKING) on the
same patient: latency, output tokens, and verified vs failed claims from the
turn's claim_verified_events.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
import warnings
from pathlib import Path

AGENT_ROOT = Path(__file__).resolve().parents[1]
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))

from evals.harness import EvalConfig, jwt_claims, load_dotenv  # noqa: E402

load_dotenv(AGENT_ROOT / ".env")
warnings.filterwarnings("ignore", message="Unverified HTTPS request")

from structlog.testing import capture_logs  # noqa: E402

from app.graph import conversation, pre_visit_brief, supervisor  # noqa: E402
from app.reasoning import draft_answer, draft_claims, draft_follow_up  # noqa: E402
from app.retrieval.preload import preload_retrieval  # noqa: E402
from app.workers.document_store import DocumentStore  # noqa: E402
from app.workers.intake_extractor.chart import fhir_chart_reader  # noqa: E402
from scripts.w2_e2e_ask import DEFAULT_QUESTION, mint_token  # noqa: E402
from tests.intake_extractor_support import (  # noqa: E402
    doc_kind,
    patient_uuid,
    pdf_bytes,
    recorded_transcription,
    recorded_vision,
)

CASES = {
    "whole-report": (
        "lab-critical-potassium-007",
        "What were the results on the uploaded lab report?",
    ),
    "glucose-uspstf": ("lab-clean-001", DEFAULT_QUESTION),
}
CONFIGS = {
    "adaptive-medium": ({"type": "adaptive"}, "medium"),
    "disabled-medium": ({"type": "disabled"}, "medium"),
    "adaptive-low": ({"type": "adaptive"}, "low"),
}


async def extract(sample: str, token: str) -> tuple[DocumentStore, str]:
    vision = recorded_vision(sample) if recorded_transcription(sample) else None
    store = DocumentStore(vision=vision)
    stored, _, _ = store.accept(
        patient_uuid(sample),
        pdf_bytes(sample),
        doc_kind(sample),
        chart_reader=fhir_chart_reader(token),
    )
    report = await store.wait(stored.document.document_id)
    assert report is not None and report.output.status.value == "complete", sample
    located = sum(f.outcome == "located" for f in report.fields)
    print(
        f"extracted {sample}: {located}/{len(report.fields)} located, "
        f"vision={'recorded' if vision else 'live'}",
        flush=True,
    )
    return store, stored.document.document_id


def summarize(body: dict, logs: list[dict], wall_ms: int) -> dict:
    answer = (body.get("answer_view") or {}).get("answer") or {}
    claims = [c for group in ("patient_facts", "guideline_evidence") for c in answer.get(group, [])]
    by_source: dict[str, int] = {}
    for claim in claims:
        for citation in claim["citations"]:
            by_source[citation["source_type"]] = by_source.get(citation["source_type"], 0) + 1
    usage = [
        e["event_data"]
        for e in logs
        if e.get("event_type") == "llm_usage_event" and "worker" not in e["event_data"]
    ]
    turn = next((e["event_data"] for e in logs if e.get("event_type") == supervisor.TURN_EVENT), {})
    return {
        "status": body["status"],
        "wall_ms": wall_ms,
        "turn_ms": body.get("total_ms"),
        "answer_node_ms": (body.get("node_ms") or {}).get("answer"),
        "output_tokens": sum(u["output_tokens"] for u in usage),
        "input_tokens": sum(u["input_tokens"] for u in usage),
        "thinking": [u.get("thinking") for u in usage],
        "effort": [u.get("effort") for u in usage],
        "stop_reasons": [u.get("stop_reason") for u in usage],
        "verified_claims": len(claims),
        "citations_by_source": by_source,
        "unverified": len(body.get("unverified", [])),
        "unverified_reasons": sorted({u["reason"] for u in body.get("unverified", [])}),
        "unsupported": len(answer.get("unsupported", [])),
        "critical_values": [
            {"analyte": c["analyte"], "direction": c["direction"], "stated": c["stated"]}
            for c in body.get("critical_values", [])
        ],
        "limitations": body.get("limitations", []),
        "cost_usd": turn.get("llm_cost_usd"),
    }


async def main_async(rounds: int, configs: list[str], cases: list[str], out: Path | None) -> int:
    cfg = EvalConfig.from_env()
    if not (cfg.client_id and cfg.client_secret):
        raise SystemExit("FHIR_CLIENT_ID / FHIR_CLIENT_SECRET not set; this run is live")
    token = await mint_token(cfg)
    user = jwt_claims(token)["sub"]
    retriever = preload_retrieval()
    documents = {case: await extract(CASES[case][0], token) for case in cases}

    results = []
    for round_no in range(1, rounds + 1):
        for case in cases:
            store, document_id = documents[case]
            sample, question = CASES[case]
            for name in configs:
                thinking, effort = CONFIGS[name]
                draft_answer.ANSWER_THINKING, draft_answer.ANSWER_EFFORT = thinking, effort
                started = time.perf_counter()
                with capture_logs() as logs:
                    response = await supervisor.run_ask_turn(
                        token,
                        user,
                        patient_uuid(sample),
                        f"thinking-{case}-{name}-{round_no}-{os.getpid()}",
                        question,
                        [document_id],
                        documents=store,
                        retriever=lambda: retriever,
                    )
                row = {
                    "round": round_no,
                    "case": case,
                    "config": name,
                    **summarize(
                        response.model_dump(mode="json"),
                        logs,
                        round((time.perf_counter() - started) * 1000),
                    ),
                }
                results.append(row)
                crit = [
                    f"{c['analyte']}:{'stated' if c['stated'] else 'NOT stated'}"
                    for c in row["critical_values"]
                ]
                print(
                    f"r{round_no} {case:<14} {name:<16} {row['status']:<19} "
                    f"turn={row['turn_ms'] / 1000:6.1f}s out={row['output_tokens']:>5} "
                    f"verified={row['verified_claims']:>3} unverified={row['unverified']} "
                    f"cites={row['citations_by_source']} critical={crit}",
                    flush=True,
                )
                if out:
                    out.write_text(json.dumps(results, indent=2))

    print("\nmedian over rounds (turn s / output tokens / verified / unverified):")
    for case in cases:
        for name in configs:
            rows = [r for r in results if r["case"] == case and r["config"] == name]
            print(
                f"  {case:<14} {name:<16} "
                f"{statistics.median(r['turn_ms'] for r in rows) / 1000:6.1f}s "
                f"{statistics.median(r['output_tokens'] for r in rows):>6.0f} "
                f"{statistics.median(r['verified_claims'] for r in rows):>4.0f} "
                f"{max(r['unverified'] for r in rows):>3} (max)  "
                f"potassium stated in {sum(any('otassium' in c['analyte'] and c['stated'] for c in r['critical_values']) for r in rows)}/{len(rows)}"
            )
    return 0


CONVERSATION_QUESTION = "What medications is the patient on, and what were her latest labs?"


async def week1_async(path: str, rounds: int, configs: list[str], out: Path | None) -> int:
    """The brief or a conversation turn, per configuration, interleaved."""
    cfg = EvalConfig.from_env()
    token = await mint_token(cfg)
    user = jwt_claims(token)["sub"]
    patient = patient_uuid("lab-clean-001")
    results = []
    for round_no in range(1, rounds + 1):
        for name in configs:
            thinking, effort = CONFIGS[name]
            session = f"thinking-{path}-{name}-{round_no}-{os.getpid()}"
            started = time.perf_counter()
            with capture_logs() as logs:
                if path == "brief":
                    draft_claims.BRIEF_THINKING, draft_claims.EFFORT = thinking, effort
                    await pre_visit_brief.run_pre_visit_brief(
                        token, patient, f"{user}:{patient}:{session}"
                    )
                else:
                    draft_follow_up.FOLLOW_UP_THINKING, draft_follow_up.EFFORT = thinking, effort
                    await conversation.run_conversation_turn(
                        token, user, patient, session, CONVERSATION_QUESTION
                    )
            wall_ms = round((time.perf_counter() - started) * 1000)
            usage = [e["event_data"] for e in logs if e.get("event_type") == "llm_usage_event"]
            verified = [
                e["event_data"] for e in logs if e.get("event_type") == "claim_verified_event"
            ]
            row = {
                "round": round_no,
                "path": path,
                "config": name,
                "wall_ms": wall_ms,
                "llm_ms": sum(u["latency_ms"] for u in usage),
                "output_tokens": sum(u["output_tokens"] for u in usage),
                "thinking": [u.get("thinking") for u in usage],
                "claims_passed": sum(v["passed"] for v in verified),
                "claims_failed": sum(not v["passed"] for v in verified),
            }
            results.append(row)
            print(
                f"r{round_no} {path:<12} {name:<16} wall={wall_ms / 1000:6.1f}s llm={row['llm_ms'] / 1000:6.1f}s "
                f"out={row['output_tokens']:>5} passed={row['claims_passed']:>3} failed={row['claims_failed']}",
                flush=True,
            )
            if out:
                out.write_text(json.dumps(results, indent=2))
    print("\nmedian over rounds (wall s / output tokens / passed / failed max):")
    for name in configs:
        rows = [r for r in results if r["config"] == name]
        print(
            f"  {path:<12} {name:<16} {statistics.median(r['wall_ms'] for r in rows) / 1000:6.1f}s "
            f"{statistics.median(r['output_tokens'] for r in rows):>6.0f} "
            f"{statistics.median(r['claims_passed'] for r in rows):>4.0f} {max(r['claims_failed'] for r in rows):>3}"
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--path", choices=["answer", "brief", "conversation"], default="answer")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--configs", nargs="+", default=list(CONFIGS), choices=list(CONFIGS))
    parser.add_argument("--cases", nargs="+", default=list(CASES), choices=list(CASES))
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if args.path != "answer":
        return asyncio.run(week1_async(args.path, args.rounds, args.configs, args.out))
    return asyncio.run(main_async(args.rounds, args.configs, args.cases, args.out))


if __name__ == "__main__":
    raise SystemExit(main())
