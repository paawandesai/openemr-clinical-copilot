# Interview Prep — Clinical Co-Pilot

A study aid for the technical interview, not a deliverable. Every answer below
is grounded in something the repository records. Answers are written in the
first person so they can be rehearsed aloud. Each one gives a **short answer**,
then the **evidence** to cite, then **known gaps**: the parts where saying
more than the repo supports would get caught out.

**Source notes, read first:**

- The case study's interview questions are **not in the repository**. The
  questions below come from the areas the case study says it will cover.
- The findings ledger (`docs/audit/findings.md`) runs **F-01 to F-61**. There
  is no F-62. `docs/AUDIT.md` was finalized at F-54, so F-55 to F-61 exist
  only in `findings.md`.
- Every eval, load-test and audit record was produced on the **DigitalOcean
  droplet** (`201.79.25.22`). The live deployment is now a **fresh install on
  Vultr** (`64.177.42.210`) with different patient ids (F-61). Nothing in the
  repo re-runs the eval or load test on Vultr.

---

## 0. Cheat sheet

### Numbers to have ready

| Figure | Value | Source |
|---|---|---|
| Findings | 61 (F-01–F-61), two environments | `findings.md` |
| Eval suite | 27 cases: **23 PASS, 4 FAIL**, 0 skip, 0 error | `agent/evals/RESULTS.md` (run 2026-09-16T22:47:45Z) |
| Failing cases → defects | A5 + B1 → F-57 · A6 → F-58 · C5 → F-59 (3 defects), plus F-60 seen during an earlier run | `agent/evals/README.md` "What the run found"; commit `6dbbbcc` |
| One measured brief | 1,575 in / 326 out tokens, 4,751 ms, 5 claims, **$0.0160** on `claude-opus-5` | `COST_ANALYSIS.md` §1.1 |
| Outlier brief (F1) | 522 encounters, 40 active records, 40/40 verified, **28.8 s** total: retrieve 0.72, draft 28.1, **verify 0.0014**, render 0.0001 | `RESULTS.md` F1 |
| LLM judge instead of code | +$0.0115 per brief, **+72%** | `COST_ANALYSIS.md` §4.7 |
| Static-prefix cache | ~750 tokens (estimated, not measured), **−21%** per brief | `COST_ANALYSIS.md` §4.5a |
| Load test, 10 VUs (stubbed LLM) | 158 req, **0% errors**, p50 888 ms, p95 1.20 s, 5.04 req/s | `agent/load-test/RESULTS.md` |
| Load test, 50 VUs | 222 req, **59% errors** (all `502 fhir_unavailable`), p95 11.98 s | same |
| CPU at 50 VUs | agent **18.7%** / 115 MiB · OpenEMR **360%** / 4.49 GiB, PIDs 39→228 · MySQL 67% | same |
| Dev API spend | **$0.36** total (Console, 30 days) | `COST_ANALYSIS.md` §2.2 |
| F-54 exposure | ≥ 3 h 47 min 48 s, ≤ 18 h 09 min 12 s | `findings.md` F-54 |
| F-56 captured payload | 14,701-byte POST, 5 runs, chart markers in every node run, bearer token 0 times | `findings.md` F-56 |
| Allergy coverage | 14/65 local, 9/53 droplet patients have **any** allergy row | F-21 |
| Lab reference ranges | **0%** of 7,259 / 29,701 result rows | F-19 |
| RxNorm coverage | 311/312 prescriptions (99.7%) | F-55 |

### Don't overclaim these

The docs describe some things as design that the code does not do yet. An
interviewer who reads the code will find the difference.

| The docs say | What is actually true | Where |
|---|---|---|
| "The agent holds its own decoded-JWT scope record and pre-checks before requesting" (ARCH §2) | **Not built.** Only the `AuthorizationCheckEvent` model exists. The agent forwards the token and maps OpenEMR's 401. | `app/models/metrics_events.py`; `evals/README.md` "Limits" says the agent-side scope record is not built |
| "State keyed to (user, patient, session), invalidated on patient switch" (ARCH §4) | **No conversation state exists.** The key is a per-request attribution label. There is nothing to invalidate. G1/G2 test request isolation, not invalidation. | `evals/README.md` "Limits"; `app/graph/follow_up.py` docstring |
| Follow-Up Drilling (Use Case 3) | A **constrained single-turn** follow-up. No memory, no Encounter/Observation re-query. | commit `73dd933`; `agent/README.md` |
| Change Detection (Use Case 2) | **Not built.** Neither is the brief's "changed since last visit" line. | `pre_visit_brief.py` docstring |
| Langfuse observability | Built and verified **locally only**. Production metrics come from `scripts/compute_metrics.py` over stdout. | ARCH §1 |
| "Domain constraints (dosage thresholds, interaction flags)" as an additive rule set (ARCH §5) | **Not built.** | — |
| Real end-to-end batch latency test | **Operator-pending, never run.** Real latency comes from the cost analysis and the eval (F1, G2). | `load-test/RESULTS.md` (b) |
| Binding checks (`ss` + external `nc`) | Done on the DigitalOcean droplet. **No equivalent record for Vultr.** | ARCH §1 |

---

## 1. The Audit

### Q: "Walk us through your most important finding."

**My pick: F-56, LangSmith PHI exfiltration.** F-54 is the fallback (below).

**Why F-56 over F-54 and F-50.**

- **F-54** had the largest *realized* exposure: MariaDB with `root`/`root` was
  public for hours. But it is deployment hygiene with a known class of fix.
- **F-50** (no per-patient scoping) shaped the design, but it is informational
  and nothing was fixed.
- **F-56** is the finding that exists *because an LLM framework was added to a
  PHI system*. Code review cannot see it: the dependency is transitive and one
  environment variable triggers it. Proving it took first-hand network capture.
  Then the obvious fix turned out to be incomplete, and **only testing showed
  that**. It is the best evidence that I audit my own AI integration, not just
  the host platform.

