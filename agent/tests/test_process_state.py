"""
The startup warning for per-process state (app/process_state.py): the
conversation and document stores are in memory, so a deploy with more than
one worker breaks uploads, status polls and follow-ups. It must not do so
silently.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from app.main import app
from app.process_state import MULTI_PROCESS_EVENT, configured_workers, warn_if_multi_process


@pytest.mark.parametrize(
    ("argv", "environ", "expected"),
    [
        (["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"], {}, 1),
        (["uvicorn", "app.main:app", "--workers", "4"], {}, 4),
        (["uvicorn", "app.main:app", "--workers=2"], {}, 2),
        (["gunicorn", "-w", "3", "app.main:app"], {}, 3),
        (["gunicorn", "app.main:app"], {"GUNICORN_CMD_ARGS": "--bind :8000 -w 2"}, 2),
        (["uvicorn", "app.main:app"], {"WEB_CONCURRENCY": "3"}, 3),
        (["uvicorn", "app.main:app", "--workers", "1"], {"WEB_CONCURRENCY": "3"}, 1),
        (["uvicorn", "app.main:app"], {"WEB_CONCURRENCY": "lots"}, 1),
    ],
)
def test_configured_workers(argv, environ, expected) -> None:
    assert configured_workers(argv, environ) == expected


def test_one_worker_is_silent() -> None:
    with capture_logs() as logs:
        assert warn_if_multi_process(["uvicorn", "app.main:app"], {}) == 1
    assert [e for e in logs if e["event"] == MULTI_PROCESS_EVENT] == []


def test_more_than_one_worker_warns() -> None:
    with capture_logs() as logs:
        warn_if_multi_process(["uvicorn", "app.main:app", "--workers", "2"], {})
    (event,) = [e for e in logs if e["event"] == MULTI_PROCESS_EVENT]
    assert event["log_level"] == "warning" and event["configured_workers"] == 2
    assert set(event["per_process_stores"]) == {"conversation", "documents", "rerank_circuit"}


def test_the_service_warns_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_CONCURRENCY", "2")
    with capture_logs() as logs, TestClient(app):
        pass
    assert [e for e in logs if e["event"] == MULTI_PROCESS_EVENT]
