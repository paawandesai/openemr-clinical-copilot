"""
Plumbing shared by the eval cases: configuration, real OpenEMR tokens, the
live agent endpoint, the in-process pipeline, and patient selection.

Nothing here is a fake of the agent. Live cases call the deployed endpoint;
in-process cases call app.graph.pre_visit_brief.run_pre_visit_brief against
the real droplet FHIR API and the real Anthropic API. Where a case replaces
one step (an injected claim, a mutated record status, a mutated renderer),
the case says so in its own docstring and in evals/README.md.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest import mock

import httpx
import pytest

from evals.oracle import BRIEF_TYPES, PatientChart

AGENT_DIR = Path(__file__).resolve().parent.parent

# The brief's own scope set, as agent/api-collection/README.md requests it.
BRIEF_SCOPES = (
    "openid",
    "user/Patient.read",
    "user/Condition.read",
    "user/MedicationRequest.read",
    "user/AllergyIntolerance.read",
)
SCOPE_SETS: dict[str, tuple[str, ...]] = {
    "brief": BRIEF_SCOPES,
    # F-07's probe token, scope for scope.
    "patient_only": ("openid", "api:fhir", "user/Patient.read"),
    # Everything the brief needs except allergies.
    "no_allergy_read": tuple(s for s in BRIEF_SCOPES if s != "user/AllergyIntolerance.read"),
    # The suite's own chart reads; never sent to the agent.
    "oracle": (*BRIEF_SCOPES, "user/Encounter.read"),
}


class Inconclusive(Exception):
    """
    An upstream dependency failed, so the case could not observe the behaviour
    it checks. Reported as ERROR ("inconclusive"), never as PASS or FAIL: an
    outage is not evidence for or against a leak or a disclosure defect.
    """


def case(id: str, *, title: str, guards: str, cites: str, level: str) -> pytest.MarkDecorator:
    """Metadata every eval case carries; printed in the summary and results file."""
    return pytest.mark.eval_case(id=id, title=title, guards=guards, cites=cites, level=level)


def load_dotenv(path: Path) -> None:
    """agent/.env, without overriding anything already exported."""
    if not path.is_file():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip().strip("'\""))


@dataclass(frozen=True)
class EvalConfig:
    agent_url: str
    fhir_base_url: str
    client_id: str | None
    client_secret: str | None
    username: str
    password: str
    latency_budget_seconds: float

    @property
    def token_url(self) -> str:
        return f"{self.fhir_base_url.rsplit('/apis/', 1)[0]}/oauth2/default/token"

    @classmethod
    def from_env(cls) -> EvalConfig:
        return cls(
            agent_url=os.environ.get("EVAL_AGENT_URL", "https://201.79.25.22:8443").rstrip("/"),
            fhir_base_url=os.environ.get("FHIR_BASE_URL", "https://201.79.25.22:9300/apis/default/fhir"),
            client_id=os.environ.get("FHIR_CLIENT_ID"),
            client_secret=os.environ.get("FHIR_CLIENT_SECRET"),
            # The smoke tests' defaults (scripts/smoke_test_fhir.py). F-32
            # records that this demo account is itself a live risk.
            username=os.environ.get("FHIR_TEST_USERNAME", "physician"),
            password=os.environ.get("FHIR_TEST_PASSWORD", "physician"),
            # USERS.md §1-2: the ~90 seconds between two patients.
            latency_budget_seconds=float(os.environ.get("EVAL_LATENCY_BUDGET_SECONDS", "90")),
        )


def jwt_claims(token: str) -> dict[str, Any]:
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class Runner:
    """One event loop for the whole session, so cached async clients stay valid."""

    def __init__(self) -> None:
        self._runner = asyncio.Runner()

    def run(self, coro: Any) -> Any:
        return self._runner.run(coro)

    def close(self) -> None:
        self._runner.close()


@dataclass
class TokenMint:
    cfg: EvalConfig
    http: httpx.AsyncClient
    aio: Runner
    _cache: dict[str, str] = field(default_factory=dict)

    def get(self, label: str) -> str:
        if label not in self._cache:
            self._cache[label] = self.aio.run(self._mint(SCOPE_SETS[label]))
        return self._cache[label]

    async def _mint(self, scopes: tuple[str, ...]) -> str:
        response = await self.http.post(
            self.cfg.token_url,
            data={
                "grant_type": "password",
                "client_id": self.cfg.client_id,
                "client_secret": self.cfg.client_secret,
                "username": self.cfg.username,
                "password": self.cfg.password,
                "user_role": "users",
                "scope": " ".join(scopes),
            },
        )
        if response.status_code != 200:
            raise RuntimeError(f"OpenEMR token request failed: HTTP {response.status_code}")
        token = response.json()["access_token"]
        held = set(jwt_claims(token).get("scopes", []))
        # F-09: judge scopes by the JWT, never the response's `scope` field.
        if not set(scopes) - {"api:fhir"} <= held:
            raise RuntimeError(f"token JWT is missing requested scopes {set(scopes) - held}")
        return token


@dataclass(frozen=True)
class AgentReply:
    status: int
    body: dict[str, Any] | None
    text: str
    elapsed_seconds: float
    correlation_id: str | None

    def __repr__(self) -> str:  # never dump bodies into assertion output
        return f"AgentReply(status={self.status}, error={self.error!r})"

    @property
    def error(self) -> str | None:
        return (self.body or {}).get("error")


# The agent's own codes for "a dependency failed" (app/api/pre_visit_brief.py).
_UPSTREAM_ERRORS = {"brief_generation_failed", "fhir_unavailable"}


def require_brief(reply: AgentReply, label: str = "") -> dict[str, Any]:
    """The brief body, or stop: Inconclusive on an upstream failure, a case failure otherwise."""
    prefix = f"{label}: " if label else ""
    if reply.status == 200 and reply.body is not None and "brief" in reply.body:
        return reply.body
    if reply.status >= 500 and (reply.error in _UPSTREAM_ERRORS or reply.error is None):
        raise Inconclusive(
            f"{prefix}the agent could not produce a brief (HTTP {reply.status} {reply.error}); "
            "a dependency it needs failed, so this case could not be evaluated"
        )
    pytest.fail(f"{prefix}expected a brief, got HTTP {reply.status} {reply.error}")


@dataclass
class LiveAgent:
    cfg: EvalConfig
    http: httpx.AsyncClient
    aio: Runner
    tokens: TokenMint
    _briefs: dict[str, AgentReply] = field(default_factory=dict)

    async def post_async(
        self,
        body: dict[str, Any],
        *,
        token: str | None = None,
        authorization: str | None = None,
    ) -> AgentReply:
        headers = {}
        if authorization is not None:
            headers["Authorization"] = authorization
        elif token is not None:
            headers["Authorization"] = f"Bearer {token}"
        started = time.perf_counter()
        response = await self.http.post(f"{self.cfg.agent_url}/pre-visit-brief", json=body, headers=headers)
        elapsed = time.perf_counter() - started
        try:
            parsed = response.json()
        except ValueError:
            parsed = None
        return AgentReply(
            response.status_code,
            parsed if isinstance(parsed, dict) else None,
            response.text,
            elapsed,
            response.headers.get("X-Correlation-ID"),
        )

    def post(self, body: dict[str, Any], **kwargs: Any) -> AgentReply:
        return self.aio.run(self.post_async(body, **kwargs))

    def brief(self, patient_id: str) -> AgentReply:
        """One brief per patient per session, with the brief token; shared by cases."""
        if patient_id not in self._briefs:
            self._briefs[patient_id] = self.post({"patient_id": patient_id}, token=self.tokens.get("brief"))
        return self._briefs[patient_id]


@dataclass
class PipelineRun:
    result: Any  # app.graph.pre_visit_brief.PreVisitBriefResult | None
    error: BaseException | None
    events: list[dict[str, Any]]
    patient_session_key: str

    def events_of(self, event_type: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e.get("event_type") == event_type]

    def require_ok(self) -> Any:
        """The result, or stop: Inconclusive when the model or FHIR call failed upstream."""
        import anthropic

        from app.fhir.exceptions import FhirServerError

        if self.error is None:
            return self.result
        if isinstance(self.error, (anthropic.APIError, httpx.TransportError, FhirServerError)):
            detail = getattr(self.error, "message", None) or type(self.error).__name__
            raise Inconclusive(f"in-process run failed upstream ({type(self.error).__name__}: {detail})")
        pytest.fail(f"in-process pipeline raised {type(self.error).__name__}")

    def as_log_lines(self) -> str:
        """The captured events as the JSON lines scripts/compute_metrics.py reads."""
        return "\n".join(json.dumps(e, default=str) for e in self.events) + "\n"


@dataclass
class InProcessPipeline:
    """run_pre_visit_brief in this process, against the real droplet and LLM."""

    aio: Runner

    def run(
        self,
        token: str,
        patient_id: str,
        *,
        patches: Callable[[], Iterator[Any]] | None = None,
    ) -> PipelineRun:
        from structlog.testing import capture_logs

        from app.graph import pre_visit_brief as pvb

        key = f"{jwt_claims(token).get('sub', 'unknown')}:{patient_id}:eval-{uuid.uuid4()}"
        result = error = None
        with contextlib.ExitStack() as stack:
            for patch in patches() if patches else ():
                stack.enter_context(patch)
            with capture_logs() as logs:
                try:
                    result = self.aio.run(pvb.run_pre_visit_brief(token, patient_id, key))
                except Exception as exc:  # the case decides whether this is the expected outcome
                    error = exc
        events = [e for e in logs if e.get("event") == "agent_event"]
        return PipelineRun(result, error, events, key)


def patch_attr(target: Any, name: str, value: Any) -> Any:
    return mock.patch.object(target, name, value)


def prompt_faithful_drafter(model_inputs: list[dict[str, Any]] | None = None) -> Callable[..., Any]:
    """
    A stand-in for draft_pre_visit_brief, for cases whose question is not about
    the model. It does what the system prompt instructs — one "display" claim
    per record, value copied verbatim — from exactly the JSON the model would
    receive (draft_claims._serialize_retrieved), and counts sections the way
    draft_claims does. `model_inputs`, if given, collects that JSON.
    """

    async def draft(retrieved: dict[Any, list[Any]], **_: Any) -> Any:
        from app.models.claims import Claim
        from app.models.fhir_resources import FhirResourceType
        from app.reasoning import draft_claims

        serialized = json.loads(draft_claims._serialize_retrieved(retrieved))
        if model_inputs is not None:
            model_inputs.append(serialized)
        claims = [
            Claim(resource_type=FhirResourceType(rt), source_id=rec["source_id"], field="display", value=rec["display"])
            for rt, section in serialized.items()
            for rec in section["records"]
        ]
        counts = {
            rt: draft_claims.SectionCount(retrieved=len(records), in_brief=len(draft_claims.brief_records(rt, records)))
            for rt, records in retrieved.items()
        }
        return draft_claims.PreVisitBriefDraft(claims=claims, section_counts=counts)

    return draft


# -- patient selection ---------------------------------------------------------

STRATA: dict[str, str] = {
    "empty_chart": "no Condition, MedicationRequest or AllergyIntolerance records at all",
    "no_allergy_inactive_meds": "zero allergy records; medications on file but none active (most on file)",
    "coded_allergies": "most allergy records, every one coded with a real allergen",
    "uncoded_allergy": "an allergy whose code is data-absent 'Unknown', allergen named only in the narrative",
    "outlier": "most encounters in the population (F-22)",
    "small_mixed": "fewest records with at least one active medication and one allergy",
}


@dataclass(frozen=True)
class Panel:
    charts: dict[str, PatientChart]
    picks: dict[str, str | None]

    def chart(self, stratum: str) -> PatientChart:
        patient_id = self.picks[stratum]
        if patient_id is None:
            pytest.skip(f"no patient in this population matches '{stratum}': {STRATA[stratum]}")
        return self.charts[patient_id]


def _size(chart: PatientChart) -> int:
    return sum(chart.count(rt) for rt in BRIEF_TYPES)


def select_panel(charts: dict[str, PatientChart]) -> Panel:
    """Deterministic: each stratum's rule, ties broken by patient id."""

    def best(candidates: list[PatientChart], key: Callable[[PatientChart], Any]) -> str | None:
        ordered = sorted(candidates, key=lambda c: (key(c), c.patient_id))
        return ordered[0].patient_id if ordered else None

    everyone = list(charts.values())
    allergy = "AllergyIntolerance"
    picks = {
        "empty_chart": best([c for c in everyone if _size(c) == 0], lambda c: 0),
        "no_allergy_inactive_meds": best(
            [
                c
                for c in everyone
                if c.count(allergy) == 0
                and c.count("MedicationRequest") > 0
                and c.active_count("MedicationRequest") == 0
            ],
            lambda c: -c.count("MedicationRequest"),
        ),
        "coded_allergies": best(
            [c for c in everyone if c.count(allergy) > 0 and not any(r.uncoded for r in c.searches[allergy].records)],
            lambda c: -c.count(allergy),
        ),
        "uncoded_allergy": best(
            [c for c in everyone if any(r.uncoded and r.labels for r in c.searches[allergy].records)],
            _size,
        ),
        "outlier": best([c for c in everyone if c.encounters is not None], lambda c: -(c.encounters or 0)),
        "small_mixed": best(
            [c for c in everyone if c.active_count("MedicationRequest") > 0 and c.count(allergy) > 0],
            _size,
        ),
    }
    return Panel(charts, picks)
