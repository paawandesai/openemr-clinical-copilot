#!/usr/bin/env python3
"""
Extraction at concurrency, in process — the expensive path, measured apart
from the ask path (D7 makes them different paths).

The intake-extractor is not on `main` yet (feat/w2-extractor), and
`POST /documents` on `main` is a stub that extracts nothing, so an HTTP load
test of the upload route measures the upload surface only (k6-upload.js).
This drives the real worker, `attach_and_extract`, directly, from a checkout
that has it:

    cd <checkout with app/workers/intake_extractor>/agent
    python <this file> --levels 1 10 50 --per-worker 2 \\
        --events logs/extraction-events.jsonl > extraction-bench.json

Two modes:

- **Default — vision replayed.** The vision call is the extractor's own test
  stub (`tests/intake_extractor_support.ground_truth_vision`), which returns
  the sample's perfect transcription at zero latency and zero cost. Every
  stage after it is real: PyMuPDF text layer, Tesseract OCR on the scanned
  samples, the locate step, the checks and the discrepancy pass. So this is
  the local CPU cost of extraction under concurrency — the part this host
  pays for — without Anthropic's latency, rate limits or bill.
- **`--live-vision N`** — N real vision calls, one at a time (not
  concurrently: 50 concurrent calls would measure Anthropic's rate limits
  and cost dollars, and the load-test convention since Week 1 is that real
  model calls are small and sequential). Gives the vision step's latency,
  tokens and cost per document.

The worker's own log lines (`intake_extraction_step`,
`intake_extraction_completed`, `llm_usage_event`) go to `--events` so the
dashboard can read them. Nothing here prints a field value.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import resource
import statistics
import sys
import time


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lower, upper = int(position), min(int(position) + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _cpu_seconds() -> float:
    own = resource.getrusage(resource.RUSAGE_SELF)
    children = resource.getrusage(resource.RUSAGE_CHILDREN)  # tesseract subprocesses
    return own.ru_utime + own.ru_stime + children.ru_utime + children.ru_stime


def _peak_rss_mib() -> float:
    # ru_maxrss is bytes on macOS, KiB on Linux.
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024


async def _one(sample: str, live: bool) -> dict:
    from app.workers.intake_extractor import attach_and_extract
    from tests.intake_extractor_support import (
        chart_for,
        doc_kind,
        ground_truth_vision,
        patient_uuid,
        pdf_bytes,
    )

    async def chart_reader(_: str):  # noqa: ANN202 - a ChartReader
        return chart_for(sample)

    kwargs = {"chart_reader": chart_reader}
    if not live:
        kwargs["vision"] = ground_truth_vision(sample)
    started = time.perf_counter()
    report = await attach_and_extract(
        patient_uuid(sample), pdf_bytes(sample), doc_kind(sample), **kwargs
    )
    elapsed = time.perf_counter() - started
    summary = report.log_summary()
    return {
        "sample": sample,
        "seconds": elapsed,
        "status": summary["status"],
        "step_ms": summary["step_ms"],
        "field_outcomes": summary["field_outcomes"],
        "mean_confidence": summary["mean_confidence"],
        "has_text_layer": summary["has_text_layer"],
        "input_tokens": summary["input_tokens"],
        "output_tokens": summary["output_tokens"],
        "cost_usd": summary["cost_usd"],
    }


async def _level(concurrency: int, per_worker: int, samples: list[str]) -> dict:
    queue = [samples[i % len(samples)] for i in range(concurrency * per_worker)]
    results: list[dict] = []

    async def worker(offset: int) -> None:
        for index in range(offset, len(queue), concurrency):
            results.append(await _one(queue[index], live=False))

    cpu_before, wall_before = _cpu_seconds(), time.perf_counter()
    await asyncio.gather(*(worker(i) for i in range(concurrency)))
    wall = time.perf_counter() - wall_before
    cpu = _cpu_seconds() - cpu_before

    seconds = [r["seconds"] for r in results]
    steps: dict[str, list[float]] = {}
    for r in results:
        for step, ms in r["step_ms"].items():
            steps.setdefault(step, []).append(ms)
    return {
        "concurrency": concurrency,
        "extractions": len(results),
        "failed": sum(1 for r in results if r["status"] != "complete"),
        "wall_seconds": round(wall, 3),
        "throughput_per_second": round(len(results) / wall, 3),
        "latency_seconds": {
            q: round(_percentile(seconds, v), 3)
            for q, v in (("p50", 0.5), ("p95", 0.95), ("p99", 0.99))
        }
        | {"max": round(max(seconds), 3)},
        "cpu_seconds": round(cpu, 2),
        "mean_cores_busy": round(cpu / wall, 2),
        "peak_rss_mib": round(_peak_rss_mib(), 1),
        "step_ms_p50": {k: round(_percentile(v, 0.5), 1) for k, v in sorted(steps.items())},
        "step_ms_p95": {k: round(_percentile(v, 0.95), 1) for k, v in sorted(steps.items())},
    }


async def _live(n: int, samples: list[str]) -> dict:
    runs = [await _one(samples[i % len(samples)], live=True) for i in range(n)]
    return {
        "mode": "live_vision_sequential",
        "runs": [
            {
                "sample": r["sample"],
                "status": r["status"],
                "seconds": round(r["seconds"], 2),
                "vision_ms": r["step_ms"].get("vision"),
                "input_tokens": r["input_tokens"],
                "output_tokens": r["output_tokens"],
                "cost_usd": r["cost_usd"],
                "field_outcomes": r["field_outcomes"],
            }
            for r in runs
        ],
        "mean_seconds": round(statistics.fmean(r["seconds"] for r in runs), 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--levels", type=int, nargs="+", default=[1, 10, 50])
    parser.add_argument("--per-worker", type=int, default=2)
    parser.add_argument("--live-vision", type=int, default=0, metavar="N")
    parser.add_argument(
        "--samples",
        nargs="+",
        default=None,
        help="doc-factory sample names (default: all six)",
    )
    parser.add_argument("--events", required=True, help="where the worker's log lines go")
    args = parser.parse_args()

    sys.path.insert(0, os.getcwd())
    from app.middleware.logging import configure_logging
    from tests.intake_extractor_support import SAMPLES

    samples = args.samples or list(SAMPLES)
    summary: dict = {"samples": samples, "cpu_count": os.cpu_count()}
    with open(args.events, "a", encoding="utf-8") as events, contextlib.redirect_stdout(events):
        configure_logging()
        if args.live_vision:
            summary["live"] = asyncio.run(_live(args.live_vision, samples))
        else:
            asyncio.run(_one(samples[0], live=False))  # warm imports and fonts
            summary["levels"] = [
                asyncio.run(_level(level, args.per_worker, samples)) for level in args.levels
            ]
    json.dump(summary, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
