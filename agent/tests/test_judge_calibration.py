"""
Judge calibration (D57): for each committed item set, the labelling page is
blind and offline and the stored judge verdicts belong to the items. `compute`
gets agreement, kappa and the confusion matrix right on a labels file whose
answers are known, and reports labels marked not independent as a rubric
audit, never as a calibration.

The sets are set 1 (60 items, docs/w2/calibration/) and set 2 (20 fresh items,
docs/w2/calibration/set2/). These tests read the sealed files, but print item
text only when an assertion fails.

No network, no credential: the judge is never called here.

    cd agent && python -m pytest tests/test_judge_calibration.py
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "judge_calibration.py"
_spec = importlib.util.spec_from_file_location("judge_calibration", _SCRIPT)
assert _spec is not None and _spec.loader is not None
jc = importlib.util.module_from_spec(_spec)
sys.modules["judge_calibration"] = jc  # @dataclass resolves its module through sys.modules
_spec.loader.exec_module(jc)


#: (name, directory, items per rubric, id prefixes, longest allowed run of one intended label)
SETS = [
    ("set1", jc.CAL_DIR, 30, ("FC-", "SR-"), 7),
    ("set2", jc.SET2_DIR, 10, ("FC2-", "SR2-"), 3),
]


@pytest.fixture(scope="module", params=SETS, ids=[s[0] for s in SETS])
def item_set(request: Any) -> dict[str, Any]:
    name, directory, per_rubric, prefixes, max_run = request.param
    items_path, verdicts_path, page_path = jc.set_paths(directory)
    return {
        "name": name,
        "items": jc.load_items(items_path),
        "verdicts_path": verdicts_path,
        "page": page_path.read_text(encoding="utf-8"),
        "per_rubric": per_rubric,
        "prefixes": prefixes,
        "max_run": max_run,
    }


@pytest.fixture(scope="module")
def items() -> list[dict[str, Any]]:
    """Set 1: the compute tests below are written against its 60 items."""
    return jc.load_items()


@pytest.fixture(scope="module")
def prompts() -> dict[str, str]:
    return jc.judge_prompts()


# ------------------------------------------------------------------- the item sets


def test_item_set_is_balanced(item_set: dict[str, Any]) -> None:
    items, n = item_set["items"], item_set["per_rubric"]
    assert len(items) == 2 * n
    for rubric, prefix in zip(jc.RUBRICS, item_set["prefixes"], strict=True):
        sub = [i for i in items if i["rubric"] == rubric]
        assert len(sub) == n
        assert sum(i["intended"] == "pass" for i in sub) == n // 2
        # Opaque ids: nothing about the intended label is in the id.
        assert sorted(i["id"] for i in sub) == [f"{prefix}{k:02d}" for k in range(1, n + 1)]


def test_intended_label_does_not_follow_id_order(item_set: dict[str, Any]) -> None:
    """A labeller who notices FC-01..15 all fail would stop being blind."""
    for rubric in jc.RUBRICS:
        sequence = [i["intended"] for i in item_set["items"] if i["rubric"] == rubric]
        assert sequence != sorted(sequence) and sequence != sorted(sequence, reverse=True)
        longest = max(
            len(m.group(0)) for m in re.finditer(r"(p+|f+)", "".join(s[0] for s in sequence))
        )
        assert longest <= item_set["max_run"]


def test_the_sets_share_no_item() -> None:
    """Set 2 is fresh: no id, key or payload of set 1 appears in it."""
    first = jc.load_items()
    second = jc.load_items(jc.set_paths(jc.SET2_DIR)[0])
    assert not {i["id"] for i in first} & {i["id"] for i in second}
    assert not {i["key"] for i in first} & {i["key"] for i in second}
    first_texts = {text for i in first for text in i["payload"].values()}
    assert not first_texts & {text for i in second for text in i["payload"].values()}


# ----------------------------------------------------------------------- the page


def test_committed_page_is_built_from_the_committed_items(
    item_set: dict[str, Any], prompts: dict[str, str]
) -> None:
    assert item_set["page"] == jc.render_page(item_set["items"], prompts), (
        f"{item_set['name']} label.html is stale; run `python scripts/judge_calibration.py build`"
    )


@pytest.mark.parametrize("source", ["committed", "freshly rendered"])
def test_page_is_blind(source: str, item_set: dict[str, Any], prompts: dict[str, str]) -> None:
    items = item_set["items"]
    html = item_set["page"] if source == "committed" else jc.render_page(items, prompts)
    data = jc.page_data(html)
    assert set(data) == {
        "labels_schema_version",
        "items_sha256",
        "prompt_sha256",
        "rubrics",
        "items",
    }
    for shown in data["items"]:
        assert set(shown) == {"id", "rubric", "payload"}
    # The rationale text is the easiest leak to miss: it is prose, not a key.
    for item in items:
        assert item["why"] not in html
    verdicts = json.loads(item_set["verdicts_path"].read_text(encoding="utf-8"))["verdicts"]
    for judged in verdicts.values():
        for reason in judged["reasons"]:
            if len(reason) > 30:
                assert reason not in html


def test_page_shows_exactly_what_the_judge_sees(
    item_set: dict[str, Any], prompts: dict[str, str]
) -> None:
    items = item_set["items"]
    data = jc.page_data(item_set["page"])
    by_id = {i["id"]: i for i in items}
    assert len(data["items"]) == len(items)
    for shown in data["items"]:
        assert shown["payload"] == by_id[shown["id"]]["payload"]
        assert set(shown["payload"]) == set(jc.PAYLOAD_FIELDS[shown["rubric"]])
    for rubric in jc.RUBRICS:
        assert data["rubrics"][rubric]["text"] == prompts[rubric]


def test_page_makes_no_network_request(item_set: dict[str, Any]) -> None:
    page = item_set["page"]
    for needle in (
        "http://",
        "https://",
        "src=",
        "href=",
        "@import",
        "fetch(",
        "XMLHttpRequest",
        "WebSocket",
        "<link",
    ):
        assert needle not in page, needle


def test_embedded_data_cannot_close_its_script_tag() -> None:
    hostile = [
        {
            "id": "FC-01",
            "rubric": "factually_consistent",
            "intended": "pass",
            "payload": {"statement": "</script><script>alert(1)</script>", "source_text": "x"},
        }
    ]
    html = jc.render_page(hostile, {"factually_consistent": "p", "safe_refusal": "q"})
    assert html.count("</script>") == 2  # the data block's and the app script's own
    assert jc.page_data(html)["items"][0]["payload"]["statement"].startswith("</script>")


# ------------------------------------------------------------ the stored verdicts


def test_stored_judge_verdicts_cover_the_committed_items(
    item_set: dict[str, Any], prompts: dict[str, str]
) -> None:
    from evals.w2.gate.loader import load_judge_config

    items = item_set["items"]
    doc = json.loads(item_set["verdicts_path"].read_text(encoding="utf-8"))
    config = load_judge_config()
    assert doc["items_sha256"] == jc.items_digest(items)
    assert doc["prompt_sha256"] == jc.prompt_digests(prompts)
    assert doc["model_version"] == config["model_version"]
    assert doc["best_of"] == config["best_of"] == 3
    assert set(doc["verdicts"]) == {i["id"] for i in items}
    for judged in doc["verdicts"].values():
        assert len(judged["votes"]) == 3 and judged["verdict"] in ("pass", "fail")
        assert judged["votes"].count(judged["verdict"]) == judged["majority"] >= 2
        assert judged["cached"] is False
    run = doc["run"]
    assert run["calls_made"] == 3 * len(items) and run["cache_hits"] == 0
    assert run["cost_usd"] > 0


# ------------------------------------------------------------------- statistics


def test_kappa_matches_the_textbook_example() -> None:
    # 20 both-pass, 5 human-pass/judge-fail, 10 human-fail/judge-pass, 15 both-fail:
    # p_o = 0.70, p_e = 0.5*0.6 + 0.5*0.4 = 0.50, kappa = 0.40.
    pairs = (
        [("pass", "pass")] * 20
        + [("pass", "fail")] * 5
        + [("fail", "pass")] * 10
        + [("fail", "fail")] * 15
    )
    assert jc.cohen_kappa(pairs) == pytest.approx(0.4)


def test_kappa_edges() -> None:
    assert jc.cohen_kappa([]) is None
    assert jc.cohen_kappa([("pass", "pass")] * 5) is None  # one shared category: undefined
    assert jc.cohen_kappa([("pass", "pass"), ("fail", "fail")]) == pytest.approx(1.0)
    assert jc.cohen_kappa([("pass", "fail"), ("fail", "pass")]) == pytest.approx(-1.0)


def test_wilson_interval() -> None:
    low, high = jc.wilson_interval(27, 30)
    assert 0.73 < low < 0.75 and 0.96 < high < 0.97
    assert jc.wilson_interval(0, 0) == (0.0, 1.0)
    assert jc.wilson_interval(30, 30)[1] == pytest.approx(1.0)


# ---------------------------------------------------------------------- compute


def _verdicts(items: list[dict[str, Any]], prompts: dict[str, str]) -> dict[str, Any]:
    """A verdicts file in which the judge agrees with the author on every item."""
    return {
        "schema_version": jc.VERDICTS_SCHEMA,
        "items_sha256": jc.items_digest(items),
        "prompt_sha256": jc.prompt_digests(prompts),
        "model_version": "claude-haiku-4-5-20251001",
        "prompt_version": "w2-judge/1.0.0",
        "best_of": 3,
        "majority": 2,
        "verdicts": {
            i["id"]: {
                "rubric": i["rubric"],
                "verdict": i["intended"],
                "votes": [i["intended"]] * 3,
                "majority": 3,
                "cached": False,
                "reasons": [f"judge reason for {i['id']}"] * 3,
            }
            for i in items
        },
    }


def _flip(label: str) -> str:
    return "fail" if label == "pass" else "pass"


def _known_labels(items: list[dict[str, Any]], prompts: dict[str, str]) -> dict[str, Any]:
    """
    Humans agree with the author except: FC — two intended-pass items flipped to
    fail, one UNSURE, one unlabelled; SR — one intended-fail item flipped to pass.
    """
    fc = [i for i in items if i["rubric"] == "factually_consistent"]
    sr = [i for i in items if i["rubric"] == "safe_refusal"]
    fc_pass = [i["id"] for i in fc if i["intended"] == "pass"]
    fc_fail = [i["id"] for i in fc if i["intended"] == "fail"]
    sr_fail = [i["id"] for i in sr if i["intended"] == "fail"]
    flipped = {fc_pass[0], fc_pass[1], sr_fail[0]}
    unsure, unlabelled = fc_fail[0], fc_fail[1]
    labels: dict[str, Any] = {}
    for i in items:
        if i["id"] == unlabelled:
            continue
        label = "unsure" if i["id"] == unsure else i["intended"]
        if i["id"] in flipped:
            label = _flip(label)
        labels[i["id"]] = {"label": label, "note": f"note {i['id']}", "labelled_at": None}
    return {
        "schema_version": jc.LABELS_SCHEMA,
        "items_sha256": jc.items_digest(items),
        "prompt_sha256": jc.prompt_digests(prompts),
        "labeller": "test",
        "labels": labels,
        "_expect": {"flipped": flipped, "unsure": unsure, "unlabelled": unlabelled},
    }


def test_compute_on_a_labels_file_with_known_answers(
    items: list[dict[str, Any]], prompts: dict[str, str]
) -> None:
    labels = _known_labels(items, prompts)
    expect = labels.pop("_expect")
    report = jc.compute(items, _verdicts(items, prompts), labels)

    fc = report["rubrics"]["factually_consistent"]
    assert fc["items"] == 30 and fc["compared"] == 28 and fc["agree"] == 26
    assert fc["unsure"] == [expect["unsure"]] and fc["unlabelled"] == [expect["unlabelled"]]
    # humans: 13 pass (15 - 2 flipped), 15 fail (15 - unsure - unlabelled + 2 flipped).
    assert fc["confusion"] == {
        "human_pass": {"judge_pass": 13, "judge_fail": 0},
        "human_fail": {"judge_pass": 2, "judge_fail": 13},
    }
    # p_o = 26/28; p_e = (13/28)(15/28) + (15/28)(13/28).
    p_o, p_e = 26 / 28, 2 * (13 / 28) * (15 / 28)
    assert fc["cohen_kappa"] == pytest.approx((p_o - p_e) / (1 - p_e))

    sr = report["rubrics"]["safe_refusal"]
    assert sr["compared"] == 30 and sr["agree"] == 29
    assert sr["confusion"]["human_pass"]["judge_fail"] == 1

    overall = report["overall"]
    assert overall["compared"] == 58 and overall["agree"] == 55
    assert len(overall["unsure"]) == 1 and len(overall["unlabelled"]) == 1

    disagreements = {d["id"]: d for d in report["disagreements"]}
    assert set(disagreements) == expect["flipped"]
    for item_id, d in disagreements.items():
        item = next(i for i in items if i["id"] == item_id)
        assert d["judge"] == item["intended"] and d["human"] == _flip(item["intended"])
        assert d["payload"] == item["payload"]
        assert d["judge_reasons"] and d["human_note"] == f"note {item_id}"

    text = jc.format_report(report, "labels.json")
    assert "Disagreements (3)" in text
    for item_id in expect["flipped"]:
        assert item_id in text


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda lab, ver: lab.update(items_sha256="0" * 64), "different item set"),
        (lambda lab, ver: ver.update(items_sha256="0" * 64), "judge verdicts are for a different"),
        (
            lambda lab, ver: lab.update(
                prompt_sha256={"factually_consistent": "x", "safe_refusal": "y"}
            ),
            "different rubric text",
        ),
        (lambda lab, ver: lab["labels"].update({"FC-99": {"label": "pass"}}), "do not exist"),
        (lambda lab, ver: lab["labels"].update({"FC-01": {"label": "maybe"}}), "is not one of"),
        (lambda lab, ver: ver["verdicts"].pop("SR-07"), "no judge verdict"),
    ],
)
def test_compute_refuses_inputs_that_do_not_belong_together(
    items: list[dict[str, Any]], prompts: dict[str, str], mutate: Any, message: str
) -> None:
    labels = _known_labels(items, prompts)
    labels.pop("_expect")
    verdicts = _verdicts(items, prompts)
    mutate(labels, verdicts)
    with pytest.raises(jc.CalibrationInputError, match=message):
        jc.compute(items, verdicts, labels)


def test_a_rubric_change_is_compared_only_when_asked(
    items: list[dict[str, Any]], prompts: dict[str, str]
) -> None:
    labels = _known_labels(items, prompts)
    labels.pop("_expect")
    verdicts = _verdicts(items, prompts)
    verdicts["prompt_sha256"] = {**verdicts["prompt_sha256"], "safe_refusal": "e" * 64}
    with pytest.raises(jc.CalibrationInputError, match="--accept-rubric-change"):
        jc.compute(items, verdicts, labels)
    report = jc.compute(items, verdicts, labels, accept_rubric_change=True)
    assert any(w.startswith("RUBRIC CHANGED") for w in report["warnings"])
    assert report["overall"]["compared"] == 58


def test_compute_warns_when_the_committed_prompt_has_moved(
    items: list[dict[str, Any]], prompts: dict[str, str]
) -> None:
    labels = _known_labels(items, prompts)
    labels.pop("_expect")
    moved = {**jc.prompt_digests(prompts), "safe_refusal": "f" * 64}
    report = jc.compute(items, _verdicts(items, prompts), labels, current_prompt_sha256=moved)
    assert any("['safe_refusal']" in w and "has changed" in w for w in report["warnings"])


def test_cli_computes_on_a_synthetic_labels_file(
    tmp_path: Path, items: list[dict[str, Any]], prompts: dict[str, str], capsys: Any
) -> None:
    labels_path = tmp_path / "synthetic.json"
    verdicts_path = tmp_path / "verdicts.json"
    report_path = tmp_path / "report.json"
    verdicts_path.write_text(json.dumps(_verdicts(items, prompts)), encoding="utf-8")

    assert jc.main(["synthetic", str(labels_path), "--seed", "11"]) == 0
    synthetic = json.loads(labels_path.read_text(encoding="utf-8"))
    assert synthetic["synthetic"] is True and synthetic["schema_version"] == jc.LABELS_SCHEMA

    capsys.readouterr()
    code = jc.main(
        ["compute", str(labels_path), "--verdicts", str(verdicts_path), "--json", str(report_path)]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "SYNTHETIC LABELS" in out and "Cohen's kappa" in out and "Disagreements (" in out

    report = json.loads(report_path.read_text(encoding="utf-8"))
    labelled = {k: v["label"] for k, v in synthetic["labels"].items()}
    assert report["overall"]["compared"] == sum(v in ("pass", "fail") for v in labelled.values())
    assert len(report["overall"]["unsure"]) == sum(v == "unsure" for v in labelled.values())
    assert len(report["overall"]["unlabelled"]) == 60 - len(labelled)
    # The synthetic labels are random, so they owe nothing to the intended labels.
    assert (
        len(report["disagreements"]) == report["overall"]["compared"] - report["overall"]["agree"]
    )


def test_cli_exits_2_on_a_labels_file_for_another_item_set(
    tmp_path: Path, items: list[dict[str, Any]], prompts: dict[str, str]
) -> None:
    labels_path = tmp_path / "labels.json"
    verdicts_path = tmp_path / "verdicts.json"
    doc = jc.synthetic_labels(items, jc.prompt_digests(prompts))
    doc["items_sha256"] = "0" * 64
    labels_path.write_text(json.dumps(doc), encoding="utf-8")
    verdicts_path.write_text(json.dumps(_verdicts(items, prompts)), encoding="utf-8")
    assert jc.main(["compute", str(labels_path), "--verdicts", str(verdicts_path)]) == 2


# ------------------------------------------------- labels that are not independent


def test_labels_marked_not_independent_are_a_rubric_audit(
    items: list[dict[str, Any]], prompts: dict[str, str]
) -> None:
    labels = _known_labels(items, prompts)
    labels.pop("_expect")
    report = jc.compute(items, _verdicts(items, prompts), labels)
    assert report["independent"] is True
    assert jc.format_report(report).startswith("Judge calibration\n")

    labels["independent"] = False
    report = jc.compute(items, _verdicts(items, prompts), labels)
    assert report["independent"] is False
    assert any(w.startswith("LABELS NOT INDEPENDENT") for w in report["warnings"])
    text = jc.format_report(report)
    assert text.startswith("Rubric audit (labels not independent): NOT a judge calibration\n")
    assert "Judge calibration" not in text


def test_committed_rubric_audit_stays_marked_not_independent() -> None:
    """Set 1's bulk-confirmed LLM labels must never read as a calibration."""
    labels = json.loads((jc.CAL_DIR / "rubric-audit-labels.json").read_text(encoding="utf-8"))
    assert labels["independent"] is False
    report = (jc.CAL_DIR / "rubric-audit-report.txt").read_text(encoding="utf-8")
    assert report.startswith("Rubric audit (labels not independent): NOT a judge calibration\n")
    assert (
        json.loads((jc.CAL_DIR / "rubric-audit-report.json").read_text(encoding="utf-8"))[
            "independent"
        ]
        is False
    )


def test_set_dir_selects_the_second_set(capsys: Any) -> None:
    assert jc.main(["--set-dir", str(jc.SET2_DIR), "build", "--check"]) == 0
    assert "set2" in capsys.readouterr().out
