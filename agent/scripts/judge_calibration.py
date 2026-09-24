"""
Judge calibration (D57): human labels against the pinned judge, and the
agreement between them.

The gate's two judge rubrics, `factually_consistent` and `safe_refusal`, are
scored by an LLM judge that every gate run prints as UNCALIBRATED. Category M
detects a judge that has drifted; it cannot say whether the judge agrees with
a person to begin with. This script produces that number, in three steps that
never see each other's answers until the last one:

    python scripts/judge_calibration.py build              # rebuild the labelling page
    python scripts/judge_calibration.py build --check      # exit 1 if the page is stale
    python scripts/judge_calibration.py judge              # the real judge on every item
    python scripts/judge_calibration.py compute LABELS.json [--json OUT] [--accept-rubric-change]
    python scripts/judge_calibration.py synthetic OUT.json [--seed N]

Every command works on one item set: by default the first set, in
docs/w2/calibration/. `--set-dir DIR`, placed before the command, selects
another set with the same layout: DIR/label.html, DIR/sealed/items.json and
DIR/sealed/judge_verdicts.json. For example, the second set is
`--set-dir ../docs/w2/calibration/set2`.

`build` writes docs/w2/calibration/label.html: one offline file that shows each
item exactly as the judge receives it, with the judge's own prompt as the
rubric. It is blind by construction: the page's embedded data is the item id,
the rubric and the payload, never a verdict and never the intended label.

`judge` runs `evals.w2.gate.judge.Judge` unchanged — the gate's pinned model,
prompt, output schema, best-of-3 and 2-of-3 majority — with a fresh cache
directory, so every vote is a live call, and stores the verdicts and the cost
in docs/w2/calibration/sealed/judge_verdicts.json. Needs ANTHROPIC_API_KEY.

`compute` reads a labels file the page downloaded and reports, per rubric and
pooled: agreement with a Wilson 95% interval, Cohen's kappa, the confusion
matrix, UNSURE and unlabelled counts, and every disagreement with both
verdicts and the item. Running it shows the judge's verdicts, so it is run
after labelling, never before.

A labels file marked `"independent": false` (labels that are not an
independent human reading, such as bulk-confirmed LLM drafts) is reported as a
rubric audit, not a calibration, with a warning, in the way synthetic labels
are.

`synthetic` writes a labels file of seeded random labels, marked synthetic,
for exercising `compute`. It is independent of the intended labels and of
the judge, so it carries no information about either.

Every item is synthetic; nothing here reads a chart.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

AGENT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = AGENT_ROOT.parent
sys.path.insert(0, str(AGENT_ROOT))

CAL_DIR = REPO_ROOT / "docs" / "w2" / "calibration"
ITEMS_PATH = CAL_DIR / "sealed" / "items.json"
VERDICTS_PATH = CAL_DIR / "sealed" / "judge_verdicts.json"
PAGE_PATH = CAL_DIR / "label.html"
TEMPLATE_PATH = Path(__file__).resolve().with_name("judge_calibration_page.html")
SET2_DIR = CAL_DIR / "set2"

ITEMS_SCHEMA = "w2-judge-calibration/items/1"
VERDICTS_SCHEMA = "w2-judge-calibration/verdicts/1"
LABELS_SCHEMA = "w2-judge-calibration/labels/1"

#: The judge renders exactly these payload fields for each rubric
#: (evals/w2/gate/judge.py `_render`), so the labeller sees exactly these.
PAYLOAD_FIELDS: dict[str, tuple[str, ...]] = {
    "factually_consistent": ("statement", "source_text"),
    "safe_refusal": ("request", "response"),
}
RUBRICS = tuple(PAYLOAD_FIELDS)
HUMAN_LABELS = ("pass", "fail", "unsure")
PLACEHOLDER = "__CALIBRATION_DATA__"

#: 95% two-sided, as in evals/w2/gate/stats.py.
Z = 1.959963984540054


class CalibrationInputError(ValueError):
    """An input file is malformed or belongs to a different item set. Exit 2."""


# --------------------------------------------------------------------------- items


def set_paths(set_dir: Path) -> tuple[Path, Path, Path]:
    """The items, verdicts and page paths of the item set kept in `set_dir`."""
    sealed = set_dir / "sealed"
    return sealed / "items.json", sealed / "judge_verdicts.json", set_dir / "label.html"


def load_items(path: Path = ITEMS_PATH) -> list[dict[str, Any]]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("schema_version") != ITEMS_SCHEMA:
        raise CalibrationInputError(f"{path}: schema_version is not {ITEMS_SCHEMA}")
    items = doc["items"]
    seen: set[str] = set()
    for item in items:
        item_id = item.get("id")
        if not isinstance(item_id, str) or item_id in seen:
            raise CalibrationInputError(f"{path}: item id {item_id!r} is missing or repeated")
        seen.add(item_id)
        rubric = item.get("rubric")
        if rubric not in PAYLOAD_FIELDS:
            raise CalibrationInputError(f"{item_id}: unknown rubric {rubric!r}")
        if set(item.get("payload", {})) != set(PAYLOAD_FIELDS[rubric]):
            raise CalibrationInputError(
                f"{item_id}: payload must be exactly {PAYLOAD_FIELDS[rubric]} for {rubric}"
            )
        if item.get("intended") not in ("pass", "fail"):
            raise CalibrationInputError(f"{item_id}: intended must be pass or fail")
    return items


def blind(items: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """What a labeller may see: id, rubric, and the payload the judge sees."""
    return [
        {"id": item["id"], "rubric": item["rubric"], "payload": dict(item["payload"])}
        for item in items
    ]


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def items_digest(items: Iterable[Mapping[str, Any]]) -> str:
    """sha256 over the blind items, so a labels file is tied to the text it labelled."""
    return hashlib.sha256(_canonical(sorted(blind(items), key=lambda i: i["id"]))).hexdigest()


def judge_prompts() -> dict[str, str]:
    """
    Each rubric's judge prompt, read the way the judge reads it.

    Uses the judge's own `_prompt` so the rubric on the page and the digest in
    every output file are of the text the model is actually sent.
    """
    from evals.w2.gate.judge import _prompt
    from evals.w2.gate.loader import load_judge_config

    config = load_judge_config()
    return {rubric: _prompt(config, rubric) for rubric in RUBRICS}


def prompt_digests(prompts: Mapping[str, str]) -> dict[str, str]:
    return {
        rubric: hashlib.sha256(text.encode("utf-8")).hexdigest() for rubric, text in prompts.items()
    }


# ---------------------------------------------------------------------------- page


def render_page(
    items: list[dict[str, Any]], prompts: Mapping[str, str], template: str | None = None
) -> str:
    template = template if template is not None else TEMPLATE_PATH.read_text(encoding="utf-8")
    if template.count(PLACEHOLDER) != 1:
        raise CalibrationInputError(f"the page template must hold {PLACEHOLDER} exactly once")
    data = {
        "labels_schema_version": LABELS_SCHEMA,
        "items_sha256": items_digest(items),
        "prompt_sha256": prompt_digests(prompts),
        "rubrics": {rubric: {"text": prompts[rubric]} for rubric in RUBRICS},
        "items": blind(items),
    }
    # ASCII-only and no literal "<", so nothing in an item can close the
    # <script type="application/json"> block it is embedded in.
    embedded = json.dumps(data, ensure_ascii=True, sort_keys=True).replace("<", "\\u003c")
    return template.replace(PLACEHOLDER, embedded)


def page_data(html: str) -> dict[str, Any]:
    """The JSON a built page embeds. Used by the tests to check the page is blind."""
    start_tag = '<script type="application/json" id="calibration-data">'
    start = html.index(start_tag) + len(start_tag)
    end = html.index("</script>", start)
    return json.loads(html[start:end])


# --------------------------------------------------------------------------- judge


def run_judge(items: list[dict[str, Any]], cache_dir: Path) -> dict[str, Any]:
    """Grade every item with the gate's Judge, unchanged. Every call is billed."""
    from evals.w2.gate.judge import Judge
    from evals.w2.gate.paths import JUDGE_CONFIG

    judge = Judge(cache_dir=cache_dir)
    prompts = judge_prompts()
    started = datetime.now(UTC).isoformat(timespec="seconds")
    verdicts: dict[str, Any] = {}
    for n, item in enumerate(items, 1):
        result = judge.grade(f"CAL-{item['id']}", item["rubric"], item["payload"])
        verdicts[item["id"]] = {
            "rubric": item["rubric"],
            "verdict": result.verdict,
            "votes": list(result.votes),
            "majority": result.majority,
            "cached": result.cached,
            "reasons": list(result.reasons),
        }
        # Progress only: a verdict on the terminal is a verdict a labeller can see.
        print(f"  {n:>2}/{len(items)} {item['id']} graded")
    return {
        "schema_version": VERDICTS_SCHEMA,
        "sealed": (
            "SEALED until you have labelled every item: the judge's verdict on each "
            "item. Reading it first makes the labels agreement with the judge."
        ),
        "items_sha256": items_digest(items),
        "prompt_sha256": prompt_digests(prompts),
        "judge_config_sha256": hashlib.sha256(JUDGE_CONFIG.read_bytes()).hexdigest(),
        "model": judge.model,
        "model_version": judge.model_version,
        "prompt_version": judge.prompt_version,
        "best_of": int(judge.config["best_of"]),
        "majority": int(judge.config["majority"]),
        "run": {
            "started_at": started,
            "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "calls_made": judge.calls_made,
            "cache_hits": judge.cache_hits,
            "input_tokens": judge.input_tokens,
            "output_tokens": judge.output_tokens,
            "price_usd_per_mtok": {"input": judge.price_in, "output": judge.price_out},
            "cost_usd": round(judge.estimated_cost_usd(), 6),
        },
        "verdicts": verdicts,
    }


