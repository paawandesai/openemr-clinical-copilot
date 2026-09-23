# Week 1 evals I2 and I11 fail on main: untagged operational events

Found by work order 19 (`feat/w2-answer-scope`) while running the Week 1
I-suite as an acceptance check. **Not fixed.** The patch alongside this file,
[`i2-i11-event-tagging.patch`](i2-i11-event-tagging.patch), is a rejected
first attempt. It is kept because it shows what breaks, not as a proposed fix.
The human reviewer confirmed that I2 and I11 also fail on bare `main`
(2026-09-23), so this predates the answer-scope change.

## What fails

`agent/evals/test_i_conversation.py:73-77`, `_check_events`, used by I2 and
I11, requires every `agent_event` a conversation turn emits to carry both
tags:

```python
if {e.get("use_case") for e in run.events} != {"follow_up_drilling"}:
    violations.append(f"{label}: an event is not tagged use_case=follow_up_drilling")
if {e.get("patient_session_key") for e in run.events} != {key}:
    violations.append(f"{label}: an event does not carry this turn's patient_session_key")
```

The harness (`evals/harness.py:386-437`, `InProcessConversation.turn`) posts
to the **whole FastAPI app** over `httpx.ASGITransport` and captures every
`agent_event` of the request, middleware included.

`feat/w2-observability` (merge `fc3143b`, D36-D38) added three operational
event types. Two of them reach a conversation turn without those tags:

| Event type | Emitted at | `use_case` | `patient_session_key` | Why |
|---|---|---|---|---|
| `tool_call_event` (tool `fhir_read`), one per FHIR read | `agent/app/fhir/client.py:96` (`_get_bundle`) and `:464` (`ensure_patient_exists`): `with tool_call(Tool.FHIR_READ, resource_type) as call:` | missing | missing | `tool_call()` accepts both (`app/middleware/telemetry.py`, and the LLM call site passes them), but the FHIR call sites do not, and `_get_bundle` has no way to receive them |
| `http_request_event`, one per request | `agent/app/middleware/correlation.py:153`: `log_event(HTTP_REQUEST_EVENT, event.model_dump(mode="json"))` | missing | missing | Emitted by `CorrelationIdMiddleware` after `call_next` returns. The middleware never learns the route's use case or the patient/session key |

The `step_event`s the same merge added to the conversation path
(`app/graph/conversation.py`, `step(...)` and `record_steps(...)`) pass both
tags and are not part of the problem.

### Evidence

- **I-suite, run 1**: this branch at `f560fe9`, whose diff does not touch the
  conversation path, the FHIR client or the middleware. PASS 10 / FAIL 2 of
  12. I2 and I11 each fail with both messages above. 17 model calls, 157,403
  input and 16,436 output tokens, about **$1.20**.
- **Offline reproduction**, no network or model: a conversation turn through
  `TestClient(app)` with `tests/test_conversation.py`'s `FakeWorld`. The FHIR
  reader functions are replaced there, so no `fhir_read` tool calls are
  emitted. Result: 11 `agent_event`s, and the only untagged one is
  `('http_request_event', use_case=None, patient_session_key=None)`. The
  middleware event is untagged on its own, independent of the FHIR one.
- **I-suite, run 2**: with the rejected patch applied (`b33332b`, since
  removed from the branch). PASS 9 / FAIL 3. I2 and I11 still fail with the
  same two messages, because the request event is still untagged, and I9
  now fails with `expected a conversation turn, got HTTP 500 internal_error`.
  16 model calls, 149,522 input and 16,747 output tokens, about **$1.17**.

## Why the first fix broke I9

The patch threaded `use_case` and `patient_session_key` into the FHIR tool
calls by widening two signatures:

```python
async def _get_bundle(token, resource_type, params, *, use_case=None, patient_session_key=None)
async def ensure_patient_exists(token, patient_id, *, use_case=None, patient_session_key=None)
```

