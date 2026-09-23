# Work order 17 — live end-to-end runs of the Week 2 card (`feat/w2-php-wiring`)

Raw output of every run this branch's session made through the module, so
no figure below exists only in a session report. Nothing here is edited
except the two console-message lists, marked `[list elided here: ...]` with what they held.

## What was run, and where

| | |
|---|---|
| Stack | **LOCAL**, not the Vultr host. OpenEMR `development-easy-openemr-1` (https://localhost:9300, this checkout mounted), agent `clinical-copilot-agent` rebuilt from this branch (commit `405b5ac`, before its rebase to `3561c9f`) behind Caddy with `chore/w2-deploy-rehearsal`'s `a78ba51` Caddyfile (main's does not route `/documents` or `/w2/ask`). |
| Path | Browser/HTTP client → `public/{upload,upload-status,ask,document}.php` → clinician token minted by the module's broker → agent `/documents`, `/documents/{id}/extraction`, `/w2/ask`. Real login as `physician`. |
| Model calls | Vision extraction **live** (not replayed) for every first extraction of a byte string on the agent process; one live drafting call per question (`claude-opus-5`, effort `medium`, per the agent's `llm_usage_event`). Retrieval local (D25). Agent had `AGENT_STUB_LLM_DRAFTING=false` for runs A-D (that flag affects `/pre-visit-brief` only). |
| Patient | pid 38, FHIR `a2c373ec-57bf-41cb-8767-94410dcf663d` — Synthea synthetic, the doc-factory sample's own patient. Checks in run E also use pid 49 (`a2c37430-0a29-4c57-8195-2c924c2b654a`, synthetic). |
| Document | `agent/evals/w2/factory/output/samples/lab-clean-001/lab-clean-001.pdf`, 32,032 bytes, 1 page, 22 result rows, text layer. SHA3-512 `5414f88e0e4e92e0…` (`documents.id` 2482 on pid 38 — written there by an earlier write-back on 2026-09-22 23:10:31 UTC, not by these runs). Run E used the same file plus 28 trailing bytes (`59e0623577a76900…`), to exercise a *new* file. |
| Question | **"What was her glucose on the uploaded lab report, and what does the USPSTF recommend about screening for prediabetes and type 2 diabetes?"** (the default question of `agent/scripts/w2_e2e_ask.py`) — the same text in runs A-D. |

## How many runs, and the per-step figures

Five runs, 2026-09-23 (UTC): A-D ask the question; E exercises the paths
A-D could not reach. Four questions were asked; three are timed.

| Run | Client | Upload → extraction complete | Ask (client wall clock) | Citations (document / chart / guideline) | Unverified |
|---|---|---|---|---|---|
| A 04:51 | HTTP (httpx) | **35.3 s** (live vision; 10 polls at 2 s) | **19.5 s** (agent `total_ms` 18,511) | **6 / 4 / 2** | **0** |
| B 06:41 | headless Chrome | duplicate of A: `complete` at once | not timed — see "Run B" | — | — |
| C 15:10 | headless Chrome | duplicate: `complete` at once | **17.7 s** | 6 / 0 / 2 | 0 |
| D 15:15 | headless Chrome | duplicate: `complete` at once | **17.2 s** | 6 / 0 / 2 | 0 |
| E 15:30 | HTTP (httpx) | **34 s** (new bytes, live vision; polled every 3 s) | no question | — | — |

So: upload to extraction complete **34-35 s** on this 1-page text-layer
report, and the question answered in **17.2-19.5 s** (3 timed runs). Chart
citations differ between A (4: one lab Observation's code, value, unit and
date) and C/D (0) — the same question, different drafts; the document and
guideline citations were the same six and two each time.

**Why 17-19 s and not the 90 s-plus measured on `feat/w2-answer-scope`.**
The two measure different questions, so they do not contradict. That
branch's `docs/w2/LOAD_TEST.md` §7 timed the **whole-report** question
"What were the results on the uploaded lab report?" on this same 22-row
report before its fix: answer step **88.2-91.8 s** (p50 89.0 s), 91 document
citations, ~11,500 output tokens, the model call 99% of the step at about a
second per citation (and 188.8-195.9 s on the 44-row report; its summary
table says "90-196 s"). The question here names **one analyte and one
guideline topic**: 6 document citations (glucose's name, value, unit and
range; the specimen's collection time and type), 0-4 chart and 2 guideline
— about 8-12 citations, which at that rate is consistent with 17-19 s
end to end (the client figure also includes the module's token mint and
the chart reads). This branch does **not** contain `feat/w2-answer-scope`'s
scoping change; the whole-report question was not asked here. (The figure
"92-131 s" does not appear on that branch; the numbers above are quoted
from its LOAD_TEST.md §7 and `docs/w2/evidence/load/answer-before-22row.json`.)

**Run B.** Chrome's `ask.php` request started 06:41:23 and its audit row
was written at 07:00:39; the Pre-Visit Brief request the same chart open
had started at 06:41:20 (Apache access log) logged its failure at 07:01:01.
Both completed together ~19 minutes late, which fits the host or Docker VM
being suspended, but the cause is not established. The harness had given
up after 120 s. Run B is excluded from the latency figures; runs C and D
repeated it without the delay.

## Run A — HTTP, the whole flow (raw)

```
login 302
chart 200 sources card present: True
card endpoints: {'document': 'document.php', 'upload': 'upload.php', 'status': 'upload-status.php', 'ask': 'ask.php'}
correlation_id (minted at render): 1fd84f94-a521-4866-9490-1cdb4ddabacc
upload 200 {'status': 'ok', 'extraction_status': 'pending', 'byte_length': 32032, 'error': None, 'correlation_id': '1fd84f94-a521-4866-9490-1cdb4ddabacc'}
document_id 5414f88e0e4e92e0...
  poll 1: 200 in_progress extraction_id=None failure=None
  poll 2: 200 in_progress extraction_id=None failure=None
  poll 3: 200 in_progress extraction_id=None failure=None
  poll 4: 200 in_progress extraction_id=None failure=None
  poll 5: 200 in_progress extraction_id=None failure=None
  poll 6: 200 in_progress extraction_id=None failure=None
  poll 7: 200 in_progress extraction_id=None failure=None
  poll 8: 200 in_progress extraction_id=None failure=None
  poll 9: 200 in_progress extraction_id=None failure=None
  poll 10: 200 complete extraction_id=w2x-8e923de644b4cacc4bd0231210a12707 failure=None
extraction complete after 35.3s
ask 200 in 19.5s {'status': 'ok', 'answer_status': 'answered', 'reason_code': 'evidence_in_hand', 'correlation_id': '1fd84f94-a521-4866-9490-1cdb4ddabacc', 'error': None}
patient bar: {'name': 'Merlyn225 Sanda877 Mueller846', 'date_of_birth': '1960-09-25'}
citations: document=6 chart=4 guideline=2; unverified=0; limitations=['guideline_applicability_not_verified', 'unsupported_document_values']
  doc: results.comprehensive_metabolic_panel.glucose.name p. 1 bbox {'page': 1, 'x0': 57, 'y0': 256.35498046875, 'x1': 88.54480743408203, 'y1': 268.17138671875} value 'Glucose'
  doc: results.comprehensive_metabolic_panel.glucose.value p. 1 bbox {'page': 1, 'x0': 270.4367980957031, 'y0': 256.35498046875, 'x1': 279.9999694824219, 'y1': 268.17138671875} value '89'
  doc: results.comprehensive_metabolic_panel.glucose.unit p. 1 bbox {'page': 1, 'x0': 326, 'y0': 256.35498046875, 'x1': 349.89935302734375, 'y1': 268.17138671875} value 'mg/dL'
  doc: results.comprehensive_metabolic_panel.glucose.reference_range p. 1 bbox {'page': 1, 'x0': 414, 'y0': 256.35498046875, 'x1': 440.7717590332031, 'y1': 268.17138671875} value '70 - 99'
  doc: order.collected p. 1 bbox {'page': 1, 'x0': 384, 'y0': 162.33998107910156, 'x1': 453.46710205078125, 'y1': 174.43118286132812} value '2026-08-30 07:12'
  doc: order.specimen p. 1 bbox {'page': 1, 'x0': 384, 'y0': 149.9400177001953, 'x1': 409.9159851074219, 'y1': 162.03121948242188} value 'Serum'
  guideline: uspstf-diabetes-screening uspstf-diabetes-screening#322b208292362b5c 'the USPSTF recommends screening for prediabetes and type 2 diabetes in adults aged 35 to
  guideline: uspstf-diabetes-screening uspstf-diabetes-screening#8f2bcbcb3aec3db1 'Prediabetes and type 2 diabetes can be detected by measuring fasting plasma glucose or Hb
session key leaked to browser: False
preview by digest 200 application/pdf 32032 bytes; identical to upload: True
```

Read back from the saved `ask.php` payload of run A:

```
http run: answer_status answered reason evidence_in_hand total_ms 18511 correlation 1fd84f94-a521-4866-9490-1cdb4ddabacc
patient_facts citations by type Counter({'document': 6, 'chart': 4}) guideline 2 unverified 0 unsupported 24 limitations ['guideline_applicability_not_verified', 'unsupported_document_values']
chart citation: Observation loinc_code a2cf7284-1d0...
chart citation: Observation value a2cf7284-1d0...
chart citation: Observation unit a2cf7284-1d0...
chart citation: Observation effective_date a2cf7284-1d0...
```

## Run C — headless Chrome, real login and dashboard, chip click (raw)

```
after login: /interface/main/tabs/main.php
co-pilot cards on the dashboard: [
  'clinical_copilot_brief_ps_expand',
  'clinical_copilot_sources_ps_expand'
]
upload control says: Extraction complete (Reference: d2e825b5-abf8-4209-bc5a-e29573dfd29b)
question box says: Documents included: 1
answer in 17.7s: {
 "bar": "Answer for Merlyn225 Sanda877 Mueller846 · DOB 1960-09-25",
 "outcome": "Answered Reference: d2e825b5-abf8-4209-bc5a-e29573dfd29b",
 "askStatus": "",
 "sections": [
  "ccw2-section--chart",
  "ccw2-section--document",
  "ccw2-section--guideline",
  "ccw2-section--unsupported",
  "ccw2-section--flags"
 ],
 "chips": {
  "chart": 0,
  "document": 6,
  "guideline": 2
 },
 "guidelineChipsUnderGuidelineHeading": true,
 "documentChipsUnderPatientFacts": true
}
clicked chip: documentp.1 · results.comprehensive_metabolic_panel.glucose.value
viewer:  canvas css [ 765, 990 ] px [ 765, 990 ] boxes drawn 1
active box(es): [
 {
  "field": "results.comprehensive_metabolic_panel.glucose.value",
  "active": true,
  "pt": {
   "x0": 270.43,
   "y0": 256.35,
   "x1": 279.99,
   "y1": 268.16
  },
  "inkInside": 0.289,
  "inkAbove": 0,
  "inkBelow": 0.278
 }
]
boxes with ink inside: 1/1; mean ink inside 0.289 vs above 0.000 / below 0.278
console messages during the run: 3 [list elided here: all three are OpenEMR core's demographics.php expecting its tabs frame - 'top.restoreSession is not a function' x2 and "Cannot read properties of undefined (reading 'setPatient')" - because the harness loads the dashboard top-level; none from the module]
```

## Run D — the same, after the card's contrast fix (raw)

```
after login: /interface/main/tabs/main.php
co-pilot cards on the dashboard: [
  'clinical_copilot_brief_ps_expand',
  'clinical_copilot_sources_ps_expand'
]
upload control says: Extraction complete (Reference: 96fbc8a3-f11d-4ac5-91c8-384b8da81511)
question box says: Documents included: 1
answer in 17.2s: {
 "bar": "Answer for Merlyn225 Sanda877 Mueller846 · DOB 1960-09-25",
 "outcome": "Answered Reference: 96fbc8a3-f11d-4ac5-91c8-384b8da81511",
 "askStatus": "",
 "sections": [
  "ccw2-section--chart",
  "ccw2-section--document",
  "ccw2-section--guideline",
  "ccw2-section--unsupported",
  "ccw2-section--flags"
 ],
 "chips": {
  "chart": 0,
  "document": 6,
  "guideline": 2
 },
 "guidelineChipsUnderGuidelineHeading": true,
 "documentChipsUnderPatientFacts": true
}
clicked chip: documentp.1 · results.comprehensive_metabolic_panel.glucose.value
viewer:  canvas css [ 765, 990 ] px [ 765, 990 ] boxes drawn 1
active box(es): [
 {
  "field": "results.comprehensive_metabolic_panel.glucose.value",
  "active": true,
  "pt": {
   "x0": 270.43,
   "y0": 256.35,
   "x1": 279.99,
   "y1": 268.16
  },
  "inkInside": 0.289,
  "inkAbove": 0,
  "inkBelow": 0.278
 }
]
boxes with ink inside: 1/1; mean ink inside 0.289 vs above 0.000 / below 0.278
console messages during the run: 3 [list elided here: all three are OpenEMR core's demographics.php expecting its tabs frame - 'top.restoreSession is not a function' x2 and "Cannot read properties of undefined (reading 'setPatient')" - because the harness loads the dashboard top-level; none from the module]
```

The active box is the glucose value's citation box in PDF points
(270.44, 256.35)-(280.00, 268.17) to within 0.01 pt, with 28.9% ink inside
and 0% in the blank leading above it.

## Run E — a new file, the 409 on another chart, the guards (raw)

```
variant digest 59e0623577a76900 ...
1. new file, chart 38                                          200 {'status': 'ok', 'extraction_status': 'pending'}
   extraction                                                  complete after 34s
2. same bytes again, chart 38                                  200 {'status': 'ok', 'extraction_status': 'complete'}
3. preview by digest, chart 38                                 200 {}
4. SAME bytes, chart 49 (D40 item 8)                           409 {'status': 'error', 'error': 'document_attached_elsewhere'}
5. status of chart 38's document, chart 49 open                404 {'status': 'error', 'error': 'extraction_not_found'}
6. preview of chart 38's document, chart 49 open               404 {'status': 'error', 'error': 'not_found'}
7. upload for chart 38 while 49 is open                        409 {'status': 'error', 'error': 'patient_context_changed'}
8. no CSRF token                                               400 {'status': 'error', 'error': 'invalid_request'}
9. correlation id that is not a UUID                           400 {'status': 'error', 'error': 'invalid_request'}
10. not a PDF                                                  400 {'status': 'error', 'error': 'upload_rejected'}
11. unknown document kind                                      400 {'status': 'error', 'error': 'upload_rejected'}
12. status poll as POST                                        400 {'status': 'error', 'error': 'invalid_request'}
13. ask with a malformed document id                           400 {'status': 'error', 'error': 'invalid_request'}
digest 59e0623577a76900006c1efdb1b1fa8902939042701c4241412efe45ebbdf51a4cc8bf29dd3edf3af29e523272f53c7d5e987c7a6b213b1b766b851619d4853c
```

Step 4 was refused by the module's own chart records, before the agent was
asked (ChartDocuments); the agent's own 409 is covered by
`agent/tests/test_w2_documents_api.py` and the module's mapping of it by
`tests/AgentClientW2Test.php`.

## Preview parity, integer vs digest `document_id` (raw)

```
chart 49 open: integer 404 application/json | digest 404 application/json | identical body: True | body: {"status":"error","error":"not_found","message":"That document is not available for the open chart."}
chart 38 open: integer 200 application/pdf | digest 200 application/pdf | identical body: True | body: 32032 PDF bytes
```

## OpenEMR audit rows for these runs (raw; `log.comments` decoded)

Identifiers, outcomes and correlation ids only — upload, question and
preview under one correlation id per chart open.

```
2026-09-23 04:51:13	38	1	Clinical Co-Pilot document upload: pending, documents.id 2482 already on chart (document 5414f88e0e4e92e0, correlation_id 1fd84f94-a521-4866-9490-1cdb4ddabacc)
2026-09-23 04:52:06	38	1	Clinical Co-Pilot sourced question: answered / evidence_in_hand (1 documents, correlation_id 1fd84f94-a521-4866-9490-1cdb4ddabacc)
2026-09-23 04:52:07	38	1	Clinical Co-Pilot source document preview: documents.id 2482
2026-09-23 06:41:23	38	1	Clinical Co-Pilot document upload: complete, documents.id 2482 already on chart (document 5414f88e0e4e92e0, correlation_id 9ce52cce-11cd-409a-90ee-b1e9f89a3b29)
2026-09-23 07:00:39	38	1	Clinical Co-Pilot sourced question: answered / evidence_in_hand (1 documents, correlation_id 9ce52cce-11cd-409a-90ee-b1e9f89a3b29)
2026-09-23 15:10:33	38	1	Clinical Co-Pilot document upload: complete, documents.id 2482 already on chart (document 5414f88e0e4e92e0, correlation_id d2e825b5-abf8-4209-bc5a-e29573dfd29b)
2026-09-23 15:10:51	38	1	Clinical Co-Pilot sourced question: answered / evidence_in_hand (1 documents, correlation_id d2e825b5-abf8-4209-bc5a-e29573dfd29b)
2026-09-23 15:10:51	38	1	Clinical Co-Pilot source document preview: documents.id 2482
2026-09-23 15:15:02	38	1	Clinical Co-Pilot document upload: complete, documents.id 2482 already on chart (document 5414f88e0e4e92e0, correlation_id 96fbc8a3-f11d-4ac5-91c8-384b8da81511)
2026-09-23 15:15:20	38	1	Clinical Co-Pilot sourced question: answered / evidence_in_hand (1 documents, correlation_id 96fbc8a3-f11d-4ac5-91c8-384b8da81511)
2026-09-23 15:15:20	38	1	Clinical Co-Pilot source document preview: documents.id 2482
2026-09-23 15:30:26	38	1	Clinical Co-Pilot document upload: pending, documents.id 2483 stored (document 59e0623577a76900, correlation_id d1ffae54-2e47-4c61-95fd-deae0da503bc)
2026-09-23 15:31:02	38	1	Clinical Co-Pilot document upload: complete, documents.id 2483 already on chart (document 59e0623577a76900, correlation_id d1ffae54-2e47-4c61-95fd-deae0da503bc)
2026-09-23 15:31:02	38	1	Clinical Co-Pilot source document preview: documents.id 2483
2026-09-23 15:31:03	49	0	Clinical Co-Pilot document upload: document_attached_elsewhere (document 59e0623577a76900, correlation_id 6994a901-8b32-482c-9b6f-751d62a73e18)
2026-09-23 16:05:23	38	1	Clinical Co-Pilot source document preview: documents.id 2482
2026-09-23 16:05:24	38	1	Clinical Co-Pilot source document preview: documents.id 2482
```

## Cleanup (raw)

Run E stored `documents.id` 2483; it was removed afterwards (file,
category link, uuid registry row, row). The runs wrote no ledger rows.

```
documents with the variant digest: 0
category links for 2483: 0
uuid_registry entry: 0
file on disk: 0
documents created since 03:00 UTC: 0
ledger rows since 03:00 UTC: 0
```
