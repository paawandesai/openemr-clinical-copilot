#!/usr/bin/env python3
"""
Scripted end-to-end run of the Week 2 ask path over one doc-factory sample —
work order 13's acceptance run. LIVE: real OpenEMR FHIR, real Anthropic.

    cd agent && python scripts/w2_e2e_ask.py [--sample lab-clean-001] [--out FILE]

What is real and what is replayed, stated because it is the whole point:

  upload        POST-equivalent: `DocumentStore.accept`, i.e. the same code
                POST /documents runs, with the real chart reader under a real
                clinician token (identity check, discrepancy check).
  vision        REPLAYED by default — the sample's recorded live transcription
                (evals/w2/staging/transcriptions/, recorded by
                evals/w2/factory/record_transcriptions.py from a real model
                call). Text layer, locate and every check run live on the PDF.
                `--live-vision` makes the real vision call instead (a staged
                sample such as lab-critical-potassium-007 has no recording).
  chart         LIVE — the Week 1 FHIR readers against FHIR_BASE_URL, under a
                password-grant token for the eval physician account.
  retrieval     LIVE, local — bge-small embeddings + local cross-encoder
                rerank over the committed corpus (D25). No network.
  drafting      LIVE — one Anthropic call (app/reasoning/draft_answer.py).
  verification  deterministic, as always.

Prints the routing trajectory, the handoffs, every citation by source type,
the rendered answer, and the turn's logged token/cost line, then exits
non-zero unless the answer carries at least one verified document citation
AND one verified guideline citation. Nothing it prints is logged anywhere.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import warnings
from pathlib import Path

import httpx

AGENT_ROOT = Path(__file__).resolve().parents[1]
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))

# Before app/ is imported: app/fhir/client.py reads FHIR_BASE_URL at import.
from evals.harness import SCOPE_SETS, EvalConfig, jwt_claims, load_dotenv  # noqa: E402

load_dotenv(AGENT_ROOT / ".env")
warnings.filterwarnings("ignore", message="Unverified HTTPS request")

from structlog.testing import capture_logs  # noqa: E402

from app.graph import supervisor  # noqa: E402
from app.models.w2.source_types import SourceType  # noqa: E402
from app.retrieval.preload import preload_retrieval  # noqa: E402
from app.workers.document_store import DocumentStore  # noqa: E402
from app.workers.intake_extractor.chart import fhir_chart_reader  # noqa: E402
from tests.intake_extractor_support import (  # noqa: E402
    doc_kind,
    patient_uuid,
    pdf_bytes,
    recorded_vision,
)

DEFAULT_SAMPLE = "lab-clean-001"
DEFAULT_QUESTION = (
    "What was her glucose on the uploaded lab report, and what does the USPSTF recommend "
    "about screening for prediabetes and type 2 diabetes?"
)


async def mint_token(cfg: EvalConfig) -> str:
    async with httpx.AsyncClient(verify=False, timeout=30) as http:
        response = await http.post(
            cfg.token_url,
            data={
                "grant_type": "password",
                "client_id": cfg.client_id,
                "client_secret": cfg.client_secret,
                "username": cfg.username,
                "password": cfg.password,
                "user_role": "users",
                "scope": " ".join(SCOPE_SETS["conversation"]),
            },
        )
    if response.status_code != 200:
        raise SystemExit(f"token request failed: HTTP {response.status_code}")
    return response.json()["access_token"]


async def run(sample: str, question: str, live_vision: bool) -> tuple[dict, list[dict]]:
    cfg = EvalConfig.from_env()
    if not (cfg.client_id and cfg.client_secret):
        raise SystemExit("FHIR_CLIENT_ID / FHIR_CLIENT_SECRET not set; this run is live")
    token = await mint_token(cfg)
    user = jwt_claims(token)["sub"]
    patient = patient_uuid(sample)

    documents = DocumentStore(vision=None if live_vision else recorded_vision(sample))
    retriever = preload_retrieval()

    with capture_logs() as logs:
        stored, accepted, _ = documents.accept(
            patient, pdf_bytes(sample), doc_kind(sample), chart_reader=fhir_chart_reader(token)
        )
        print(f"upload: document_id={stored.document.document_id[:16]}… status={accepted.status}")
        report = await documents.wait(stored.document.document_id)
        assert report is not None
        print(
            f"extraction: {report.output.status.value} extraction_id={report.output.extraction_id} "
            f"located={sum(f.outcome == 'located' for f in report.fields)}/{len(report.fields)} "
            f"identity_hard_flag={report.identity.hard_flag if report.identity else None}"
        )
        response = await supervisor.run_ask_turn(
            token,
            user,
            patient,
            f"w2-e2e-{os.getpid()}",
            question,
            [stored.document.document_id],
            documents=documents,
            retriever=lambda: retriever,
        )
    return response.model_dump(mode="json"), logs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--sample", default=DEFAULT_SAMPLE)
    parser.add_argument("--question", default=DEFAULT_QUESTION)
    parser.add_argument("--out", type=Path, help="write the full AskResponse JSON here")
    parser.add_argument(
        "--live-vision", action="store_true", help="make the real vision call (costs money)"
    )
    parser.add_argument(
        "--expect-critical",
        action="store_true",
        help="also require a stated critical value (H6) for acceptance",
    )
    args = parser.parse_args()

    body, logs = asyncio.run(run(args.sample, args.question, args.live_vision))

    print("\nrouting:")
    for decision in body["routing"]:
        print(f"  {decision['decision']:<9} {decision['reason_code']}")
    print("handoffs:")
    for handoff in body["handoffs"]:
        print(
            f"  #{handoff['sequence']} {handoff['node']}: {handoff['handed']} -> "
            f"{handoff['returned']} concepts={handoff['concept_count']} hits={handoff['hit_count']} "
            f"reranked={handoff['reranked']} guideline_ids={handoff['guideline_ids']}"
        )
    print(f"status: {body['status']}  reason: {body['reason_code']}")
    stated_critical = [c for c in body["critical_values"] if c["stated"]]
    print(
        "critical values: "
        + str(
            [
                (c["source_type"], c["analyte"], c["direction"], c["stated"])
                for c in body["critical_values"]
            ]
        )
    )
    print(f"limitations: {body['limitations']}")

    view = body["answer_view"] or {}
    answer = view.get("answer") or {}
    counts = {kind.value: 0 for kind in SourceType}
    print("\ncitations:")
    for group in ("patient_facts", "guideline_evidence"):
        for claim in answer.get(group, []):
            for citation in claim["citations"]:
                counts[citation["source_type"]] += 1
                where = (
                    f"page {citation['bbox']['page']} bbox=({citation['bbox']['x0']:.0f},"
                    f"{citation['bbox']['y0']:.0f},{citation['bbox']['x1']:.0f},"
                    f"{citation['bbox']['y1']:.0f})"
                    if citation.get("bbox")
                    else citation["page_or_section"]
                )
                quote = str(citation["quote_or_value"])
                print(
                    f"  [{citation['source_type']}] {citation['source_id'][:32]} "
                    f"{citation['field_or_chunk_id']} @ {where}: {quote[:90]!r}"
                )
    print(f"\nunverified: {[(u['source_type'], u['reason']) for u in body['unverified']]}")
    print(f"unsupported fields disclosed: {len(answer.get('unsupported', []))}")
    print("\n--- answer as rendered ---")
    print(body["message"])

    turn = next(
        (e["event_data"] for e in logs if e.get("event_type") == supervisor.TURN_EVENT), None
    )
    if turn is not None:
        print("\n--- w2_ask_turn_completed (logged line) ---")
        print(json.dumps(turn, indent=2))

    if args.out:
        args.out.write_text(json.dumps(body, indent=2))
        print(f"\nfull response written to {args.out}")

    ok = counts["document"] > 0 and counts["guideline"] > 0
    if args.expect_critical:
        ok = ok and bool(stated_critical)
    print(
        f"\nACCEPTANCE: document citations={counts['document']} guideline citations="
        f"{counts['guideline']} chart citations={counts['chart']} -> {'PASS' if ok else 'FAIL'}"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
