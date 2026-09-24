"""
No patient identifier reaches stdout through uvicorn's access log.

Both document routes take `?patient_id=<uuid>`, and uvicorn's access log,
on by default, prints the request line with its query string: every upload
and every status poll wrote the patient's uuid to stdout (D56,
"Consequences"). What that line carried that nothing else does is the query
string. Method, route template, status and latency are already on
`http_request_event` (D38), so the access log is off, twice:

- the Dockerfile's command passes `--no-access-log`;
- `configure_logging` puts a filter on the `uvicorn.access` logger that
  drops every record (`app/middleware/logging.py`), so a launcher that
  forgets the flag (a start script, a compose `command:`, a bare
  `uvicorn app.main:app`) is held to it too.

These tests run the real server, not a TestClient (which never reaches
uvicorn's logging): `tests/access_log_server.py` starts the agent through
uvicorn's own CLI with the Dockerfile's arguments, OpenEMR and the vision
call faked, then an upload and its status polls go over a socket and the
process's whole stdout and stderr are scanned.

UUIDs the agent minted itself for the request are allowed: the correlation
id the test sent (one per encounter, D36) and the extraction id the status
route returned. Any other UUID fails the test, the patient's first.

    cd agent && python -m pytest tests/test_access_log.py
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import pytest

from tests.access_log_server import PATIENT, SAMPLE
from tests.intake_extractor_support import pdf_bytes
from tests.test_w2_documents_api import bearer

AGENT_ROOT = Path(__file__).resolve().parents[1]
UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def dockerfile_command() -> list[str]:
    """The `CMD` of agent/Dockerfile, as the exec-form list it is."""
    text = (AGENT_ROOT / "Dockerfile").read_text()
    lines = [line for line in text.splitlines() if line.startswith("CMD ")]
    assert len(lines) == 1, "expected exactly one CMD in agent/Dockerfile"
    return json.loads(lines[0][len("CMD ") :])


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def uvicorn_args(command: list[str], port: int) -> list[str]:
    """The Dockerfile's uvicorn arguments, bound to loopback on a free port."""
    assert command[0] == "uvicorn"
    args = list(command[1:])
    args[args.index("--host") + 1] = "127.0.0.1"
    args[args.index("--port") + 1] = str(port)
    return args