**Short answer (~60 s).**
> LangGraph pulls in `langsmith` transitively: langgraph 1.2.11 requires
> langchain-core, which requires langsmith 0.12.5. If `LANGSMITH_TRACING=true`
> is ever set, for example by a copied `.env` or a stray default, every graph
> run POSTs its full state to `api.smith.langchain.com`. That is a third-party
> SaaS with no BAA, and no API key is needed for the request to go out. The
> Pre-Visit Brief's graph state holds the retrieved conditions, medications and
> allergies. So one misconfigured variable sends patient chart data off-host,
> with no attacker involved.
>
> I proved it with a local HTTP recorder and every non-loopback DNS lookup
> refused. Running the real graph with fake marker records produced a
> 14,701-byte POST of 5 runs, with the markers in every node's inputs or
> outputs. I also checked what did *not* leak. The bearer token lives in
> LangGraph's runtime context, not graph state, and appeared 0 times.
>
> The first fix, `tracing_context(enabled=False)`, looked correct but wasn't: a
> tracer passed explicitly as a callback still attaches. So the final guard has
> two layers. The exported graph is an `UntracedGraph` whose only method,
> `ainvoke`, forces tracing off and accepts no config. And every node raises
> `TracingEnabledError` before doing anything if a LangSmith tracer is attached.
> Eight regression tests fail against the pre-fix code and against each
> deliberate breakage of the guard.

**Evidence to cite.**
- Dependency chain and default endpoint: F-56 Evidence.
- Guard code: `agent/app/graph/pre_visit_brief.py:201` (`UntracedGraph`),
  `:168` (`_refuse_if_traced`), `:189` (`_untraced`).
- Tests: `agent/tests/test_langsmith_tracing_guard.py`. Against pre-fix commit
  `39ccd52` they fail with a 16,458-byte POST. They also include control tests
  that turn both layers off, so a silent recorder cannot pass.
- The follow-up pipeline deliberately does **not** use LangGraph, so it has no
  graph state to trace (`app/graph/follow_up.py` docstring).

**How it was caught.** It was first seen while building the graph. With
tracing on and the endpoint pointed at a closed local port, `langsmith` logged
a failed multipart POST. I then proved it by capture instead of reasoning about
it.

**Honest gaps (F-56 "Residual gaps").**
1. Any *new* graph needs both layers applied by hand. The module scan only
   catches a compiled graph held at module level.
2. Python has no private access, so code that calls node functions directly is
   outside both layers.
3. The node check recognizes `LangChainTracer` only. Another exporting callback
   would not be refused.
4. Delivery to LangSmith Cloud was deliberately never attempted. The real-FHIR
   payload shape is inferred from the fake-record capture.

**The general lesson, which pairs with F-54.** F-54's rule is *check the ports
that actually listen, not the firewall rules*. F-56's rule is the egress
version: *audit dependencies for outbound calls, and test your guards*.

**Fallback version: F-54 in 30 s.**
> Docker DNATs published ports in `PREROUTING`, and its chains run ahead of
> UFW's in `FORWARD`. So UFW's default-deny never saw traffic to MariaDB
> (`root`/`root`) or phpMyAdmin, and both were publicly reachable for between
> 3 h 48 min and 18 h 09 min. My first fix, a Compose override, made it worse:
> override files merge `ports` additively, which caused dual bindings and 5
> failed container starts. The fix that worked edited the base file to bind
> `127.0.0.1` (commit `b0a0bcc`). I verified it three ways: `ss -tlnp` on the
> host, external `nc`, and an independent port checker. The rule I kept: never
> infer exposure from firewall state. I applied it to the agent too, which has
> one public port (8443) and its own Docker network with no route to MySQL.

If pushed on F-54: the exact window can't be pinned down, and whether anyone
*used* the exposure can't be established, because `general_log=0` (F-34, F-39).
Also admit that the exposure came from deploying the dev stack (F-48).

---

### Q: "What would you have missed if you'd skipped the audit?"

**Short answer.**
> The design decisions that make the brief safe came from measured data
> problems, not generic best practice. Without the audit I would have built a
> brief that says "no allergies" for most patients, calls lab values high or
> low with no basis, tries to fit whole charts into the prompt, and trusts the
> platform to scope patients, which it doesn't.

| Missed | Finding | What it changed |
|---|---|---|
| Only 21.5% of patients have **any** allergy row. An empty list is not "no known allergies". | F-21 | Rendered "not recorded" vs "none active (N on file)", computed in code. Evals A1–A3. |
| **Zero** reference ranges or abnormal flags on any lab row | F-19 | Lab interpretation is out of scope (USERS §4). C4 checks that a `severity`-style claim can't verify. |
| One chart has 530 encounters and ~974 KB of rows | F-22, F-46 | Narrow retrieval (3 resource types, active only). F1 outlier eval. |
| No per-patient scoping anywhere in ACL or OAuth | F-50 | No reliance on the platform for a patient boundary. Cross-patient evals G1, G2, C3. |
| Expired and out-of-scope tokens both return 401 | F-08 | Error message names all three causes. D3: a missing allergy scope returns 401, never "Allergies: not recorded". |
| Docker bypasses UFW | F-54 | Agent's listen addresses verified directly. Separate network with no route to MySQL. |
| Tools print success on failure (0/2 and 4/5 imports) | F-15 | Core verification principle: check retrieved data, never trust an upstream success signal. |
| OAuth discovery advertises `localhost` | F-49 | The chart embed performs the SMART EHR launch server-side over loopback. |
| Reads are not audited; the log is ~80% noise | F-10, F-11 | The embed writes one `patient-record` audit event per brief. |
| DO Managed Databases is not HIPAA-eligible | F-41 | DB and agent stay on the host. No new PHI datastore. |

**The honest counterpoint (worth volunteering).**
> The audit also missed something. F-21 counted allergy *rows* but never looked
> at allergy *coding quality*. The uncoded penicillin allergy (F-57) surfaced
> only when the eval's independent oracle read raw FHIR. The audit measured the
> database, while the agent reads FHIR, and that mapping layer is where the bug
> was.

