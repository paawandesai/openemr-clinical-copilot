# Week 2 demo script (3–5 minutes)

A shot list for recording the Week 2 demo. Each step gives what to click or
run, what to say, and what the viewer should see. It is built only from what
exists at the snapshot below. Where a step has not been measured on the
deployed host, the step says so.

**Snapshot: `main@bc5236f`**, which is on `origin`. A claim marked PENDING
rests on a branch not yet merged into `main`: **[PENDING `feat/w2-eval-gate`]** or **[PENDING `feat/w2-answer-scope`]**.

- **The host runs `main@21f1053`**, deployed 2026-09-23. It was measured
  after the deploy through the real chart card, one document at a time
  (`docs/w2/DEPLOY.md` §8.0). The snapshot adds only that section on top of
  it.
- **Still to merge:** `feat/w2-eval-gate` and `feat/w2-answer-scope`, both on
  `origin`. Every citation is then repinned to the one resulting `main`
  commit, and the markers come out.
- **Host figures** come from `DEPLOY.md` §8.0. Anything measured before the
  deploy is labelled "rehearsal host, before the Week 2 deploy".

## What the demo shows, and where

| Scene | Where it runs | Measured on the deployed host? |
|---|---|---|
| Upload a lab report, watch it extract, ask a document + guideline question | **the real chart on the host**, Documents & Sources card | **yes**: 3 of 3 answered, 7 document + 2 guideline citations, 0 unverified, 15.3–21.2 s (`DEPLOY.md` §8.0) |
| Click a document chip, see the box on the page | the same chart | the preview by a citation's digest is verified on the host (§8.0). The chip → box draw is verified on the **local** stack (`docs/w2/evidence/php-wiring-e2e-run.md`, run C) |
| A critical potassium leads the answer | the same chart | **no**: gate case W2-H6 **[PENDING `feat/w2-eval-gate`]** and D40 item 9 say it should. Rehearse it |
| A heavy scan refuses to state a value it cannot place | the chart, on the scan's own patient | **no**: the scan's extraction was measured on the host (§8.0), the question was not. Rehearse it |
| The eval gate blocks a planted regression | a terminal, in a `feat/w2-eval-gate` worktree **[PENDING `feat/w2-eval-gate`]** | n/a: recorded fixtures (D6) |
| One encounter is one trace | the committed Langfuse screenshot (**local** Langfuse) | no: the host's Langfuse state after the deploy is not recorded |

**Fallbacks** exist for the two unmeasured chart scenes (step 4 below):
the in-process `w2_e2e_ask.py`, and the fixture page. Use them if a
rehearsal on the host does not show what this script says.

## Before you press record (about 30 minutes, about $1 in model calls, estimated)

### 1. The chart, the files and the patients

- **OpenEMR:** `https://64.177.42.210:9300`. The certificate is self-signed,
  so accept the warning once. Log in as `physician` / `physician`.
- **Files to upload**, from a clone of the repository:

| File | Its patient (synthetic, Synthea) | Why this one |
|---|---|---|
| `agent/evals/w2/factory/output/samples/lab-clean-001/lab-clean-001.pdf` | **Merlyn225 Sanda877 Mueller846**, DOB 1960-09-25; FHIR `a2c373ec-57bf-41cb-8767-94410dcf663d`; pid 38 on the host (`DEPLOY.md` §7) | the document §8.0 measured |
| `agent/evals/w2/staging/samples/lab-critical-potassium-007/lab-critical-potassium-007.pdf` | the same patient | potassium 6.8 mmol/L |
| `agent/evals/w2/factory/output/samples/lab-scanned-heavy-abnormal-002/lab-scanned-heavy-abnormal-002.pdf` | **Jae222 Dach178**, DOB 1959-12-28; FHIR `a2c373d1-c8c6-47f3-b566-e4ceaacdb1c7`; find the pid with Finder | the heavy scan |

The patients come from each sample's `*.ground_truth.json`
(`chart_source.patient_uuid`) and the names from
`agent/evals/w2/factory/cache/`.

