# Work order 32 — local end-to-end runs of degraded document handling (`feat/w2-degraded-docs`)

Raw output of every run this branch's session made through the module's real
routes, so no figure in D60 exists only in a session report. Nothing is edited
except long document ids, shortened to their first 16 characters where marked
`…`.

## What was run, and where

| | |
|---|---|
| Date | 2026-09-25 (UTC) |
| Stack | **LOCAL only.** OpenEMR `development-easy-openemr-1` (https://localhost:9300, this checkout mounted); agent `clinical-copilot-agent` rebuilt from this branch's working tree with the compose file's new `extraction_data` volume, `AGENT_ENV=production`, a locally generated `AGENT_EXTRACTION_STORE_KEY`, `FHIR_BASE_URL=https://host.docker.internal:9300/...` (checked in `/proc/1/environ`), `LANGFUSE_*` empty. Neither host was touched. |
| Path | HTTP client (httpx) → login as `physician` → `demographics.php?set_pid=` → the card's `data-*` attributes → `public/upload.php`, `upload-status.php`, `ask.php` → the module's broker-minted clinician token → agent. |
| Module SQL | `InstallModuleSQL(17)` (DEPLOY.md §4 path): created `categories` id 35 "Quarantine - Patient Mismatch", parent 1, lft 67 / rght 68, `patients|docs`; root rght 67 → 69; `categories_seq` 35. A second run changed nothing. |
| Model calls | 3 live vision extractions (runs 1, 4, 5), 3 live drafting calls (runs 1, 2, 5). Run 4's question was refused before the answer step. |
| Patients | pid 38 = FHIR `a2c373ec-…` (lab-clean-001's patient); pid 49 = `a2c37430-…`; pid 31 = `a2c373d1-…` (the heavy scan's patient). Synthea synthetic. |

## Run 1 — upload lab-clean-001 to its own chart, then ask

```
login 302 /interface/main/tabs/main.php?token_main=…
chart 200 sources card present: True
card pid 38 correlation_id a2c26659-77f7-42dd-abc0-8b3cecd8a478
upload 200 {"status": "ok", "document_id": "5414f88e0e4e92e0…", "kind": "lab_pdf", "extraction_status": "pending", "byte_length": 32032, "correlation_id": "a2c26659-77f7-42dd-abc0-8b3cecd8a478", "identity_check": null, "quarantine": null}
  poll 1..7: 200 in_progress failure=None identity=None quarantine=None
  poll 8: 200 complete failure=None identity=confirmed quarantine=None
upload to terminal 32.3s
```

```
ask 200 in 13.5s answered extraction_available
```

Agent log: 2 `llm_usage_event` lines (the vision call and the drafting call).

## Restart — a new agent container on the same volume

```
docker compose ... up -d --force-recreate --no-deps --wait agent
container 8149ef4e00e9 -> e7e75bc24fdd
{"documents": 1, "event": "extraction_store_opened", ...}
FHIR_BASE_URL=https://host.docker.internal:9300/apis/default/fhir
```

## Run 2 — the same document after the restart

```
status 200 {"status": "ok", "document_id": "5414f88e0e4e92e0…", "kind": "lab_pdf", "extraction_status": "complete", "extraction_id": "w2x-8e923de644b4cacc4bd0231210a12707", "failure_reason": null, "correlation_id": "e5b304cc-0092-4086-816d-d6e534fc6d28", "identity_check": "confirmed", "quarantine": null}
ask 200 in 12.8s answered extraction_available
llm calls since restart:
follow_up_drilling claude-opus-5 1127
{"event_type": "w2_document_restored", "event_data": {"document_id": "5414f88e0e4e92e0…", "status": "complete", "has_report": true}, ...}
```

One model call after the restart, the drafting call. No vision call: nothing
was extracted again. Comparing run 1's and run 2's `ask.php` payloads:

```
answer_status same
reason_code same
limitations same
critical_values same
unverified same
discrepancies same
citations same: True 53
message same: False
unsupported same: True 24
answer_view (minus correlation id) same: False
--- before restart
+++ after restart
@@ -4 +3,0 @@
-  - Lab result: LOINC 2345-7 (Glucose [Mass/volume] in Serum or Plasma) of 2026-08-30 [most recent of 13 results for this test (LOINC 2339-0, 2345-7)]
@@ -6,0 +6 @@
+  - Lab result: LOINC 2345-7 (Glucose [Mass/volume] in Serum or Plasma) of 2026-08-30 [most recent of 13 results for this test (LOINC 2339-0, 2345-7)]
answer_view.answer differs in answer
answer_view.answer differs in patient_facts
answer_view.answer differs in correlation_id
```

Every one of the 53 citations (value, field and bbox), the 24 unsupported
values, the status, the limitations and the critical values are identical.
The one difference is where one **chart** line appears: the live drafting call
listed its chart claims in a different order. The document lines are the
same lines. `tests/test_extraction_store.py` shows exact equality of the whole
response across a restart with a scripted drafter.

## Run 3 — the same bytes for another chart, after the restart

Through the card (the module refuses first, from the chart's own records):

```
card pid 49 correlation_id db44e270-adc4-45b6-892d-057dd8948719
upload 409 {"status": "error", "error": "document_attached_elsewhere", "message": "This file is already attached to a different chart, so it cannot be attached to this one.", "correlation_id": "db44e270-adc4-45b6-892d-057dd8948719"}
upload to terminal 0.1s
```

Directly to the agent (a local password-grant token for `physician`), so the
agent's own binding is what answers:

```
token granted: True
agent upload, same bytes, pid 49's chart: 409 This file is already attached to a different chart and cannot be attached here.
agent status, pid 38's document under pid 49: 404 No extraction is recorded for that document.
agent status, under its own chart: 200 complete confirmed
```

## Run 4 — another patient's report uploaded to pid 49

lab-clean-001 (pid 38's patient) plus 28 trailing bytes, so the digest is new
and the module's 409 does not stop it: SHA3-512 `01abdd9e7f51fd91…`.

```
card pid 49 correlation_id 719bddb4-39a2-416d-a1bd-37bbf0870346
upload 200 {"status": "ok", "document_id": "01abdd9e7f51fd91…", "kind": "lab_pdf", "extraction_status": "pending", "byte_length": 32060, "correlation_id": "719bddb4-39a2-416d-a1bd-37bbf0870346", "identity_check": null, "quarantine": null}
  poll 1..7: 200 in_progress failure=None identity=None quarantine=None
  poll 8: 200 complete failure=None identity=mismatch quarantine=quarantined
upload to terminal 31.7s
```

The chart afterwards (MariaDB, `openemr`):

```
id	pid	deleted	mimetype	category
2489	49	0	application/pdf	Quarantine - Patient Mismatch
category_links
1
foreign_id	owner	date	note
2489	6	2026-09-25 16:36:05	Quarantined by Clinical Co-Pilot: the name or date of birth printed on this document does not match this chart. Nothing from it was used or written to the chart. Kept, not deleted. Reference: 719bddb4-39a2-416d-a1bd-37bbf0870346
ledger_rows
0
date	patient_id	success	comment
2026-09-25 16:35:35	49	1	Clinical Co-Pilot document upload: pending, documents.id 2489 stored (document 01abdd9e7f51fd91, correlation_id 719bddb4-39a2-416d-a1bd-37bbf0870346)
2026-09-25 16:36:05	49	1	Clinical Co-Pilot document quarantined: identity mismatch (documents.id 2489, document 01abdd9e7f51fd91, correlation_id 719bddb4-39a2-416d-a1bd-37bbf0870346)
```

Agent: `lab_writeback_after_extraction` with `identity_name: mismatch`,
`identity_date_of_birth: mismatch`, decision `identity_not_confirmed`: no
write-back (D54 item 3, unchanged).

A second poll, to show the move is keyed and not repeated:

```
{'extraction_status': 'complete', 'identity_check': 'mismatch', 'quarantine': 'quarantined'}
notes
1
quarantine_audits
1
```

A question naming it (no model call; refused before the answer step):

```
ask 200 in 1.0s refused document_identity_mismatch
Answer (turn 1)

The name or date of birth printed on that document does not match this chart, so nothing from it is stated. Check that it was uploaded to the right patient.

Nothing about the patient is stated in this answer.
```

## Run 5 — the heavy scan on its own chart (pid 31)

```
card pid 31 correlation_id 8f1f6067-4644-4d56-b24b-23b436b625d5
upload 200 {"status": "ok", "document_id": "9cbb4b2c619c1c02…", "kind": "lab_pdf", "extraction_status": "pending", "byte_length": 152895, "correlation_id": "8f1f6067-4644-4d56-b24b-23b436b625d5", "identity_check": null, "quarantine": null}
  poll 11: 200 complete failure=None identity=confirmed quarantine=None
upload to terminal 43.2s
field_outcomes {'located': 11, 'unlocated': 96, 'missing': 19} has_text_layer False cost_usd 0.12619
ask 200 in 10.5s partially_answered extraction_available
limitations ['unsupported_document_values', 'degraded_document']
critical_values []
---- message ----
Answer (turn 1)

This scan is heavily degraded: 11 of 107 values read from it could be located on the page and verified; the other 96 are listed as unsupported and are not stated.

From this patient's record and uploaded documents:
  - Lab report, page 1: specimen: collected: 2026-09-09 06:05; reported: 2026-09-10 20:08; type: Whole blood, EDTA

115 value(s) on the uploaded document(s) are unsupported: not located on the page, missing, or illegible. They are listed, not stated.

Not in the sources read for this question:
  - The lab results considered for this turn do not include it.

Sources read for this question: the chart (Condition, MedicationRequest, AllergyIntolerance, Observation), 1 uploaded document(s), 0 guideline passage(s).
```

**11 of 107, not the 36 of 107 measured before** (the committed real-vision
transcription, and replay). Same bytes. This run's vision call and the
container's OCR both differ from the recorded run's; which one moved the
count was not investigated. Both are far below the 75% threshold.

## No patient identifier in the agent's log

The recreated container's whole log (202 lines, runs 2-5), searched for the
three patient uuids, the lab report's patient name parts and two test names:

```
a2c373ec-57bf-41cb-8767-94410dcf663d: 0
a2c37430-0a29-4c57-8195-2c924c2b654a: 0
a2c373d1-c8c6-47f3-b566-e4ceaacdb1c7: 0
Merlyn225: 0
Mueller846: 0
Glucose: 0
Potassium: 0
```

## Left on the local stack

The agent keeps running this branch's image with the `extraction_data` volume
(3 documents saved). pid 49 keeps `documents.id` 2489 in quarantine, with its
note and two audit rows. pid 31's heavy scan was filed in Lab Report. Its D54
write-back wrote nothing: `lab_writeback_after_extraction` reports
`results_total: 22, results_writable: 0, results_skipped_by_reason:
{value_not_located: 22}, decision: nothing_writable`, and the ledger holds 0
rows for its digest. Run 1's document is the one the earlier sessions had
already written (D54 A1); its write-back this time was not inspected.