---

### Q: "How did the audit change your AI integration plan?"

**Short answer.**
> It changed the persona, the tool scope, the trust model and the deployment.
> Nearly every section of ARCHITECTURE.md cites a finding.

1. **Persona.** I moved from "huddle prep" to "chart-open, ~90 s between
   patients". The appointments table has exactly 11 rows in *both*
   independently seeded databases, so there's no real schedule to prepare
   against (USERS §1, F-43).
2. **Tool scope.** Five FHIR types, each traced to a use case. Vitals are
   excluded from Change Detection because they're recorded once per patient
   (F-20). Labs are filtered to `laboratory` because 92% of "lab orders" aren't
   labs (F-42).
3. **Verification strength stated per field.** Medications bind to RxNorm, with
   99.7% coverage (F-55). Labs can be cited, never characterized (F-19).
4. **Authorization delegated, not reimplemented.** Every read uses the
   clinician's own token, and OpenEMR's scope enforcement is the boundary
   (F-07). The agent holds **one** credential, `ANTHROPIC_API_KEY`, and no FHIR
   credentials.
5. **No direct DB access, enforced by the network.** The agent has its own
   Docker network and reaches OpenEMR only through the published FHIR port.
   MySQL, still on `root`/`root` (F-34), is unreachable from it (tested live,
   ARCH §1).
6. **Observability without a new PHI surface.** Metrics come from a local
   script over stdout, not a SaaS. Langfuse is self-hosted and deferred
   (F-41, F-12). F-56 then showed the framework itself was an egress risk.
7. **Scope cut.** A drug-interaction check was considered and excluded:
   allergy data is incomplete (F-21) and it would be decision support, not
   surfacing (USERS §4).

---

## 2. The Architecture

### Q: "Why did you design the verification layer the way you did?"

**Short answer.**
> The model never writes what the physician reads. It emits structured claims,
> each one `source_id` + `field` + `value`, forced by a JSON schema. Plain code
> then checks three things. Was that source_id retrieved *this turn*, as that
> resource type? Is the field a real declared field on the record? Does the
> value match exactly? Code also renders the output: verified claims plainly,
> failed ones as `UNVERIFIED: "<value>" (<reason>)`. Nothing is ever dropped.
>
> I chose deterministic code over a second LLM for three reasons. Its answer
> isn't a second opinion with its own uncertainty. Its failures are legible:
> there is a closed set of three reasons. And it costs nothing. On the
> 522-encounter patient, verification took 1.4 ms against a 28.1 s model call.

**Design details worth naming (`agent/app/verification/checker.py`).**
- **Closed failure reasons**: `SOURCE_NOT_FOUND`, `FIELD_NOT_ON_SOURCE`,
  `VALUE_MISMATCH` (`:71`).
- **Lookup scoped by resource type** (`:133`). An id retrieved as an allergy
  can't verify a claim that calls it a Condition.
- **Claimable fields are declared model fields minus `source_id`** (`:165`).
  So `getattr` can't reach methods, and "id equals id" can't inflate Metric 1.
- **Exact matching** (`:197`): case- and whitespace-insensitive only, no fuzzy
  or prefix matching. "Metformin" ≠ "Metformin 500 MG Oral Tablet". Numbers
  need exact equality ("7.2 mg/dL" ≠ 7.2). Dates compare as calendar dates in
  the source's own offset. The `datetime.min` placeholder counts as absent.
  Unknown types fail closed.
- **Duplicates must all agree.** If a record was fetched twice, the claim must
  match every copy.
- **A failure can't be hidden at the source.** `_failed()` has no parameter to
  set `disclosed_as_unverified` to anything but `True` (`:155`).
- **Enforced isolation.** `tests/test_verifier.py:241` parses the module's
  imports and fails if it imports `anthropic`, `langgraph`, `langchain`,
  `httpx`, `app.fhir`, and similar.
- **The brief's schema allows only `field: "display"`.** Empty and none-active
  sections are computed from `len()` before the model runs, so the model is
  never asked to say a section is empty.

**Where the evidence is.**
- C1–C4: injected bad claims all render UNVERIFIED with the right reason (a
  fabricated id, an added dose, another patient's real allergy, and an
  unclaimable `severity` field).
- B2: every rendered line is backed by a claim, a count or a heading, so the
  model's prose never reaches the physician.
- Cost: an LLM judge would add $0.0115 per brief, +72% (COST §4.7).

**If pushed: "Isn't that just string equality? Why use an LLM at all?"**
> Right now the brief *is* extraction. The prompt asks for every active record,
> verbatim, in order, and the cost analysis says so: "the floor is no model
> call at all" (COST §4.7). The model is there for prioritizing and compressing
> big charts. The outlier took 40 claims and 28 s, and a ~100-record chart is
> estimated at ~96 s, over budget. That isn't prompted yet, and I haven't
> claimed it is. When it is, *selection* becomes judgment, and verification
> can't check selection.

**Known limits (ARCH §5, stated as limits).** Verification checks faithfulness
to the record *as parsed*, not to what OpenEMR stored (F-57). It doesn't check
retrieval completeness, multi-record synthesis ("trending down"), or that a
source belongs to the patient.

**Point worth volunteering.** An LLM judge would **also** have passed F-57,
because it would read the same parsed "(unspecified allergen)" record. What
catches that class is a reader that shares no code with the agent (§3).

---

### Q: "What does the agent do when a tool fails or a record is missing?"

**Short answer.**
> It fails explicitly and never returns a partial or "empty" brief that looks
> like a real one. Each failure has its own error code, and error bodies never
> include exception text. Missing *data* is a different thing from a *failure*,
> and code renders that distinction, not the model.

