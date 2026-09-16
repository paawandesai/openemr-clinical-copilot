"""
Fixtures and the pass/fail summary for the eval suite (evals/README.md).

    cd agent
    python -m pytest evals                                   # summary at the end
    python -m pytest evals --eval-results evals/RESULTS.md   # also write the results file

Credentials come from the environment, or agent/.env for anything not
exported: FHIR_CLIENT_ID, FHIR_CLIENT_SECRET, ANTHROPIC_API_KEY (in-process
cases), optionally FHIR_TEST_USERNAME / FHIR_TEST_PASSWORD, EVAL_AGENT_URL,
EVAL_EXPIRED_TOKEN_FILE. Without FHIR client credentials every live case is
skipped, and reported as SKIP, never as PASS.
"""

from __future__ import annotations

import datetime as dt
import subprocess
import warnings
from pathlib import Path
from typing import Any

import httpx
import pytest

from evals.harness import (
    AGENT_DIR,
    EvalConfig,
    Inconclusive,
    InProcessPipeline,
    LiveAgent,
    Runner,
    TokenMint,
    load_dotenv,
    select_panel,
)
from evals.oracle import FhirOracle

# Before anything imports app/: app/fhir/client.py reads FHIR_BASE_URL and
# FHIR_VERIFY_TLS at import time.
load_dotenv(AGENT_DIR / ".env")

# Both hops use certificates that are not publicly trusted: OpenEMR's is
# self-signed (F-36), the agent's comes from Caddy's local CA
# (agent/docs/deployment.md §1). Verification is off for both, as in the API
# collection; this suite therefore does not test transport security.
warnings.filterwarnings("ignore", message="Unverified HTTPS request")

_RESULTS = pytest.StashKey[dict[str, dict[str, Any]]]()
_ENVIRONMENT = pytest.StashKey[dict[str, Any]]()


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--eval-results", default=None, help="write a Markdown results file to this path")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "eval_case(id, title, guards, cites, level): eval case metadata, see evals/README.md"
    )
    config.stash[_RESULTS] = {}
    config.stash[_ENVIRONMENT] = {}


# -- fixtures ------------------------------------------------------------------


@pytest.fixture(scope="session")
def cfg() -> EvalConfig:
    return EvalConfig.from_env()


@pytest.fixture(scope="session")
def aio() -> Any:
    runner = Runner()
    yield runner
    runner.close()


@pytest.fixture(scope="session")
def http(aio: Runner) -> Any:
    client = httpx.AsyncClient(verify=False, timeout=300.0)
    yield client
    aio.run(client.aclose())


@pytest.fixture(scope="session")
def tokens(cfg: EvalConfig, http: httpx.AsyncClient, aio: Runner) -> TokenMint:
    if not (cfg.client_id and cfg.client_secret):
        pytest.skip("FHIR_CLIENT_ID / FHIR_CLIENT_SECRET not set: live cases cannot get an OpenEMR token")
    return TokenMint(cfg, http, aio)


@pytest.fixture(scope="session")
def oracle(cfg: EvalConfig, http: httpx.AsyncClient, tokens: TokenMint) -> FhirOracle:
    return FhirOracle(http, cfg.fhir_base_url, tokens.get("oracle"))


def _describe(chart: Any) -> str:
    return (
        f"{chart.patient_id} (conditions {chart.count('Condition')}/{chart.active_count('Condition')} active, "
        f"medications {chart.count('MedicationRequest')}/{chart.active_count('MedicationRequest')} active, "
        f"allergies {chart.count('AllergyIntolerance')}, encounters {chart.encounters})"
    )


@pytest.fixture(scope="session")
def panel(oracle: FhirOracle, aio: Runner, request: pytest.FixtureRequest) -> Any:
    """Every patient's chart, read by the oracle once per session, and the strata picked from it."""

    async def read_all() -> dict[str, Any]:
        import asyncio

        ids = await oracle.patient_ids()
        charts = await asyncio.gather(*(oracle.chart(pid, encounters=True) for pid in ids))
        return {chart.patient_id: chart for chart in charts}

    charts = aio.run(read_all())
    selected = select_panel(charts)
    env = request.config.stash[_ENVIRONMENT]
    env["population"] = f"{len(charts)} patients visible to the test clinician"
    env["panel"] = {stratum: None if pid is None else _describe(charts[pid]) for stratum, pid in selected.picks.items()}
    return selected


@pytest.fixture(scope="session")
def agent(
    cfg: EvalConfig, http: httpx.AsyncClient, aio: Runner, tokens: TokenMint, request: pytest.FixtureRequest
) -> LiveAgent:
    live = LiveAgent(cfg, http, aio, tokens)
    env = request.config.stash[_ENVIRONMENT]
    try:
        openapi = aio.run(http.get(f"{cfg.agent_url}/openapi.json")).json()
        ready = aio.run(http.get(f"{cfg.agent_url}/ready"))
        env["agent"] = f"{cfg.agent_url} (API version {openapi['info']['version']}, /ready HTTP {ready.status_code})"
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        pytest.skip(f"agent endpoint {cfg.agent_url} unreachable: {type(exc).__name__}")
    return live


@pytest.fixture(scope="session")
def pipeline(aio: Runner, tokens: TokenMint) -> InProcessPipeline:
    import os

    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set: in-process cases draft with the real model")
    return InProcessPipeline(aio)


