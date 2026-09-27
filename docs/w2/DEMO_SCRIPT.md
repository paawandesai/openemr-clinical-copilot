# Week 2 demo script (3–5 minutes)

A shot list for recording the Week 2 demo. Each step gives what to click or
run, what to say, and what the viewer should see. It is built only from what
exists at the snapshot below. Where a step has not been measured on the
deployed host, the step says so.

**Snapshot: `main@54266a9`**, which is on `origin`. Every path, number and
decision below is on that commit. Numbers measured elsewhere name the build
they were measured on.

**The host runs agent `main@d61f739` and module `main@a434ffd`**, plus the
patient dashboard on port 8444 (`docs/w2/DEPLOY.md`, "What the host runs
now"). Between them and the snapshot, no deployed code differs: the agent's
code (`agent/app/`) is unchanged since `d61f739` and the module since
`a434ffd`, apart from code comments this branch renumbered to D62–D64.

**Host figures name the build they were measured on:**

| Build | Deployed | What it added | Measured in |
|---|---|---|---|
| `21f1053` | 2026-09-23 16:40 UTC | the first Week 2 deploy: card, routes, upload guard, D41 | `DEPLOY.md` §8.0 |
| `ea198c8` | 2026-09-23 17:12 UTC | D50, the scoped document answer; the eval gate | `DEPLOY.md` §8.00 |
| `06f0d45` | 2026-09-25 14:44 UTC | D54's upload scopes, D56, D58 (production Langfuse); then the patient dashboard on port 8444 | `docs/w2/evidence/prod-langfuse/deploy-2026-09-25/`, `docs/w2/evidence/dashboard-deploy-2026-09-25/` |
| **`d61f739`** (agent) | 2026-09-26 18:57 UTC | D60 (degraded documents, quarantine), drafting thinking off (D62), the duplicate-result guard (D63) | `LOAD_TEST.md` §8 |
| **`a434ffd`** (module only) | 2026-09-27 | the per-user token-fetch lock (D64); the agent was not restarted | `docs/w2/evidence/token-race-deploy-2026-09-27/` |

Figures labelled "rehearsal host, before the Week 2 deploy" predate all five.

## What the demo shows, and where

Recorded on Sunday 2026-09-27 against the host, which runs agent
`main@d61f739` and module `main@a434ffd` (D64, the token-fetch lock).

| Scene | Where it runs | Measured on the deployed host? |
|---|---|---|
| Upload a lab report, watch it extract, ask a document + guideline question | **the real chart on the host**, Documents & Sources card | **before D50 only**: 3 of 3 answered, 7 document + 2 guideline citations, 0 unverified, 15.3–21.2 s (`DEPLOY.md` §8.0, `21f1053`). Not re-measured since. Rehearse it |
| Click a document chip, see the box on the page | the same chart | the preview by a citation's digest is verified on the host (§8.0). The chip → box draw is verified on the **local** stack (`docs/w2/evidence/php-wiring-e2e-run.md`, run C) |
| Ask about the whole report: a scoped answer | the same chart | **yes, after D50**: 6.9–8.4 s, `partially_answered`, 49 document citations, 0 unverified (`DEPLOY.md` §8.00) |
| A critical potassium leads the answer | the same chart | **no**: gate case W2-H6 and D40 item 9 say it should. Rehearse it |
| **A degraded scan says so**, and states nothing it cannot place | the chart, on the scan's own patient | **not on the host**. D60 run 5, on the local stack with the image's Tesseract 5.5.0, located 11 of 107 and put the notice first (`docs/w2/evidence/degraded-docs-e2e-run.md`). Rehearse it |
| **A wrong-patient upload is quarantined** | a chart the document does not name | **not on the host**. D60 runs 4 and 7, local stack. Rehearse it |
| **The patient dashboard**, React over FHIR | <https://64.177.42.210:8444> | **yes**: every card for pid 38 and pid 31, 0 CSP violations, 2026-09-25 (`docs/w2/evidence/dashboard-deploy-2026-09-25/README.md`) |
| The eval gate blocks a planted regression | a terminal, in `agent/` on `main` | n/a: recorded fixtures (D6) |
| **One encounter is one trace, in production** | the **production** Langfuse, <https://64-177-51-119.sslip.io> | **yes**: the host exports to it since 2026-09-25; a real chart-card upload arrived as one trace with a `psk_` session (`docs/w2/evidence/prod-langfuse/deploy-2026-09-25/README.md`) |

**Fallbacks** exist for the unmeasured chart scenes (step 4 below): the
in-process `w2_e2e_ask.py`, the fixture page, and the committed evidence
files. Use them if a rehearsal on the host does not show what this script
says.

## Before you press record (about 45 minutes, about $1.50 in model calls, estimated)

### 1. The chart, the files and the patients

- **OpenEMR:** `https://64.177.42.210:9300`. The certificate is self-signed,
  so accept the warning once. Log in as `physician` / `physician`.
- **The dashboard:** `https://64.177.42.210:8444`. Accept its certificate
  once too; it signs in through the same OpenEMR login.
- **The production Langfuse:** `https://64-177-51-119.sslip.io`, with the
  read-only grader account. Its credentials are in the submission notes,
  never in the repository. Log in before recording.
- **Files to upload**, from a clone of the repository:

| File | Its patient (synthetic, Synthea) | Upload it to | Why this one |
|---|---|---|---|
| `agent/evals/w2/factory/output/samples/lab-clean-001/lab-clean-001.pdf` | **Merlyn225 Sanda877 Mueller846**, DOB 1960-09-25; FHIR `a2c373ec-57bf-41cb-8767-94410dcf663d`; pid 38 on the host (`DEPLOY.md` §7) | her own chart | the document §8.0 measured |
| `agent/evals/w2/staging/samples/lab-critical-potassium-007/lab-critical-potassium-007.pdf` | the same patient | her own chart | potassium 6.8 mmol/L |
| `agent/evals/w2/factory/output/samples/lab-scanned-heavy-abnormal-002/lab-scanned-heavy-abnormal-002.pdf` | **Jae222 Dach178**, DOB 1959-12-28; FHIR `a2c373d1-c8c6-47f3-b566-e4ceaacdb1c7`; pid 31 on the host (the dashboard evidence) | his own chart | the heavy scan |
| `agent/evals/w2/factory/output/samples/lab-multipage-003/lab-multipage-003.pdf` | **Tonja658 Grant908**, DOB 2014-01-24; FHIR `a2c37430-0a29-4c57-8195-2c924c2b654a` | **Merlyn Mueller's chart (pid 38), on purpose** | the wrong-patient scene |

The patients come from each sample's `*.ground_truth.json`
(`chart_source.patient_uuid`) and the names from
`agent/evals/w2/factory/cache/`.

**Upload the first three to their own patient's chart.** Two things go wrong
otherwise:

- A document whose printed name or date of birth does not match the chart
  is quarantined (the wrong-patient scene), not answered from.
- The same bytes, already bound to the chart they name, are refused 409 on
  a second chart (`DEPLOY.md` §8.0). Since D60 that binding is saved to
  disk and survives restarts for 30 days, so it applies to anything
  uploaded in a rehearsal too.

### 2. Rehearse the chart scenes once on the host

Estimated at about $1.50 in total:

- about $0.13 per lab upload (vision, measured), four of them;
- about $0.18 per document + guideline question (measured, §8.0);
- about $0.09 per whole-report question (§8.00).

Four of the chart scenes have never run on the host (the table above), so
the rehearsal is their test. What a rehearsal changes:

- **Record what you actually see.** If a scene differs from this script,
  film what the host does and fix this script afterwards.
- **Re-uploads are idempotent.** Uploading the same file to the same chart
  again returns the same document, already extracted. To film an upload and
  extraction live after a rehearsal, use a chart that has not seen that
  file yet, or accept that the status reads `complete` at once.
- **The wrong-patient file is used up by its rehearsal.** A quarantined
  document's binding is released (D60), so re-uploading `lab-multipage-003`
  to pid 38 quarantines again, but check that it was never uploaded to
  Tonja Grant's own chart: a matched binding would make it a 409 instead.
  The backup is `intake-missing-allergy-006.pdf`, Tonja Grant's intake form.
- **Clean up afterwards**: the quarantined copies stay in pid 38's
  "Quarantine - Patient Mismatch" category, and lab uploads write rows to
  Pending Review (D54). Remove both from the ledger as `DEPLOY.md` §7.4 does.

### 3. The eval gate

It is on `main`. For the recorded run to print `det 75/75` and exit 0, the
delegated PHP result must exist for the checked-out commit:

```bash
docker run --rm -v "$PWD":/app -w /app composer:2 composer install --ignore-platform-reqs --no-scripts   # once per clone
scripts/w2-writeback-result.sh      # after every commit or checkout; 272 tests
cd agent && python -m pip install -r requirements.txt -r requirements-dev.txt
export ANTHROPIC_API_KEY=...        # in this shell; the gate does not read agent/.env
python -m evals.w2.gate             # once, to warm the judge cache: 24 calls, ~$0.03
```

- Without the PHP result, W2-K4 and W2-K6 are unscored, the tier reads
  73/73 and the run exits 3 (INCOMPLETE). Do not film that by accident.
- The judge cost is measured in `agent/evals/w2/README.md` ("What a run
  costs"). With the cache warm, the recorded runs make 0 judge calls.
- Tesseract is required (`brew install tesseract` or
  `apt-get install tesseract-ocr`).

### 4. Fallbacks for the unmeasured scenes (optional)

**In process**, against real OpenEMR FHIR, with one live drafting call. It
needs `agent/.env` with:

- `ANTHROPIC_API_KEY`;
- `FHIR_CLIENT_ID` and `FHIR_CLIENT_SECRET`, for a password-grant client
  (`docs/w2/DEPLOY.md` §5.3);
- `FHIR_BASE_URL`, which defaults to the host.

```bash
cd agent
python scripts/w2_e2e_ask.py --sample lab-critical-potassium-007 --live-vision \
  --expect-critical --out /tmp/demo-critical.json | tee /tmp/demo-critical.txt
python scripts/w2_e2e_ask.py --sample lab-scanned-heavy-abnormal-002 \
  --question "What was the potassium on the uploaded lab report?" \
  --out /tmp/demo-heavy.json | tee /tmp/demo-heavy.txt
```

No output from `w2_e2e_ask.py` is committed. The heavy run exits 1 by
design: its acceptance line wants a document **and** a guideline citation,
and this run has neither.

**The committed runs**, for the D60 scenes:
`docs/w2/evidence/degraded-docs-e2e-run.md` (runs 4, 5 and 7).

**The fixture page**, for the box drawn over a scan's ink:

```bash
cd agent && python scripts/build_ui_fixtures.py && cd ..
node interface/modules/custom_modules/oe-module-clinical-copilot/dev/serve.mjs
```

Then open `http://127.0.0.1:8760/dev/fixture.html?scenario=lab-multipage-003`.

### 5. The trace

**Live, in production.** After the rehearsal, open the production Langfuse,
Tracing, and find the rehearsal's chart open: its trace id is the
correlation id the card minted, and its session is a `psk_` HMAC, never a
patient uuid (D56). The brief card's trace is a separate one. Keep that trace's tab open for the recording.

**Fallback:** the committed screenshots,
`docs/w2/evidence/prod-langfuse/deploy-2026-09-25/langfuse-encounter-trace.png`
(production) and `docs/w2/evidence/screenshots/langfuse-encounter-trace.png`
(the earlier local stack).

---

## The script

About five minutes. Where time is short, cut the chip scene first, then
the critical-potassium scene; both have committed evidence.

### 0:00–0:15 — What Week 2 is

**Show:** the patient dashboard for Merlyn225 Mueller846, with both
co-pilot cards visible: "Clinical Co-Pilot: Pre-Visit Brief" and "Clinical
Co-Pilot: Documents & Sources".

**Say:** "Week 1's co-pilot read a chart and verified every claim against
it. Week 2 reads uploaded lab reports and intake forms, and answers with
guideline evidence beside chart facts. The rule stays the same: the model
drafts, and deterministic code decides what is true."

### 0:15–1:00 — Upload, extraction, and an answer citing a document and a guideline

**Do:**

1. In the Documents & Sources card, choose a lab report and upload
   `lab-clean-001.pdf`.
2. Wait for the status to reach complete.
3. Ask: **"What was her glucose on the uploaded lab report, and what does
   the USPSTF recommend about screening for prediabetes and type 2
   diabetes?"**

**Viewer should see:**

| Step | What appears | Source |
|---|---|---|
| Upload | the status moves from pending to complete, in about half a minute | the vision step took 29.4 s for this document on the host (§8.0); upload to complete took 34–35 s locally |
| Answer | arrives in about **15–21 s**, measured before D50 | 15.3, 20.9, 21.2 s on `21f1053` (§8.0). Not re-measured since D50 |
| Sections | chart, document, guideline, unsupported and flags, each in its own section | the local run C (`docs/w2/evidence/php-wiring-e2e-run.md`) |
| Chips | **2 guideline** chips, **no UNVERIFIED line**, and the glucose row's document chips | before D50: 7 document + 2 guideline, 3 of 3 (§8.0). After D50, expect **more** document chips: code adds one cited statement counting the in-range rows it did not itemise (D50 item 3). The status should stay `answered`, because the question names a test (D50 item 7). Unmeasured: confirm it in the rehearsal |

**Say:**

- "Extraction ran when I uploaded, not when I asked."
- "Two kinds of citation, kept apart. The glucose value cites the PDF, down
  to a page and a box. The recommendation cites a verbatim USPSTF passage.
  The verifier rebuilt both from the source."
- "The answer also says the guideline hasn't been checked against this
  patient. That's a limit we state rather than hide."

That last point is the `guideline_applicability_not_verified` limitation
(D40 item 7). The lab upload also wrote its verified rows to the chart's
Pending Review (D54); the dashboard scene shows them.

### 1:00–1:15 — Click a chip, see the box

**Do:** click the document chip on the **glucose value**.

**Viewer should see:** the uploaded PDF opens in the card, and one box is
drawn over the "89". On the local stack this chip drew one box over the
value's ink (`php-wiring-e2e-run.md`, run C); on the host, the preview a
citation opens is 200 `application/pdf`, bytes identical to the upload
(§8.0).

**Say:** "The box comes from the locate step, never from the model. If
PDF.js's own page transform ever disagrees with ours, the viewer draws no
boxes at all."

Sources: D27, D33, D51.

### 1:15–1:35 — Ask about the whole report: a scoped answer (measured on the host)

**Do:** on the same chart, ask **"What were the results on the uploaded lab
report?"**

**Viewer should see** (`DEPLOY.md` §8.00): an answer in about **7–8 s**,
status `partially_answered` with 0 unverified, and a cited statement saying
how many in-range results were not listed individually.

**Say:** "Before this change the same question took ninety seconds: the model
wrote a citation for every value. Now code decides the scope. Anything
critical, abnormal or named is itemised; the rest is counted, in one
statement that cites each row and never calls them normal."

Sources: D50; `LOAD_TEST.md` §7; `DEPLOY.md` §8.00.

### 1:35–1:55 — A critical potassium leads the answer (rehearse first)

**Do:** on the same chart, upload `lab-critical-potassium-007.pdf`, then ask
the glucose-and-screening question again.

**Viewer should see:** the answer **opens** with the potassium of 6.8 mmol/L
as a critical value, before anything about glucose. **Not measured on the
host**: this rests on gate case W2-H6 and D40 item 9. If the rehearsal
differs, show `/tmp/demo-critical.txt` from the fallback.

**Say:** "I asked about glucose. The first thing it tells me is a potassium
of 6.8. Critical values come from a committed table matched on LOINC code
and exact unit, not the printed flag and not the model. These thresholds are
a demo list, not reviewed for any site."

### 1:55–2:25 — A degraded scan says so (rehearse first)

**Do:**

1. Open Jae222 Dach178's chart (pid 31).
2. Upload `lab-scanned-heavy-abnormal-002.pdf`. On the host, this file took
   42.2 s from upload to complete (§8.0).
3. Ask: **"What was the potassium on the uploaded lab report?"**

**Viewer should see:**

- **the degraded-scan notice as the answer's first line** (there is no
  critical value on this scan): "This scan is heavily degraded: N of M
  values read from it could be located on the page and verified; the other
  M−N are listed as unsupported and are not stated." With the host's OCR,
  expect about **11 of 107** (D60 run 5, the image's Tesseract 5.5.0);
- no document citation for potassium, and potassium in the unsupported
  section;
- the answer never states the page's 3.1.

The chart may hold potassium results of its own. A chart citation stating
the chart's value is a different source, verified separately; point it out.

**Measured, and not measured:** the notice and the 11 of 107 are from the
local stack running the deployed image's OCR (D60 run 5). The host makes a
**new** vision call, so the counts, and whether potassium locates, can
differ (§11 item 21 in `W2_ARCHITECTURE.md`). If potassium does locate, ask
about a field the rehearsal shows as unsupported.

**Say:** "This is a degraded scan, and the answer says so first, with the
counts. The model read the potassium, but code couldn't find that exact
number on the page, so it isn't stated. Numbers are never near-matched:
3.1 and 8.1 are one character apart. On this scan that costs us most of
the values. That's the price of never showing a number that isn't on the
page."

Sources: D24; D60 item 3; `docs/w2/evidence/degraded-docs-e2e-run.md`.

### 2:25–2:50 — A wrong-patient upload is quarantined (rehearse first)

**Do:**

1. Back on Merlyn Mueller's chart (pid 38), upload
   **`lab-multipage-003.pdf`**, Tonja658 Grant908's lab report.
2. Wait for the extraction to finish.
3. Open the chart's **Documents**.

**Viewer should see:**

- the card's status line: "Not used: the name or date of birth printed on
  this document does not match this chart. Nothing from it is stated or
  written to the chart." and "It was moved to the “Quarantine - Patient
  Mismatch” category of this chart's Documents, with a note; it was not
  deleted.";
- in Documents, the file under **Quarantine - Patient Mismatch**, with the
  module's note;
- the card never offers it for a question, and nothing reached Pending
  Review.

**Not measured on the host.** D60 runs 4 and 7 on the local stack: moved to
quarantine with a note, `deleted = 0`, 0 ledger rows, a question naming it
refused. If the page is closed before the extraction ends, the next card
load quarantines it (`reconcile.php`, run 7).

**Say:** "Uploaded to the wrong chart. The identity check read the name and
date of birth off the page, and they don't match. Nothing from it is stated,
nothing is written back, and it's quarantined in the chart, not deleted, so
the record of what was uploaded here stays."

Sources: D60 item 2; D35 item 10; D54 item 3.

### 2:50–3:15 — The patient dashboard, ported to React over FHIR

**Do:** open `https://64.177.42.210:8444`, sign in through OpenEMR as
`physician`, and open pid 38.

**Viewer should see:**

- the header with the patient's name, and the Allergies, Problems,
  Medications, Prescriptions, Care Team and Lab Results cards, all without
  errors (pid 38: 3 allergies, 13 problems, 4 medications, 11
  prescriptions, no care team recorded);
- in Lab Results, the rows the lab upload wrote back, marked **"From
  document"**.

**Say:** "This is OpenEMR's patient dashboard, rebuilt as a React app over
the same FHIR API the co-pilot uses. A small server on the same origin signs
the clinician in with OpenEMR's OAuth and holds the tokens, so the browser
never sees one. It can read only what the dashboard shows: anything else is
refused."

Sources: D59; `PATIENT_DASHBOARD_MIGRATION.md` §5 (the parity matrix) and
§8; `docs/w2/evidence/dashboard-deploy-2026-09-25/README.md`.

### 3:15–4:05 — The eval gate blocks a planted regression

Every figure in this scene is from `docs/w2/EVAL_GATE_EVIDENCE.md`, run
against the committed baseline.

**Do:** in `agent/`:

```bash
python -m evals.w2.gate; echo "exit=$?"
```

**Viewer should see:** the category table, then
`VERDICT: PASS | specs 98 | active 81 | pending 15 | det 75/75 | judge 8/8 (RUN [UNCALIBRATED])`
and `exit=0`.

**Say:** "Ninety-eight cases. Seventy-five deterministic checks at a hundred
percent, and eight judge meta-checks. Now I reintroduce a bug we actually
had: treating a blank flag column as 'normal'."

**Do:** open `agent/app/models/w2/lab_values.py` and find line 588:
`return ReportedFlag.NOT_REPORTED`, under `if not stripped:`. Change it to
`return ReportedFlag.NORMAL`, save, and re-run:

```bash
python -m evals.w2.gate; echo "exit=$?"
```

**Viewer should see:**

- `BLOCKED by rule (b) deterministic tier at 100%`;
- `BLOCKED by rule (c) critical-case override`, naming **W2-D2**;
- `VERDICT: BLOCKED | specs 98 | active 81 | pending 15 | det 74/75 | judge 8/8 (RUN [UNCALIBRATED])`;
- `exit=1`.

That is regression 1 in the evidence file.

**Say:** "One line, and the critical-case rule blocks it on its own. That's
twenty-two of twenty-two planted regressions caught. Our judge is
uncalibrated: its one independent measurement is eighty-two percent on
seventeen labels, too few to rely on, and the gate prints that on every
run."

**Do:** restore the file with `git checkout -- app/models/w2/lab_values.py`.

**Fallback, or a second shot:** open [MR !1](https://labs.gauntletai.com/paawandesai/openemr-clinical-copilot/-/merge_requests/1),
the same regression left open with a red pipeline (`EVAL_GATE.md`), or the
evidence file's "Planted regressions" table: 22 rows, all BLOCKED.

### 4:05–4:40 — One encounter, one trace, in production

**Do:** switch to the production Langfuse tab from the rehearsal (§5 above)
and open the chart open's trace.

**Viewer should see:**

- one trace for the Documents & Sources card's chart open: the upload and
  its status polls, the `intake_extractor` steps, the `lab_writeback` tool
  call and the ask turns, under one correlation id. The deploy evidence's
  trace was 162 spans;
- the Week 1 brief as a **second** trace (about 40 spans): that card mints
  its own correlation id, a known seam;
- the session shown as `psk_…`, an HMAC, and no patient uuid anywhere;
- in any observation, ids and counts only. Check one in the rehearsal and
  pick it for the shot.

**Say:** "This is our production telemetry: a Langfuse we host ourselves,
next to stdout. Everything the documents card did in this chart open shares
one correlation id.
Events carry counts, ids and class names, never values, and the patient
appears only as an HMAC, so the tracing store holds no patient identifier.
We measured that, rather than assuming it."

Sources: D36, D38, D56, D58; `docs/w2/OBSERVABILITY.md` §7;
`docs/w2/evidence/prod-langfuse/deploy-2026-09-25/README.md`.

**Fallback:** the production screenshot,
`docs/w2/evidence/prod-langfuse/deploy-2026-09-25/langfuse-encounter-trace.png`.

### 4:40–5:00 — Close, honestly

**Say:** "What's not done:

- Only the conversation route has been load-tested on the host: ten users,
  no errors, p95 under twenty seconds. Uploads and document questions ran
  there one at a time.
- A degraded scan locates far less with the deployed OCR build than on a
  laptop.
- The prompt-injection cases prove containment, not refusal.
- The judge is uncalibrated.

Each of those is in the architecture doc's limitations section. Every number
we showed traces to a file."

---

## Asset index

| Asset | Where | Branch |
|---|---|---|
| The deployed chart | `https://64.177.42.210:9300`, `physician`; cards "Clinical Co-Pilot: Pre-Visit Brief" and "…: Documents & Sources" | host runs agent `main@d61f739`, module `main@a434ffd` |
| The patient dashboard | `https://64.177.42.210:8444`; `docs/w2/evidence/dashboard-deploy-2026-09-25/` | host, since 2026-09-25 |
| The production Langfuse | `https://64-177-51-119.sslip.io`, read-only grader account (credentials in the submission notes); `docs/w2/evidence/prod-langfuse/deploy-2026-09-25/` | host, since 2026-09-25 |
| Post-deploy measurements | `docs/w2/DEPLOY.md` §8.0 (`21f1053`); §8.00 (`ea198c8`, D50); `docs/w2/LOAD_TEST.md` §8 (`d61f739`) | `main` |
| Degraded-document and quarantine runs | `docs/w2/evidence/degraded-docs-e2e-run.md` | `main` |
| Local chart run, including the chip → box | `docs/w2/evidence/php-wiring-e2e-run.md` | `main` |
| Samples | `agent/evals/w2/factory/output/samples/{lab-clean-001,lab-multipage-003,lab-scanned-heavy-abnormal-002}/` | `main` |
| Critical sample | `agent/evals/w2/staging/samples/lab-critical-potassium-007/` | `main`, with the gate's copy in `agent/evals/w2/fixtures/scenarios/` |
| Recorded vision | `agent/evals/w2/staging/transcriptions/` | `main` |
| In-process ask path | `agent/scripts/w2_e2e_ask.py` | `main` |
| Fixture UI | `interface/modules/custom_modules/oe-module-clinical-copilot/dev/` | `main` |
| Eval gate and its evidence | `agent/evals/w2/gate/`, `.gitlab-ci.yml`, `docs/w2/EVAL_GATE_EVIDENCE.md` | `main` |
| Langfuse screenshots | `docs/w2/evidence/prod-langfuse/deploy-2026-09-25/*.png` (production); `docs/w2/evidence/screenshots/*.png` (local) | `main` |
| Offline evidence (measurement, heavy-scan replay, routing check) | `docs/w2/evidence/extractor-measurement.txt`, `docs/w2/evidence/demo_checks.py` | `main` |