| Situation | Behavior | Evidence |
|---|---|---|
| **Unknown or typo'd patient id** | OpenEMR answers a search for an unknown id with **200 and an empty Bundle**, the same as a real patient with nothing recorded. That would render "Allergies: not recorded" for a patient who doesn't exist. Fix: read `Patient/<id>` first; 404 or 400 → `PatientNotFoundError` → **404 `patient_not_found`**, before any search runs or anything is logged. | `app/fhir/client.py:236`; commit `5ba6c61`; evals E1 (random UUID), E2 (one-character typo), E3 (legacy pid `"20"` → 422, and the **control**: with the Patient read removed, the bug comes back) |
| **Token rejected / expired / out of scope** | **401 `fhir_token_rejected`**. OpenEMR returns 401 for all three causes (F-08), so the message names all three and doesn't guess. No brief, and the model is never called. | `app/api/pre_visit_brief.py:143`; D2 (in-process events `[]`), **D3** (missing only allergy scope → 401, never "not recorded" for a patient with 10 allergies), D6 (expired 129 s) |
| **Forged or tampered token** | The agent reads `sub` *unsigned*, but only to build the attribution key. Nothing is retrieved or logged until OpenEMR accepts the same token, so a forgery fails on the first FHIR read. | `pre_visit_brief.py:94`; D4 (random signature, `alg=none`, self-issued), D5 (widened scopes, swapped `sub`, extended `exp`) |
| **FHIR down or erroring** | **502 `fhir_unavailable`** | Load test at 50 VUs: all 131 failures returned this code, with no partial briefs |
| **LLM failure, including the credit outage** | **502 `brief_generation_failed`**, "No partial brief is returned." On 2026-09-16 around 22:05Z the Anthropic balance ran out mid-eval. Briefs failed in 0.7 s and 1.0 s. | F-60; `pre_visit_brief.py:168` |
| **…but readiness didn't notice** | `/ready` kept reporting `llm_provider: healthy`, because its probe is `models.retrieve`, which spends no tokens and can't see billing. **A real gap (F-60).** | `app/health.py:116` |
| **No records of a type** | `Allergies: not recorded` | A1, A2; `render_brief` at `pre_visit_brief.py:303` |
| **Records exist, none active** | `Current medications: none active (26 on file, none with active status)` | A3 |
| **Model skips a record** | Code finds brief records no claim cites and renders `N more … on file, not included in this brief` | B3 (with a control that removes a claim) |
| **A required resource type missing from drafting input** | `ValueError`: "never queried" must not count as "zero records" | `draft_claims.py` `_serialize_retrieved` |
| **Upstream outage during evals** | Scored **ERROR (inconclusive)**, never PASS or FAIL. Changed after the outage run wrongly scored G1, G2 and E3 as FAIL. | `evals/README.md` |

**Honest gaps.**
- **Graceful degradation caused F-57.** The FHIR client substitutes
  `"(unspecified allergen)"` rather than drop a record with no usable display
  (`client.py:139`, `:348`). The placeholder then *verified*. The same fallback
  exists for problems (`:280`) and medications (`:311`).
- **Allergy status (F-58).** "None active" can't be reached for allergies.
- **No degraded mode.** A no-LLM brief of plain record lists is proposed
  (COST §4.8) but not built. Today an LLM outage means no brief at all.
- No retries, no rate limiting, and **no FHIR paging**: `_resources_from_bundle`
  (`client.py:107`) ignores `next` links. F1 confirmed OpenEMR didn't page
  (120 conditions, total = entries, no next link), and the oracle checks this
  on every run. It is still latent.

---

### Q: "Where are the trust boundaries and how are they enforced?"

**Short answer.**
> The agent has no identity of its own with respect to patient data. Every
> read runs under the requesting clinician's own OpenEMR token. OpenEMR's scope
> enforcement is the authorization boundary, and I test it rather than assume
> it. The model is untrusted too: nothing it writes reaches the physician
> without passing through the verifier and code rendering.

**Boundaries, outside in.**