# ------------------------------------------------------------------------- compute


def wilson_interval(successes: int, n: int, z: float = Z) -> tuple[float, float]:
    if n <= 0:
        return (0.0, 1.0)
    p = successes / n
    z2 = z * z
    denominator = 1.0 + z2 / n
    centre = p + z2 / (2 * n)
    half = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    return (max(0.0, (centre - half) / denominator), min(1.0, (centre + half) / denominator))


def cohen_kappa(pairs: list[tuple[str, str]]) -> float | None:
    """
    Cohen's kappa for (human, judge) pairs over {pass, fail}.

    None when it is undefined: no pairs, or both raters used a single, shared
    category, so chance agreement is 1 and there is nothing to correct for.
    """
    n = len(pairs)
    if n == 0:
        return None
    observed = sum(h == j for h, j in pairs) / n
    expected = sum(
        (sum(h == c for h, _ in pairs) / n) * (sum(j == c for _, j in pairs) / n)
        for c in ("pass", "fail")
    )
    if math.isclose(expected, 1.0):
        return None
    return (observed - expected) / (1 - expected)


@dataclass(frozen=True)
class Tally:
    items: int
    unsure: tuple[str, ...]
    unlabelled: tuple[str, ...]
    pairs: tuple[tuple[str, str, str], ...]  # (id, human, judge)

    @property
    def compared(self) -> int:
        return len(self.pairs)

    @property
    def agree(self) -> int:
        return sum(h == j for _, h, j in self.pairs)

    def confusion(self) -> dict[str, dict[str, int]]:
        return {
            f"human_{h}": {
                f"judge_{j}": sum(p[1:] == (h, j) for p in self.pairs) for j in ("pass", "fail")
            }
            for h in ("pass", "fail")
        }

    def as_dict(self) -> dict[str, Any]:
        low, high = wilson_interval(self.agree, self.compared)
        kappa = cohen_kappa([(h, j) for _, h, j in self.pairs])
        return {
            "items": self.items,
            "labelled": self.items - len(self.unlabelled),
            "compared": self.compared,
            "agree": self.agree,
            "agreement": (self.agree / self.compared) if self.compared else None,
            "agreement_wilson95": [low, high],
            "cohen_kappa": kappa,
            "confusion": self.confusion(),
            "unsure": list(self.unsure),
            "unlabelled": list(self.unlabelled),
        }


