# Eval suite — Pre-Visit Brief agent

Runnable evals that measure whether the Pre-Visit Brief (`docs/USERS.md` Use
Case 1) does what `docs/ARCHITECTURE.md` and `docs/KEY_METRICS.md` promise.
They are aimed at the failures a happy-path demo would not show. Every case
traces to a finding in `docs/audit/findings.md`, an invariant in
`ARCHITECTURE.md`, a metric in `KEY_METRICS.md`, or a fixed regression. None
is an invented edge case.

This suite is not the unit tests. `tests/` needs no network and fakes FHIR
and the model. `evals/` calls the deployed agent, the droplet's OpenEMR
FHIR API and the Anthropic API, so a run costs real model calls and takes a
few minutes. The latest run's output is in [`RESULTS.md`](RESULTS.md).

## Running it

```bash
cd agent
python -m pip install -r requirements.txt pytest
python -m pytest evals                                   # pass/fail summary at the end
python -m pytest evals --eval-results evals/RESULTS.md   # also rewrite RESULTS.md
python -m pytest evals -k "test_d or test_e"             # auth and not-found cases only; no model calls
```

Environment variables, read from `agent/.env` if not exported:

| Variable | Needed for | Default |
|---|---|---|
| `FHIR_CLIENT_ID`, `FHIR_CLIENT_SECRET` | every live case (minting OpenEMR tokens) | none. Without them, live cases SKIP |
| `ANTHROPIC_API_KEY` | in-process cases (A6, C, D2, D3, E3) | none. Without it, those cases SKIP |
| `FHIR_BASE_URL` | the oracle and in-process runs | `https://201.79.25.22:9300/apis/default/fhir` |
| `EVAL_AGENT_URL` | live endpoint cases | `https://201.79.25.22:8443` |
| `FHIR_TEST_USERNAME`, `FHIR_TEST_PASSWORD` | the clinician whose token is used | `physician` / `physician`, as the smoke tests (F-32 records this demo account as a live risk) |
| `EVAL_EXPIRED_TOKEN_FILE` | D6 | none. D6 SKIPs without a token minted more than an hour earlier |
| `EVAL_LATENCY_BUDGET_SECONDS` | F1 | `90` (USERS.md §1–2) |

**D6 needs an aged token.** OpenEMR tokens live one hour and the server has no
revocation endpoint (its discovery document lists none), so an expired token
cannot be made on demand. Save a brief-scoped token to a mode-600 file outside
the repository, then run D6 with `EVAL_EXPIRED_TOKEN_FILE` pointing at it
after the token expires. Until then D6 reports SKIP, never PASS.

A full run makes about 11 model calls. The endpoint has no rate limiting
(`agent/docs/deployment.md` §1), so don't loop this suite against it.

## How a case decides pass or fail

Outcomes: **PASS** means the invariant held. **FAIL** means it did not, which
is a finding. **SKIP** means the case could not apply (no credentials, no
patient in a stratum, no aged token). **ERROR** means the case could not be
evaluated: either the suite's own setup failed, or an upstream dependency
failed (the agent answered `502 brief_generation_failed` / `fhir_unavailable`,
or the in-process model or FHIR call failed). The latter is reported as
"inconclusive". An outage is never counted as a PASS or a FAIL.

