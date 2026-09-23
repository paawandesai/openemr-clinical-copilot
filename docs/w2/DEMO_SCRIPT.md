# Week 2 demo script (3–5 minutes)

A shot list for recording the Week 2 demo. Each step gives what to run or
click, what to say, and what the viewer should see. It is built only from
what exists and works at the snapshot below.

**Snapshot: `main@f8b16c7`**, which is on `origin`. A claim marked PENDING
rests on a branch not yet merged into `main`: **[PENDING `feat/w2-php-wiring`]**, **[PENDING `chore/w2-deploy-rehearsal`]**, **[PENDING `feat/w2-eval-gate`]** or **[PENDING `feat/w2-answer-scope`]**.

- **Merge order:** the first three merge before submission in this order:
  `feat/w2-php-wiring`, then `chore/w2-deploy-rehearsal` (which deploys),
  then `feat/w2-eval-gate`.
- **Repin:** every citation is then repinned to the one resulting `main`
  commit, and the markers come out.
- **Answer-scope:** `feat/w2-answer-scope` fixes the slow document
  question. Its before/after numbers land before submission.
- **Live host:** figures from the live host are labelled "rehearsal host, before the Week 2 deploy". The
  deploy session re-measures after it deploys, and those numbers replace
  them.
- **[UNSOURCED: reported by the `feat/w2-php-wiring` session; not yet in a committed artifact]** marks a figure a session reported that no committed file holds yet.
Where a step has never been rehearsed end to end, the step says so.

## Read this before recording: what the demo cannot show

- **This script is written for the state before the merges.** Once
  `feat/w2-php-wiring` merges and `chore/w2-deploy-rehearsal` deploys, the
  upload, the answer and the chip → box steps can run in the real chart
  **[PENDING `feat/w2-php-wiring`]** **[PENDING `chore/w2-deploy-rehearsal`]**. The PHP session reports that flow working: 35.3 s to
  extraction, 17–19 s to answer **[UNSOURCED: reported by the `feat/w2-php-wiring` session; not yet in a committed artifact]**. Revise the shot list against the
  deployed host before recording.
- **There is no single live UI for Week 2 on `main` yet.** The live host (rehearsal host, before the Week 2 deploy) **[PENDING `chore/w2-deploy-rehearsal`]**
  (`64.177.42.210`) does not run the Week 2 ask path. And the documents card
  is not registered in the OpenEMR module on `main` (`W2_ARCHITECTURE.md`
  §8). So the demo uses three surfaces, and says so on camera:
  1. **A terminal** running the real ask path in process:
     `agent/scripts/w2_e2e_ask.py`. It calls real OpenEMR FHIR and makes one
     real drafting call.
  2. **The fixture-mode browser page** for the chip → bounding-box overlay.
     Its answers are built from the committed samples, not from the live run.
  3. **The eval gate** in the `feat/w2-eval-gate` **[PENDING `feat/w2-eval-gate`]** worktree.
- **Do not claim** the upload happened in the OpenEMR chart, or that the
  overlay is drawing the live answer. Say "same contract, fixture data" for
  the overlay.

## Before you press record (about 30 minutes, about $1.50 in model calls, estimated)

### 1. Credentials for the terminal steps

In `agent/.env`:

- `ANTHROPIC_API_KEY`
- `FHIR_CLIENT_ID` and `FHIR_CLIENT_SECRET`, for a **password-grant** OAuth
  client on the OpenEMR you point at.
- `FHIR_BASE_URL`. The default is the live host,
  `https://64.177.42.210:9300/apis/default/fhir`.

`FHIR_TEST_USERNAME` and `FHIR_TEST_PASSWORD` default to `physician` /
`physician`. The variable names are read by `agent/evals/harness.py`,
`EvalConfig.from_env`.

No password-grant client is published. To create one, follow
`docs/w2/DEPLOY.md` §5.3 **[PENDING `chore/w2-deploy-rehearsal`]** or
`docs/w2/LOCAL_ENV.md` §4. The sample patient,
`a2c373ec-57bf-41cb-8767-94410dcf663d`, exists on the live host (rehearsal host, before the Week 2 deploy) **[PENDING `chore/w2-deploy-rehearsal`]** (pid 38,
`DEPLOY.md` §7 **[PENDING `chore/w2-deploy-rehearsal`]**) and on a local restore of it (`LOCAL_ENV.md` §1).