def load_labels(path: Path) -> dict[str, Any]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CalibrationInputError(f"{path}: cannot read labels JSON: {exc}") from exc
    if doc.get("schema_version") != LABELS_SCHEMA:
        raise CalibrationInputError(
            f"{path}: schema_version is {doc.get('schema_version')!r}, not {LABELS_SCHEMA}"
        )
    if not isinstance(doc.get("labels"), dict):
        raise CalibrationInputError(f"{path}: no `labels` object")
    return doc


def compute(
    items: list[dict[str, Any]],
    verdicts_doc: Mapping[str, Any],
    labels_doc: Mapping[str, Any],
    current_prompt_sha256: Mapping[str, str] | None = None,
    accept_rubric_change: bool = False,
) -> dict[str, Any]:
    """
    The whole report as data. Raises CalibrationInputError on mismatched inputs.

    Labels made under one judge prompt and verdicts made under another are
    refused unless `accept_rubric_change`: that comparison is the normal loop
    after a prompt edit (re-judge, compare with the same human labels), but it
    measures a rubric the labeller never read, so it has to be asked for.
    """
    digest = items_digest(items)
    if labels_doc.get("items_sha256") != digest:
        raise CalibrationInputError(
            "the labels file was made for a different item set (items_sha256 "
            f"{labels_doc.get('items_sha256')!r}, current {digest!r}); rebuild the page and relabel"
        )
    if verdicts_doc.get("schema_version") != VERDICTS_SCHEMA:
        raise CalibrationInputError(f"judge verdicts schema_version is not {VERDICTS_SCHEMA}")
    if verdicts_doc.get("items_sha256") != digest:
        raise CalibrationInputError(
            "the judge verdicts are for a different item set; re-run "
            "`python scripts/judge_calibration.py judge`"
        )
    warnings: list[str] = []
    if labels_doc.get("prompt_sha256") != verdicts_doc.get("prompt_sha256"):
        if not accept_rubric_change:
            raise CalibrationInputError(
                "the labels were made against a different rubric text from the one the judge "
                "graded with (prompt_sha256 differs); pass --accept-rubric-change to compare "
                "them anyway"
            )
        warnings.append(
            "RUBRIC CHANGED: the labeller read a different judge prompt from the one these "
            "verdicts were graded under (--accept-rubric-change)."
        )

    by_id = {item["id"]: item for item in items}
    labels = labels_doc["labels"]
    unknown = sorted(set(labels) - set(by_id))
    if unknown:
        raise CalibrationInputError(f"the labels file names items that do not exist: {unknown}")
    verdicts = verdicts_doc["verdicts"]
    missing_verdicts = sorted(set(by_id) - set(verdicts))
    if missing_verdicts:
        raise CalibrationInputError(f"no judge verdict for {missing_verdicts}")

    if current_prompt_sha256 is not None and dict(current_prompt_sha256) != dict(
        verdicts_doc["prompt_sha256"]
    ):
        changed = sorted(
            r
            for r in RUBRICS
            if current_prompt_sha256.get(r) != verdicts_doc["prompt_sha256"].get(r)
        )
        warnings.append(
            f"the committed judge prompt for {changed} has changed since these verdicts were "
            "made. The numbers below describe the prompt that was measured, not the one "
            "committed now."
        )
    if labels_doc.get("synthetic"):
        warnings.append("SYNTHETIC LABELS: random, for exercising this command. Not a measurement.")
    independent = labels_doc.get("independent") is not False
    if not independent:
        warnings.append(
            "LABELS NOT INDEPENDENT: the labels file says they are not an independent human "
            "reading. This is a rubric audit. The agreement below is not a judge calibration."
        )

    buckets: dict[str, dict[str, list[Any]]] = {
        r: {"unsure": [], "unlabelled": [], "pairs": []} for r in (*RUBRICS, "overall")
    }
    disagreements: list[dict[str, Any]] = []
    for item_id, item in sorted(by_id.items()):
        entry = labels.get(item_id) or {}
        human = entry.get("label")
        if human is not None and human not in HUMAN_LABELS:
            raise CalibrationInputError(f"{item_id}: label {human!r} is not one of {HUMAN_LABELS}")
        judged = verdicts[item_id]
        for key in (item["rubric"], "overall"):
            if human is None:
                buckets[key]["unlabelled"].append(item_id)
            elif human == "unsure":
                buckets[key]["unsure"].append(item_id)
            else:
                buckets[key]["pairs"].append((item_id, human, judged["verdict"]))
        if human in ("pass", "fail") and human != judged["verdict"]:
            disagreements.append(
                {
                    "id": item_id,
                    "rubric": item["rubric"],
                    "human": human,
                    "human_note": entry.get("note", ""),
                    "judge": judged["verdict"],
                    "judge_votes": judged["votes"],
                    "judge_reasons": judged["reasons"],
                    "author_intended": item["intended"],
                    "author_mode": item.get("mode"),
                    "payload": item["payload"],
                }
            )

    tallies = {
        key: Tally(
            items=len(b["unsure"]) + len(b["unlabelled"]) + len(b["pairs"]),
            unsure=tuple(b["unsure"]),
            unlabelled=tuple(b["unlabelled"]),
            pairs=tuple(b["pairs"]),
        ).as_dict()
        for key, b in buckets.items()
    }
    return {
        "labeller": labels_doc.get("labeller") or "",
        "synthetic": bool(labels_doc.get("synthetic")),
        "independent": independent,
        "judge": {
            "model_version": verdicts_doc.get("model_version"),
            "prompt_version": verdicts_doc.get("prompt_version"),
            "best_of": verdicts_doc.get("best_of"),
            "majority": verdicts_doc.get("majority"),
            "prompt_sha256": verdicts_doc.get("prompt_sha256"),
        },
        "warnings": warnings,
        "rubrics": {r: tallies[r] for r in RUBRICS},
        "overall": tallies["overall"],
        "disagreements": disagreements,
    }


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:.1f}%"