@pytest.fixture
def observe(request: pytest.FixtureRequest) -> Any:
    """Record a measured value on the case; it goes into the summary and results file."""

    def record(name: str, value: Any) -> None:
        request.node.user_properties.append((name, value))

    return record


# -- summary -------------------------------------------------------------------


def _message(report: pytest.TestReport) -> str:
    if report.skipped and isinstance(report.longrepr, tuple):
        return str(report.longrepr[2]).removeprefix("Skipped: ")
    crash = getattr(report.longrepr, "reprcrash", None)
    text = crash.message if crash is not None else str(report.longrepr)
    return text.removeprefix("Failed: ")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[Any]) -> Any:
    outcome = yield
    report: pytest.TestReport = outcome.get_result()
    marker = item.get_closest_marker("eval_case")
    if marker is None:
        return
    entry = item.config.stash[_RESULTS].setdefault(
        item.nodeid,
        {"meta": marker.kwargs, "outcome": "ERROR", "message": "did not run", "observations": item.user_properties},
    )
    if report.when == "setup":
        if report.skipped:
            entry.update(outcome="SKIP", message=_message(report))
        elif report.failed:
            inconclusive = call.excinfo is not None and call.excinfo.errisinstance(Inconclusive)
            prefix = "inconclusive: " if inconclusive else "setup error: "
            entry.update(outcome="ERROR", message=prefix + _message(report))
    elif report.when == "call":
        if report.passed:
            entry.update(outcome="PASS", message="")
        elif report.skipped:
            entry.update(outcome="SKIP", message=_message(report))
        elif call.excinfo is not None and call.excinfo.errisinstance(Inconclusive):
            entry.update(outcome="ERROR", message=f"inconclusive: {call.excinfo.value}")
        else:
            entry.update(outcome="FAIL", message=_message(report))
    elif report.when == "teardown" and report.failed and entry["outcome"] == "PASS":
        entry.update(outcome="ERROR", message=_message(report))


def _order(entry: dict[str, Any]) -> tuple[str, int]:
    case_id = entry["meta"]["id"]
    return case_id[0], int(case_id[1:])


def pytest_terminal_summary(terminalreporter: Any, exitstatus: int, config: pytest.Config) -> None:
    entries = sorted(config.stash[_RESULTS].values(), key=_order)
    if not entries:
        return
    tr = terminalreporter
    tr.section("Pre-Visit Brief eval summary")
    for entry in entries:
        meta = entry["meta"]
        tr.write_line(f"{entry['outcome']:<5} {meta['id']:<4} {meta['title']}")
        tr.write_line(f"             guards: {meta['guards']}")
        tr.write_line(f"             traces: {meta['cites']}  [{meta['level']}]")
        if entry["message"]:
            for line in entry["message"].splitlines()[:6]:
                tr.write_line(f"             > {line}")
    totals = {k: sum(1 for e in entries if e["outcome"] == k) for k in ("PASS", "FAIL", "SKIP", "ERROR")}
    tr.write_line("")
    tr.write_line("  ".join(f"{k} {v}" for k, v in totals.items()) + f"  (of {len(entries)} cases)")

    path = config.getoption("--eval-results")
    if path:
        _write_results(Path(path), entries, totals, config.stash[_ENVIRONMENT])
        tr.write_line(f"results written to {path}")


def _git_head() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=AGENT_DIR, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _write_results(path: Path, entries: list[dict[str, Any]], totals: dict[str, int], env: dict[str, Any]) -> None:
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        "# Eval results — Pre-Visit Brief agent",
        "",
        "Generated by `python -m pytest evals --eval-results evals/RESULTS.md`; do not edit by hand.",
        "Case definitions and what each guards against: `evals/README.md`.",
        "",
        f"- **Run:** {now}, code at `{_git_head()}` (in-process cases run this checkout)",
        f"- **Agent:** {env.get('agent', 'not reached')}",
        f"- **Population:** {env.get('population', 'not read')} (synthetic Synthea data, F-41)",
        "- **Totals:** " + ", ".join(f"{k} {v}" for k, v in totals.items()) + f" of {len(entries)} cases",
        "",
        "Patients picked for each stratum this run (F-43: results name the population they came from):",
        "",
    ]
    for stratum, description in (env.get("panel") or {}).items():
        lines.append(f"- `{stratum}`: {description or 'none in this population'}")
    lines += ["", "| Result | Case | Title | Level |", "|---|---|---|---|"]
    for entry in entries:
        meta = entry["meta"]
        lines.append(f"| **{entry['outcome']}** | {meta['id']} | {_cell(meta['title'])} | {meta['level']} |")
    lines += ["", "## Per case", ""]
    for entry in entries:
        meta = entry["meta"]
        lines += [
            f"### {meta['id']} — {entry['outcome']} — {meta['title']}",
            "",
            f"- **Guards against:** {meta['guards']}",
            f"- **Traces to:** {meta['cites']}",
        ]
        for name, value in entry["observations"]:
            lines.append(f"- {name}: {value}")
        if entry["message"]:
            lines += ["", "```", entry["message"].rstrip(), "```"]
        lines.append("")
    path.write_text("\n".join(lines))