### 2. Tesseract

`brew install tesseract`, or `apt-get install tesseract-ocr`. The scanned
samples use it.

### 3. Pre-run the slow terminal steps and save their output

A question about an uploaded report took **91.9 s and 130.5 s** on the real
routes (`docs/w2/LOAD_TEST.md` §6), and all
three runs below ask about the document. Expect up to about 2 minutes each.
Record the command, then **cut to the saved output**. Do not film the wait.

```bash
cd agent
python scripts/w2_e2e_ask.py --out /tmp/demo-clean.json | tee /tmp/demo-clean.txt
python scripts/w2_e2e_ask.py --sample lab-critical-potassium-007 --live-vision \
  --expect-critical --out /tmp/demo-critical.json | tee /tmp/demo-critical.txt
python scripts/w2_e2e_ask.py --sample lab-scanned-heavy-abnormal-002 \
  --question "What was her potassium on the uploaded lab report?" \
  --out /tmp/demo-heavy.json | tee /tmp/demo-heavy.txt
```

| Run | Model calls | Estimated cost | Expected exit |
|---|---|---|---|
| clean | 1 drafting call (vision replayed) | ~$0.46 | 0 |
| critical | 1 live vision call + 1 drafting call | ~$0.126 + ~$0.46 | 0 |
| heavy | 1 drafting call (vision replayed) | ≤ ~$0.46 | **1** |

The costs are estimates. $0.46 is the measured cost of one document question
on `lab-clean-001` (34,948 in / 11,523 out, `LOAD_TEST.md` §6), and $0.126 is
one measured vision call (`docs/w2/COST_AND_LATENCY.md` §1). The heavy
scan's answer should be cheaper, because fewer of its values are located.
None of these three runs has a committed cost. The heavy run's acceptance line requires both a
document and a guideline citation, and this run is meant to have neither, so
exit 1 is correct. The staged critical sample has no recording, which is why
it needs `--live-vision`.

> **None of these three runs has been recorded before.** No output from
> `w2_e2e_ask.py` is committed on any branch. If a run's output differs from
> what this script says the viewer should see, film what the run actually
> did, and fix this script afterwards.

### 4. Build the fixtures and start the fixture server

In a second terminal:

```bash
cd agent && python scripts/build_ui_fixtures.py && cd ..
node interface/modules/custom_modules/oe-module-clinical-copilot/dev/serve.mjs
# open http://127.0.0.1:8760/dev/fixture.html?scenario=lab-multipage-003
```

### 5. The eval-gate worktree

```bash
git worktree add ../w2-eval-gate origin/feat/w2-eval-gate   # skip if it already exists
cd ../w2-eval-gate/agent && python -m pip install -r requirements.txt -r requirements-dev.txt
python -m evals.w2.gate          # once, to warm the judge cache: 24 calls, ~$0.03
```

The judge cost is measured in the gate README **[PENDING `feat/w2-eval-gate`]** ("What a run costs"). With
`ANTHROPIC_API_KEY` set and the cache warm, the recorded runs make 0 judge
calls.

### 6. The trace view

Choose one before recording:

- **Live.** A local Langfuse, set up from nothing with the runbook in
  `docs/w2/OBSERVABILITY.md` §6.
- **Committed screenshots**, on `main` under `docs/w2/evidence/screenshots/`:

  | File | What it shows |
  |---|---|
  | `langfuse-encounter-trace.png` | a **real** encounter on the real routes |
  | `langfuse-dashboard.png` | a fresh instance filled by the **traffic generator**, not real use |
  | `dashboard-export.png` | the stdout dashboard over an earlier load demo |


---

## The script

### 0:00–0:20 — What Week 2 is

**Show:** `W2_ARCHITECTURE.md`, scrolled to the Summary.

**Say:** "Week 1's co-pilot read a chart and verified every claim against
it. Week 2 reads uploaded lab reports and intake forms, and answers with
guideline evidence alongside chart facts. The rule stays the same: the model
drafts, and deterministic code decides what is true. Nothing is stated that
code cannot put back on the page."