def _tally_lines(name: str, t: Mapping[str, Any]) -> list[str]:
    low, high = t["agreement_wilson95"]
    kappa = t["cohen_kappa"]
    if kappa is not None:
        kappa_text = f"{kappa:.3f}"
    elif t["compared"] == 0:
        kappa_text = "undefined (nothing compared)"
    else:
        kappa_text = "undefined (both sides used one and the same category)"
    c = t["confusion"]
    return [
        f"{name}",
        f"  items {t['items']}  labelled {t['labelled']}  UNSURE {len(t['unsure'])}  "
        f"unlabelled {len(t['unlabelled'])}  compared {t['compared']}",
        f"  agreement      {t['agree']}/{t['compared']} = {_pct(t['agreement'])}"
        f"   (Wilson 95%: {_pct(low)} to {_pct(high)})",
        f"  Cohen's kappa  {kappa_text}",
        "  confusion                judge pass   judge fail",
        f"    human pass          {c['human_pass']['judge_pass']:>10}   {c['human_pass']['judge_fail']:>10}",
        f"    human fail          {c['human_fail']['judge_pass']:>10}   {c['human_fail']['judge_fail']:>10}",
        "  UNSURE: " + (", ".join(t["unsure"]) or "none"),
        "  unlabelled: " + (", ".join(t["unlabelled"]) or "none"),
    ]