**Upload each file to its own patient's chart.** Two things go wrong
otherwise:

- A document whose printed name or date of birth does not match the chart
  is refused as a wrong-patient document (`document_identity_mismatch`,
  D40).
- The same bytes on a second chart are refused 409 (`DEPLOY.md` §8.0).

### 2. Rehearse the chart scenes once on the host

Estimated at about $1 in total:

- about $0.13 per lab upload (vision, measured);
- about $0.18 per document + guideline question (measured, §8.0).

Two of the chart scenes have never run on the host (the table above), so
the rehearsal is their test. What a rehearsal can change:

- **Record what you actually see.** If a scene differs from this script,
  film what the host does and fix this script afterwards.
- **Re-uploads are idempotent.** Uploading the same file to the same chart
  again returns the same document, already extracted. To film the upload
  and extraction live after a rehearsal, use a chart that has not seen that
  file yet, or accept that the status reads `complete` at once.

### 3. The eval-gate worktree

```bash
git fetch origin && git worktree add ../w2-eval-gate origin/feat/w2-eval-gate
cd ../w2-eval-gate/agent && python -m pip install -r requirements.txt -r requirements-dev.txt
python -m evals.w2.gate          # once, to warm the judge cache: 24 calls, ~$0.03
```

The judge cost is measured in the gate README **[PENDING `feat/w2-eval-gate`]**
("What a run costs"). With `ANTHROPIC_API_KEY` set and the cache warm, the
recorded runs make 0 judge calls. Tesseract is required
(`brew install tesseract` or `apt-get install tesseract-ocr`).

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

**The fixture page**, for the box drawn over a scan's ink:

```bash
cd agent && python scripts/build_ui_fixtures.py && cd ..
node interface/modules/custom_modules/oe-module-clinical-copilot/dev/serve.mjs
```

Then open `http://127.0.0.1:8760/dev/fixture.html?scenario=lab-multipage-003`.

### 5. The trace view

Use the committed screenshots on `main`, under
`docs/w2/evidence/screenshots/`:

| File | What it shows |
|---|---|
| `langfuse-encounter-trace.png` | a **real** encounter on the real routes, **local** stack |
| `langfuse-dashboard.png` | a fresh instance filled by the **traffic generator**, not real use |

Or bring up a local Langfuse with `docs/w2/OBSERVABILITY.md` §6.

---

## The script

### 0:00–0:20 — What Week 2 is

**Show:** the patient dashboard for Merlyn225 Mueller846, with both
co-pilot cards visible: "Clinical Co-Pilot: Pre-Visit Brief" and "Clinical
Co-Pilot: Documents & Sources".

**Say:** "Week 1's co-pilot read a chart and verified every claim against
it. Week 2 reads uploaded lab reports and intake forms, and answers with
guideline evidence beside chart facts. The rule stays the same: the model
drafts, and deterministic code decides what is true. Nothing is stated that
code can't put back on the page."

### 0:20–1:20 — Upload, extraction, and an answer citing a document and a guideline

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
| Answer | arrives in about **15–21 s** | 15.3, 20.9, 21.2 s on the host (§8.0) |
| Answer header | a patient bar naming her | the local run C (`docs/w2/evidence/php-wiring-e2e-run.md`) |
| Sections | chart, document, guideline, unsupported and flags, each in its own section | same |
| Chips | **7 document** and **2 guideline**, and **no UNVERIFIED line** | host, 3 of 3 (§8.0); locally the chart chips varied between 0 and 4 with the draft |

**Say:**

- "Extraction ran when I uploaded, not when I asked, so the question doesn't
  wait on it."
- "Two kinds of citation, kept apart. The glucose value cites the PDF, down
  to a page and a box. The recommendation cites a verbatim USPSTF passage.
  The verifier rebuilt both from the source; neither was copied from the
  model."
- "The answer also says the guideline hasn't been checked against this
  patient. That's a limit we state rather than hide."

That last point is the `guideline_applicability_not_verified` limitation
(D40 item 7).

### 1:20–1:50 — Click a chip, see the box

**Do:** click the document chip on the **glucose value**. Then click a
guideline chip.

