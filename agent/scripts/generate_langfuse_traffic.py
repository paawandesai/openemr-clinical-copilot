#!/usr/bin/env python3
"""
Fill a fresh Langfuse instance with a representative Week 2 event mix, so
every widget of the operations dashboard has data. One command, no OpenEMR,
no Anthropic call:

    cd agent
    set -a; . observability/agent-langfuse.env; set +a
    python scripts/generate_langfuse_traffic.py            # ~40 encounters

What is real and what is synthesized — read this before quoting a number
from the result:

- **Real code, real exporter** (`--no-real` skips both):
  - the intake extractor on the six committed doc-factory samples. The
    vision call is replayed from ground truth, and text layer, OCR, locate
    and the checks all run. Located-rate and confidence scores are genuine.
  - the evidence retriever over the 30 concept-only dev queries, with the
    local embedder and reranker. Hits and reranked-rate are genuine. The
    models load from the local cache and are downloaded on first use; if
    they cannot load, this part is skipped and says so.
- **Synthesized, through the real event models** (so every shape is
  validated): HTTP requests, pipeline steps (supervisor turns included), FHIR and LLM tool calls,
  verification and latency events, and LLM token usage for encounters.
  Values are drawn from the distributions measured in docs/w2/LOAD_TEST.md,
  and include a small share of FHIR 500s, 5xx responses, SDK retries and
  failed-then-disclosed claims, so the error widgets are not empty either.

This is demonstration data for a dashboard, never a measurement. Session
keys are `synthetic-user:synthetic-patient-NNN:synthetic-session-NNN`: no
UUID, no name, nothing that looks like a real identifier. Nothing is
written to disk unless `--log PATH` is given, and that file belongs under
the gitignored `agent/logs/`, never in a commit.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import itertools
import json
import os
import random
import sys
import uuid
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AGENT))

from app.middleware import langfuse_export  # noqa: E402
from app.middleware.correlation import correlation_scope  # noqa: E402
from app.middleware.logging import configure_logging, log_event  # noqa: E402
from app.models.fhir_resources import FhirResourceType  # noqa: E402
from app.models.metrics_events import (  # noqa: E402
    ClaimDisclosureEvent,
    ClaimVerifiedEvent,
    LatencyEvent,
    RetrievalEvent,
    UseCase,
)
from app.models.telemetry_events import (  # noqa: E402
    HTTP_REQUEST_EVENT,
    STEP_EVENT,
    TOOL_CALL_EVENT,
    HttpRequestEvent,
    Pipeline,
    StepEvent,
    StepOutcome,
    Tool,
    ToolCallEvent,
)

MODEL = "claude-opus-5"
_ANSWERS = itertools.count()
_ASKS = itertools.count()
_LLM_CALLS = itertools.count()
BRIEF_TYPES = (
    FhirResourceType.CONDITION,
    FhirResourceType.MEDICATION_REQUEST,
    FhirResourceType.ALLERGY_INTOLERANCE,
)


def _emit(event_type: str, model, *, use_case=None, key=None) -> None:
    log_event(event_type, model.model_dump(mode="json"), use_case=use_case, patient_session_key=key)


class Encounter:
    """One synthetic encounter: its correlation id, session key and clock."""

    def __init__(self, rng: random.Random, index: int) -> None:
        self.rng = rng
        self.correlation_id = str(uuid.uuid4())
        self.key = f"synthetic-user:synthetic-patient-{index:03d}:synthetic-session-{index:03d}"
        self.clock = 1_790_000_000.0 + index * 120

    def step(
        self,
        pipeline: Pipeline,
        name: str,
        ms: float,
        outcome=StepOutcome.OK,
        error_type=None,
        counts=None,
        use_case=None,
    ) -> None:
        _emit(
            STEP_EVENT,
            StepEvent(
                pipeline=pipeline,
                step=name,
                outcome=outcome,
                latency_ms=round(ms, 3),
                error_type=error_type,
                counts=counts or {},
                correlation_id=self.correlation_id,
            ),
            use_case=use_case,
            key=self.key,
        )

    def tool(
        self,
        tool: Tool,
        target: str | None,
        ms: float,
        *,
        status: int | None,
        ok: bool = True,
        retries: int = 0,
        error_type=None,
        use_case=None,
    ) -> None:
        _emit(
            TOOL_CALL_EVENT,
            ToolCallEvent(
                tool=tool,
                target=target,
                outcome=StepOutcome.OK if ok else StepOutcome.ERROR,
                latency_ms=round(ms, 3),
                attempts=retries + 1,
                retries=retries,
                error_type=error_type,
                status_code=status,
                correlation_id=self.correlation_id,
            ),
            use_case=use_case,
            key=self.key,
        )

    def request(self, method: str, route: str, status: int, ms: float) -> None:
        _emit(
            HTTP_REQUEST_EVENT,
            HttpRequestEvent(
                method=method,
                route=route,
                status_code=status,
                latency_ms=round(ms, 3),
                correlation_id=self.correlation_id,
            ),
        )


def _answer(enc: Encounter, use_case: UseCase, pipeline: Pipeline, route: str) -> None:
    """One brief or conversation turn: FHIR reads, draft, verify, render."""
    rng = enc.rng
    total = 0.0
    if pipeline is Pipeline.CONVERSATION:
        refused = rng.random() < 0.10
        enc.step(
            pipeline,
            "routing",
            rng.uniform(0.05, 0.4),
            StepOutcome.REFUSED if refused else StepOutcome.OK,
            counts={"refused": refused, "rules": rng.randint(1, 3)},
            use_case=use_case,
        )
        if refused:
            enc.request("POST", route, 200, rng.uniform(150, 300))
            return

    # FHIR: Patient, then the three base resources. Every twelfth answer
    # meets an OpenEMR 500 (the saturation failure LOAD_TEST.md §1 measured),
    # on a fixed schedule rather than a coin flip: at 5% a seed can draw no
    # failure at all, which leaves the error widgets empty.
    fhir_failed = next(_ANSWERS) % 12 == 5
    retrieve_ms = 0.0
    for index, target in enumerate(("Patient", *[t.value for t in BRIEF_TYPES])):
        ms = rng.uniform(120, 420)
        retrieve_ms = max(retrieve_ms, ms)
        if fhir_failed and index == 2:
            enc.tool(
                Tool.FHIR_READ,
                target,
                ms,
                status=500,
                ok=False,
                error_type="FhirServerError",
                use_case=use_case,
            )
            enc.step(
                pipeline,
                "retrieve",
                retrieve_ms,
                StepOutcome.ERROR,
                error_type="FhirServerError",
                use_case=use_case,
            )
            enc.request("POST", route, 502, retrieve_ms + rng.uniform(5, 20))
            return
        enc.tool(Tool.FHIR_READ, target, ms, status=200, use_case=use_case)
        if target != "Patient":
            _emit(
                "retrieval_event",
                RetrievalEvent(
                    resource_type=FhirResourceType(target),
                    correlation_id=enc.correlation_id,
                    use_case=use_case,
                ),
                use_case=use_case,
                key=enc.key,
            )
    enc.step(pipeline, "retrieve", retrieve_ms, use_case=use_case)
    total += retrieve_ms

    # Draft: real-LLM latency on this project's charts runs 4-19 s.
    draft_ms = rng.uniform(3_800, 18_000)
    # Every twentieth model call took one SDK retry: fixed, like the FHIR
    # failures, so the retries widget cannot come up empty by chance.
    retries = 1 if next(_LLM_CALLS) % 20 == 7 else 0
    enc.tool(Tool.LLM, MODEL, draft_ms, status=None, retries=retries, use_case=use_case)
    input_tokens = rng.randint(1_500, 13_000)
    log_event(
        "llm_usage_event",
        {
            "correlation_id": enc.correlation_id,
            "model": MODEL,
            "input_tokens": input_tokens,
            "output_tokens": rng.randint(300, 3_300),
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "latency_ms": round(draft_ms),
        },
        use_case=use_case,
        patient_session_key=enc.key,
    )
    enc.step(pipeline, "draft", draft_ms, use_case=use_case)
    total += draft_ms

    claims = rng.randint(3, 12)
    verify_ms = rng.uniform(0.2, 5.0)
    for number in range(claims):
        passed = rng.random() > 0.03
        resource = rng.choice(BRIEF_TYPES)
        source_id = f"synthetic-{resource.value.lower()}-{number:02d}"
        _emit(
            "claim_verified_event",
            ClaimVerifiedEvent(
                source_id=source_id,
                resource_type=resource,
                passed=passed,
                disclosed_as_unverified=not passed,
                correlation_id=enc.correlation_id,
                use_case=use_case,
                patient_session_key=enc.key,
            ),
            use_case=use_case,
            key=enc.key,
        )
        if not passed:
            _emit(
                "claim_disclosure_event",
                ClaimDisclosureEvent(
                    source_id=source_id,
                    resource_type=resource,
                    field="display",
                    disclosed=True,
                    correlation_id=enc.correlation_id,
                    use_case=use_case,
                    patient_session_key=enc.key,
                ),
                use_case=use_case,
                key=enc.key,
            )
    enc.step(pipeline, "verify", verify_ms, counts={"claims": claims}, use_case=use_case)
    render_ms = rng.uniform(0.1, 1.0)
    enc.step(pipeline, "render", render_ms, use_case=use_case)
    total += verify_ms + render_ms
    _emit(
        "latency_event",
        LatencyEvent(
            use_case=use_case,
            trigger_ts=enc.clock,
            rendered_ts=enc.clock + total / 1000,
            correlation_id=enc.correlation_id,
        ),
        use_case=use_case,
        key=enc.key,
    )
    enc.request("POST", route, 200, total + rng.uniform(5, 40))


def _ask(enc: Encounter) -> None:
    """
    One /w2/ask turn through the supervisor (D40): route by rule, maybe
    retrieve guideline evidence, answer. Latencies follow the real-route
    run in docs/w2/LOAD_TEST.md: about 10 s for a guideline question, and
    now and then a very long answer over a whole lab report (130 s measured,
    one 35k-in / 11.5k-out call).
    """
    rng = enc.rng
    use_case = UseCase.FOLLOW_UP_DRILLING
    total = 0.0
    enc.step(Pipeline.SUPERVISOR, "supervisor", rng.uniform(0.2, 1.5), use_case=use_case)
    if rng.random() < 0.5:
        retrieval_ms = rng.uniform(200, 600)
        enc.tool(Tool.GUIDELINE_RETRIEVER, "hybrid", retrieval_ms, status=None, use_case=use_case)
        enc.step(Pipeline.SUPERVISOR, "evidence_retriever", retrieval_ms, use_case=use_case)
        enc.step(Pipeline.SUPERVISOR, "supervisor", rng.uniform(0.2, 1.5), use_case=use_case)
        total += retrieval_ms
    long_answer = next(_ASKS) % 8 == 3
    draft_ms = rng.uniform(90_000, 135_000) if long_answer else rng.uniform(8_000, 14_000)
    input_tokens = rng.randint(30_000, 36_000) if long_answer else rng.randint(9_000, 14_000)
    output_tokens = rng.randint(9_000, 12_000) if long_answer else rng.randint(400, 2_000)
    ask_retries = 1 if next(_LLM_CALLS) % 20 == 7 else 0
    enc.tool(Tool.LLM, MODEL, draft_ms, status=None, retries=ask_retries, use_case=use_case)
    log_event(
        "llm_usage_event",
        {
            "correlation_id": enc.correlation_id,
            "model": MODEL,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "latency_ms": round(draft_ms),
        },
        use_case=use_case,
        patient_session_key=enc.key,
    )
    enc.step(Pipeline.SUPERVISOR, "answer", draft_ms + rng.uniform(5, 40), use_case=use_case)
    total += draft_ms
    enc.request("POST", "/w2/ask", 200, total + rng.uniform(20, 80))


def _upload(enc: Encounter) -> None:
    rng = enc.rng
    enc.step(
        Pipeline.DOCUMENTS,
        "upload",
        rng.uniform(0.3, 3.0),
        counts={"byte_length": rng.randint(20_000, 400_000), "duplicate": False},
    )
    enc.request("POST", "/documents", 200, rng.uniform(2, 12))
    for poll in range(rng.randint(2, 5)):
        final = poll >= 1 and rng.random() < 0.5
        enc.step(
            Pipeline.DOCUMENTS,
            "extraction_status",
            rng.uniform(0.1, 0.5),
            StepOutcome.OK if final else StepOutcome.PENDING,
            counts={"complete": final},
        )
        enc.request("GET", "/documents/{document_id}/extraction", 200, rng.uniform(1, 4))
        if final:
            break


def synthesize(encounters: int, seed: int) -> None:
    rng = random.Random(seed)
    for index in range(encounters):
        enc = Encounter(rng, index)
        with correlation_scope(enc.correlation_id):
            enc.request("GET", "/ready", 200, rng.uniform(1, 30))
            if rng.random() < 0.3:
                _upload(enc)
            if rng.random() < 0.6:
                _answer(enc, UseCase.PRE_VISIT_BRIEF, Pipeline.PRE_VISIT_BRIEF, "/pre-visit-brief")
            for _ in range(rng.randint(0, 2)):
                _answer(
                    enc,
                    UseCase.FOLLOW_UP_DRILLING,
                    Pipeline.CONVERSATION,
                    "/pre-visit-brief/conversation",
                )
            if rng.random() < 0.4:
                _ask(enc)
    print(f"synthesized {encounters} encounters", file=sys.stderr)


async def _extract_samples() -> int:
    from app.workers.intake_extractor import attach_and_extract
    from tests.intake_extractor_support import (
        SAMPLES,
        chart_for,
        doc_kind,
        ground_truth_vision,
        patient_uuid,
        pdf_bytes,
    )

    for sample in SAMPLES:

        async def chart_reader(_: str, sample: str = sample):
            return chart_for(sample)

        await attach_and_extract(
            patient_uuid(sample),
            pdf_bytes(sample),
            doc_kind(sample),
            vision=ground_truth_vision(sample),
            chart_reader=chart_reader,
            correlation_id=str(uuid.uuid4()),
        )
    return len(SAMPLES)


def real_extraction() -> None:
    count = asyncio.run(_extract_samples())
    print(f"extracted {count} committed samples (vision replayed)", file=sys.stderr)


def real_retrieval() -> None:
    try:
        from app.models.w2.workers import ClinicalConcept, EvidenceRetrieverInput
        from app.retrieval.preload import preload_retrieval
        from app.retrieval.worker import run_evidence_retriever

        retriever = preload_retrieval(with_reranker=True)
    except Exception as error:  # noqa: BLE001 - optional part; say why and go on
        print(f"retrieval skipped: models could not load ({type(error).__name__})", file=sys.stderr)
        return
    queries = [
        json.loads(line)["query"]
        for line in (AGENT / "evals/w2/staging/retrieval_dev.jsonl").read_text().splitlines()
        if line.strip()
    ]
    for query in queries:
        correlation_id = str(uuid.uuid4())
        with correlation_scope(correlation_id):
            run_evidence_retriever(
                EvidenceRetrieverInput(
                    concepts=[ClinicalConcept(text=query[:120])],
                    top_k=5,
                    rerank=True,
                    correlation_id=correlation_id,
                ),
                retriever,
            )
    print(f"retrieved for {len(queries)} dev queries", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--encounters", type=int, default=40)
    parser.add_argument("--seed", type=int, default=14)
    parser.add_argument(
        "--no-real", action="store_true", help="skip the real extractor and retriever runs"
    )
    parser.add_argument("--log", help="also keep the stdout event lines here (agent/logs/ only)")
    args = parser.parse_args()

    if not langfuse_export.export_configured():
        print(
            "LANGFUSE_BASE_URL / LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY are not set.\n"
            "  set -a; . observability/agent-langfuse.env; set +a",
            file=sys.stderr,
        )
        return 2

    sink = open(args.log, "a", encoding="utf-8") if args.log else open(os.devnull, "w")  # noqa: SIM115
    with sink, contextlib.redirect_stdout(sink):
        configure_logging()
        synthesize(args.encounters, args.seed)
        if not args.no_real:
            real_extraction()
            real_retrieval()
        langfuse_export.flush_export()
        try:
            langfuse_export.check_export_health()
            status = "Langfuse accepted the keys"
        except langfuse_export.ObservabilityExportUnavailable as error:
            status = f"export health: {error}"
    print(f"done; {status}. Open the dashboard (docs/w2/OBSERVABILITY.md §6).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