def format_report(report: Mapping[str, Any], labels_path: str = "") -> str:
    j = report["judge"]
    title = (
        "Judge calibration"
        if report.get("independent", True)
        else "Rubric audit (labels not independent): NOT a judge calibration"
    )
    lines = [
        title,
        f"  labels     {labels_path}  (labeller: {report['labeller'] or 'unnamed'})",
        f"  judge      {j['model_version']}, prompt {j['prompt_version']}, "
        f"best-of-{j['best_of']} majority {j['majority']}",
        "  agreement and kappa exclude UNSURE and unlabelled items",
    ]
    for warning in report["warnings"]:
        lines.append(f"  WARNING: {warning}")
    lines.append("")
    for rubric in RUBRICS:
        lines += _tally_lines(rubric, report["rubrics"][rubric]) + [""]
    lines += _tally_lines("overall (both rubrics pooled)", report["overall"]) + [""]

    lines.append(f"Disagreements ({len(report['disagreements'])})")
    if not report["disagreements"]:
        lines.append("  none")
    for d in report["disagreements"]:
        lines += [
            "",
            f"  {d['id']}  [{d['rubric']}]  human {d['human'].upper()}  judge {d['judge'].upper()} "
            f"(votes {', '.join(d['judge_votes'])})  author intended {d['author_intended'].upper()} "
            f"({d['author_mode']})",
        ]
        for field in PAYLOAD_FIELDS[d["rubric"]]:
            lines.append(f"    {field.upper()}:")
            lines += [f"      {line}" for line in d["payload"][field].splitlines() or [""]]
        lines.append(f"    human note: {d['human_note'] or '(none)'}")
        for n, reason in enumerate(d["judge_reasons"], 1):
            lines.append(f"    judge reason {n}: {reason}")
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------- synthetic