**Viewer should see:**

- The uploaded PDF opens in the card, and one box is drawn over the "89".
  - On the local stack, the chip for
    `results.comprehensive_metabolic_panel.glucose.value` drew one box over
    the value's ink (`docs/w2/evidence/php-wiring-e2e-run.md`, run C).
  - On the host, the preview a citation opens is verified: 200
    `application/pdf`, bytes identical to the upload (§8.0).
- The guideline chip opens its excerpt and publisher, because it has no page
  to point at.

**Say:** "The box comes from the locate step, never from the model. If
PDF.js's own page transform ever disagrees with ours, the viewer draws no
boxes at all rather than plausible wrong ones."

Sources: D27, D33, D51. The chip opens the stored PDF by the citation's
digest, on the open chart only.

### 1:50–2:30 — A critical potassium leads the answer (rehearse first)

**Do:** on the same chart, upload `lab-critical-potassium-007.pdf`, then ask
the same glucose-and-screening question.

**Viewer should see:** the answer **opens** with the potassium of 6.8 mmol/L
as a critical value, before anything about glucose.

- **Not measured on the host.** This scene rests on gate case W2-H6
  **[PENDING `feat/w2-eval-gate`]**, which asserts this sample surfaces the
  6.8 as a critical value with its reading, and on D40 item 9.
- If the rehearsal differs, use the in-process fallback and show
  `/tmp/demo-critical.txt`. Its `critical values:` line should show a stated
  document potassium.

**Say:** "I asked about glucose. The first thing it tells me is a potassium
of 6.8. Critical values come from a committed table, matched on LOINC code
and exact unit: not the printed flag, not the model. They lead every answer
that has them in hand, and the lab-result cap can never drop one."

Also say on camera: "These thresholds are a demo list, not reviewed for any
site."

### 2:30–3:10 — The heavy scan refuses to state what it cannot place (rehearse first)

**Do:**

1. Open Jae222 Dach178's chart.
2. Upload `lab-scanned-heavy-abnormal-002.pdf`. On the host, extraction of
   this file took 42.2 s from upload to complete (§8.0).
3. Ask: **"What was the potassium on the uploaded lab report?"**

**Viewer should see:**

- no document citation for potassium;
- potassium listed in the unsupported section;
- the answer never states the page's 3.1.

The chart may hold potassium results of its own. If so, a chart citation may
state the chart's value. That is a different source, verified separately;
point it out.

**Measured, and not measured:**

- **Measured offline, with the recorded transcription.** Every potassium
  field on this scan comes back `unlocated`, and 36 of 126 fields are
  located (`docs/w2/evidence/extractor-measurement.txt`, part 2).
- **Not measured on the host.** The host makes a **new** vision call, so
  which fields locate there can differ. Rehearse it. If potassium does
  locate, pick a field the rehearsal shows as unsupported instead, or use
  the in-process fallback, which replays the recorded transcription.

**Say:** "This is a degraded scan. The model read the potassium, but code
couldn't find that exact number on the page, so it isn't stated. It's listed
as unsupported. Numbers, units, dates and flags are never near-matched: 3.1
and 8.1 are one character apart. On this scan that costs us two thirds of
the values. That's the price of never showing a number with a box behind it
that isn't on the page."

Sources: D24; `docs/w2/evidence/extractor-measurement.txt`, 33.6% (36/107)
located.

### 3:10–4:10 — The eval gate blocks a planted regression

Every figure in this scene is **[PENDING `feat/w2-eval-gate`]**, from its
`docs/w2/EVAL_GATE_EVIDENCE.md` at `origin` tip `6d40ac5`.

**Do:** in the `../w2-eval-gate/agent` terminal:

```bash
python -m evals.w2.gate; echo "exit=$?"
```

**Viewer should see:** the category table, then
`VERDICT: PASS | specs 96 | active 79 | pending 15 | det 73/73 | judge 8/8`
and `exit=0`.

**Say:** "Ninety-six cases. Seventy-three deterministic checks at a hundred
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
- `VERDICT: BLOCKED | specs 96 | active 79 | pending 15 | det 72/73 | judge 8/8`;
- `exit=1`.