def run_encounter(args: list[str], port: int) -> tuple[str, set[str]]:
    """
    Start the server with `args`, upload the sample and poll it to the end.
    Returns everything the process wrote, and the UUIDs the agent minted.
    """
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("LANGFUSE_", "LANGCHAIN_", "LANGSMITH_", "AGENT_ENV"))
    }
    env["PYTHONUNBUFFERED"] = "1"
    process = subprocess.Popen(
        [sys.executable, "-m", "tests.access_log_server", *args],
        cwd=AGENT_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    correlation_id = str(uuid.uuid4())
    minted = {correlation_id}
    headers = {**bearer(), "X-Correlation-ID": correlation_id}
    base = f"http://127.0.0.1:{port}"
    try:
        with httpx.Client(base_url=base, timeout=30) as client:
            deadline = time.monotonic() + 60
            while True:
                try:
                    probe = client.get("/health", headers={"X-Correlation-ID": correlation_id})
                    if probe.status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                if process.poll() is not None or time.monotonic() > deadline:
                    raise AssertionError("the agent did not start")
                time.sleep(0.2)

            uploaded = client.post(
                "/documents",
                params={"kind": "lab_pdf", "patient_id": PATIENT},
                content=pdf_bytes(SAMPLE),
                headers={**headers, "Content-Type": "application/pdf"},
            )
            assert uploaded.status_code == 200, uploaded.text
            document_id = uploaded.json()["document_id"]

            polls = 0
            deadline = time.monotonic() + 60
            while True:
                state = client.get(
                    f"/documents/{document_id}/extraction",
                    params={"patient_id": PATIENT},
                    headers=headers,
                )
                polls += 1
                assert state.status_code == 200, state.text
                body = state.json()
                if body["status"] in ("complete", "failed"):
                    break
                assert time.monotonic() < deadline, "extraction did not finish"
                time.sleep(0.1)
            assert body["status"] == "complete", body
            assert polls >= 1
            if body.get("extraction_id"):
                minted.add(body["extraction_id"])
    finally:
        process.terminate()
        try:
            output, _ = process.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            output, _ = process.communicate()
    return output, minted


def assert_no_foreign_uuid(output: str, minted: set[str]) -> None:
    assert PATIENT not in output, "the patient's uuid reached stdout"
    foreign = {match for match in UUID.findall(output) if match.lower() not in minted}
    lines = [line for line in output.splitlines() if any(value in line for value in foreign)]
    assert not foreign, f"UUID-shaped values on stdout the agent did not mint: {lines}"
    assert "patient_id=" not in output, "a query string reached stdout"


@pytest.fixture(scope="module")
def dockerfile_run() -> tuple[str, set[str]]:
    port = free_port()
    return run_encounter(uvicorn_args(dockerfile_command(), port), port)


@pytest.fixture(scope="module")
def flagless_run() -> tuple[str, set[str]]:
    port = free_port()
    args = [arg for arg in uvicorn_args(dockerfile_command(), port) if arg != "--no-access-log"]
    return run_encounter(args, port)


def test_the_dockerfile_command_turns_the_access_log_off() -> None:
    assert "--no-access-log" in dockerfile_command()


def test_the_deployed_command_writes_no_patient_uuid_on_upload_and_poll(
    dockerfile_run: tuple[str, set[str]],
) -> None:
    output, minted = dockerfile_run
    # The run happened: the agent's own event lines for both routes are there.
    assert "w2_document_upload_accepted" in output
    assert "w2_extraction_state_read" in output
    assert_no_foreign_uuid(output, minted)


def test_a_launcher_without_the_flag_writes_no_patient_uuid_either(
    flagless_run: tuple[str, set[str]],
) -> None:
    output, minted = flagless_run
    assert "w2_document_upload_accepted" in output
    assert "w2_extraction_state_read" in output
    assert_no_foreign_uuid(output, minted)


def test_the_request_line_survives_as_a_route_template(
    flagless_run: tuple[str, set[str]],
) -> None:
    """What the access log said, minus the query string, is on `http_request_event`."""
    events = []
    for line in flagless_run[0].splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and record.get("event_type") == "http_request_event":
            events.append(record["event_data"])
    routes = {(event["method"], event["route"], event["status_code"]) for event in events}
    assert ("POST", "/documents", 200) in routes
    assert ("GET", "/documents/{document_id}/extraction", 200) in routes
    assert all(isinstance(event["latency_ms"], int | float) for event in events)


def test_the_filter_survives_uvicorn_configuring_its_loggers_afterwards() -> None:
    """
    The reverse of the CLI's order: the app configured first, then uvicorn's
    `dictConfig` (an embedded `uvicorn.run(app)`, or a test harness). That
    resets `disabled` and the handlers, and keeps filters.
    """
    import logging
    import logging.config

    from uvicorn.config import LOGGING_CONFIG

    from app.middleware.logging import silence_access_log

    access = logging.getLogger("uvicorn.access")
    saved = (list(access.filters), list(access.handlers), access.disabled, access.propagate)
    try:
        silence_access_log()
        logging.config.dictConfig(LOGGING_CONFIG)
        seen: list[logging.LogRecord] = []
        catcher = logging.Handler()
        catcher.emit = seen.append  # type: ignore[method-assign]
        access.addHandler(catcher)
        access.info(
            '%s - "%s %s HTTP/%s" %d',
            "127.0.0.1:1",
            "GET",
            f"/documents/x/extraction?patient_id={PATIENT}",
            "1.1",
            200,
        )
        assert seen == []
    finally:
        access.filters[:] = saved[0]
        access.handlers[:] = saved[1]
        access.disabled, access.propagate = saved[2], saved[3]
