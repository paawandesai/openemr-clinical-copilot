# Clinical Co-Pilot Agent

FastAPI service implementing the Clinical Co-Pilot agent described in
`docs/ARCHITECTURE.md` (repository root), grounded in the use cases in
`docs/USERS.md` and the success metrics in `docs/KEY_METRICS.md`.

## Status: components built, pipeline not yet wired

Retrieval, claim drafting, and verification each exist and each has been
exercised on its own, but they are **not yet wired together into one
runnable pipeline**. That wiring is the LangGraph graph, which is not built
yet. There is no endpoint that takes a patient ID and returns an answer —
the only HTTP endpoints are `/health` and `/ready`.

What exists today:

- **Calls FHIR — yes.** `app/fhir/client.py` has one async function per
  resource type in `ARCHITECTURE.md` §3's tool scope: `Condition`,
  `MedicationRequest`, `AllergyIntolerance`, `Encounter`, `Observation`,
  read through OpenEMR's FHIR R4 API. Every call takes the clinician's
  bearer token as a parameter; the module holds no credential of its own
  (§2). Results are parsed into the narrow models in
  `app/models/fhir_resources.py`, never returned as raw FHIR JSON, and each
  call emits a `retrieval_event`. Exercised against the deployed droplet by
  `scripts/smoke_test_fhir.py`; no pytest coverage.
- **Calls an LLM — yes, for Pre-Visit Brief claim drafting only.**
  `app/reasoning/draft_claims.py` drafts structured claims
  (`docs/USERS.md` Use Case 1) from retrieved `Condition` /
  `MedicationRequest` / `AllergyIntolerance` records via the Anthropic API,
  and logs token counts as an `llm_usage_event`. Empty sections are not
  drafted by the model: they are counted in code with `len()` and returned
  as `section_counts` for the rendering step. Change Detection and
  Follow-Up Drilling have no drafting step. Exercised against the deployed
  droplet by `scripts/smoke_test_claims.py`; no pytest coverage.
- **Deterministic verification layer — yes.** `app/verification/checker.py`
  (`verify_claims`) confirms each claim's `source_id` was retrieved this
  turn and that the claimed value matches that source. Plain comparison
  code — no LLM call, no FHIR call (§5). Emits one `claim_verified_event`
  per claim. Unit-tested in `tests/test_verifier.py` and
  `tests/test_verification_result.py`.
- **Structured logging, with optional Langfuse export — yes.**
  `app/middleware/logging.py` and `app/middleware/correlation.py` write one
  JSON line per event to stdout, carrying `correlation_id`, `use_case`,
  `patient_session_key`, and an `event_type`/`event_data` payload — the
  shape `docs/KEY_METRICS.md` states is a precondition for computing any of
  its six metrics. When `LANGFUSE_BASE_URL`, `LANGFUSE_PUBLIC_KEY`, and
  `LANGFUSE_SECRET_KEY` are all set, each event is also exported to
  self-hosted Langfuse (`app/middleware/langfuse_export.py`); otherwise
  output is stdout only. Decision, PHI boundary, and deployment status:
  `agent/docs/observability.md`.
- **Metrics — `scripts/compute_metrics.py`.** Computes all six
  `KEY_METRICS.md` metrics directly from the stdout log (default
  `logs/agent-stdout.jsonl`, gitignored). This is the observability path
  for this submission. Langfuse is built and verified locally but not
  deployed (`agent/docs/observability.md` §9; `ARCHITECTURE.md` §1, §6).
  Until the pipeline is wired, real runs only produce retrieval and
  LLM-usage events,
  so only Metrics 5 and 6 have data: `verify_claims` emits Metric 1–2
  events but nothing real calls it yet, and nothing emits Metric 3
  (authorization check) or Metric 4 (latency) events.
- Pydantic models for the claim/verification contract
  (`app/models/claims.py`), the five FHIR resource types
  (`app/models/fhir_resources.py`), and the metric event shapes
  (`app/models/metrics_events.py`).
- `/health` and `/ready`, kept deliberately separate (`app/health.py`).
  `/ready`'s observability check is real: it reports whether Langfuse
  export is configured, reachable, and accepting its API keys. Its FHIR and
  LLM provider checks are still `NotImplementedError` stubs, so `/ready`
  currently always returns 503 `not_ready` rather than an unconditional 200.

Not built yet:

- The LangGraph graph connecting retrieval → drafting → verification →
  rendering, and any endpoint that runs it.
- The rendering step, including turning `section_counts` into "not
  recorded" / "none active" wording (`USERS.md` §3 Use Case 1).
- Claim drafting for Change Detection and Follow-Up Drilling, and
  multi-turn conversation state (`ARCHITECTURE.md` §4).
- Emission of authorization-check and latency events (Metrics 3 and 4).
- Real FHIR and LLM provider checks in `/ready`.

## Running locally

Without Docker:

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

With Docker:

```bash
docker build -t clinical-copilot-agent .
docker run -p 127.0.0.1:8000:8000 clinical-copilot-agent
```

Then:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/ready
```

Tests and scripts (credentials each script needs are listed in its module
docstring):

```bash
pytest tests/
python scripts/smoke_test_fhir.py      # live: deployed droplet
python scripts/smoke_test_claims.py    # live: deployed droplet + Anthropic API
python scripts/compute_metrics.py --log-file logs/agent-stdout.jsonl
```

Not yet added to `docker/development-easy/docker-compose.yml` — this
service's own shape in the Compose stack is still open (`ARCHITECTURE.md`
§1/§6). The observability path for this submission is decided:
`scripts/compute_metrics.py` over the stdout log, with self-hosted Langfuse
deferred to a post-submission deployment.
