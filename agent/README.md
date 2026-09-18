# Clinical Co-Pilot Agent

FastAPI service implementing the Clinical Co-Pilot agent described in
`docs/ARCHITECTURE.md` (repository root), grounded in the use cases in
`docs/USERS.md` and the success metrics in `docs/KEY_METRICS.md`.

## Status: Pre-Visit Brief runs end to end over HTTP

The Pre-Visit Brief (`docs/USERS.md` Use Case 1) is a runnable pipeline
behind an HTTP endpoint, deployed next to OpenEMR on the Vultr host
`64.177.42.210` (`agent/docs/deployment.md`). The eval and load-test
results were recorded on the earlier DigitalOcean droplet and have not been
re-run on Vultr (`docs/ARCHITECTURE.md` §1). A constrained, single-turn
form of Follow-Up Drilling (Use Case 3) is built alongside it; Change
Detection is not built.

| Endpoint | Purpose |
|---|---|
| `POST /pre-visit-brief` | Runs the brief for one patient. `Authorization: Bearer <clinician's OpenEMR token>`; body `{"patient_id": "<uuid>", "session_id": "<optional>"}`. Returns the rendered brief, every claim's verification outcome, section counts, per-node timing and the correlation id. |
| `POST /pre-visit-brief/follow-up` | One follow-up question on a brief. Same bearer token; body `{"patient_id": "<uuid>", "session_id": "<the brief's>", "question": "<≤500 chars>"}`. Answers only from the records the brief retrieves, re-read under the same token, with every claim verified against them. Returns the answer, `answer_status` (`answered` / `partially_answered` / `not_in_record`), `gaps`, and the brief's claims / sections / timing shape. |
| `GET /ready` | 200 only when OpenEMR's FHIR API and the LLM provider are both reachable; otherwise 503 naming which. |
| `GET /health` | Liveness, always 200. |
| `GET /docs`, `/openapi.json` | The contracts (`app/models/api.py`). |

A runnable Postman/Newman collection covering all of these, including how to
get a token, is in `api-collection/`.

## How a brief is produced

`app/graph/pre_visit_brief.py`, a LangGraph graph run by
`run_pre_visit_brief()`:

1. **retrieve** (`app/fhir/client.py`) — reads the Patient first, then
   `Condition`, `MedicationRequest` and `AllergyIntolerance` through OpenEMR's
   FHIR R4 API, under the clinician's own token. The agent holds no FHIR
   credential of its own (`ARCHITECTURE.md` §2). The Patient read matters:
   FHIR search answers an unknown patient id with empty results, which would
   otherwise render as "not recorded".
2. **draft** (`app/reasoning/draft_claims.py`) — the only LLM call: structured
   claims bound to `source_id` + field + value, plus section counts computed
   in code.
3. **verify** (`app/verification/checker.py`) — deterministic: each claim's
   source was retrieved this turn and holds the claimed value (§5).
4. **render** — plain code. Verified claims plainly; failed ones marked
   UNVERIFIED, never dropped; empty sections as "not recorded" or
   "none active".

## How a follow-up is answered

`app/graph/follow_up.py`, run by `run_follow_up()` — deliberately smaller
than Use Case 3 as `ARCHITECTURE.md` §3–§4 describe it:

1. **retrieve** — the brief's retrieval exactly (Patient, then `Condition`,
   `MedicationRequest`, `AllergyIntolerance`), under the caller's token. No
   other resource type is ever fetched, and the request cannot carry records
   or claims, so the set claims are verified against is never caller-supplied.
2. **draft** (`app/reasoning/draft_follow_up.py`) — claims bound to
   `source_id` + field + value, plus a coverage and gap choice from closed sets.
3. **verify** — the same `verify_claims` as the brief, over the same records.
4. **render** — plain code. No verified claim means "That information isn't in
   the retrieved record for this visit."; failed claims render UNVERIFIED.

No conversation state is kept: each question stands alone. Events are tagged
`use_case="follow_up_drilling"` under the brief's `patient_session_key`; the
question text is never logged.

LangSmith tracing, which LangGraph brings in, can never run over the graph:
its state holds chart records (`docs/audit/findings.md` F-56;
`tests/test_langsmith_tracing_guard.py`).

## Observability

Every event is one JSON line on stdout (`app/middleware/logging.py`), carrying
`correlation_id`, `use_case`, `patient_session_key` and the event payload.
`scripts/compute_metrics.py` computes `KEY_METRICS.md`'s metrics from that
log — the observability path for this submission. The pipeline emits the
events for Metrics 1, 2, 4, 5 and 6. Nothing emits Metric 3's
authorization-check event yet. Self-hosted Langfuse export is built but
off unless `LANGFUSE_*` is set. It was verified locally only, and the
deployed compose file does not pass those variables to the agent
(`agent/docs/observability.md`).

## Running locally

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...                                   # drafting + /ready
export FHIR_BASE_URL=https://64.177.42.210:9300/apis/default/fhir
export FHIR_VERIFY_TLS=false                                   # OpenEMR's self-signed cert (F-36)
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

The full containerized stack (agent behind the TLS proxy) is
`deploy/docker-compose.agent.yml`; see `agent/docs/deployment.md`.

Tests and scripts (credentials each script needs are in its module
docstring):

```bash
pytest tests/                                   # no network, no credentials
python scripts/smoke_test_pre_visit_brief.py    # live: deployed FHIR + Anthropic API
python scripts/smoke_test_follow_up.py          # live: follow-up answer / refusal / injected claims
python scripts/smoke_test_fhir.py               # live: deployed FHIR
python scripts/smoke_test_claims.py             # live: deployed FHIR + Anthropic API
python scripts/compute_metrics.py --log-file logs/agent-stdout.jsonl
```

## Not built yet

- Change Detection, and Follow-Up Drilling beyond one question on the
  brief's own records: multi-turn conversation state (`ARCHITECTURE.md` §4),
  re-querying `Encounter` / `Observation`, and multi-record synthesis — so
  also the brief's "changed since last visit" line.
- The agent-side scope record and pre-check (`ARCHITECTURE.md` §2) and the
  authorization-check events Metric 3 is computed from. The agent forwards
  the token and maps OpenEMR's 401 to `fhir_token_rejected`.
- Domain-constraint rules such as dosage thresholds and interaction flags
  (`ARCHITECTURE.md` §5). The verifier checks source, field and value only.
- Rate limiting on the public endpoint, and a publicly trusted certificate
  (`agent/docs/deployment.md` §1).
