# Clinical Co-Pilot Agent

FastAPI/LangGraph service implementing the Clinical Co-Pilot agent
described in `docs/ARCHITECTURE.md` (repository root), grounded in the use
cases in `docs/USERS.md` and the success metrics in `docs/KEY_METRICS.md`.

## Status: scaffold only

This service does not yet call FHIR or run verification.
What exists today:

- A FastAPI app with correlation-ID + structured JSON logging
  (`app/middleware/`) — the metrics-aware logging shape `docs/KEY_METRICS.md`
  states is a *precondition* for computing any of its six metrics, not an
  optional nice-to-have. Every log line can carry a `correlation_id`
  (bound automatically per request), a `use_case`, a `patient_session_key`,
  and a free-form `event_type`/`event_data` payload.
- Pydantic models for: the structured-claim / verification contract
  (`app/models/claims.py`), the five FHIR resource types in tool scope
  (`app/models/fhir_resources.py`, per `ARCHITECTURE.md` §3), and the log
  event shapes `KEY_METRICS.md`'s six metrics will eventually be computed
  from (`app/models/metrics_events.py`).
- One LLM call: `app/reasoning/draft_claims.py` drafts Pre-Visit Brief
  claims (`docs/USERS.md` Use Case 1) from retrieved FHIR records via the
  Anthropic API. No endpoint calls it yet; `scripts/smoke_test_claims.py`
  exercises it against the deployed droplet.
- `/health` and `/ready` endpoints, kept deliberately separate. `/ready`'s
  dependency checks (FHIR, LLM provider, observability backend) are stubs
  — see `app/health.py`.

Every stub in this codebase raises `NotImplementedError` with a comment
citing the `ARCHITECTURE.md` or `KEY_METRICS.md` section it implements, so
the next task knows exactly where to pick up. Nothing here computes an
actual metric, calls FHIR, or verifies a claim.

## Running locally

Without Docker:

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

With Docker:

```bash
docker build -t clinical-copilot-agent .
docker run -p 8000:8000 clinical-copilot-agent
```

Then:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/ready
```

Not yet added to `docker/development-easy/docker-compose.yml` — this
service's own placement in the Compose stack, and the observability
backend's placement, are both still-open decisions per `ARCHITECTURE.md`
§1/§6.
