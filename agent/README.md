# Clinical Co-Pilot Agent

FastAPI service implementing the Clinical Co-Pilot agent described in
`docs/ARCHITECTURE.md` (repository root), grounded in the use cases in
`docs/USERS.md` and the success metrics in `docs/KEY_METRICS.md`.

## Status: Pre-Visit Brief runs end to end over HTTP

The Pre-Visit Brief (`docs/USERS.md` Use Case 1) is a runnable pipeline
behind an HTTP endpoint, deployed next to OpenEMR on the Vultr host
`64.177.42.210` (`agent/docs/deployment.md`). The eval and load-test
results were recorded on the earlier DigitalOcean droplet and have not been
re-run on Vultr (`docs/ARCHITECTURE.md` §1). Follow-Up Drilling (Use Case
3) is built in two forms: a constrained single-turn follow-up, which is
deployed, and the multi-turn conversation, which is built and tested on the
`feat/multiturn-chat` branch and **not deployed**. Change Detection is not
built.

| Endpoint | Purpose |
|---|---|
| `POST /pre-visit-brief` | Runs the brief for one patient. `Authorization: Bearer <clinician's OpenEMR token>`; body `{"patient_id": "<uuid>", "session_id": "<optional>"}`. Returns the rendered brief, every claim's verification outcome, section counts, per-node timing and the correlation id. |
| `POST /pre-visit-brief/follow-up` | One follow-up question on a brief. Same bearer token; body `{"patient_id": "<uuid>", "session_id": "<the brief's>", "question": "<≤500 chars>"}`. Answers only from the records the brief retrieves, re-read under the same token, with every claim verified against them. Returns the answer, `answer_status` (`answered` / `partially_answered` / `not_in_record`), `gaps`, and the brief's claims / sections / timing shape. |
| `POST /pre-visit-brief/conversation` | One turn of the multi-turn follow-up conversation (not deployed). Same bearer token and the same body as the single-turn follow-up: the caller sends the question and nothing about earlier turns. The agent keeps, per `(user, session)` and bound to one patient, only the claims earlier turns verified. Returns the answer, `turn_status` (`answered` / `partially_answered` / `not_in_record` / `refused_out_of_scope` / `refused_unverifiable`), `refusals`, `gaps`, `conversation` (turn number, what happened to the state, context size), `retrieval` (which record types were read and by which rule) and per-claim outcomes with the rendered line. A lab or visit question needs `user/Observation.read` / `user/Encounter.read` on the token. |
| `POST /pre-visit-brief/conversation/end` | Discards a conversation's stored state now instead of at its TTL. Body `{"patient_id", "session_id"}`. |
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

## How a conversation turn is answered

`app/graph/conversation.py`, run by `run_conversation_turn()` — the
single-turn follow-up extended into a conversation, with one rule per turn:
every statement about the patient is a claim that passed the brief's own
`verify_claims` against a bounded set of records retrieved *that turn*, or
it is shown UNVERIFIED, or it is not said.

1. **authorize** — the Patient read under the caller's token. First on
   purpose: conversation state is keyed by the token's `sub`, which nothing
   has proven until OpenEMR accepts the token. No state is read, created or
   discarded before it.
2. **open state** (`app/conversation/state.py`) — the conversation for
   `(user, session)`, if it is bound to this patient. If it is bound to a
   different patient it is deleted, all of it, and an empty one starts; going
   back to the first patient starts over too. This is the agent's own
   defense against cross-patient leakage, because OpenEMR has no per-patient
   scoping (F-50). Stored per conversation: the claims that *passed*
   verification, by turn. Never the question, the answer text, or a failed
   claim. In-memory with an idle TTL (30 min, `AGENT_CONVERSATION_TTL_SECONDS`)
   and a maximum age (1 h); Redis with the same key and binding is the
   production answer, and is required before running more than one worker.
3. **route** (`app/conversation/routing.py`) — by rule, never by the model.
   Questions outside tool scope (vitals, notes, procedures, immunizations,
   imaging, family/social history, scheduling, demographics, other patients)
   and questions asking for a judgment, comparison or trend — which the
   verifier cannot check (`ARCHITECTURE.md` §5) — are refused before any
   chart search or model call. Otherwise: `Condition`, `MedicationRequest`
   and `AllergyIntolerance` on every turn; lab wording adds `Observation`;
   visit wording ("last visit", "since", "last time") adds `Encounter`; a
   type an earlier turn verified claims on is carried.
4. **retrieve** — the existing FHIR readers, unchanged, patient-scoped under
   the caller's token, every turn. Lab results are then narrowed by rule to
   the most recent results for the tests the question names (one chart on the
   host has 2,203 lab results); the cap is stated in the answer.
5. **draft** (`app/reasoning/draft_conversation_turn.py`) — the only LLM
   call. It sees this turn's records, the question, and the earlier turns'
   *verified claims* as structured context for resolving "that medication".
   The output schema has no free-text field.
6. **verify** — `verify_claims`, unchanged, over the same mapping the draft
   saw. Earlier turns' claims are not in it: nothing verifies against context.
7. **render**, then **commit** the claims that passed as the next turn's
   context. The "most recent of N" tag on a lab result or visit is computed in
   code from everything retrieved, not taken from the model's pick.

Known limits, stated rather than hidden: the refusal and retrieval rules are
wording heuristics (a missed judgment question still meets a model that can
only cite single verified values and must report the `interpretation` gap,
eval I6; a missed lab question is told how to ask). Lab names come from a
code-owned LOINC table (`app/conversation/lab_codes.py`) because the
`Observation` model carries a code and no name. Which records the model
*selects* is not verifiable, only that each one it cites is real.

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
pytest evals/test_i_conversation.py             # live: the multi-turn conversation, in-process (evals/README.md)
python scripts/smoke_test_pre_visit_brief.py    # live: deployed FHIR + Anthropic API
python scripts/smoke_test_follow_up.py          # live: follow-up answer / refusal / injected claims
python scripts/smoke_test_fhir.py               # live: deployed FHIR
python scripts/smoke_test_claims.py             # live: deployed FHIR + Anthropic API
python scripts/compute_metrics.py --log-file logs/agent-stdout.jsonl
```

## Not built yet

- Change Detection, so also the brief's "changed since last visit" line.
- Deployment of the multi-turn conversation, and a conversation view inside
  the OpenEMR chart: the embed module keeps its single-turn brief card, and
  the conversation's only surface is the standalone UI (`deploy/ui/`).
  Embedding it natively in the chart is the documented next step.
- Verification of multi-record synthesis ("is it worsening?"). The
  conversation refuses those questions instead (`ARCHITECTURE.md` §5).
- A shared store for conversation state (Redis). It is in-memory, which is
  correct only for the single uvicorn worker the Dockerfile runs.
- The agent-side scope record and pre-check (`ARCHITECTURE.md` §2) and the
  authorization-check events Metric 3 is computed from. The agent forwards
  the token and maps OpenEMR's 401 to `fhir_token_rejected`.
- Domain-constraint rules such as dosage thresholds and interaction flags
  (`ARCHITECTURE.md` §5). The verifier checks source, field and value only.
- Rate limiting on the public endpoint, and a publicly trusted certificate
  (`agent/docs/deployment.md` §1).