It also changed every `get_*` reader to call
`_get_bundle(..., use_case=use_case, patient_session_key=patient_session_key)`.

Three gated Week 1 evals patch `_get_bundle` with a replacement that has the
**old three-argument signature**:

| Eval | Where | Replacement |
|---|---|---|
| I9 | `evals/test_i_conversation.py:643-660` | `async def resolve_one(token, resource_type, params)` |
| A6 | `evals/test_a_allergy_distinction.py:240-259` | same shape |
| H1-H4 | `evals/test_h_uncoded_names.py:99-119` | same shape |

With the patch, the real `get_allergy_intolerances` calls the patched
`_get_bundle` with two keyword arguments that `resolve_one` does not accept.
The resulting `TypeError` surfaces as HTTP 500 `internal_error`, and I9 fails.
Only I9 ran in that session; A6 and H1-H4 were not run, but they install the
same shape of replacement and would fail the same way.

These evals are gate files (CLAUDE.md invariant 7), so the harness cannot be
changed from an implementation session. In effect, `_get_bundle(token,
resource_type, params)` is a signature the Week 1 evals pin. Any fix has to
leave it callable with exactly those three positional arguments, and to
leave `ensure_patient_exists` callable as `(token, patient_id)`. The unit
tests' fakes had the same shape: the patch had to widen 10 fake functions across 8 test files.

## What a proper fix has to decide

Tagging the FHIR tool calls alone would not have made I2 and I11 pass. Both
event types need tags, and neither call site receives them as arguments.
Options, for the owner of D38 to choose between:

1. **Request-scoped tags, set once per turn and read at emission.** A holder
   (a mutable object in a `ContextVar`) is set by the middleware before
   `call_next`. The turn entry points (`run_conversation_turn`,
   `run_follow_up`, the brief, `run_ask_turn`) fill in `use_case` and
   `patient_session_key` as soon as they compute the key. `tool_call()` and
   `_emit_request_event` use the holder's values whenever their caller does
   not pass any. Nothing about `_get_bundle`'s signature changes, so the
   evals' patches keep working.
   Decisions this needs:
   - It must be a mutable holder, not a `ContextVar` set inside the route.
     Under `BaseHTTPMiddleware` the endpoint runs in a copied context, so a
     value it sets is invisible to the middleware after `call_next`.
   - Requests that never compute a key (`/health`, `/ready`, `/documents`
     uploads, a 401 before the Patient read) keep emitting untagged request
     events. That is correct, and the dashboard must not assume otherwise.
   - A request that fails before the key exists stays untagged.
2. **Keep `http_request_event` untagged by design** and have the I2/I11
   attribution check skip the operational event types
   (`TELEMETRY_EVENT_TYPES` in `app/models/telemetry_events.py`). That is a
   change to a gated eval, so only a `W2_GATE_SESSION=1` session can make it,
   and D38 would need a line saying request events are not patient-attributed.
3. **Stop emitting request events for the conversation route inside the turn
   capture.** Not recommended: it hides a real request from the dashboard to
   satisfy a check.

Separately from the choice above, the FHIR tool-call tags can be supplied
without a signature change, through option 1's holder. Or `_get_bundle`'s
callers can wrap the call in a context that `tool_call()` reads. Either way
the three-argument `_get_bundle` stays as the evals expect.

**Invariant 1 check for option 1:** `patient_session_key` is an identifier
built from three UUIDs (`user:patient:session`), already on every other event
of the turn, and no chart content enters either event. So tagging the
request event adds no PHI.

## What is on the branch

- This file, and the rejected patch alongside it. The patch's commit message
  says the untagged FHIR tool calls are why I2 and I11 failed. That is only
  half of it; see the table above.
- No change to the FHIR client, the middleware or any eval. `feat/w2-answer-scope`
  still fails I2 and I11 exactly as `main` does, and passes the other 10
  I-suite cases.
