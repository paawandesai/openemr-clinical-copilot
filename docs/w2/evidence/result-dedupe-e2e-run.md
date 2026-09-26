# Work order 34 — local end-to-end runs of duplicate-result handling (`fix/w2-result-dedupe`)

Raw output of every run this branch's session made through the module's real
routes, so no figure in the D-unassigned (duplicate results) entry exists only
in a session report. Nothing is edited except long digests and keys, shortened
to their first 16 characters where marked `…`, and one login token, redacted.

## What was run, and where

| | |
|---|---|
| Date | 2026-09-26 (UTC) |
| Stack | **LOCAL only.** OpenEMR `development-easy-openemr-1` (https://localhost:9300, this checkout mounted, so the module code is this branch's); agent `clinical-copilot-agent:local` rebuilt from this branch's working tree and recreated on a fresh extraction store, `AGENT_ENV=production`, `FHIR_BASE_URL=https://host.docker.internal:9300/...` (checked in `/proc/1/environ`). Neither host was touched. |
| Path, runs 1–2 | HTTP client (httpx) → login as `physician` → `demographics.php?set_pid=38` → the card's `data-*` attributes → `public/upload.php`, then `upload-status.php` every 3 s until `write_back` is set → the module's broker-minted clinician token → agent → `POST /api/clinical-copilot/lab-writeback`. |
| Path, run 3 | A local password-grant token for `physician` (the write-back test client) → `POST /api/clinical-copilot/lab-writeback` directly, with one hand-built result. No model call. |
| Patient | pid 38 = FHIR `a2c373ec-…`, lab-clean-001's patient. Synthea synthetic. |
| Model calls | 2 live vision extractions (runs 1 and 2). `AGENT_STUB_LLM_DRAFTING=true` touches only the brief. |
| Before the runs | The local ledger held 64 rows from earlier sessions' runs (pid 38's lab-clean-001 write of 2026-09-24 and pid 49's lab-multipage-003), with their `procedure_*` rows and two chart copies. They were removed first with the ledger-driven cleanup (DEPLOY.md §7.4, scope: every ledger row) and their two files deleted, so that run 1 could show `created` rather than the ledger's `duplicate_ignored`. The counts after that are the baseline below. |

## State 1 — baseline, after the pre-clean

```
ledger	0
procedure_order	8197
procedure_order_code	8197
procedure_report	761
procedure_result	14848
documents	4
categories_to_documents	4
uuid_registry	30474
pid38 results 2026-08-30	0
w2x results	0
```

## Run 1 — lab-clean-001 on its own chart

```
sha3-512 5414f88e0e4e92e0… bytes 32032
login 302 /interface/main/tabs/main.php?token_main=…
chart 200 card present: True pid 38
upload 200 {"status": "ok", "document_id": "5414f88e0e4e92e0…", "kind": "lab_pdf", "extraction_status": "pending", "byte_length": 32032, "correlation_id": "d822fb21-f224-4888-b810-721825473c5b", "identity_check": null, "quarantine": null, "write_back": null}
  poll 1: 200 in_progress identity=None write_back=null
  poll 2: 200 in_progress identity=None write_back=null
  poll 3: 200 in_progress identity=None write_back=null
  poll 4: 200 in_progress identity=None write_back=null
  poll 5: 200 in_progress identity=None write_back=null
  poll 6: 200 in_progress identity=None write_back=null
  poll 7: 200 in_progress identity=None write_back=null
  poll 8: 200 complete identity=confirmed write_back=written
terminal after 33.5s
status payload: {"status": "ok", "document_id": "5414f88e0e4e92e0…", "kind": "lab_pdf", "extraction_status": "complete", "extraction_id": "w2x-8e923de644b4cacc4bd0231210a12707", "failure_reason": null, "correlation_id": "d822fb21-f224-4888-b810-721825473c5b", "identity_check": "confirmed", "quarantine": null, "write_back": {"decision": "written", "results_total": 22, "skipped": 1, "created": 21, "duplicate_ignored": 0, "already_in_chart": 0, "conflict": 0, "conflicts": []}}
```

The card's status payload carries `write_back` as null on every poll until the
write has ended, then the summary: **21 created**, 1 skipped (the eGFR row,
`no_loinc_code`), 0 already in the chart, 0 conflicts.

## State 2 — after run 1

```
ledger	21
procedure_order	8198
procedure_order_code	8218
procedure_report	782
procedure_result	14869
documents	5
categories_to_documents	5
uuid_registry	30519
pid38 results 2026-08-30	21
w2x results	21
5414f88e0e4e92e0	2496	intake-extractor/1.0.0+claude-opus-5	21	8216
2345-7	89	mg/dL	2026-08-30 07:12:00	received
3094-0	15	mg/dL	2026-08-30 07:12:00	received
2160-0	0.84	mg/dL	2026-08-30 07:12:00	received
2951-2	137	mmol/L	2026-08-30 07:12:00	received
42
lab-results	1	Clinical Co-Pilot write-back: procedure_order 8216 committed with 21 result(s) [14980,14981,14982,14983,14984,14985,14986,14987,14988,14989,
```

21 ledger rows, one order (8216), 21 reports and 21 results, each
`review_status = received` (Pending Review, D29), `date_collected` carrying
the time the report prints (`2026-08-30 07:12:00`, item 3 of the decision),
and one chart copy (2496). 42 `lab-results-insert` audit rows and the
module's own `procedure_order 8216 committed with 21 result(s)` row.

## Run 2 — a byte-variant of the same report on the same chart

The same PDF with 30 bytes appended: a new digest, so the module's "already
on this chart" check and the ledger both see a new file.

```
sha3-512 0578ce8b47c26464… bytes 32062
login 302 /interface/main/tabs/main.php?token_main=…
chart 200 card present: True pid 38
upload 200 {"status": "ok", "document_id": "0578ce8b47c26464…", "kind": "lab_pdf", "extraction_status": "pending", "byte_length": 32062, "correlation_id": "89a3f312-a50d-45c0-b416-a0f981a6b2de", "identity_check": null, "quarantine": null, "write_back": null}
  poll 1: 200 in_progress identity=None write_back=null
  poll 2: 200 in_progress identity=None write_back=null
  poll 3: 200 in_progress identity=None write_back=null
  poll 4: 200 in_progress identity=None write_back=null
  poll 5: 200 in_progress identity=None write_back=null
  poll 6: 200 in_progress identity=None write_back=null
  poll 7: 200 in_progress identity=None write_back=null
  poll 8: 200 complete identity=confirmed write_back=written
terminal after 31.9s
status payload: {"status": "ok", "document_id": "0578ce8b47c26464…", "kind": "lab_pdf", "extraction_status": "complete", "extraction_id": "w2x-5b47bb30232ee39da761e06b0f7924ff", "failure_reason": null, "correlation_id": "89a3f312-a50d-45c0-b416-a0f981a6b2de", "identity_check": "confirmed", "quarantine": null, "write_back": {"decision": "written", "results_total": 22, "skipped": 1, "created": 0, "duplicate_ignored": 0, "already_in_chart": 21, "conflict": 0, "conflicts": []}}
```

**0 created, 21 already in the chart**, 1 skipped as before, 0 conflicts.

Agent events for run 2 (the 21 chart rows matched are run 1's
`procedure_result` ids):

```
"event_data": {"extraction_id": "w2x-5b47bb30232ee39da761e06b0f7924ff", "extractor_version": "intake-extractor/1.0.0+claude-opus-5", "document_id": 2497, "document_outcome": "duplicate_ignored", "procedure_order_id": 8216, "results_total": 21, "results_created": 0, "results_duplicate_ignored": 0, "results_already_in_chart": 21, "results_conflict": 0, "procedure_result_ids": [], "matched_procedure_result_ids": [14980, 14981, 14982, 14983, 14984, 14985, 14986, 14987, 14988, 14989, 14990, 14991, 14992, 14993, 14994, 14995, 14996, 14997, 14998, 14999, 15000]}
"event_data": {"document_id": "0578ce8b47c26464…", "extraction_id": "w2x-5b47bb30232ee39da761e06b0f7924ff", "correlation_id": "89a3f312-a50d-45c0-b416-a0f981a6b2de", "results_total": 22, "results_writable": 21, "results_skipped_by_reason": {"no_loinc_code": 1}
```

## State 3 — after run 2

```
ledger	21
procedure_order	8198
procedure_order_code	8218
procedure_report	782
procedure_result	14869
documents	6
categories_to_documents	6
uuid_registry	30521
pid38 results 2026-08-30	21
w2x results	21
2497	38	0578ce8b47c26464	2026-09-26
2496	38	5414f88e0e4e92e0	2026-09-26
Lab Report	2496
Lab Report	2497
```

Against state 2: the ledger and every `procedure_*` table are unchanged.
`documents` +1 and `uuid_registry` +2 are the second file's chart copy
(2497), filed by `upload.php` before the agent ran (D54 item 5: one document
per file); the endpoint reused it (`document_outcome: duplicate_ignored`).
**Zero result rows were written.**

## Run 3 — the same glucose result with a different value, under a third file

Straight at the endpoint: one result, LOINC 2345-7 collected
2026-08-30 07:12, value **90** where the chart (run 1's row 14980) holds
**89**, under a digest the chart has never seen.

```
token granted: True scope: openid user/lab-writeback.c
document sha3-512 8722378a5e02dc02… value sent 90.0
POST lab-writeback 200 {"patient_uuid": "a2c373ec-57bf-41cb-8767-94410dcf663d", "document_id": 2498, "document_outcome": "created", "procedure_order_id": null, "results": [{"test_code": "2345-7", "idempotency_key": "8722378a5e02dc02…:2345-7:2026-08-30", "extraction_id": "w2x-aec06ed744edb4283d951e86318f0388", "outcome": "conflict", "procedure_report_id": null, "procedure_result_id": null, "matched_procedure_result_id": 14980, "chart_value": "89", "chart_unit": "mg/dL"}], "correlation_id": "978f3e14-5743-4dd9-b50b-19d4839eef6e", "counts": {"created": 0, "duplicate_ignored": 0, "already_in_chart": 0, "conflict": 1}}
```

`conflict`: no ids of its own, `matched_procedure_result_id` 14980,
`chart_value` "89", `procedure_order_id` null, counts `conflict: 1`.

## State 4 — after run 3

```
ledger	21
procedure_order	8198
procedure_order_code	8218
procedure_report	782
procedure_result	14869
documents	7
categories_to_documents	7
uuid_registry	30523
pid38 results 2026-08-30	21
w2x results	21
chart row 14980	2345-7	89	mg/dL
lab-results	1	Clinical Co-Pilot write-back: 1 result(s) in the document disagree with procedure_result [14980] already in the chart; nothing written or overwritten
lab-results	1	Clinical Co-Pilot write-back: procedure_order 8216 committed with 21 result(s) [14980,14981,14982,14983,14984,14985,14986,14987,14988,14989,14990,14991,14992,14993,14994,14995,14996,14997,14998,14999,
2497	38	0578ce8b47c26464	file:///var/www/localhost/htdocs/openemr/sites/default/documents/38/a2d7137e-eeba-4314-9ddb-d56b9c2994e6
2498	38	8722378a5e02dc02	file:///var/www/localhost/htdocs/openemr/sites/default/documents/38/a2d71405-9750-4ae6-8f2b-fc2235dcb51b
```

Against state 3: no ledger row, no `procedure_*` row; the chart row 14980
still reads 89 mg/dL; a `lab-results` audit row names it. `documents` +1 is
the third file, stored by the endpoint with no result pointing at it (see
"Not settled" in the decision).

## Cleanup — from the ledger, and prove it

```
2496	file:///var/www/localhost/htdocs/openemr/sites/default/documents/38/a2d71325-3914-434e-a8cc-5343be808037
2497	file:///var/www/localhost/htdocs/openemr/sites/default/documents/38/a2d7137e-eeba-4314-9ddb-d56b9c2994e6
2498	file:///var/www/localhost/htdocs/openemr/sites/default/documents/38/a2d71405-9750-4ae6-8f2b-fc2235dcb51b
```

The ledger-driven cleanup (DEPLOY.md §7.4) over every ledger row removed run
1's 21 rows, order 8216, its 21 reports and results, chart copy 2496 and
their `uuid_registry` rows. The two chart copies outside the ledger — 2497
(run 2's `upload.php` copy) and 2498 (run 3's, stored by the endpoint) —
were removed by hand with the same statements, and the three encrypted files
deleted.

## State 5 — after cleanup

```
ledger	0
procedure_order	8197
procedure_order_code	8197
procedure_report	761
procedure_result	14848
documents	4
categories_to_documents	4
uuid_registry	30474
pid38 results 2026-08-30	0
w2x results	0
```

`diff` of state 1 against state 5: **identical**. Zero leftovers.

## The agent's log

After the three runs the agent's log held none of: the patient uuid, any
part of the patient's name, any test name (0 matching lines). Two
`llm_usage_event` lines: the two vision calls.