That is regression 1 in the evidence file.

**Say:** "One line. The critical-case rule blocks it on its own. On a
seven-case category one case is fourteen points, and a five-percent band is
below the resolution of a single case. That's fourteen out of fourteen
planted regressions caught. Our judge is uncalibrated, and the gate prints
that on every run."

**Do:** restore the file with `git checkout -- app/models/w2/lab_values.py`.

**Fallback:** show `docs/w2/EVAL_GATE_EVIDENCE.md` in that worktree: the
control block, then the "Planted regressions" table, 14 rows, all BLOCKED.

### 4:10–4:40 — One encounter, one trace

**Do:** open `langfuse-encounter-trace.png`, then `langfuse-dashboard.png`.

**Viewer should see** (checked against the committed screenshot):

- trace `a88a5c1242ae3ae81855d07a117f62b1`, whose header reads Latency
  2m 52s, $0.821785, and 67,967 prompt → 19,278 completion (Σ 87,245);
- a list of `step_event`, `tool_call_event` and `http_request_event` rows
  for the upload, the extraction polls and the rest of the encounter;
- the selected observation showing Input `null` and Output `undefined`: no
  chart content leaves in telemetry;
- then the dashboard, "Clinical Co-pilot: Week 2 operations".

**The dashboard's numbers come from generated traffic.** Show the layout;
do not quote its numbers.

**Say:** "Every event for this encounter shares one correlation id: upload,
extraction, retrieval, the answer. This is also how we found our slowest
path: a question about a whole report makes about ninety claims, at about a
second each. Events carry counts, ids and class names, never values, and
the event schema enforces that. Production metrics come from stdout,
because no BAA covers a hosted tracing service."

Sources: D36, D38; `docs/w2/OBSERVABILITY.md` §2 (encounter `07c0fd35-…`,
**local** stack); Week 1 `agent/docs/observability.md` §1.

### 4:40–5:00 — Close, honestly

**Say:** "What's not done:

- A question about a whole report is too slow, at about ninety seconds,
  because it cites every value. The fix is measured but not merged.
- The deployed flow has been measured one document at a time, never under
  load.
- The prompt-injection cases prove containment, not refusal.
- The judge is uncalibrated.

Each of those is in the architecture doc's limitations section. Every number
we showed traces to a file."

---

## Asset index

| Asset | Where | Branch |
|---|---|---|
| The deployed chart | `https://64.177.42.210:9300`, `physician`; card "Clinical Co-Pilot: Documents & Sources" | host runs `main@21f1053` |
| Post-deploy measurements | `docs/w2/DEPLOY.md` §8.0 | `main` |
| Local chart run, including the chip → box | `docs/w2/evidence/php-wiring-e2e-run.md` | `main` |
| Samples | `agent/evals/w2/factory/output/samples/{lab-clean-001,lab-scanned-heavy-abnormal-002}/` | `main` |
| Critical sample | `agent/evals/w2/staging/samples/lab-critical-potassium-007/` | `main`; moved to the gated `agent/evals/w2/fixtures/scenarios/` on `feat/w2-eval-gate` **[PENDING `feat/w2-eval-gate`]** |
| Recorded vision | `agent/evals/w2/staging/transcriptions/` | `main` |
| In-process ask path | `agent/scripts/w2_e2e_ask.py` | `main` |
| Fixture UI | `interface/modules/custom_modules/oe-module-clinical-copilot/dev/` | `main` |
| Eval gate and its evidence | `agent/evals/w2/gate/`, `.gitlab-ci.yml`, `docs/w2/EVAL_GATE_EVIDENCE.md` | `feat/w2-eval-gate` **[PENDING `feat/w2-eval-gate`]** |
| Langfuse screenshots | `docs/w2/evidence/screenshots/*.png` | `main` |
| Offline evidence (measurement, heavy-scan replay, routing check) | `docs/w2/evidence/extractor-measurement.txt`, `docs/w2/evidence/demo_checks.py` | this branch |
