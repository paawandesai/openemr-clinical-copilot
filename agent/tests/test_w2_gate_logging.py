"""
The gate runner's stdout carries HMAC'd session keys and record ids, never
raw ones (D56, D61).

`docs/w2/EVAL_GATE_EVIDENCE.md` is the gate's own stdout, committed. Before
D61 the runner rendered with structlog's default chain, so every supervisor
line in it held `sentinel-user:<patient uuid>:<session>`. These tests pin the
runner's chain and that `main` installs it before anything runs.

    cd agent && python -m pytest tests/test_w2_gate_logging.py
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
import structlog

from app.middleware import telemetry_key
from evals.w2.gate import run

PATIENT = "a2c373ec-57bf-41cb-8767-94410dcf663d"
KEY = f"sentinel-user:{PATIENT}:session-w2-gate-0001"
RECORD = "cond-1"


@pytest.fixture
def gate_logging(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv(telemetry_key.SECRET_ENV, raising=False)
    monkeypatch.delenv(telemetry_key.MODE_ENV, raising=False)
    telemetry_key.reset_for_tests()
    run.configure_logging()
    yield
    structlog.reset_defaults()
    telemetry_key.reset_for_tests()


def test_a_gate_log_line_carries_the_hmac_not_the_key(
    gate_logging: None, capsys: pytest.CaptureFixture[str]
) -> None:
    structlog.get_logger().info(
        "agent_event",
        patient_session_key=KEY,
        event_data={"source_id": RECORD, "patient_session_key": KEY},
    )
    out = capsys.readouterr().out
    assert PATIENT not in out
    assert KEY not in out
    assert f"'{RECORD}'" not in out
    assert f"patient_session_key={telemetry_key.emitted_key(KEY)}" in out
    assert telemetry_key.emitted_key(KEY).startswith(telemetry_key.DEV_PREFIX)
    assert telemetry_key.emitted_record_id(RECORD) in out


def test_a_key_bound_in_contextvars_is_replaced_too(
    gate_logging: None, capsys: pytest.CaptureFixture[str]
) -> None:
    structlog.contextvars.bind_contextvars(patient_session_key=KEY)
    try:
        structlog.get_logger().info("agent_event")
    finally:
        structlog.contextvars.clear_contextvars()
    out = capsys.readouterr().out
    assert PATIENT not in out
    assert telemetry_key.emitted_key(KEY) in out


def test_capture_logs_still_sees_the_raw_event(gate_logging: None) -> None:
    """The I and B checks read `capture_logs`, which must keep working under this chain."""
    from structlog.testing import capture_logs

    with capture_logs() as entries:
        structlog.get_logger().info("agent_event", patient_session_key=KEY)
    assert entries and entries[0]["patient_session_key"] == KEY


def test_main_configures_logging_before_anything_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    class Configured(Exception):
        pass

    def configure() -> None:
        raise Configured

    def must_not_run(*_: Any, **__: Any) -> None:
        raise AssertionError("the gate loaded its checks before configuring logging")

    monkeypatch.setattr(run, "configure_logging", configure)
    monkeypatch.setattr(run, "load_all_checks", must_not_run)
    with pytest.raises(Configured):
        run.main(["--det-only"])
