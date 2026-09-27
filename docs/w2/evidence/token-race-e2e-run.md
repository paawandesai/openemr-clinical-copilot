# Work order 38 — the chart-open token race, reproduced and fixed locally (`fix/w2-token-race`)

Raw output of every run this branch's session made through the module's real
routes, so no figure in the D64 (token fetch race) entry exists only
in a session report. Nothing is edited.

## What was run, and where

| | |
|---|---|
| Date | 2026-09-26 (UTC) |
| Stack | **LOCAL only.** OpenEMR `development-easy-openemr-1` (http://localhost:8300, this checkout bind-mounted, so the module code is whatever this branch held at the moment of the run); agent `clinical-copilot-agent-agent-1`, `FHIR_BASE_URL=https://host.docker.internal:9300/apis/default/fhir` and `AGENT_STUB_LLM_DRAFTING=true` (checked in `/proc/1/environ`). Neither host was touched. |
| Path | curl → login as `physician` → `demographics.php?set_pid=38` (the chart open: renders the card and its CSRF token and correlation id) → `brief.php` and `reconcile.php` fired at the same instant, plus `upload.php` in the runs marked "with upload". This is what the card fires on mount (`pre-visit-brief-card.js` `generate()`, `source-card.mjs` `reconcileSource.reconcile()`). |
| Patient | pid 38 = FHIR `a2c373ec-…`, lab-clean-001's patient. Synthea synthetic. |
| Model calls | None. The brief is stubbed; the upload is lab-clean-001, whose digest the agent's store already held, so it is a duplicate with no extraction. |
| Side effect | The first upload that got a token (run 2, open 4) filed lab-clean-001 on pid 38's chart (a `documents` row in "Lab Report"). That is what made reconcile mint a token on every later open: D60's reconcile brokers one only when the chart holds an unquarantined copy. It was left in place. |

The driver (a scratch script, not committed). Runs 1–4 used this with
`-w "%{http_code}"` on the brief and `cat` in the success test; the timing run
added `%{time_total}` and the `cut`:

```bash
#!/usr/bin/env bash
# Open pid $PID's chart N times as physician and fire what the card fires on
# mount, concurrently: brief.php + reconcile.php (+ upload.php with UPLOAD=1).
# Prints one line per open: HTTP status and error code of each request.
# Usage: chart_open.sh N [PID]   env: UPLOAD=1 to add the upload leg.
set -u
N=${1:-1}; PID=${2:-38}
S=$(cd "$(dirname "$0")" && pwd)
J=$S/jar.txt; B=http://localhost:8300
M=/interface/modules/custom_modules/oe-module-clinical-copilot/public
PDF="<checkout>/interface/modules/custom_modules/oe-module-clinical-copilot/dev/fixtures/lab-clean-001/document.pdf"

rm -f "$J"
curl -s -c "$J" -b "$J" -o /dev/null "$B/interface/login/login.php?site=default"
curl -s -c "$J" -b "$J" -o /dev/null -X POST "$B/interface/main/main_screen.php?auth=login&site=default" \
  --data "new_login_session_management=1&authProvider=Default&authUser=physician&clearPass=physician&languageChoice=1"

brief_ok=0
for i in $(seq 1 "$N"); do
  D=$S/open-$i; rm -rf "$D"; mkdir -p "$D"
  curl -s -c "$J" -b "$J" -o "$D/demo.html" "$B/interface/patient_file/summary/demographics.php?set_pid=$PID"
  CSRF=$(grep -o 'data-csrf-token="[^"]*"' "$D/demo.html" | head -1 | cut -d'"' -f2)
  CORR=$(grep -o 'data-correlation-id="[^"]*"' "$D/demo.html" | head -1 | cut -d'"' -f2)
  curl -s -b "$J" -o "$D/brief.json" -w "%{http_code} %{time_total}" -X POST "$B$M/brief.php" \
    --data "pid=$PID&csrf_token_form=$CSRF" > "$D/brief.code" &
  curl -s -b "$J" -o "$D/reconcile.json" -w "%{http_code}" -X POST "$B$M/reconcile.php" \
    --data "pid=$PID&csrf_token_form=$CSRF&correlation_id=$CORR" > "$D/reconcile.code" &
  if [ "${UPLOAD:-0}" = 1 ]; then
    curl -s -b "$J" -o "$D/upload.json" -w "%{http_code}" -X POST "$B$M/upload.php" \
      -F "pid=$PID" -F "csrf_token_form=$CSRF" -F "correlation_id=$CORR" -F "document_kind=lab_pdf" \
      -F "document=@$PDF;type=application/pdf;filename=lab-clean-001.pdf" > "$D/upload.code" &
  fi
  wait
  line="open $i:"
  for leg in brief reconcile upload; do
    [ -f "$D/$leg.code" ] || continue
    err=$(python3 -c "import json,sys
try: d=json.load(open(sys.argv[1]))
except Exception: print('non-json'); sys.exit()
print(d.get('error') or d.get('extraction_status') or ('checked=%s' % d.get('checked')) if isinstance(d, dict) else '?')" "$D/$leg.json")
    line="$line $leg=$(cat "$D/$leg.code")($err)"
  done
  [ "$(cut -d" " -f1 "$D/brief.code")" = 200 ] && brief_ok=$((brief_ok + 1))
  echo "$line"
done
echo "brief succeeded $brief_ok/$N"
```

`checked=None` on a brief line is the driver printing a field the brief
payload does not have; it means the brief answered 200 with a brief.

## Before the fix (branch head = main@d6119e6, module unchanged)

### Run 1 — one open, with upload, 23:01:44

```
open 1: brief=200(checked=None) reconcile=200(checked=0) upload=502(token_unavailable)
brief succeeded 1/1
```

The OpenEMR log for that second — the loopback token request, then the module:

```
[Sat Sep 26 23:01:47.082576 2026] [php:notice] [pid 7450:tid 7450] [client 127.0.0.1:49464] OpenEMR.ERROR: AuthorizationController->oauthAuthorizeToken() CSRF check failed [] []
[Sat Sep 26 23:01:47.085813 2026] [php:notice] [pid 11800:tid 11800] [client 172.19.0.1:49636] OpenEMR.ERROR: Clinical Co-Pilot did not obtain an access token {"stage":"token","http_status":"400","client_id":"GEBy1cVhjEOTP_ETfMhEdOxmnkfWMGyPW_LB_kE26tU"} []
```

and the audit rows (`log`, comments decoded):

```
2026-09-26 23:01:47	patient-record	0	Clinical Co-Pilot document upload: token_unavailable (document 5414f88e0e4e92e0, correlation_id 7920c331-5e92-41ed-aece-eb6eaddda92f)
2026-09-26 23:01:47	patient-record	1	Clinical Co-Pilot Pre-Visit Brief: shown (correlation_id a0b0f12a-9185-47d8-965f-17d213e5d230)
```

"CSRF check failed" is `oauthAuthorizeToken()` finding no trusted-user row
holding the code it was given (`sessionUserByCode()` empty, so the restored
session has no `csrf`): the brief's authorize had overwritten it.

### Run 2 — ten opens, with upload, 23:02:02

```
open 1: brief=200(checked=None) reconcile=200(checked=0) upload=502(token_unavailable)
open 2: brief=200(checked=None) reconcile=200(checked=0) upload=502(token_unavailable)
open 3: brief=200(checked=None) reconcile=200(checked=0) upload=502(token_unavailable)
open 4: brief=502(token_unavailable) reconcile=200(checked=0) upload=200(complete)
open 5: brief=502(token_unavailable) reconcile=200(checked=1) upload=200(complete)
open 6: brief=502(token_unavailable) reconcile=200(checked=1) upload=502(token_unavailable)
open 7: brief=502(token_unavailable) reconcile=200(checked=1) upload=502(token_unavailable)
open 8: brief=200(checked=None) reconcile=200(checked=1) upload=502(token_unavailable)
open 9: brief=502(token_unavailable) reconcile=200(checked=1) upload=502(token_unavailable)
open 10: brief=200(checked=None) reconcile=200(checked=1) upload=502(token_unavailable)
brief succeeded 5/10
```

### Run 3 — ten plain chart opens (brief + reconcile), 23:02:29

```
open 1: brief=502(token_unavailable) reconcile=200(checked=1)
open 2: brief=200(checked=None) reconcile=200(checked=1)
open 3: brief=502(token_unavailable) reconcile=200(checked=1)
open 4: brief=502(token_unavailable) reconcile=200(checked=1)
open 5: brief=200(checked=None) reconcile=200(checked=1)
open 6: brief=200(checked=None) reconcile=200(checked=1)
open 7: brief=502(token_unavailable) reconcile=200(checked=1)
open 8: brief=200(checked=None) reconcile=200(checked=1)
open 9: brief=200(checked=None) reconcile=200(checked=1)
open 10: brief=502(token_unavailable) reconcile=200(checked=1)
brief succeeded 5/10
```

`stage=token, http_status=400` lines in the OpenEMR log across runs 1–3
(23:01–23:03): **27**. The responses above show 19 (`token_unavailable` on a
brief or upload). The other 8 were reconcile's: `reconcile.php` treats a
failed token as "nothing known" and answers 200 (D60: fire and forget), so on
those opens the quarantine check silently did not run.

## The concurrency test, before and after (module PHPUnit)

With the lock plumbed into the constructor but not yet taken in `issueFor()`
(`--filter ClinicianTokenBroker`):

```
FFFFE.............                                                18 / 18 (100%)
1) ...ClinicianTokenBrokerConcurrencyTest::testALockNotObtainedInTimeIsTokenUnavailableAndSendsNothing
1) ...ClinicianTokenBrokerConcurrencyTest::testTheThreeRequestsOfOneChartOpenEachGetAToken
   Failed asserting that an instance of class OpenEMR\Modules\ClinicalCopilot\BriefUnavailable is an instance of class OpenEMR\Modules\ClinicalCopilot\AccessToken.
2) ...ClinicianTokenBrokerConcurrencyTest::testTwoTabsOfTheSameClinicianAreSerializedToo
3) ...ClinicianTokenBrokerConcurrencyTest::testDifferentCliniciansDoNotWaitForEachOther
4) ...ClinicianTokenBrokerConcurrencyTest::testTheLockIsReleasedWhenTheExchangeFails
Tests: 18, Assertions: 57, Errors: 1, Failures: 4.
```

After: `OK (18 tests, 67 assertions)`; full module suite `OK (272 tests, 597 assertions)`.

## After the fix

### Run 4 — ten opens, with upload, 23:18:17

```
open 1: brief=200(checked=None) reconcile=200(checked=1) upload=200(complete)
open 2: brief=200(checked=None) reconcile=200(checked=1) upload=200(complete)
open 3: brief=200(checked=None) reconcile=200(checked=1) upload=200(complete)
open 4: brief=200(checked=None) reconcile=200(checked=1) upload=200(complete)
open 5: brief=200(checked=None) reconcile=200(checked=1) upload=200(complete)
open 6: brief=200(checked=None) reconcile=200(checked=1) upload=200(complete)
open 7: brief=200(checked=None) reconcile=200(checked=1) upload=200(complete)
open 8: brief=200(checked=None) reconcile=200(checked=1) upload=200(complete)
open 9: brief=200(checked=None) reconcile=200(checked=1) upload=200(complete)
open 10: brief=200(checked=None) reconcile=200(checked=1) upload=200(complete)
brief succeeded 10/10
```

### Run 5 — the acceptance run: twenty chart opens in a row, 23:18:59

```
open 1: brief=200(checked=None) reconcile=200(checked=1)
open 2: brief=200(checked=None) reconcile=200(checked=1)
open 3: brief=200(checked=None) reconcile=200(checked=1)
open 4: brief=200(checked=None) reconcile=200(checked=1)
open 5: brief=200(checked=None) reconcile=200(checked=1)
open 6: brief=200(checked=None) reconcile=200(checked=1)
open 7: brief=200(checked=None) reconcile=200(checked=1)
open 8: brief=200(checked=None) reconcile=200(checked=1)
open 9: brief=200(checked=None) reconcile=200(checked=1)
open 10: brief=200(checked=None) reconcile=200(checked=1)
open 11: brief=200(checked=None) reconcile=200(checked=1)
open 12: brief=200(checked=None) reconcile=200(checked=1)
open 13: brief=200(checked=None) reconcile=200(checked=1)
open 14: brief=200(checked=None) reconcile=200(checked=1)
open 15: brief=200(checked=None) reconcile=200(checked=1)
open 16: brief=200(checked=None) reconcile=200(checked=1)
open 17: brief=200(checked=None) reconcile=200(checked=1)
open 18: brief=200(checked=None) reconcile=200(checked=1)
open 19: brief=200(checked=None) reconcile=200(checked=1)
open 20: brief=200(checked=None) reconcile=200(checked=1)
brief succeeded 20/20
```

Cross-checks, taken straight after run 5:

```
--- token failures logged since start of the after-fix runs (23:18:17):
0
--- brief audit rows since 2026-09-26 23:18:59 (success, count):
1	20
--- api_token rows for the module client issued during run 5
    (expiry within [23:18:59, now] + 1 h; brokered tokens live 3600 s):
40
```

40 tokens for 20 opens: the brief **and** reconcile each got one on every
open, so reconcile's 200s here are real checks, not the silent skip of runs 2–3.

### Run 6 — latency, 23:27

Ten plain opens with the lock (brief time is curl's `time_total`):

```
open 1: brief=200 1.909152(checked=None) reconcile=200(checked=1)
open 2: brief=200 1.076323(checked=None) reconcile=200(checked=1)
open 3: brief=200 1.097121(checked=None) reconcile=200(checked=1)
open 4: brief=200 1.068896(checked=None) reconcile=200(checked=1)
open 5: brief=200 1.132289(checked=None) reconcile=200(checked=1)
open 6: brief=200 1.044689(checked=None) reconcile=200(checked=1)
open 7: brief=200 1.068833(checked=None) reconcile=200(checked=1)
open 8: brief=200 1.043504(checked=None) reconcile=200(checked=1)
open 9: brief=200 1.166723(checked=None) reconcile=200(checked=1)
open 10: brief=200 1.110384(checked=None) reconcile=200(checked=1)
brief succeeded 10/10
```

and ten briefs alone, nothing concurrent, same session:

```
brief alone: 200 1.186083
brief alone: 200 1.130716
brief alone: 200 1.196845
brief alone: 200 1.182355
brief alone: 200 1.111044
brief alone: 502 30.986744
brief alone: 200 1.362929
brief alone: 200 1.141230
brief alone: 200 1.099677
brief alone: 200 1.084056
```

No measurable cost from waiting behind reconcile's exchange on this stack.
**The 502 is not the race and not a token failure:** its audit row is
`Clinical Co-Pilot Pre-Visit Brief: agent_unavailable (correlation_id
834397d9-…)`, and the agent logged `pre_visit_brief_failed`,
`error: fhir_unavailable`, `httpx.ConnectTimeout`. The token was issued; the
agent's FHIR read back to `host.docker.internal:9300` (Colima's forward)
could not connect within its timeout. That is this laptop's network path, and
it happened with no concurrent request at all.

## Mutation test of the new tests

Each mutant applied to the source in place, the full module suite run in the
container, the original restored (SHA-256 checked):

```
KILLED    exit=2  no lock taken  | ERRORS! Tests: 272, Assertions: 587, Errors: 1, Failures: 4.
KILLED    exit=2  acquire result ignored  | ERRORS! Tests: 272, Assertions: 590, Errors: 2.
KILLED    exit=2  released before the code is redeemed  | ERRORS! Tests: 272, Assertions: 592, Errors: 1.
KILLED    exit=1  released only on success (no finally)  | FAILURES! Tests: 272, Assertions: 596, Failures: 1.
KILLED    exit=2  never released  | ERRORS! Tests: 272, Assertions: 588, Errors: 2, Failures: 1.
KILLED    exit=1  lock keyed by core session, not user  | FAILURES! Tests: 272, Assertions: 593, Failures: 2.
KILLED    exit=1  lock name ignores the user  | FAILURES! Tests: 272, Assertions: 590, Failures: 2.
KILLED    exit=1  lock name without a separator  | FAILURES! Tests: 272, Assertions: 593, Failures: 1.
KILLED    exit=1  lock name carries the raw client id  | FAILURES! Tests: 272, Assertions: 597, Failures: 1.
KILLED    exit=1  GET_LOCK with no wait  | FAILURES! Tests: 272, Assertions: 597, Failures: 1.
KILLED    exit=1  GET_LOCK '0' taken as acquired  | FAILURES! Tests: 272, Assertions: 597, Failures: 2.
KILLED    exit=1  GET_LOCK string '1' rejected  | FAILURES! Tests: 272, Assertions: 597, Failures: 1.
KILLED    exit=2  acquire lets a query failure escape  | ERRORS! Tests: 272, Assertions: 596, Errors: 1.
KILLED    exit=2  release lets a query failure escape  | ERRORS! Tests: 272, Assertions: 596, Errors: 1.
KILLED    exit=2  release frees nothing  | ERRORS! Tests: 272, Assertions: 593, Errors: 1, Failures: 1.
sources restored byte-for-byte: True
15/15 killed
```

Not mutated, because no unit test reaches it: the routes' glue, i.e. that each
route passes `$session->get('authUserID')` to `CardServices::broker()`.
Runs 4–5 exercise it for brief, reconcile and upload.