### 0:20–1:10 — Upload, extraction, and one answer that cites a document and a guideline

**Do:** in terminal 1, run or replay `python scripts/w2_e2e_ask.py`. The
default sample is `lab-clean-001`, and the default question is about her
glucose and the USPSTF's prediabetes screening recommendation.

**Viewer should see, in this order** (the order `w2_e2e_ask.py` prints):

| Printed line | What it shows |
|---|---|
| `upload: document_id=… status=pending` | D7: extraction starts at upload |
| `extraction: complete … located=…/…` | the located count |
| `routing:` | a `retrieve  evidence_needed_for_recommendation` line ("recommend" matches the recommendation rule first; checked offline with `classify()`), then an answer line with `evidence_in_hand` |
| `handoffs:` | the evidence-retriever with `concepts=`, `hits=` and `reranked=` |
| `citations:` | at least one `[document] … @ page 1 bbox=(…)` line and one `[guideline] …` line |
| `--- answer as rendered ---` | the answer text |
| last line | `ACCEPTANCE: document citations=N guideline citations=M … -> PASS` |

**Say, pointing at each part:**

- **routing:** "Routing is a rule table, not a model. It saw
  recommendation wording, sent the turn to retrieval, and logged why."
- **handoffs:** "The search ran on concepts from a fixed lexicon, never on my
  question's words. A patient's name can't reach a query."
- **citations:** "Two kinds of citation, kept apart. The glucose value cites
  the PDF, down to a page and a box. The recommendation cites a verbatim
  USPSTF passage. Both were rebuilt from the source by the verifier, not
  copied from the model."
- **the answer:** "And the answer says the guideline hasn't been checked
  against this patient. That's a limit we state rather than hide."

The last point is the `guideline_applicability_not_verified` limitation
(D40 item 7).

### 1:10–1:50 — Click a chip, see the box

