"""
``python -m evals.w2.factory`` — generate and validate synthetic documents.

    cd agent
    python -m evals.w2.factory generate evals/w2/factory/specs
    python -m evals.w2.factory generate evals/w2/factory/specs/lab-clean-001.yaml --refresh
    python -m evals.w2.factory validate evals/w2/factory/output/samples

Output never names a patient: scenario ids, counts and file paths only.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from evals.w2.factory.chart import ChartUnavailable
from evals.w2.factory.generate import (
    DEFAULT_CACHE_DIR,
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_SPEC_DIR,
    generate,
)
from evals.w2.factory.spec import SpecError, load_specs
from evals.w2.factory.validate import validate

AGENT_DIR = Path(__file__).resolve().parents[3]
DEFAULT_ENV = AGENT_DIR / ".env"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m evals.w2.factory", description=__doc__.strip().splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="render documents + ground truth from a spec file or directory")
    gen.add_argument("target", nargs="?", type=Path, default=DEFAULT_SPEC_DIR,
                     help="a scenario .yaml, or a directory of them (default: evals/w2/factory/specs)")
    gen.add_argument("--out", type=Path, default=DEFAULT_OUTPUT_ROOT,
                     help="output root; each scenario gets a subdirectory (default: evals/w2/factory/output)")
    gen.add_argument("--cache", type=Path, default=DEFAULT_CACHE_DIR,
                     help="chart cache directory (default: evals/w2/factory/cache)")
    gen.add_argument("--refresh", action="store_true",
                     help="re-read the chart from local OpenEMR instead of using the cache")
    gen.add_argument("--env", type=Path, default=DEFAULT_ENV,
                     help="dotenv file for the OpenEMR connection settings (default: agent/.env)")

    val = sub.add_parser("validate", help="check generated ground truth against the clean PDFs")
    val.add_argument("target", nargs="?", type=Path, default=DEFAULT_OUTPUT_ROOT,
                     help="an output directory, or a single *.ground_truth.json")
    val.add_argument("-v", "--verbose", action="store_true", help="list every failure, not just the first three")

    return parser


def _run_generate(args: argparse.Namespace) -> int:
    try:
        specs = load_specs(args.target)
    except SpecError as exc:
        print(f"spec error: {exc}", file=sys.stderr)
        return 2

    failures = 0
    for spec in specs:
        try:
            result = generate(
                spec,
                output_root=args.out,
                cache_dir=args.cache,
                refresh=args.refresh,
                env_path=args.env if args.env.is_file() else None,
            )
        except (SpecError, ChartUnavailable) as exc:
            print(f"FAIL  {spec.scenario_id}: {exc}", file=sys.stderr)
            failures += 1
            continue

        extra = "" if result.clean_path == result.document_path else f" (+ {result.clean_path.name})"
        print(
            f"ok    {spec.scenario_id:<34} {spec.doc_type.value:<12} {spec.quality.value:<14} "
            f"{result.page_count}p  {result.field_count:>3} fields  chart={result.chart_origin}"
        )
        print(f"      {result.document_path}{extra}")

    print(f"\n{len(specs) - failures}/{len(specs)} scenario(s) generated into {args.out}")
    return 1 if failures else 0


def _run_validate(args: argparse.Namespace) -> int:
    report = validate(args.target)
    if not report.scenarios:
        print(f"no *.ground_truth.json found under {args.target}", file=sys.stderr)
        return 2

    for scenario in report.scenarios:
        status = "PASS" if scenario.ok else "FAIL"
        print(f"{status}  {scenario.scenario_id:<34} {scenario.checks:>5} checks, {len(scenario.failures)} failure(s)")
        shown = scenario.failures if args.verbose else scenario.failures[:3]
        for failure in shown:
            print(f"        - {failure}")
        if len(scenario.failures) > len(shown):
            print(f"        ... and {len(scenario.failures) - len(shown)} more (-v to show all)")

    passed = sum(1 for s in report.scenarios if s.ok)
    print(
        f"\n{passed}/{len(report.scenarios)} scenario(s) passed; "
        f"{report.checks} checks, {report.failures} failure(s)"
    )
    return 0 if report.ok else 1


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "generate":
        return _run_generate(args)
    return _run_validate(args)