def synthetic_labels(
    items: list[dict[str, Any]],
    prompt_sha256: Mapping[str, str],
    seed: int = 7,
    unsure_rate: float = 0.08,
    unlabelled_rate: float = 0.05,
) -> dict[str, Any]:
    """Random labels in the page's download format, for exercising `compute`."""
    rng = random.Random(seed)
    labels: dict[str, Any] = {}
    for item in items:
        roll = rng.random()
        if roll < unlabelled_rate:
            continue
        label = "unsure" if roll < unlabelled_rate + unsure_rate else rng.choice(["pass", "fail"])
        labels[item["id"]] = {
            "label": label,
            "note": f"synthetic label, seed {seed}",
            "labelled_at": None,
        }
    return {
        "schema_version": LABELS_SCHEMA,
        "synthetic": True,
        "items_sha256": items_digest(items),
        "prompt_sha256": dict(prompt_sha256),
        "labeller": f"synthetic-seed-{seed}",
        "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "item_count": len(items),
        "labels": labels,
    }


# ----------------------------------------------------------------------------- cli


def _write_json(path: Path, doc: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--set-dir",
        type=Path,
        default=CAL_DIR,
        help="the item set's directory (default: the first set, docs/w2/calibration)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p_build = sub.add_parser("build", help="rebuild docs/w2/calibration/label.html")
    p_build.add_argument("--check", action="store_true", help="exit 1 if the page is stale")
    p_judge = sub.add_parser("judge", help="run the pinned judge on every item (billed)")
    p_judge.add_argument("--cache-dir", type=Path, help="judge cache (default: a fresh temp dir)")
    p_judge.add_argument("--out", type=Path, help="default: the set's sealed/judge_verdicts.json")
    p_compute = sub.add_parser("compute", help="agreement between a labels file and the judge")
    p_compute.add_argument("labels", type=Path)
    p_compute.add_argument(
        "--verdicts", type=Path, help="default: the set's sealed/judge_verdicts.json"
    )
    p_compute.add_argument("--json", type=Path, help="also write the report as JSON")
    p_compute.add_argument(
        "--accept-rubric-change",
        action="store_true",
        help="compare labels and verdicts made under different judge prompts, with a warning",
    )
    p_synth = sub.add_parser("synthetic", help="write a random, synthetic labels file")
    p_synth.add_argument("out", type=Path)
    p_synth.add_argument("--seed", type=int, default=7)
    args = parser.parse_args(argv)
    items_path, verdicts_path, page_path = set_paths(args.set_dir)

    try:
        items = load_items(items_path)
        if args.command == "build":
            rendered = render_page(items, judge_prompts())
            if args.check:
                current = page_path.read_text(encoding="utf-8") if page_path.is_file() else ""
                if current != rendered:
                    print(f"{page_path} is stale; run `python scripts/judge_calibration.py build`")
                    return 1
                print(f"{page_path} is up to date")
                return 0
            page_path.parent.mkdir(parents=True, exist_ok=True)
            page_path.write_text(rendered, encoding="utf-8")
            print(f"wrote {page_path} ({len(items)} items)")
            return 0

        if args.command == "judge":
            cache_dir = args.cache_dir or Path(tempfile.mkdtemp(prefix="w2-judge-calibration-"))
            print(f"judging {len(items)} items, best-of-3, cache {cache_dir}")
            doc = run_judge(items, cache_dir)
            out = args.out or verdicts_path
            _write_json(out, doc)
            run = doc["run"]
            print(
                f"wrote {out}: {run['calls_made']} calls, {run['cache_hits']} cache hits, "
                f"{run['input_tokens']} input + {run['output_tokens']} output tokens, "
                f"${run['cost_usd']:.4f}"
            )
            return 0

        if args.command == "synthetic":
            _write_json(
                args.out, synthetic_labels(items, prompt_digests(judge_prompts()), args.seed)
            )
            print(f"wrote {args.out} (synthetic, seed {args.seed})")
            return 0

        verdicts_doc = json.loads((args.verdicts or verdicts_path).read_text(encoding="utf-8"))
        report = compute(
            items,
            verdicts_doc,
            load_labels(args.labels),
            current_prompt_sha256=prompt_digests(judge_prompts()),
            accept_rubric_change=args.accept_rubric_change,
        )
        sys.stdout.write(format_report(report, str(args.labels)))
        if args.json:
            _write_json(args.json, report)
        return 0
    except CalibrationInputError as exc:
        print(f"judge_calibration: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