**Do:** in the browser, open
`http://127.0.0.1:8760/dev/fixture.html?scenario=lab-multipage-003`. Click
the **document** chip on the Ferritin line (the claim reads "The report
gives Ferritin as …, flagged low"). Then click a **guideline** chip.

Why this sample and this line: fixture lab claims are built only for rows
the laboratory flagged (`build_ui_fixtures.py`, `_lab_claims`), and D20 means
a blank flag is never claimed as normal. `lab-clean-001` has no flagged row.
`lab-multipage-003` has one, ferritin 6 ng/mL flagged L, and it is on
**page 2** of its ground truth.

**Viewer should see:**

- The PDF, rendered by vendored PDF.js, scrolls to page 2, and boxes
  highlight the ferritin row's value, unit, range, flag and name.
- The guideline chip opens its excerpt and publisher instead, because it has
  no page to point at. **In fixture mode that excerpt is placeholder text,
  labelled "FIXTURE SOURCE - not clinical guidance"**
  (`build_ui_fixtures.py`, `FIXTURE_GUIDELINE`). The real guideline citation
  was the one in the terminal.
- Patient facts and guideline evidence sit under separate headings. The
  flag table never renders a blank-flag row as "normal", and shows the
  comparable count with its denominator (`dev/README.md`,
  "check-answer-view").

**Say:** "This is the same `AnswerView` contract the ask route returns, drawn
here from the committed sample. In production the box comes from the locate
step, never from the model. We check alignment against ground truth at four
zoom levels, and if PDF.js's own transform ever disagrees with ours, the
viewer draws no boxes at all rather than plausible wrong ones."

Sources: D27; D33; `dev/README.md`, "check-overlay-alignment". Do not say
"this box is the live answer's box".

### 1:50–2:30 — A critical potassium leads the answer

**Do:** show `/tmp/demo-critical.txt`, the `lab-critical-potassium-007` run.
The question was about glucose and screening, not potassium.

**Viewer should see:**

- `critical values: [('document', 'Potassium', 'high', True)]`. The exact
  tuple formatting is the script's.
- The rendered answer **opens** with the critical potassium, before anything
  about glucose.
- `ACCEPTANCE: … -> PASS`, because `--expect-critical` also requires a stated
  critical value.

**Say:** "I asked about glucose. The first thing it tells me is a potassium
of 6.8. Critical values come from a committed table, matched on LOINC code
and exact unit. Not the printed flag, not the model. They lead every answer
that has them in hand. The lab-result cap can never drop one either; that's
gate case G7."

Sources: D40 item 9; gate cases W2-H6 **[PENDING `feat/w2-eval-gate`]** and W2-G7 (`feat/w2-eval-gate`,
the commits that activated H6 and the supervisor batch). Add on camera: "these thresholds are a
demo list, not reviewed for any site."

### 2:30–3:10 — The heavy scan refuses to state what it cannot verify

**Do:** first, in the browser, switch the scenario picker to
`lab-scanned-heavy-abnormal-002`, just to show the page. Then show
`/tmp/demo-heavy.txt`.

**Viewer should see:**

- A skewed, noisy scan in the browser. **The boxes on this page come from the
  factory's ground truth, not from the locate step**
  (`agent/scripts/build_ui_fixtures.py`). Fixture mode cannot show an
  `unlocated` field, so use the browser only to show why OCR struggles.
- In the terminal, the refusal:
  - the `citations:` block has **no `[document]` line for potassium**;
  - `unsupported fields disclosed:` is non-zero;
  - the rendered answer does not state the document's potassium, which is
    3.1 on this page.
- The chart may also hold potassium results; pid 38 has 170 lab results
  (`DEPLOY.md` §8 **[PENDING `chore/w2-deploy-rehearsal`]**). If it does, the answer may state the chart's value
  under a `[chart]` citation. That is correct: it is a different source,
  verified separately. If it happens, point it out.

This was checked offline with the recorded transcription (`docs/w2/evidence/extractor-measurement.txt`, part 2):
every potassium field on this scan (name, value, unit, range and flag) comes
back `unlocated`, and 36 of 126 fields are located. The answer text itself
has not been produced before, which is why the run is pre-recorded in step 3.

**Say:** "This is a degraded scan. The model read the potassium, but code
couldn't find that exact number on the page, so it isn't stated. It's listed
as unsupported, with a pointer to the page. Numbers, units, dates and flags
are never near-matched: 3.1 and 8.1 are one character apart. On this scan
that costs us two thirds of the values. That's the price of never showing a
number with a box behind it that isn't on the page."

Sources: D24; `docs/w2/evidence/extractor-measurement.txt`, 33.6%
(36/107).

### 3:10–4:10 — The eval gate blocks a planted regression

**Do:** in the `../w2-eval-gate/agent` terminal:

```bash
python -m evals.w2.gate; echo "exit=$?"
```

**Viewer should see:** the category table, then
`VERDICT: PASS | specs 96 | active 78 | pending 16 | det 72/72 | judge 8/8`
and `exit=0`.

**Say:** "Ninety-five cases. Seventy deterministic checks at a hundred
percent, and eight judge meta-checks. Now I reintroduce a bug we actually
had: treating a blank flag column as 'normal'."

**Do:** open `agent/app/models/w2/lab_values.py`, find line 588
(`return ReportedFlag.NOT_REPORTED`, under `if not stripped:`), change it to
`return ReportedFlag.NORMAL`, save, and re-run:

```bash
python -m evals.w2.gate; echo "exit=$?"
```

**Viewer should see:**

- `BLOCKED by rule (b) deterministic tier at 100%`
- `BLOCKED by rule (c) critical-case override`, naming **W2-D2**
- `VERDICT: BLOCKED … det 69/70`
- `exit=1`

Expected text: `EVAL_GATE_EVIDENCE.md`, regression 1.

**Say:** "One line. The critical-case rule blocks it on its own, because on a
seven-case category one case is fourteen points, and a five-percent band is
below the resolution of a single case. That's thirteen out of thirteen planted
regressions caught. Our judge is uncalibrated, and the gate prints that
every run."

**Do:** restore the file:

```bash
git checkout -- app/models/w2/lab_values.py
```

**Fallback if the gate cannot run live:** show
`docs/w2/EVAL_GATE_EVIDENCE.md` in that worktree: the control block, then
the "Planted regressions" table, 12 rows, all BLOCKED.

### 4:10–4:40 — One encounter, one trace

**Do:** open the Langfuse trace, or `langfuse-encounter-trace.png`. Then the
dashboard, or `langfuse-dashboard.png`.

**Viewer should see** (checked against the committed screenshot):

- Trace `a88a5c1242ae3ae81855d07a117f62b1`, whose header reads Latency
  2m 52s, $0.821785, and 67,967 prompt → 19,278 completion (Σ 87,245).
- A left-hand list of `step_event`, `tool_call_event` and
  `http_request_event` rows for the upload, the extraction polls and the
  rest of the encounter.
- The selected observation shows Input `null` and Output `undefined`: no
  chart content leaves in telemetry.
- Then the dashboard, "Clinical Co-pilot: Week 2 operations", with p95 and
  p50 latency by step, tool calls by tool, the dependency-failure rate,
  retrieval hits, the reranked-true rate and the extraction located rate.
- **The dashboard's numbers come from generated traffic**
  (`OBSERVABILITY.md`, evidence table). Show the layout; do not quote its
  numbers as measurements.

The trace is encounter `07c0fd35-…`, described in `OBSERVABILITY.md` §2.
One id carries the upload, 26 status polls, the background extraction, the
brief, a conversation turn, 3 routing decisions and 55 tool calls.

**Say:** "Every event for this encounter shares one correlation id: upload,
extraction, retrieval, the answer. This is also how we found our slowest
path: a question about the whole report takes a minute and a half, in one
model call. Events carry counts, ids and class names,
never values, and that's enforced by the event schema, not by care. This
dashboard runs locally; production metrics come from stdout, because no BAA
covers a hosted tracing service."

Sources: D36; D38; Week 1 `agent/docs/observability.md` §1. The screenshot is
the **local** Langfuse. The live host (rehearsal host, before the Week 2 deploy) **[PENDING `chore/w2-deploy-rehearsal`]** runs with Langfuse disabled.

### 4:40–5:00 — Close, honestly

**Say:** "What's not done: a question about a whole report is too slow, at
92 to 131 seconds, and the fix is still landing. The live server runs last week's ask path. And the four
prompt-injection cases only prove containment so far: whether a model
that reads an injected PDF refuses it is specified but not yet tested. Each of those
is written down in the architecture doc's limitations section. Everything we
showed is a number you can trace to a file."

---

## Asset index

| Asset | Where | Branch |
|---|---|---|
| Ask path, in process | `agent/scripts/w2_e2e_ask.py` | `main` |
| Samples | `agent/evals/w2/factory/output/samples/{lab-clean-001,lab-scanned-heavy-abnormal-002}/` | `main` |
| Critical sample | `agent/evals/w2/staging/samples/lab-critical-potassium-007/` | `main`; moved to gated `agent/evals/w2/fixtures/scenarios/` on `feat/w2-eval-gate` **[PENDING `feat/w2-eval-gate`]** |
| Recorded vision | `agent/evals/w2/staging/transcriptions/` | `main` |
| Fixture UI | `interface/modules/custom_modules/oe-module-clinical-copilot/dev/` (`serve.mjs`, `fixture.html`, `README.md`) | `main` |
| Fixture builder | `agent/scripts/build_ui_fixtures.py` | `main` |
| Eval gate | `agent/evals/w2/gate/`, `.gitlab-ci.yml` | `feat/w2-eval-gate` **[PENDING `feat/w2-eval-gate`]** (on `origin`) |
| Planted-regression evidence | `docs/w2/EVAL_GATE_EVIDENCE.md` **[PENDING `feat/w2-eval-gate`]** | `feat/w2-eval-gate` |
| Langfuse screenshots | `docs/w2/evidence/screenshots/*.png` | `main` |
| Dashboard and provisioning | `agent/scripts/w2_dashboard.py`, `agent/scripts/provision_langfuse_dashboard.py` | `main` |
| Offline evidence (measurement, heavy-scan replay, routing check) | `docs/w2/evidence/extractor-measurement.txt`, `docs/w2/evidence/demo_checks.py` | this branch |
