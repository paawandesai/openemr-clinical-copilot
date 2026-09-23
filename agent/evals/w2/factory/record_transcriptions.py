"""
Record real vision transcriptions of the factory samples, for replay.

    cd agent && python -m evals.w2.factory.record_transcriptions lab-clean-001 ...

**Spends money**: one Anthropic vision call per sample (about $0.13 per
page at the time of writing). Needs credentials the SDK can find.

Each sample's transcription is written to
`agent/evals/w2/staging/transcriptions/<sample>.transcription.json`: the
model's raw output exactly as the worker parsed it
(`app/workers/intake_extractor/vision_schema.py`), plus the provenance a
replay needs to know whether it is still valid — the requested and served
model, the extractor version, a hash of the prompt and output schema, the
document's SHA-256 (the doc-factory's own digest, so a regenerated PDF is
detected), token counts, cost and latency.

Replaying one (`tests/intake_extractor_support.recorded_vision`) runs the
worker end to end on real model output with no network — which is what the
pending W2-B cases need instead of the ground-truth stub, whose output is
correct by construction.

Staging is ungated (D8). Promoting a transcription into the gate's fixtures
is a gate change for a `W2_GATE_SESSION=1` session, not something this
script does.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from app.graph.intake_extractor import EXTRACTOR_VERSION
from app.models.w2.documents import DocumentKind
from app.reasoning.draft_claims import MODEL
from app.workers.intake_extractor.vision import extract_with_claude

# Gated: the gate's staleness check compares against this same function, so
# it lives where an implementation session cannot edit it (samples.py).
from evals.w2.gate.samples import prompt_fingerprint

AGENT_ROOT = Path(__file__).resolve().parents[3]
SAMPLES_DIR = AGENT_ROOT / "evals/w2/factory/output/samples"
STAGING_DIR = AGENT_ROOT / "evals/w2/staging/transcriptions"

TRANSCRIPTION_FORMAT = "w2-staging/vision-transcription/1.0.0"


async def record(sample: str) -> dict[str, object]:
    ground_truth = json.loads((SAMPLES_DIR / sample / f"{sample}.ground_truth.json").read_text())
    kind = DocumentKind(ground_truth["scenario"]["doc_type"])
    pdf = (SAMPLES_DIR / sample / f"{sample}.pdf").read_bytes()
    result = await extract_with_claude(kind, pdf, ground_truth["document"]["page_count"])
    usage = result.usage
    return {
        "format": TRANSCRIPTION_FORMAT,
        "sample": sample,
        "doc_type": kind.value,
        "document_sha256": hashlib.sha256(pdf).hexdigest(),
        "requested_model": MODEL,
        "served_models": usage.served_models,
        "extractor_version": EXTRACTOR_VERSION,
        "prompt_fingerprint": prompt_fingerprint(kind),
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "usage": {
            "calls": usage.calls,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_creation_input_tokens": usage.cache_creation_input_tokens,
            "cache_read_input_tokens": usage.cache_read_input_tokens,
            "latency_ms": usage.latency_ms,
            "cost_usd": usage.cost_usd,
        },
        "raw": json.loads(result.raw.model_dump_json()),
    }


async def main(samples: list[str]) -> int:
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    total = 0.0
    for sample in samples:
        recorded = await record(sample)
        path = STAGING_DIR / f"{sample}.transcription.json"
        path.write_text(json.dumps(recorded, indent=2, sort_keys=True) + "\n")
        usage = recorded["usage"]
        total += usage["cost_usd"] or 0.0
        print(
            f"{sample}: {usage['input_tokens']} in / {usage['output_tokens']} out, "
            f"${usage['cost_usd']:.5f}, {usage['latency_ms']} ms -> {path.relative_to(AGENT_ROOT)}"
        )
    print(f"total: ${total:.5f}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