1. **Internet ↔ host.** One new public port, `0.0.0.0:8443`, with TLS at Caddy.
   Uvicorn's 8000 is not published. Checked with `ss` and external `nc`
   (F-54's rule), **on the DO droplet only**.
2. **Browser ↔ chart embed ↔ OpenEMR OAuth.** The PHP module does a SMART EHR
   launch **server-side over loopback**. It uses a confidential client flagged
   *Skip EHR Launch Authorization*, PKCE, and a client secret. The token never
   reaches the browser and the browser never calls the agent. One token is
   minted per brief, and its `sub` is the logged-in physician (verified live).
   Config refuses any non-loopback host. One `patient-record` audit event is
   written per brief (ARCH §2; module README).
3. **Caller ↔ agent.** `Authorization: Bearer`. The agent reads `sub` without
   checking the signature, **for attribution only** (D4/D5 show why that's
   safe).
4. **Agent ↔ OpenEMR FHIR.** The token is forwarded unchanged. D1 reproduces
   F-07 first-hand: a `user/Patient.read`-only token gets Patient 200, and all
   5 other types get 401 (5/5).
5. **Agent ↔ database.** No path. The separate Docker network was tested live:
   MySQL is unreachable by name and through the gateway.
6. **Agent ↔ Anthropic.** PHI leaves the host **by design**: record ids,
   names, statuses, RxNorm codes and dates, with no demographics and no patient
   id. This is covered by the case study's instruction to assume a BAA, **not
   by an executed BAA** (ARCH §6).
7. **Agent ↔ LangSmith.** Blocked (F-56).
8. **Model ↔ physician.** Schema-constrained claims, the verifier, and code
   rendering. In the follow-up, the physician's question is untrusted input:
   it's fenced, the output schema allows only claims plus two closed enums, and
   every claim is verified.

**Patient scoping, and exactly what to claim.**
- `patient_session_key = <token sub>:<patient_id>:<session_id>`
  (`pre_visit_brief.py:138`). The caller can't supply the key or its user part.
  It tags every event for Metrics 1–6.
- **Nothing is carried between requests.** Each request re-retrieves under the
  token. The follow-up re-reads the records instead of accepting them from the
  caller, because "a caller-supplied set would let any claim verify"
  (`follow_up.py` docstring).
- **Cross-patient evidence:**
  - G1: same token and session, patients A → B → A, no leak.
  - G2: A and B concurrently (12.6 s / 14.0 s), no leak.
  - C3: another patient's real allergy fails `SOURCE_NOT_FOUND`.
  - Leaks are detected by the oracle's own per-patient chart reads, not by the
    agent.

**Gaps to state before they're found.**
- **F-50.** A clinician token can read any patient. The agent adds no
  per-patient ACL, deliberately, because an agent-side ACL would drift from
  OpenEMR's. The only platform option is patient-context tokens.
- **"Invalidation on patient switch" (ARCH §4) doesn't apply today**, because
  there is no stored state. Don't describe it as a working mechanism.
- **The JWT scope pre-check (ARCH §2) is not implemented.**
- Verification checks claim → source, not source → patient. Retrieved records
  carry no patient field.
- The agent → OpenEMR hop skips TLS verification (self-signed, F-36; it stays
  on-host). No rate limiting. Tokens aren't revoked after use (1 h expiry).
- **Vultr has no BAA (F-61)**, which is acceptable only because every record is
  synthetic.

---

## 3. The Evaluation

### Q: "What does your eval suite test that a happy-path demo wouldn't?"

**Short answer.**
> Every live brief in the run verified 100% of its claims, and four cases still
> failed. A demo shows "5 of 5 verified". The suite showed that one of those
> five was a penicillin allergy rendered as "(unspecified allergen)".
>
> The design choice that caught it is an **independent oracle**. `oracle.py`
> parses raw FHIR JSON itself and imports nothing from `app/`. That includes
> the resource narrative (`text.div`), where OpenEMR puts an uncoded allergy's
> name. The agent's verifier compares claims against the agent's *own* parsed
> records, so a parsing bug passes verification. Only a separate reader of the
> source can see it.

**What makes the suite hard to fool (`agent/evals/README.md`).**
- **Controls, so a check can't pass by being unable to fail.** B1–B3 run their
  checker on a corrupted copy of a real response. C5's text check must catch
  deliberately dropped claims. E3 removes the Patient-exists guard and requires
  the original bug to come back. If a control doesn't trip, the case fails with
  "eval control failed".
- **Injection at the checker (C1–C4).** The live model rarely drafts a failing
  claim, so the failure path is exercised by appending known-bad claims to the
  model's *real* draft.
- **Mutation.** A6 flips `clinicalStatus` to resolved on a real Bundle, in the
  test, with no write to the chart. C5 swaps in a renderer that drops failures.
- **Patients chosen by rule, not hardcoded ids** (F-43). Strata are: empty
  chart, no allergies with inactive meds, most coded allergies, uncoded
  allergy, most encounters, smallest mixed chart.
- **Adversarial authorization.** Forged signature, `alg=none`, self-issued,
  widened scopes, swapped `sub`, extended `exp`, a genuinely expired token,
  missing and malformed headers.
- **Scale and completeness.** The 522-encounter chart must have nothing
  truncated, every active record accounted for, and finish within 90 s.

**The four defects (F-57 to F-60, logged in commit `6dbbbcc`).**

1. **F-57, uncoded allergy verifies as "(unspecified allergen)"** (A5, B1).
   The chain, step by step:
   - OpenEMR sets `code` to data-absent "Unknown" when there's no allergen
     code, and puts the title "penicillin" in the narrative
     (`FhirAllergyIntoleranceService.php:217`, `:221`).
   - The agent's client reads only `code`, skips the data-absent coding, and
     returns the fallback string (`client.py:139`, `:154`, `:348`).
   - The model copies that string, and the verifier compares it with the same
     parsed record, so it passes.
   - The result on the API collection's *own* demo patient, Phil Belford:
     `Allergies: - (unspecified allergen)` with
     `{"claims": 5, "verified": 5, "unverified": 0}`, identical in every run
     from 21:40Z to 22:48Z.
2. **F-58, allergy status discarded** (A6). The allergy model has only
   `source_id`, `display` and `recorded_date`. So 10 resolved allergies render
   as current, and "none active" can never appear for allergies. This is latent:
   all 50 live allergies are `active`.
3. **F-59, Metric 2 can't see what it exists to catch** (C5). With a renderer
   that hid 4 failed claims, the suite's text check found 5 violations, but
   `compute_metrics.py` still reported **100.0% (4 disclosed of 4)**. The
   verifier sets `disclosed_as_unverified=True` *before* rendering, and nothing
   records what was actually shown. The real renderer is correct today (B2,
   C1–C4); the monitor is what's broken.
4. **F-60, readiness blind to billing.** Observed during an earlier run, not a
   designed case. The failure itself was well contained: fast 502s, nothing
   partial. But `/ready` said healthy the whole time.

**Precise wording to use.** "4 failing cases from 3 defects, plus a fourth
defect observed during an earlier run." Don't say "4 failing cases = 4 bugs",
because A5 and B1 are the same bug.

---

### Q: "What did you find when you ran it?"

**Short answer.**
> 27 cases: 23 pass, 4 fail. Run on 2026-09-16 at 22:47Z against the deployed
> agent and 50 synthetic patients visible to the test clinician. The failures
> were A5, A6, B1 and C5, described above. The passes that weren't guaranteed
> matter just as much.

**Passes worth quoting.**
- **D1** made F-07's out-of-scope 401s, previously operator-reported and never
  reproduced, into a repeatable check (5/5 refused). It also confirmed F-08's
  body difference: `"Unauthorized"` vs `"The resource owner or authorization
  server denied the request."`
- **D3.** A missing allergy scope returns 401, never "Allergies: not recorded",
  and the model isn't called.
- **E1–E3.** OpenEMR *still* answers unknown ids with an empty 200, so the
  Patient read (`5ba6c61`) is the only thing preventing a brief about nobody.
- **F1.** 40/40 active records cited, nothing truncated, 28.8 s of which the
  model took 28.1 s. That is within 90 s, but one moderately large chart used a
  third of the budget.
- **G1/G2.** No cross-patient leak, sequential or concurrent, on a shared token
  and session.

**Caveats to state first.**
- **One model run per patient.** A pass is a sample, not a bound.
- In-process cases ran checkout `82ea759`, while the live agent was `2d46f0f`.
  The deployed commit can't be observed from outside.
- It ran on the **pre-migration DO population**. The Vultr host has different
  patient ids.
- TLS isn't tested (verification is off on both hops).
- The follow-up endpoint was built *after* this run. It has unit tests
  (`tests/test_follow_up.py`) and a live smoke script, **not eval cases**.

---

### Q: "What would you add next?"

In priority order:

1. **Fix F-57, and treat it as a class, not one allergy.** When `code` holds
   only data-absent codings, take the display from the narrative (tags
   stripped) *and* keep the record marked **uncoded**, rendered as a distinct
   non-verifiable state. A5 and B1 should flip to PASS. Then extend A5 to
   Condition and MedicationRequest, which use the same fallback
   (`client.py:280`, `:311`).
2. **A data-quality census case.** Count data-absent codes, missing statuses
   and missing dates per resource type across the whole population on every
   run. The population had exactly one uncoded allergy, so the stratum can
   disappear on a new population without anyone noticing.
3. **Fix F-58 and F-59 and add their regression cases.** Carry
   `clinicalStatus`. Emit a render-side disclosure event per failed claim and
   compute Metric 2 from that, so C5 passes.
4. **Repeated trials.** k runs per model-dependent case, reporting the pass
   rate, not one sample.
5. **Re-run on the Vultr population.** Current results name ids that don't
   exist there.
6. **Follow-up evals in the suite:** answerable, not-in-record, injected
   claims, **prompt injection through the question**, and a follow-up across a
   patient switch.
7. **A model and effort sweep:** Opus 5 at low effort, Sonnet 5 and Haiku 4.5,
   scored on Metric 1 and skipped-record counts. About 585 calls, at most ~$24
   (COST §4.7).
8. **Row-level completeness at larger scale:** a chart big enough to force FHIR
   paging (`next` links), plus a latency case near the estimated ~100-record
   budget breach.
9. **Clinical ground truth.** Clinician-reviewed "what should this brief have
   said" for a sample. This is the only way to evaluate *selection* once the
   prompt compresses.

---

## 4. Production Thinking

### Q: "How would you scale to a 500-bed hospital with 300 concurrent clinical users?"

**Short answer.**
> The load test says the agent isn't the bottleneck; OpenEMR is. At 50
> concurrent requests the agent peaked at 18.7% CPU and 115 MiB, while
> OpenEMR's Apache/PHP pinned 3.6 of 4 cores, grew from 39 to 228 processes,
> and 59% of requests failed with `fhir_unavailable`. So the first move is
> separating and scaling the EHR's PHP tier, not tuning the agent. After that,
> the constraints are Anthropic spend caps and chart size, not throughput.

**1. Translate "300 concurrent users" into load first.** These are *my
estimates*, built from the project's measured numbers and assumptions; the
project never ran a 300-user test.
- A k6 VU is **not a user**. Each VU sends a brief, waits for the answer,
  sleeps 1 s and repeats (`k6-infra.js:73`). At 10 VUs that came to ~0.5
  briefs/s *per VU*, hundreds of times a clinician's modeled peak of ~5 briefs
  an hour. So 50 VUs is far more load than 50 clinicians, and 300 humans
  generate far less than 300 VUs would.
- Using COST_ANALYSIS's assumptions (20 briefs per clinician per day, 2× peak):
  300 users ≈ **25 briefs/min ≈ 0.4 briefs/s ≈ 1.7 FHIR reads/s**. Each brief
  is 4 reads: Patient plus 3 searches.
- A hospital isn't primary care. Inpatient staff reopen charts far more often,
  and shift change bunches it up. If all 300 open a chart in the same minute,
  that's 5 briefs/s ≈ **20 FHIR reads/s**.
- **Measured ceiling:** at 10 VUs the droplet served 5.04 req/s ≈ **20 FHIR
  reads/s** with OpenEMR already at **336% CPU**, and that was with *no*
  clinician EHR traffic at all. So a shift-change burst lands right at the
  measured knee of a 4-vCPU shared host, before the hospital's own EHR use.

**2. What to change, in order.**
- **Separate the agent from OpenEMR** (COST §4.9 estimated this at ~1K users;
  a hospital's usage pattern pulls it earlier). That also removes the reason
  the agent → OpenEMR hop skips TLS verification.
- **Scale OpenEMR's PHP tier horizontally** behind a load balancer, and cap
  workers so it sheds load instead of spawning 228 processes.
- **Database: careful here.** MySQL peaked at **67%** of one core, so it wasn't
  the constraint. Read replicas are a sensible *second* step, since FHIR reads
  are read-only and replica-friendly, but the evidence points at PHP first.
  Read replicas are not in the repo's docs, so present them as your own
  reasoning.
- **Agent tier.** It's stateless per request, so replicas are trivial. Today
  it's one uvicorn process, and `compute_metrics.py` reads one stdout log;
  both need changing (COST §4.9 at 10K). Also a cheap win I found in the code:
  `client.py:99` opens a **new `httpx.AsyncClient` per FHIR call**, meaning a
  new TLS handshake for each of the 4 reads per brief. That handshake is served
  by the same saturated Apache. F-47's ~110 ms per TLS connection was measured
  over the internet; the on-host hop is cheaper, and nobody has measured it.
  Use a shared connection pool.
- **Admission control:**
  - A per-clinician rate limit. There's none today, and one looping client
    costs **$277/day** (COST §4.4).
  - A global cap on in-flight LLM calls.
  - A **deadline-aware queue** that drops a brief it can't finish inside 90 s.
  - Per-tenant FHIR concurrency caps.
  - A **no-LLM degraded mode** (COST §4.8).

**3. Cost inflection points (COST_ANALYSIS).**
- **The spend cap binds before throughput.** 300 users of briefs alone is
  ≈ 300 × 420 × $0.016 ≈ **$2,019/mo**, over Start ($500) and Build ($1,000).
  Request the Scale tier **before launch**, because a new HIPAA organization
  starts with no usage history (§4.3).
- **Static-prefix cache on day 1.** It holds no PHI and saves 21% per brief.
  The ~750-token prefix is an estimate, and `count_tokens` should confirm it
  clears Opus 5's 512-token minimum.
- **Model tiering.** Evaluate at 1K users, decide by 10K. Sonnet 5 would halve
  brief cost, but its 1,024-token cache minimum means that prefix wouldn't
  cache. Keep diffing and follow-ups on Opus.
- **Patient-context cache** only when follow-ups are common: ≥ 28% of briefs
  followed up within 5 minutes, 5-minute TTL (§4.5b).
- **Not available:** the Batch API is not HIPAA-eligible and takes up to 24 h
  (§4.6).

**4. What breaks that isn't capacity: chart size.** Inpatient charts are
bigger. Output grows linearly with the one-claim-per-record prompt. F1 took
28 s for 40 records, and a ~101-record chart is *estimated* at ~96 s, over the
90 s budget (COST §1.3). The prompt has to prioritize and compress, which opens
the selection blind spot (§5 below).

**Prerequisites before any of this touches real PHI:** a host under an
executed BAA (F-61), the hardened release image instead of the dev stack with
Xdebug (F-48, F-35), a publicly trusted certificate (F-36), and a BAA with
Anthropic (ARCH §6).

---

### Q: "What would you change before a real physician relied on this?"

**Short answer.**
> Three groups: fix the known correctness defects, make the monitoring able to
> see failures, and close the compliance preconditions I documented as open.
> Then clinical validation, which this project hasn't done at all.

1. **Correctness.**
   - **F-57**: an uncoded record becomes a distinct, visible state such as
     "allergy recorded without a code — check the chart", never a placeholder
     that verifies. Generalize it to problems and medications.
   - **F-58**: carry allergy status.
   - Follow FHIR paging.
2. **Observability that can actually detect failures.**
   - **F-59**: measure disclosure from the rendered output.
   - **F-60**: readiness reflects recent LLM failures, e.g. N consecutive
     billing errors → not ready.
   - Deploy the self-hosted Langfuse (built and verified locally, **not
     deployed**) on BAA-covered infrastructure, with prompt and completion
     export off.
   - Page someone on any Metric 3 regression.
   - Today the six metrics come from a script run by hand over one host's
     stdout.
3. **Compliance and hardening. None of these are done.**
   - A BAA for the host: Vultr has none (F-61).
   - An executed BAA with Anthropic (the case study's assumption isn't one).
   - The **server-side refusal fallback beta** in `draft_claims.py:81` is
     not on Anthropic's HIPAA-eligible list. A HIPAA organization gets a 400
     for non-eligible features, which would **fail every brief**. Confirm it or
     remove it (COST §5).
   - The hardened image without Xdebug (F-48, F-35).
   - Rotate the five username-as-password demo accounts (F-32). The live demo
     login is one of them.
   - Enable `audit_events_query` (F-10).
   - A trusted certificate, TLS verification on every hop, and rate limiting.
4. **Domain-constraint rules, framed carefully.** ARCH §5 names an additive
   rule layer (dosage thresholds, interaction flags). It's not built, and
   USERS §4 *excluded* prescribing-time decision support. For v1 I'd build
   **presentation-safety rules** instead:
   - never render "no known allergies" (A2 already checks for NKDA-style
     wording);
   - never characterize a lab value (F-19);
   - flag uncoded or placeholder values;
   - flag a brief whose counts don't reconcile.
   Clinical rules like dose thresholds need a vetted reference source the audit
   never evaluated.
5. **Resolve the doc/code mismatches.** Either implement the JWT scope
   pre-check (ARCH §2) or remove the claim.
6. **Clinical validation.** A shadow-mode period where clinicians compare
   briefs to charts, with a UI that says the chart is authoritative.
   Repeated-trial evals on the real population, and a clinician-labeled
   relevance set.

---

### Q: "What failure mode worries you most, and why?"

**Short answer: verified-but-wrong.**
> A claim that is faithful to a *mis-parsed* record. F-57 is the real example,
> not a hypothetical. The brief showed "(unspecified allergen)" with a badge
> reading 5 of 5 verified, while the chart says penicillin. It worries me more
> than hallucination for four reasons.
>
> - **Hallucination is loud.** A fabricated id or altered value renders
>   UNVERIFIED (C1–C4 pass). Verified-but-wrong is silent.
> - **The trust signal makes it worse.** The whole UI teaches the physician to
>   trust verified lines. A parsing bug upstream of verification turns that
>   badge into false confidence, on a *drug allergy*, at the point before
>   prescribing.
> - **It's systematic, not random.** Every uncoded record, every run, every
>   clinician gets the same result. The run showed it identically from 21:40Z
>   to 22:48Z. Retrying doesn't help.
> - **Nothing in the pipeline can catch it by construction.** Not the verifier,
>   and not an LLM judge either, since both read the same parsed record. The
>   happy-path demo looks perfect.

**Its sibling: verified-but-incomplete.** A record that was never retrieved or
never drafted makes every remaining claim pass (ARCH §5, §6 open risk). That's
the next question.

**What defends against it.**
- **Now:** an independent oracle that shares no code with the agent and reads
  raw FHIR including the narrative. Controls in the eval. Counts rendered to
  the physician ("26 on file, none with active status", "N more … not
  included").
- **The design fix:** never let "unknown" take a form that can verify. An
  uncertain parse should produce a distinct rendered state the physician can
  see, not a string the model copies and the verifier accepts. Verification
  should stay *fidelity* checking, with source quality reported separately.
- **In production:** a periodic data-quality census (data-absent codes per
  type), plus sampled offline re-checks of rendered briefs by the oracle on the
  same host, so no PHI leaves.
- **Stated plainly:** verification guarantees a claim matches the record *as
  the agent parsed it*. It never guaranteed clinical truth or source fidelity,
  and ARCH §5 lists "source data accuracy" as a limit.

---

## 5. Likely Curveballs

### Q: "Is it actually multi-turn / conversational?"

**Short answer. Don't hedge.**
> No, not yet. The Pre-Visit Brief is single-turn by design: USERS.md defines
> it as one pass over the chart when it opens. I built a **constrained
> single-turn follow-up** (`POST /pre-visit-brief/follow-up`, commit
> `73dd933`): one question about the brief just rendered. Full multi-turn
> conversation is the next build, and I stopped where I did on purpose.

**What the follow-up does (`agent/app/graph/follow_up.py`).**
- It re-retrieves exactly the brief's record set under the same token (Patient,
  then Condition, MedicationRequest, AllergyIntolerance). The caller cannot
  send records, claims or prior answers.
- The model drafts claims on any declared field (status, dates, RxNorm code),
  plus `coverage` and `gaps` from closed sets.
- The **same `verify_claims`** runs on the same records.
- `answer_status` can only be *lowered* by verification: no verified claim
  means `not_in_record` (`:174`).
- Gaps render as fixed sentences, e.g. "Lab results were not retrieved for this
  visit." The model's text is never shown.
- The question is never logged. It isn't a LangGraph graph, so there's no state
  to trace (F-56).
- Tested with unit tests and a live smoke script covering three runs:
  answerable, not in record (HbA1c, blood pressure), and injected bad claims.
  **Not in the eval suite.**

**Why I stopped there.**
> Multi-turn adds stored state, and stored state is exactly where cross-patient
> leakage and caller-influenced context get in. OpenEMR won't stop that for me
> (F-50), and verification can't see which patient a source belongs to. I built
> the version that keeps the verification guarantee intact.

**What the next build is** (from ARCH §4 and COST §4.5, §4.9):
- Server-side state keyed by (user, patient, session), invalidated on patient
  switch, holding source ids and verified claims rather than free text, and
  re-verified every turn.
- A self-hosted store on BAA-covered infrastructure, e.g. Redis.
- Resolving "that" and "last time" against the prior turn, and re-querying
  Encounter and Observation.
- Context managed in the agent, since server-side compaction isn't
  HIPAA-eligible.
- A 5-minute-TTL patient-context cache.
- A G-style eval that switches patients mid-conversation.

---

### Q: "Your verification only confirms cited values exist — it can't tell if the model omitted a relevant record. How do you handle that?"

**Short answer. Agree first.**
> Correct, and it's by construction. ARCHITECTURE.md §5 says so, and §6 lists
> retrieval incompleteness as an open risk. So I don't rely on verification for
> completeness. I made omissions *visible* in code at every layer I control,
> and I test for them with a reader that doesn't share the agent's code.

**The layers.**
1. **Retrieval is fixed, not chosen by the model.** All three resource types
   are queried on every run. `_serialize_retrieved` refuses input missing a
   type, because "never queried" must not render as "zero records".
2. **Selection is a rule in code.** "Active" means `clinical_status`/`status`
   is `active`; every allergy is included. It is not the model's judgment
   (`draft_claims.py` `brief_records`).
3. **The renderer counts what the model skipped.** For every in-brief record no
   claim cites, it renders `N more … on file, not included in this brief`
   (`pre_visit_brief.py:354`). A model omission becomes text the physician
   sees.
4. **Section counts come from `len()`,** so "not recorded" and "none active
   (N on file)" are facts, not model output.
5. **Metric 5 (retrieval coverage)** is a type-level proxy, and the docs call it
   a proxy.
6. **Evals:** B3 requires retrieved and active counts to equal the oracle's
   chart, and every active record cited or counted, with a control that deletes
   a claim. F1 checks the oracle read was complete (total = entries, no `next`
   link) on a 120-condition chart. D3 makes sure an auth failure never shows up
   as an empty section.

**Where it still leaks (say this).**
- **Row-level completeness depends on FHIR.** The client doesn't follow paging
  `next` links (`client.py:107`), so a paged response would shrink the
  retrieved count and the "N more" line would be wrong too. Only the oracle's
  completeness check would notice.
- **Omissions *inside* a record.** F-57 (a name missing from `code`) and F-58
  (status dropped) are omission bugs that count-based checks can't see.
- **Compression turns selection into judgment.** Today the prompt asks for
  every record, so "N more" is a strong signal. Once the model prioritizes long
  charts, which large or inpatient charts will require, choosing what to
  surface is judgment. The only defense then is a clinician-labeled relevance
  eval, which doesn't exist yet (COST §4.7, "one loss would be silent").
- **Change Detection** (not built) makes this harder: a missed "stopped
  medication" in a diff is an omission with no record to count against.

---

### Other curveballs worth a one-line answer

- **"Why LangGraph if you had to fight its tracing?"** The brief's pipeline is
  a linear four-node graph and it's guarded. The follow-up deliberately isn't a
  LangGraph graph, because a fixed sequence needs no graph runtime and then
  there's no state to trace. I'd weigh dropping it for the brief too.
- **"Your ARCHITECTURE says the agent pre-checks JWT scopes."** That was the
  plan; it isn't implemented. Today the agent forwards the token and maps
  OpenEMR's 401, and D1–D7 test that path.
- **"These results are from a host you no longer run on."** True. The
  migration to Vultr was a payment constraint and it's a fresh install with new
  ids. The code under test is the same, and a re-run on Vultr is on the next
  list.
- **"Prompt injection through chart text?"** The model can only emit
  schema-constrained claims, and every value must exactly match a retrieved
  field, so injected text can at most become a verified copy of what the chart
  literally says, or an UNVERIFIED line. Not claimed: HTML escaping in the UI
  isn't covered by any eval.
- **"What did it cost to build?"** $0.36 of Claude API spend across all keys
  over 30 days. The coding assistant and infrastructure are excluded (COST
  §2.2).
- **"Why is the demo login `physician`/`physician`?"** It's a seeded demo
  account that the audit itself flags as a live risk (F-32). A real deployment
  wouldn't ship it.