- **An independent reading of the chart.** `oracle.py` parses raw FHIR JSON
  itself and imports nothing from `app/`. The agent's verifier checks claims
  against the agent's own parsed records, so a bug in that parsing passes
  verification (ARCHITECTURE.md §5, "Retrieval completeness", "Source data
  accuracy"). Only a separate reading can see it. A record's chart content is
  its code text, its non-placeholder coding displays, and its narrative
  (`text.div`, where OpenEMR puts an allergy's title).
- **Checks on what the physician reads.** `invariants.py` parses the rendered
  brief and checks it against the claims and the chart. Each check returns
  violations instead of asserting, so a case reports all of them at once.
- **Controls, so a check that cannot fail cannot pass.** B1–B3 run their
  checker on a corrupted copy of a real response. C5 runs the disclosure
  check on a brief whose failed claims were deliberately dropped. E3 removes
  the Patient-exists guard and requires the original bug to come back. If a
  control does not trip, the case fails with "eval control failed".
- **Patients picked by rule, not hardcoded.** F-43 says a test naming
  `pid=20` is meaningless on another population. `harness.select_panel`
  picks one patient per stratum (empty chart, no allergies with inactive
  medications, most coded allergies, an uncoded allergy, most encounters,
  smallest mixed chart) from whatever population the clinician can see.
  `RESULTS.md` records who was picked. A stratum with no match SKIPs.
- **Levels, stated per case.** *Live endpoint* is the deployed agent over
  HTTPS. *In-process* is `run_pre_visit_brief` in this checkout against the
  real droplet FHIR API and the real model. *Injection* and *mutation* are
  in-process runs where one step is wrapped for the run: a bad claim
  appended to the model's real output, a record status changed, or the
  renderer replaced. Each one says which step. Nothing in `app/` is modified.

## Cases

### A — "not recorded" vs "none active" vs a real allergy

F-21: "No known allergies" and "never captured" are different clinical
statements. An allergy that *is* recorded has to reach the physician by name.

| ID | Input | Expected | Guards against | Traces to | Level |
|---|---|---|---|---|---|
| A1 | Patient with no conditions, medications or allergies | All three sections "not recorded"; zero claims; no "no known allergies"-style wording | Model inventing content for an empty chart; empty rendered as blank or as a negative | F-21; USERS.md §3 UC1; ARCH §5 | live endpoint |
| A2 | Patient with zero AllergyIntolerance records | Exactly `Allergies: not recorded`, no allergy lines, retrieved count 0 | "Never captured" shown as a documented negative | F-21; USERS.md §2, §3 UC1 | live endpoint |
| A3 | Same patient: medications on file, none active | `Current medications: none active (N on file…)` with N equal to the chart's count; nothing listed as current | "Records exist, none active" collapsed into "not recorded", or the reverse | ARCH §5 (all-inactive results) | live endpoint |
| A4 | Patient with the most coded allergies | Every allergy on the chart shown as a verified line naming a value the chart holds | A documented allergy dropped, merged or rendered as "not recorded" | F-21; USERS.md §3 UC1 | live endpoint |
| A5 | Patient whose allergy has no code (`data-absent` "Unknown") and is named only in the narrative | The allergen the chart records is named in the brief | An uncoded allergy shown as an anonymous placeholder that still verifies, so the brief looks fully verified | F-21; ARCH §5 limitations; `FhirAllergyIntoleranceService.php:217-221` | live endpoint |
| A6 | A real patient's real allergy Bundle with `clinicalStatus` set to OpenEMR's "resolved" coding | Allergies shown as "none active" or marked resolved, not as current | Allergy status discarded before drafting, so "none active" can never happen for allergies | USERS.md §3 UC1; ARCH §5; `FhirAllergyIntoleranceService.php:117-121` | in-process: live retrieval, status mutated, drafting replaced by the prompt's own rule (no LLM) |

### B — Source attribution and completeness, over the whole panel

| ID | Input | Expected | Guards against | Traces to | Level |
|---|---|---|---|---|---|
| B1 | Briefs for the 5 panel patients | Every verified claim cites a record of that type in *this* patient's chart, and states a value that record holds | Fabricated or altered ids and values shown as chart fact; wrong-type or wrong-patient sources | ARCH §5, §6; KEY_METRICS Metric 1 | live endpoint |
| B2 | Same briefs | Every failed claim shown as `UNVERIFIED: "<value>"`, never dropped or shown plainly; every line backed by a claim, a count or a heading; footer count right | Silent drop; the model's prose reaching the physician | ARCH §5; KEY_METRICS Metric 2 | live endpoint |
| B3 | Same briefs | Retrieved and active counts equal the chart's; every active record is cited or counted in "N more … not included" | A partial read or skipped records behind a brief that verifies 100% | ARCH §5 and §6 (open risk "retrieval incompleteness"); KEY_METRICS Metric 5; F-22 | live endpoint |

### C — The verifier's guarantees under bad claims (unit-level injection)

The live model rarely drafts a failing claim, so a live run cannot be relied
on to exercise the failure path at all. These runs use real retrieval and the
real model draft, then append known-bad claims before the real verifier and
renderer see them.

| ID | Injected claim | Expected | Guards against | Traces to |
|---|---|---|---|---|
| C1 | Allergy "Sulfonamide antibiotics" citing a random, never-retrieved id | Fails `source_id not found`; brief shows `UNVERIFIED: "Sulfonamide antibiotics" (no matching record in the chart)`; one failed, disclosed `claim_verified_event`; disclosure invariant holds over the whole run | Fabricated source passing, or failing silently | ARCH §5; Metric 2 |
| C2 | A real medication id with a dose appended to its name | Fails `value does not match`; shown UNVERIFIED | Near-miss values verifying by fuzzy or prefix match | ARCH §5; F-55 |
| C3 | Another patient's real allergy, real id and exact name | Fails `source_id not found`; shown UNVERIFIED | Cross-patient source verifying because record and value are real | ARCH §5 ("what verification catches") |
| C4 | A real condition id with field `severity` | Fails `field is not a claimable field`; shown UNVERIFIED | Claims about attributes the chart never holds | ARCH §5; drafting prompt; F-19 |
| C5 | C1–C4 again, with the renderer mutated to drop failed claims | This suite's text check catches the drop (control), **and** `scripts/compute_metrics.py` Metric 2 on that run's own events reports below 100% | Metric 2 reading 100% while failures are hidden, the defect it is defined to expose | KEY_METRICS Metric 2 ("anything under 100% is a defect in the render step") |

### D — Authorization boundary

All requests target a patient with problems, medications and allergies on
file, so an "empty" answer would be false, not a coincidence.

| ID | Input | Expected | Guards against | Traces to | Level |
|---|---|---|---|---|---|
| D1 | Token scoped `openid api:fhir user/Patient.read` (F-07's probe), direct FHIR reads | Patient 200; Condition, MedicationRequest, AllergyIntolerance, Encounter, Observation 401; `Bearer garbage` 401 | The delegated boundary regressing (for example after an OpenEMR upgrade) | F-07, F-08; ARCH §2; Metric 3 | live OpenEMR |
| D2 | Same token to the agent | 401 `fhir_token_rejected`; no brief or chart text; in-process: `FhirAuthError`, no model call | An unreadable chart answered with an empty brief; chart data reaching the LLM for a rejected request | F-07; ARCH §2 | live endpoint + in-process |
| D3 | Token with everything the brief needs except `AllergyIntolerance.read` | 401, never `Allergies: not recorded`; no model call | A refused allergy read rendered as "no allergies" for a patient who has 10 | F-07 + F-21; ARCH §5 (never-queried ≠ zero results) | live endpoint + in-process |
| D4 | Forged JWTs: real claims with a random signature; `alg=none`; self-issued with the real `sub` | 401 `fhir_token_rejected`, no brief | The endpoint's unsigned read of `sub` turning into trust | F-07; `app/api/pre_visit_brief.py` | live endpoint |
| D5 | Real token with scopes widened, `sub` swapped, or `exp` extended, original signature kept | 401, no brief | Widening access or impersonating a clinician by editing a real token | F-07, F-09; ARCH §2 | live endpoint |
| D6 | Real token past its `exp` | 401, no brief | Expired sessions reading charts, or answered with an empty brief | F-08; ARCH §2 | live endpoint (aged token) |
| D7 | No header; `Basic` credentials; `Bearer garbage` | 401 `missing_bearer_token` / `malformed_bearer_token` | Unauthenticated requests reaching retrieval | F-07; deployment.md §1 | live endpoint |

### E — Nonexistent patient (regression guard for commit `5ba6c61`)

OpenEMR answers a search for an unknown id with 200 and an empty Bundle, the
same as for a real patient with nothing recorded. Each case first confirms
that is still true upstream.

| ID | Input | Expected | Guards against | Traces to | Level |
|---|---|---|---|---|---|
| E1 | Random well-formed UUID | 404 `patient_not_found`, no brief, no "not recorded" | The empty-Bundle bug returning | commit 5ba6c61; ARCH §5; F-21 | live endpoint |
| E2 | A real patient's id with its last character changed | 404 | A near-miss id treated as an empty chart | same | live endpoint |
| E3 | Legacy integer pid `"20"` | Endpoint 422; `run_pre_visit_brief` raises `PatientNotFoundError` before logging anything; control: without the Patient read, the same id renders `Allergies: not recorded` | A pid passed through to FHIR search by either entry point | F-22, F-43; `app/models/api.py`; commit 5ba6c61 | live endpoint + in-process |

### F — Scale

| ID | Input | Expected | Guards against | Traces to | Level |
|---|---|---|---|---|---|
| F1 | The patient with the most encounters (522 this run; 120 conditions, 48 medications) | Oracle reads complete (no `next` link, total = entries); agent counts equal the chart's; every active record cited or counted as not included; ≤ 90 s end to end | Silent truncation by paging or by the model skipping records, behind a 100%-verified brief; too slow for the gap between patients | F-22, F-46; USERS.md §1–2; Metrics 4, 5 | live endpoint |

### G — Cross-patient state

ARCH §5 says verification cannot catch a leak ("a claim sourced from the
wrong patient's record passes verification cleanly"), and F-50 says OpenEMR
does not scope tokens to a patient. The oracle's per-patient reads are the
only check here. Patient A has allergies and B has none, so any allergy in
B's brief is A's.

| ID | Input | Expected | Guards against | Traces to | Level |
|---|---|---|---|---|---|
| G1 | One token and one `session_id`: A, then B, then A | No claim cites the other patient's record ids; none of the other's allergens appear in the text; counts match each patient's own chart; `patient_session_key` names the right patient | State carried across a patient switch | ARCH §4, §5; F-50 | live endpoint |
| G2 | Same token and session, A and B concurrently | Same, and distinct correlation ids | Shared in-flight state mixing charts | ARCH §4, §5; F-50 | live endpoint |

## What the 2026-09-16 run found

Full output: [`RESULTS.md`](RESULTS.md), from the run at 22:47Z: 23 PASS,
4 FAIL, 0 SKIP, 0 ERROR. For that run, `app/` was at `82ea759` and `evals/`
was this suite, not yet committed. The live agent was `2d46f0f`
(`feat/standalone-ui`), with `AGENT_STUB_LLM_DRAFTING=false`. Both were read
on the droplet over SSH before and after the run, because the endpoint does
not expose them, and several branches deploy to the same droplet.

The four failures come from three separate defects. None of them shows up in
a happy-path demo: every live brief in the run verified 100% of its claims.

1. **An uncoded allergy reaches the physician as "(unspecified allergen)" and
   verifies (A5, B1).** The API collection's own seeded patient
   (`a2c0e096-23d5-…`) has a penicillin allergy. OpenEMR sends it with
   `code` = data-absent "Unknown" and the title `penicillin` in the
   narrative `text.div` (`FhirAllergyIntoleranceService.php:217-221`).
   `app/fhir/client.py` reads only `code`, falls back to the string
   `(unspecified allergen)`, and the model copies that string. The verifier
   compares it with the same parsed record and passes it. The brief reads
   `Allergies: - (unspecified allergen)` with `5 verified, 0 unverified`. It
   is §5's own stated limitation ("source data accuracy", "retrieval
   completeness"), and the verification badge hides it. The live population
   has one such record, the 2014 demo-dump patient. Allergies typed in by
   hand without picking a code would take the same path; that is an
   inference, not something this population can show.
2. **Allergy status never reaches the brief (A6).** `AllergyIntolerance` is
   parsed without `clinicalStatus` (the model is given only `display`,
   `recorded_date`, `source_id`). A resolved allergy therefore renders as a
   current one, and "none active" cannot occur for allergies. No live record
   is resolved today, so this is latent.
3. **Logged Metric 2 cannot detect the defect it exists for (C5).** With the
   renderer mutated to hide 4 failed claims, this suite's text check found
   the drop, but `compute_metrics.py` still reported
   `100.0% (4 disclosed of 4 failed claims)`. `disclosed_as_unverified` is
   set to `True` by the verifier (`checker.py` `_failed`) before rendering,
   and nothing after rendering records what was shown. The render step does
   disclose failures correctly today (B2, C1–C4), so the gap is in
   monitoring: a future render regression would not move the metric.

Passes worth stating because they were not guaranteed:

- D1 turns F-07's out-of-scope 401s, which findings.md still records as
  unreproduced, into a repeatable check (5 of 5 refused). It also confirms
  F-08's body difference: `"Unauthorized"` for out of scope, `"The resource
  owner or authorization server denied the request."` for a bad token.
- D3: a token missing only allergy scope gets a 401 and never
  "Allergies: not recorded", and the model is not called.
- D6: a real token 129 s past its `exp` gets a 401 with no brief.
- F1: the 522-encounter patient's brief cited all 40 active records (22
  problems, 18 medications). Nothing was truncated, and it took 28.8 s, of
  which the model call was 28.1 s.
- E1–E3 confirm OpenEMR still answers unknown ids with an empty 200, so the
  `5ba6c61` guard is still what prevents a brief for nobody.

Observed during an earlier run the same evening, not a designed case: at
about 22:05Z the Anthropic account's credit balance ran out mid-run
(`400 Your credit balance is too low`). The deployed agent failed closed. It
answered every brief with `502 brief_generation_failed` in under a second and
returned no partial or empty brief. But `GET /ready` kept reporting
`llm_provider: healthy` throughout, because its probe (`models.retrieve` in
`app/health.py`) uses no tokens, so readiness cannot see a billing outage.
That run also scored the outage as FAIL on G1, G2 and E3. That is why the
suite now reports upstream failures as ERROR (inconclusive), and why E3's
control no longer calls the model.

## Limits of this suite

- **Transport security is not tested.** TLS verification is off on both hops
  (F-36; deployment.md §1), as in the API collection.
- **The deployed commit is not observable from outside.** Live cases record
  the API version only. In-process cases test this checkout, which may differ
  from what runs on the droplet (it did for the recorded run; see above).
- **One model run per patient.** A pass shows the model behaved on that run,
  not that it always will. F1's full citation of 40 records is a sample, not
  a bound.
- **Population-specific.** Strata are picked from what the clinician can see
  (F-43), and cases SKIP when a stratum is absent.
- **Out of scope because it is not built:** Change Detection, Follow-Up
  Drilling and multi-turn state (so G tests single-turn request isolation,
  not ARCH §4's invalidation on patient switch), and the agent-side scope
  record behind Metric 3 (ARCH §2). D1 measures OpenEMR's side of Metric 3
  directly.
- **RESULTS.md names synthetic patients** (F-41) and a few of their record
  names. Do not commit results from a run against real PHI.
