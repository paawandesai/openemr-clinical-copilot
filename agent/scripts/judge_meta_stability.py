"""
Category M stability under the committed judge prompts (D46, D48).

A meta-check fixture is admitted only if it is STABLE: five independent
best-of-3 gradings under the committed prompt all agree with its frozen label.
That was measured under the prompt in force when each fixture was admitted.
A judge prompt edit changes what "the committed prompt" is, so the
measurement has to be taken again before the fixtures can be trusted as a
drift detector for the new prompt (D57, "re-check category M"; D61).

This runs the gate's own `Judge`, unchanged, on every category-M fixture,
`--rounds` times, each round with a fresh cache directory, so every vote is a
live call. It reports each fixture's round verdicts and vote tally and exits
1 if any round disagreed with a fixture's label.

Only stability is measured here. Discrimination (D48) compares against a
generic one-line rubric, which a change to the committed prompt does not
touch, so it is not re-measured.

    cd agent && python scripts/judge_meta_stability.py [--rounds 5] [--json OUT]

Needs ANTHROPIC_API_KEY. Billed: rounds x fixtures x 3 calls.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

AGENT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AGENT_ROOT))


def m_fixtures() -> list[tuple[str, dict[str, Any]]]:
    """(case id, fixture) for every category-M case, as the gate loads them."""
    from evals.w2.gate.loader import load_cases
    from evals.w2.gate.paths import FIXTURES_DIR

    out: list[tuple[str, dict[str, Any]]] = []
    for case in load_cases():
        if case.category != "M" or not case.is_active:
            continue
        (relative,) = case.fixtures()
        out.append((case.id, json.loads((FIXTURES_DIR / relative).read_text(encoding="utf-8"))))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--json", type=Path, help="also write the result as JSON")
    args = parser.parse_args(argv)

    from evals.w2.gate.judge import Judge

    fixtures = m_fixtures()
    started = datetime.now(UTC).isoformat(timespec="seconds")
    rounds: dict[str, list[dict[str, Any]]] = {case_id: [] for case_id, _ in fixtures}
    totals = {"calls_made": 0, "cache_hits": 0, "input_tokens": 0, "output_tokens": 0}
    cost = 0.0
    prompt_version = ""
    for n in range(1, args.rounds + 1):
        judge = Judge(cache_dir=Path(tempfile.mkdtemp(prefix=f"w2-m-stability-{n}-")))
        prompt_version = judge.prompt_version
        for case_id, fixture in fixtures:
            verdict = judge.grade(f"MSTAB-{n}-{case_id}", fixture["rubric"], fixture["payload"])
            rounds[case_id].append({"verdict": verdict.verdict, "votes": list(verdict.votes)})
        for key in totals:
            totals[key] += getattr(judge, key)
        cost += judge.estimated_cost_usd()
        print(f"round {n}/{args.rounds} done")

    unstable: list[str] = []
    result: dict[str, Any] = {"fixtures": {}}
    for case_id, fixture in fixtures:
        expected = fixture["expected_verdict"]
        per_round = rounds[case_id]
        agree = sum(r["verdict"] == expected for r in per_round)
        votes = sum(v == expected for r in per_round for v in r["votes"])
        stable = agree == len(per_round)
        if not stable:
            unstable.append(case_id)
        result["fixtures"][case_id] = {
            "fixture": fixture["fixture_id"],
            "expected": expected,
            "rounds_agreeing": agree,
            "rounds": len(per_round),
            "votes_agreeing": votes,
            "votes": 3 * len(per_round),
            "stable": stable,
            "per_round": per_round,
        }
        print(
            f"{case_id:<6} {fixture['fixture_id']:<36} expected {expected:<4} "
            f"rounds {agree}/{len(per_round)}  votes {votes}/{3 * len(per_round)}  "
            f"{'STABLE' if stable else 'UNSTABLE'}"
        )
    result.update(
        {
            "prompt_version": prompt_version,
            "started_at": started,
            "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
            **totals,
            "cost_usd": round(cost, 6),
            "unstable": unstable,
        }
    )
    print(
        f"{totals['calls_made']} calls, {totals['cache_hits']} cache hits, "
        f"{totals['input_tokens']} input + {totals['output_tokens']} output tokens, ${cost:.4f}"
    )
    if args.json:
        args.json.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return 1 if unstable else 0


if __name__ == "__main__":
    raise SystemExit(main())
