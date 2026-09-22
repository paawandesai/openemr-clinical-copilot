"""
The judge's price table has to name the model the judge actually uses.

`judge_config.json` pins a model and carries a price per million tokens for
it. Those two facts are one edit apart, and the failure mode when they drift
is quiet: `estimated_cost_usd()` returns 0.0, the run prints `~$0.0000`, and
that is indistinguishable from a run whose cache was warm. A number that
reads the same whether it is right or missing is worse than no number.

So the pairing is a hard error at startup — `check_pricing_config()` runs on
every gate run, `--det-only` included, where the judge is otherwise never
constructed — and these tests pin it.

They live in `tests/` rather than in the gate because they are about the
gate's own configuration and want to run in the offline unit suite. The
enforcement itself lives in `evals/w2/gate/judge.py`, which is gated: an
implementation session can delete this file, and the gate will still refuse
to start.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from evals.w2.gate.judge import check_pricing_config, price_per_mtok
from evals.w2.gate.models import GateError
from evals.w2.gate.paths import JUDGE_CONFIG

COMMITTED: dict[str, Any] = json.loads(JUDGE_CONFIG.read_text(encoding="utf-8"))


def test_the_committed_config_prices_the_model_it_pins() -> None:
    """The check that would have caught a model change with no price."""
    price_in, price_out = price_per_mtok(COMMITTED)
    assert price_in > 0
    assert price_out > 0


def test_the_startup_check_passes_on_the_committed_config() -> None:
    check_pricing_config()


def test_every_priced_model_is_a_string_pair_of_numbers() -> None:
    table = COMMITTED["pricing_usd_per_mtok"]
    assert table, "an empty price table is not a price table"
    for model, entry in table.items():
        assert isinstance(model, str) and model
        assert set(entry) == {"input", "output"}, entry
        assert isinstance(entry["input"], (int, float))
        assert isinstance(entry["output"], (int, float))


def test_a_model_with_no_price_entry_raises() -> None:
    """The regression this file exists for: change the model, forget the price."""
    config = dict(COMMITTED)
    config["model_version"] = "claude-some-future-model-20991231"
    with pytest.raises(GateError) as caught:
        price_per_mtok(config)
    message = str(caught.value)
    assert "claude-some-future-model-20991231" in message
    assert "pricing_usd_per_mtok" in message


def test_a_missing_price_table_raises() -> None:
    config = {key: value for key, value in COMMITTED.items() if key != "pricing_usd_per_mtok"}
    with pytest.raises(GateError):
        price_per_mtok(config)


def test_an_empty_price_table_raises() -> None:
    config = dict(COMMITTED, pricing_usd_per_mtok={})
    with pytest.raises(GateError):
        price_per_mtok(config)


@pytest.mark.parametrize(
    "entry",
    [
        {"input": 1.0},
        {"output": 5.0},
        {"input": "one", "output": 5.0},
        {"input": None, "output": None},
        "1.0/5.0",
    ],
)
def test_a_malformed_price_entry_raises(entry: Any) -> None:
    config = dict(
        COMMITTED,
        pricing_usd_per_mtok={COMMITTED["model_version"]: entry},
    )
    with pytest.raises(GateError):
        price_per_mtok(config)


def test_the_cost_is_computed_from_the_table_not_from_a_constant() -> None:
    """
    A thousand input and a thousand output tokens priced at the committed
    rates. Done arithmetically rather than against a hard-coded dollar figure,
    so changing the price updates the expectation with it.
    """
    price_in, price_out = price_per_mtok(COMMITTED)
    expected = (1000 * price_in + 1000 * price_out) / 1_000_000

    class _Priced:
        estimated_cost_usd = __import__(
            "evals.w2.gate.judge", fromlist=["Judge"]
        ).Judge.estimated_cost_usd

        def __init__(self) -> None:
            self.input_tokens = 1000
            self.output_tokens = 1000
            self.price_in = price_in
            self.price_out = price_out

    assert _Priced().estimated_cost_usd() == pytest.approx(expected)


def test_the_gate_source_no_longer_carries_a_hard_coded_price() -> None:
    """
    The constants this replaced were `PRICE_PER_MTOK_INPUT` / `_OUTPUT` in
    judge.py. If either name comes back, the price has two homes again and
    one of them will go stale.
    """
    source = (Path(__file__).resolve().parents[1] / "evals/w2/gate/judge.py").read_text(
        encoding="utf-8"
    )
    assert "PRICE_PER_MTOK_INPUT" not in source
    assert "PRICE_PER_MTOK_OUTPUT" not in source
