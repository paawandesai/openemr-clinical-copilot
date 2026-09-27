# Token-fetch lock deployed on `64.177.42.210`, 2026-09-27 (module only)

This is work order 38's fix, D-unassigned (token fetch race), deployed as
the Clinical Co-Pilot module from **`main@a434ffd`**.

## The problem it fixes

A clinician's chart open fires several requests at once:

- the brief;
- D60's reconcile check;
- an upload, if the clinician sends one.

Each of them made OpenEMR mint a token for the same user and client, and
all of them share one `oauth_trusted_user` row. One flow's code could
overwrite another's before it was redeemed. On 2026-09-26 at 21:13:53 the
brief got `token_unavailable`, and the chart showed a 502.

## What changed on the host

| Item | Detail |
|---|---|
| Changed files | 8: `public/{ask,brief,conversation,reconcile,upload,upload-status}.php`, `src/ClinicianTokenBroker.php`, `src/W2/CardServices.php` |
| New files | 2: `src/TokenFetchLock.php` and `src/DatabaseTokenFetchLock.php` (a MariaDB `GET_LOCK` named per user, 15 s wait) |
| Not changed | No SQL, no deletions. The agent is untouched and was not restarted: agent code differs from the deployed `d61f739` only in a calibration page and its test. |
| Before the swap | The live module was confirmed to be exactly `d61f739`, then snapshotted. A staged copy linted 81/81 clean inside the OpenEMR container. |
| After the swap | 81/81 lint clean in place. 0 files differ from `a434ffd`, and none are missing. |

## Verified, as `physician`, through the real chart

Runner: [`chart-open-20.mjs`](chart-open-20.mjs). Raw log:
[`chart-opens.txt`](chart-opens.txt).

**20 opens of pid 38's chart: the brief succeeded 20/20.** Each open fires
the brief and the reconcile check at once:

| Response | Status | Time after open |
|---|---|---|
| `brief.php` | 200 every time | 16.5–17.5 s (once 20.6 s) |
| `reconcile.php` | 200 every time | 3.8–4.3 s |

**The audit log:** entries after the pre-deploy baseline (`log.id` 56045)
hold 21 "Pre-Visit Brief: shown": the 20 opens plus the upload run below.
There are **0 `token_unavailable`**, and no other `*_unavailable`.

**An upload while the brief is loading: both succeed.**

- A byte-variant of `lab-clean-001` was sent 1.7 s after the chart opened.
- `upload.php` answered 200 at 4.8 s, and the brief answered 200 at 16.7 s.
  [`upload-during-brief.png`](upload-during-brief.png) shows the brief
  rendered.
- The extraction completed and deduped: "21 already in the chart". The
  write-back ledger is unchanged at 21.

**Cleanup:** the variant's chart copy (`documents.id` 2493) was removed.
Each of these went from 1 to 0:

- the documents row;
- its category link;
- the document UUID and the drive UUID;
- the encrypted file;
- the agent's sealed store row.

There were no notes. Pid 38 holds its two demo documents again.

## Rollback (one line)

`rm -rf` the module directory and untar
`/root/w2-module-20260927/module-pre.tgz` in its place, then
`chown -R 1001:1001`; no restart.
