# Stage 3 Instrumentation — Raw Findings Ledger

Evidence file. `AUDIT.md` will be written from this; this is not that document.
One entry per finding, no summary, no narrative.

**System state at time of audit:** OpenEMR 8.2.0-dev (`v_database` 541), 284
tables, `docker/development-easy` stack (mysql, openemr, phpmyadmin only).

**Data census re-run 2026-09-14T18:25:02Z** and used throughout this ledger:

```
patients 65 · encounters 2800 · problems 1669 · allergies 43 · med_list 309
prescriptions 305 · lab_orders 7417 · lab_results 7259 · vitals 65
appointments 11 · documents 7 · log 2295 · audit_master 0 · audit_details 0
```

Counts differ from earlier instrumentation notes because a 5-patient Synthea
import was run during this session (60→65 patients, 2561→2800 encounters,
6833→7259 lab results). Entries carrying corrected figures are marked
**Correction:**.

All database commands below were run from `docker/development-easy/`.

---

## SECURITY

### F-01 — GitHub token committed to the repository in three encodings
- **Category:** Security
- **Severity:** Medium
- **Claim:** A GitHub personal access token is committed to
  `docker/development-easy/docker-compose.yml` in three distinct encodings —
  plaintext, base64, and space-separated ASCII decimal — such that scanning for
  any one form misses the other two.
- **Evidence:**
  ```
  $ grep -n "GITHUB_COMPOSER_TOKEN" docker/development-easy/docker-compose.yml
  75:      GITHUB_COMPOSER_TOKEN: c313de1ed5a00eb6ff9309559ec9ad01fcc553f0
  76:      GITHUB_COMPOSER_TOKEN_ENCODED: Z2hwX0NpbnJ4aXlNd0NzcGZXWG1UWFUwcXhGa040elFKSDJoZGJXVw==
  77:      GITHUB_COMPOSER_TOKEN_ENCODED_ALTERNATE: '103 104 112 95 57 54 108 115 88 116 87 72 51 75 81 105 69 88 88 119 97 79 80 78 109 69 66 115 85 97 106 78 112 71 49 81 90 102 74 121'
  ```
- **Verified:** `first-hand`
- **Implication for the agent:** Any secret the co-pilot introduces (LLM API
  keys, service credentials) must not follow this file's precedent. Secrets
  belong in an untracked env file or a secret store, in exactly one encoding.
  This is an inherited upstream credential and is likely already revoked — the
  finding is about the committed pattern, not this specific token.

### F-02 — OAuth2 registration produces a client that cannot obtain a token
- **Category:** Security
- **Severity:** Medium
- **Claim:** A client created by `register-oauth2-client` is unusable as
  registered: the schema defaults `is_enabled` to 0 and `is_confidential` to 1,
  and the registration grants only `authorization_code`, while the deployment
  globally advertises `oauth_password_grant: 3` — a grant no registered client
  holds.
- **Evidence:**
  ```
  $ grep -n "is_enabled\|is_confidential" sql/database.sql
  14118:`is_confidential` tinyint(1) NOT NULL DEFAULT '1',
  14126:`is_enabled` tinyint(1) NOT NULL DEFAULT '0',

  $ grep -n "oauth_password_grant" docker/development-easy/docker-compose.yml
  84:      OPENEMR_SETTING_oauth_password_grant: 3
  ```
  The devtools registration payload carries no `grant_types` field at all
  (`/root/devtools:802-810`):
  ```
  returnJson=$(curl -X POST -k -H 'Content-Type: application/json' -s https://localhost/oauth2/${site}/registration --data "{
     \"application_type\": \"private\",
     \"redirect_uris\":
       [\"https://localhost:9300/swagger/oauth2-redirect.html\"],
     \"client_name\": \"A Private App\",
     \"token_endpoint_auth_method\": \"client_secret_post\",
     \"contacts\": [\"me@example.org\", \"them@example.org\"],
     \"scope\": \"${scopes}\"
    }")
  ```
  Live table state:
  ```
  $ docker compose exec -T mysql mariadb -uopenemr -popenemr openemr -t -e "SELECT client_id, is_enabled, is_confidential, grant_types FROM oauth_clients;"
  +---------------------------------------------+------------+-----------------+-------------------------------------------+
  | client_id                                   | is_enabled | is_confidential | grant_types                               |
  +---------------------------------------------+------------+-----------------+-------------------------------------------+
  | 773KrZky-wldK_iBDh-Il71sae3Y8EmMMP8cwu8Mpuc |          1 |               1 | authorization_code,password,refresh_token |
  | lfNdNEhC8IgkYgSRVXQlOcvszc20A6nUB9JaCFRE4hE |          0 |               1 | authorization_code                        |
  | zYinBZAW5lI5wznAUKl-GVw21tX4QyDeVE0UZQbwaIg |          0 |               1 | authorization_code                        |
  +---------------------------------------------+------------+-----------------+-------------------------------------------+
  ```
- **Correction / addition to the brief:** three clients exist, not one. The two
  bottom rows are as-registered and match the claim exactly. The top row has
  `is_enabled=1` and two extra grants, i.e. it was modified after registration.
  It is almost certainly the client behind F-07's successful token, so this
  ledger does not claim an as-shipped registered client ever worked.
- **Verified:** `first-hand` (schema defaults, compose setting, live table
  state). The claim that all three failure modes surface as an identical opaque
  `invalid_client` is `operator-reported` — no token exchange was exercised here.
- **Implication for the agent:** The co-pilot cannot rely on
  `register-oauth2-client` alone to provision its own API identity. Provisioning
  must explicitly set `is_enabled=1` and the grant types it needs, and the
  bootstrap must verify it can actually mint a token rather than assuming
  registration succeeded.

### F-03 — Client secret printed to stdout on registration
- **Category:** Security
- **Severity:** Low
- **Claim:** `register-oauth2-client` writes the client secret in cleartext to
  stdout, placing it in shell history, terminal scrollback and any CI log that
  captures the command.
- **Evidence:**
  ```
  $ docker compose exec -T openemr /root/devtools register-oauth2-client
  Registering a oauth2 client to site default
  client id: "zYinBZAW5lI5wznAUKl-GVw21tX4QyDeVE0UZQbwaIg"
  client secret: "81VXoufIzsEZm1R9acpTRZbstOfk3MG_EzjZGLOr7iXuZzoI3paf2YJVlAdCsWjIkUh55InsyV79QHmCRmwAXw"
  EXIT_CODE=0
  ```
  Source: `/root/devtools:811-814` extracts both with `jq` and `echo`s them.
- **Verified:** `first-hand`
- **Implication for the agent:** If the co-pilot's provisioning reuses this
  path, the secret must be redirected to a file with restrictive modes or piped
  into a secret store, never echoed. Treat any secret produced this way as
  already disclosed and rotate it.

### F-04 — OAuth signing keys generated lazily into the patient-document tree
- **Category:** Security
- **Severity:** Medium
- **Claim:** The OAuth2 private and public keys are created on first OAuth2
  request — not at install — and are written into
  `sites/default/documents/certificates/`, the same document tree that holds
  patient documents.
- **Evidence:** `src/Common/Auth/OAuth2KeyConfig.php:63-64` sets the paths:
  ```php
  $this->privateKey = $siteDir . '/documents/certificates/oaprivate.key';
  $this->publicKey = $siteDir . '/documents/certificates/oapublic.key';
  ```
  The constructor generates on demand — `:68` `if ($this->verifyKeys() === false) {`
  → `:70` `$this->createOrRecreateKeys();`. Modes are set at `:250`
  `chmod($this->privateKey, 0640);` and `:252` `chmod($this->publicKey, 0660);`.
  Observed on disk:
  ```
  $ docker compose exec -T openemr ls -la /var/www/localhost/htdocs/openemr/sites/default/documents/certificates/
  -rw-r--r--    1 apache   root          3673 Sep 14 16:46 README.md
  -rw-r-----    1 apache   dialout       1886 Sep 14 16:49 oaprivate.key
  -rw-rw----    1 apache   dialout        451 Sep 14 16:49 oapublic.key
  ```
  Sibling directories in the same tree:
  ```
  $ docker compose exec -T openemr ls /var/www/localhost/htdocs/openemr/sites/default/documents/
  00  certificates  couchdb  custom_menus  edi  era  letter_templates
  logs_and_misc  onsite_portal_documents  procedure_results  temp
  ```
  `00/` is patient document storage and `procedure_results/` holds lab
  documents; the signing keys sit beside them under one parent.
- **Verified:** `first-hand`
- **Implication for the agent:** Any document-tree traversal, backup, export or
  indexing the co-pilot performs must exclude `certificates/` explicitly. A
  naive "read everything under documents/" would ingest the OAuth private key.

### F-05 — POSITIVE: client secrets are not stored in plaintext
- **Category:** Security
- **Severity:** Positive
- **Claim:** Stored client secrets are encrypted at rest — the persisted value
  carries a `007` CryptoGen version prefix, is 219 characters, and does not
  match the 88-character secret printed at registration.
- **Evidence:**
  ```
  $ docker compose exec -T mysql mariadb -uopenemr -popenemr openemr -t -e "SELECT client_id, LEFT(client_secret,12) AS secret_prefix, LENGTH(client_secret) AS secret_len FROM oauth_clients;"
  | zYinBZAW5lI5wznAUKl-GVw21tX4QyDeVE0UZQbwaIg | 007Z87FyXQQ/  |        219 |
  | lfNdNEhC8IgkYgSRVXQlOcvszc20A6nUB9JaCFRE4hE | 007QjV5U0gvd  |        219 |
  | 773KrZky-wldK_iBDh-Il71sae3Y8EmMMP8cwu8Mpuc | 007wGb6T1Yb0  |        219 |
  ```
  The printed secret for `zYinBZAW…` was
  `81VXoufIzsEZm1R9acpTRZbstOfk3MG_EzjZGLOr7iXuZzoI3paf2YJVlAdCsWjIkUh55InsyV79QHmCRmwAXw`
  (88 chars) — no prefix match, different length. The `007` prefix is the
  zero-padded current key version: `src/Common/Crypto/KeyVersion.php:30`
  `public const PREFIX_LENGTH = 3;`, `:31` `public const CURRENT = self::SEVEN;`,
  `:39` `case SEVEN = 7;`, and `:64-67`:
  ```php
  public function toPaddedString(): string
  {
      return str_pad((string)$this->value, 3, '0', STR_PAD_LEFT);
  }
  ```
- **Verified:** `first-hand`
- **Implication for the agent:** None. Credential storage can be reused as-is;
  no compensating control needed.

### F-06 — POSITIVE: private key passphrase is per-install CSPRNG random
- **Category:** Security
- **Severity:** Positive
- **Claim:** The passphrase protecting the OAuth private key is generated fresh
  per install from a cryptographically secure RNG — it is not static, derived,
  or predictable from environment.
- **Evidence:** `src/Common/Auth/OAuth2KeyConfig.php:215`:
  ```php
  $this->passphrase = RandomGenUtils::produceRandomString(60, "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789");
  ```
  60 characters from a 62-character alphabet. `src/Common/Utils/RandomGenUtils.php:33-41`:
  ```php
  public static function produceRandomString(int $length = 26, string $alphabet = 'abcdefghijklmnopqrstuvwxyz234567'): string
  {
      $str = '';
      $alphamax = strlen($alphabet) - 1;
      for ($i = 0; $i < $length; ++$i) {
          $str .= $alphabet[random_int(0, $alphamax)];
      }
      return $str;
  }
  ```
  `random_int()` at `:38` is PHP's CSPRNG. Generated inside
  `createOrRecreateKeys()` (`OAuth2KeyConfig.php:194`), used at `:235`, stored
  encrypted at `:254`. The sibling encryption key at `:212` is
  `base64_encode(random_bytes(32))`.
- **Verified:** `first-hand`
- **Implication for the agent:** None. No key-material weakness to design around.

### F-07 — POSITIVE: FHIR scope enforcement holds (Door 2 closed)
- **Category:** Security
- **Severity:** Positive
- **Claim:** A token scoped to `openid api:fhir user/Patient.read` can read
  Patient and is refused on every resource outside that scope; a forged bearer
  token is also refused.
- **Evidence:** operator-reported, quoted verbatim:
  ```
  Token scoped to `openid api:fhir user/Patient.read`:
    Patient?_count=5              → 200 (0.375s)
    MedicationRequest?patient=20  → 401 {"error":"An error occurred","message":"Unauthorized","code":0}
    Observation?patient=20        → 401
    Bearer garbage                → 401
  ```
- **Verified:** `operator-reported`
- **Implication for the agent:** The co-pilot cannot widen its own data access
  by reusing a narrow token — scope must be requested up front for every
  resource type it intends to read. Scope enforcement is a real boundary and can
  be relied on as one.

### F-08 — Out-of-scope and invalid-token responses are indistinguishable
- **Category:** Security
- **Severity:** Low
- **Claim:** A request refused for insufficient scope returns 401, the same
  status and body as a request bearing a garbage token, so an authorization
  failure cannot be told apart from an authentication failure.
- **Evidence:** operator-reported. From the F-07 probe set, the scoped-out
  resources and the forged token produce the same result:
  ```
    MedicationRequest?patient=20  → 401 {"error":"An error occurred","message":"Unauthorized","code":0}
    Observation?patient=20        → 401
    Bearer garbage                → 401
  ```
  Per RFC 6750 an insufficient-scope refusal would be 403; no `403` was observed
  in the probe set.
- **Verified:** `operator-reported`
- **Implication for the agent:** The co-pilot must not treat a 401 as "token
  expired, refresh and retry" — an infinite refresh loop against a
  permanently-out-of-scope resource is the failure mode. It needs its own record
  of which scopes it holds and must check that before requesting a resource.

### F-09 — Token response `scope` disagrees with the JWT `scopes` claim
- **Category:** Security
- **Severity:** Medium
- **Claim:** The token endpoint's `scope` field and the scopes actually carried
  in the issued JWT are not the same set — the response omitted `api:fhir`,
  which the token itself contained.
- **Evidence:** operator-reported, quoted verbatim:
  ```
  Token response `scope` field was "openid user/Patient.read" while the JWT
  `scopes` claim contained ["openid","api:fhir","user/Patient.read"].
  ```
- **Verified:** `operator-reported`
- **Implication for the agent:** The co-pilot must derive its effective
  permissions by decoding the JWT `scopes` claim, never by parsing the `scope`
  string in the token response. Trusting the response field would understate
  what it holds and could drive incorrect self-limiting or incorrect error
  handling.

---

## COMPLIANCE

### F-10 — Read/query audit logging ships disabled
- **Category:** Compliance
- **Severity:** High
- **Claim:** Every write-side audit event category is enabled, but the
  query/read category is empty — so record *reads* are not audited by default.
- **Evidence:**
  ```
  $ docker compose exec -T mysql mariadb -uopenemr -popenemr openemr -t -e "SELECT gl_name, gl_value FROM globals WHERE gl_name LIKE 'audit_events%' OR gl_name IN ('enable_auditlog','enable_auditlog_encryption','enable_atna_audit','gbl_force_log_breakglass') ORDER BY gl_name;"
  +--------------------------------------+----------+
  | gl_name                              | gl_value |
  +--------------------------------------+----------+
  | audit_events_backup                  | 1        |
  | audit_events_cdr                     |          |
  | audit_events_http-request            | 1        |
  | audit_events_lab-results             | 1        |
  | audit_events_order                   | 1        |
  | audit_events_other                   | 1        |
  | audit_events_patient-record          | 1        |
  | audit_events_query                   |          |
  | audit_events_scheduling              | 1        |
  | audit_events_security-administration | 1        |
  | enable_atna_audit                    |          |
  | enable_auditlog                      | 1        |
  | enable_auditlog_encryption           |          |
  | gbl_force_log_breakglass             | 1        |
  +--------------------------------------+----------+
  ```
- **Correction / addition to the brief:** `audit_events_cdr` is also empty, not
  only `audit_events_query`. The eight categories listed as enabled are
  confirmed `1`.
- **Verified:** `first-hand`
- **Implication for the agent:** A read-only co-pilot generates almost no audit
  trail in the default configuration — the exact opposite of what a chart-reading
  assistant needs. Enabling `audit_events_query` is a one-setting change and
  must be a deployment precondition, not an afterthought.

### F-11 — `log` table has no usable event category for access review
- **Category:** Compliance
- **Severity:** High
- **Claim:** 81% of audit log rows are classified `other-insert`/`other-delete`,
  while genuine read events are negligible, leaving the log unusable for "who
  looked at what" review.
- **Evidence:**
  ```
  $ docker compose exec -T mysql mariadb -uopenemr -popenemr openemr -t -e "SELECT (SELECT COUNT(*) FROM log) AS total, (SELECT COUNT(*) FROM log WHERE event='view') AS view_evt, (SELECT COUNT(*) FROM log WHERE event='http-request-select') AS http_sel, (SELECT COUNT(*) FROM log WHERE event LIKE 'other-%') AS other_evt, (SELECT COUNT(*) FROM log WHERE event='patient-record-insert') AS pr_insert;"
  +-------+----------+----------+-----------+-----------+
  | total | view_evt | http_sel | other_evt | pr_insert |
  +-------+----------+----------+-----------+-----------+
  |  2295 |       10 |       28 |      1862 |        50 |
  +-------+----------+----------+-----------+-----------+

  $ ... -e "SELECT event, COUNT(*) n FROM log GROUP BY event ORDER BY n DESC LIMIT 15;"
  | other-delete                   | 990 |
  | other-insert                   | 816 |
  | security-administration-insert | 120 |
  | patient-record-insert          |  50 |
  | other-update                   |  49 |
  | patient-record-update          |  44 |
  | security-administration-update |  31 |
  | http-request-select            |  28 |
  | patient-record-delete          |  27 |
  | logout                         |  25 |
  | login                          |  25 |
  | security-administration-delete |  18 |
  | api                            |  17 |
  | scheduling-insert              |  11 |
  | view                           |  10 |
  ```
  Log span: `2014-02-01 18:26:37` to `2026-09-14 18:19:32`.
- **Correction:** counts drifted upward from the brief's figures because this
  session's own activity wrote to the table. Current: total **2295** (was 2220),
  `http-request-select` **28** (was 14), `other-*` **1862** (was 1800).
  1862/2295 = **81.1%**, so the 81% claim stands. `patient-record-insert` fired
  50 times against **65** patients now on file (brief said 60).
- **Verified:** `first-hand`
- **Implication for the agent:** The co-pilot cannot depend on OpenEMR's
  existing log for its own access accounting. It must emit its own structured
  access record — patient id, resource, timestamp, requesting user, purpose —
  into a purpose-built table it controls.

### F-12 — Audit records unencrypted at rest, no ATNA export
- **Category:** Compliance
- **Severity:** Medium
- **Claim:** `enable_auditlog_encryption` is off, so audit rows sit in plaintext
  in the database, and `enable_atna_audit` is off, so nothing is exported to an
  external ATNA syslog collector.
- **Evidence:** from the same `globals` query shown in F-10 — both values are
  empty:
  ```
  | enable_atna_audit                    |          |
  | enable_auditlog_encryption           |          |
  ```
- **Verified:** `first-hand`
- **Implication for the agent:** Anything the co-pilot writes to the audit log —
  including prompts or context excerpts, if it logs those — lands unencrypted
  and stays only on this host. Do not log PHI-bearing prompt content to the
  audit trail under this configuration.

### F-13 — POSITIVE: break-glass logging is forced on
- **Category:** Compliance
- **Severity:** Positive
- **Claim:** `gbl_force_log_breakglass` is set to 1, so break-glass access is
  logged and cannot be silently suppressed.
- **Evidence:** from the same `globals` query:
  ```
  | gbl_force_log_breakglass             | 1        |
  ```
- **Verified:** `first-hand`
- **Implication for the agent:** None. Emergency-access logging is already on;
  the co-pilot should not disable it (note that the CCDA importer's dev mode
  temporarily zeroes this — see F-15's surrounding code path).

### F-14 — BLOCKED VERIFICATION: audit-log comparison set could not be produced
- **Category:** Compliance
- **Severity:** Informational
- **Claim:** The intended audit-logged-import comparison set does not exist,
  because the only code path that would have produced it silently no-ops (F-15).
  This is a blocked verification, not evidence that audit logging fails.
- **Evidence:**
  ```
  $ docker compose exec -T mysql mariadb -uopenemr -popenemr openemr -t -e "SELECT (SELECT COUNT(*) FROM audit_master) AS audit_master, (SELECT COUNT(*) FROM audit_details) AS audit_details;"
  audit_master  0
  audit_details 0
  ```
  The path that would populate these is `import-random-patients N false`
  (development mode off, "audit log will be used"), which exits 0 while
  creating no patients — see F-15.
- **Verified:** `first-hand`
- **Implication for the agent:** None directly. Recorded so that the empty
  `audit_master`/`audit_details` tables are not later misread as proof that
  audited import does not work — it was never successfully exercised.

---

## DATA QUALITY

### F-15 — `import-random-patients N false` is a success-shaped silent failure
- **Category:** Data Quality
- **Severity:** High
- **Claim:** `import-random-patients <count> false` exits 0 and prints
  per-record success messages while creating zero patients, leaving orphan
  `documents` rows behind.
- **Evidence:**
  ```
  $ docker compose exec -T openemr /root/devtools import-random-patients 2 false
  ...
  Development Mode is OFF (audit log will be used)

  Starting patients import.
  System has successfully imported CCDA number: 1
  System has successfully imported CCDA number: 2

  Completed patients import (2 patients) (1 total seconds) (0.5 average seconds per patient)
  Started uuid creation
  Completed uuid creation (1 total seconds; 0.00027777777777778 total hours)

  Finished patients import 2
  Completed run for following number of random patients: 2
  EXIT_CODE=0
  ```
  Patient count before and after was identical at 65. Neither generated patient
  reached the database:
  ```
  $ ... -e "SELECT pid, fname, lname FROM patient_data WHERE lname IN ('Lebsack687','Anderson154') OR fname IN ('Faustino767','Annett908');"
  (empty set)
  ```
  Root cause, read from source — `contrib/util/ccda_import/import_ccda.php:195-207`:
  ```php
  } else {
      // development mode is off
      //  1. import ccda document
      $fileContents = file_get_contents($file);
      $document = new Document();
      // TODO: collect CCDA category id instead of hardcoding 13
      $document->createDocument('00', 13, basename($file), 'text/xml', $fileContents);
      $documentId = $document->get_id();
      //  2. import to ccda table
      exec("php " . $openemrPath . "/bin/console openemr:ccda-import --site=" . $session->get('site_id') . " --document_id=" . $documentId . " --auth_name=" . $authName);
      $auditId = sqlQueryNoLog("SELECT max(`id`) as `maxid` FROM `audit_master`")['maxid'];
      //  3. import as new patient
      exec("php " . $openemrPath . "/bin/console openemr:ccda-newpatient --site=" . $session->get('site_id') . " --am_id=" . $auditId . " --document_id=" . $documentId . " --auth_name=" . $authName);
  }
  ```
  `$authName` is `''` because the devtools wrapper never passes `--authName`
  (`import_ccda.php:73` documents it as "Required if isDev=false"), and neither
  `exec()` return status is checked. Step 1 succeeds, steps 2 and 3 do not, and
  nothing surfaces the failure. Resulting orphan rows:
  ```
  $ ... -e "SELECT id, foreign_id, LEFT(name,46) AS name, date FROM documents ORDER BY id;"
  | 2573 |          0 | Angel97_Dare640_c72a5ee4-6bf5-a49a-86ad-42a872 | 2026-09-14 16:54:03 |
  | 2574 |          0 | Cami698_Abbott774_3c0cef23-2fdb-2d22-65c3-3d7e | 2026-09-14 16:54:03 |
  | 2575 |          0 | Corey514_Hahn503_dd8a5c1a-6560-e8dc-b831-74082 | 2026-09-14 16:54:04 |
  | 2576 |          0 | Pattie91_Laquita685_Turner526_609f7ced-ca46-ac | 2026-09-14 16:54:04 |
  | 2577 |          0 | Tobias236_Wisozk929_b6c0c946-1423-6dc6-8cd8-21 | 2026-09-14 16:54:04 |
  | 2817 |          0 | Annett908_Estrella224_Anderson154_f718a423-c1f | 2026-09-14 18:15:40 |
  | 2818 |          0 | Faustino767_Lebsack687_dd66c7b5-85a3-8181-0d07 | 2026-09-14 18:15:40 |
  ```
  Rows 2817–2818 are from this test. Rows 2573–2577 predate the session; their
  timestamps and filenames match five CCDA files observed stranded in
  `/tmp/synthea/output/ccda` at 16:54, and they carry the identical signature
  (document row, `foreign_id=0`, no patient). That they came from another
  `false`-mode run is **suspected, unconfirmed** — the invocation was not
  observed.
- **Verified:** `first-hand`
- **Implication for the agent:** Exit code 0 and success-shaped log output are
  not evidence of work done anywhere in this codebase. Every ingestion or write
  path the co-pilot depends on must be verified by reading back the resulting
  rows, not by trusting the tool's own report.

### F-16 — Synthea generator/importer count mismatch, unreconciled
- **Category:** Data Quality
- **Severity:** Medium
- **Claim:** A 50-patient generation produced only 48 importable records; two
  patients threw an exception during CCDA export so no file was written, and
  nothing in the pipeline reconciles the generator's count against the
  importer's.
- **Evidence:** operator-reported, quoted verbatim:
  ```
  Synthea generated 50 patients, 48 imported. Two threw
  `java.lang.RuntimeException: Person does not have insurance at time ...` in
  CCDAExporter.export, so no CCDA was written. Generator reports 50, importer
  reports 48, nothing reconciles the gap. 4% silent loss.
  ```
  Not reproduced here — the 5-patient run performed in this session reported
  `Records: total=5, alive=5, dead=0` and `Finished patients import 5`, with no
  loss.
- **Verified:** `operator-reported`
- **Implication for the agent:** The patient population is not a known-complete
  set. The co-pilot must not infer absence of a record from absence in the
  database, and any evaluation set built from this data needs its own count
  reconciliation.

### F-17 — Shipped example patient data is demographics-only
- **Category:** Data Quality
- **Severity:** Informational
- **Claim:** `sql/example_patient_data.sql` contains 14 inserts into
  `patient_data` and nothing else — no encounters, problems, medications or
  results.
- **Evidence:**
  ```
  $ grep -cE '^INSERT' sql/example_patient_data.sql
  14
  $ grep -oE '^INSERT INTO [`a-zA-Z_]+' sql/example_patient_data.sql | sort | uniq -c
    14 INSERT INTO `patient_data`
  $ wc -l sql/example_patient_data.sql
        28 sql/example_patient_data.sql
  ```
- **Verified:** `first-hand`
- **Implication for the agent:** This file is useless as a co-pilot fixture —
  there is no clinical content to reason over. Test fixtures must be built from
  Synthea output or hand-authored.

### F-18 — Demo dataset is 2014-dated, clinically empty, and migrated from 5.0.0
- **Category:** Data Quality
- **Severity:** Medium
- **Claim:** `dev-reset-install-demodata` yields 3 patients and 3 encounters all
  stamped 2014-02-01 with zero lab orders and zero lab results, because it
  imports a 5.0.0-era dump and migrates it forward — leaving every column added
  after 5.0.0 NULL or defaulted.
- **Evidence:** The devtools path (`/root/devtools:454-461`) runs
  `resetOpenemr; installOpenemr; demoData; setGlobalSettings`, and `demoData`
  imports `/root/demo_5_0_0_5.sql` (51,473,078 bytes) then runs
  `upgradeOpenEMR 5.0.0` → `sql_upgrade.php --from=5.0.0`. Measured directly
  after that command completed, earlier in this session:
  ```
  | patients      |  3 |    | encounter | pid  | date                | reason   |
  | encounters    |  3 |    |         5 |    1 | 2014-02-01 00:00:00 | Sad      |
  | lab_orders    |  0 |    |         8 |    2 | 2014-02-01 00:00:00 | toe pain |
  | lab_results   |  0 |    |        12 |    3 | 2014-02-01 00:00:00 | Fever    |
  ```
  Column staleness, same measurement point — `lists` (9 rows) had 10 columns
  100% NULL (`begdate`, `enddate`, `returndate`, `extrainfo`,
  `external_allergyid`, `severity_al`, `external_id`, `list_option_id`, `udi`,
  `udi_data`) and 11 columns 100% empty-string; `prescriptions` (1 row) had 23
  columns 100% NULL; `procedure_result` had 0 rows.
- **Verified:** `first-hand` — measured immediately after
  `dev-reset-install-demodata` and before Synthea seeding superseded these
  numbers. The current database no longer reflects them.
- **Implication for the agent:** Never demo or evaluate the co-pilot on demo
  data alone — three same-day encounters with no labs will make retrieval and
  citation look like they work when neither has been exercised.

### F-19 — Lab results carry values and units but no reference ranges or abnormal flags
- **Category:** Data Quality
- **Severity:** High
- **Claim:** All 7259 lab result rows have a numeric result, units and a LOINC
  code, and **zero** have a reference range or an abnormal flag — though the
  schema supports both.
- **Evidence:**
  ```
  $ docker compose exec -T mysql mariadb -uopenemr -popenemr openemr -t -e "SELECT COUNT(*) total, SUM(\`range\` IS NULL OR \`range\`='') AS range_blank, SUM(abnormal IS NULL OR abnormal='') AS abnormal_blank, SUM(result IS NOT NULL AND result<>'') AS has_result, SUM(units IS NOT NULL AND units<>'') AS has_units, SUM(result_code IS NOT NULL AND result_code<>'') AS has_result_code FROM procedure_result;"
  +-------+-------------+----------------+------------+-----------+-----------------+
  | total | range_blank | abnormal_blank | has_result | has_units | has_result_code |
  +-------+-------------+----------------+------------+-----------+-----------------+
  |  7259 |        7259 |           7259 |       7259 |      7259 |            7259 |
  +-------+-------------+----------------+------------+-----------+-----------------+

  $ ... -e "SELECT result_code, result, units, \`range\`, abnormal FROM procedure_result LIMIT 5;"
  | 6690-2      | 5.934353893528585  | 10*3/uL |       |          |
  | 789-8       | 4.053644759474008  | 10*6/uL |       |          |
  | 718-7       | 14.22571396551373  | g/dL    |       |          |
  | 4544-3      | 48.619486119221584 | %       |       |          |
  | 787-2       | 87.95140740814158  | fL      |       |          |
  ```
  The schema does support them:
  ```
  $ ... -e "SELECT COLUMN_NAME, COLUMN_TYPE, COLUMN_COMMENT FROM information_schema.columns WHERE table_schema='openemr' AND table_name='procedure_result' AND COLUMN_NAME IN ('abnormal','range','result_code');"
  | result_code | varchar(31)  | LOINC code, might match a procedure_type.procedure_code |
  | range       | varchar(255) |                                                        |
  | abnormal    | varchar(31)  | no,yes,high,low                                        |
  ```
- **Verified:** `first-hand`
- **Implication for the agent:** This is the single hardest constraint on the
  co-pilot. It can cite a lab value but cannot justify calling it "normal",
  "high" or "low" from the record — that judgement is not in the data. Either
  the agent refuses to characterise results, or it brings its own reference-range
  source keyed on the LOINC code in `result_code` and cites that source
  separately from the chart.

### F-20 — Vitals recorded once per patient, not per encounter
- **Category:** Data Quality
- **Severity:** Medium
- **Claim:** There are 65 vitals rows against 2800 encounters — one per patient,
  not one per visit.
- **Evidence:** From the census of 2026-09-14T18:25:02Z: `vitals 65`,
  `encounters 2800`, `patients 65`.
- **Correction:** 65 vitals, not 60 — the count tracks the patient count, which
  rose to 65 after this session's 5-patient import. The one-per-patient
  relationship is unchanged.
- **Verified:** `first-hand`
- **Implication for the agent:** The co-pilot cannot show vitals trends over
  time, and must not imply a vitals reading belongs to a specific encounter.
  Any "vitals at this visit" feature has no data behind it.

### F-21 — Allergies present for a minority of patients
- **Category:** Data Quality
- **Severity:** Medium
- **Claim:** 43 allergy rows exist across 65 patients, so most patients have no
  allergy record at all.
- **Evidence:** From the census of 2026-09-14T18:25:02Z: `allergies 43`,
  `patients 65`. Row count is of `lists` where `type='allergy'`; 43 rows may
  cover fewer than 43 distinct patients.
- **Correction:** 43 rows, not 39 — count rose with this session's import.
- **Verified:** `first-hand`
- **Implication for the agent:** "No known allergies" and "allergies were never
  captured" are different clinical statements and this data cannot distinguish
  them. The co-pilot must say "no allergies recorded", never "no known
  allergies", and must not treat an empty allergy list as safe to prescribe
  against.

### F-22 — Outlier patient exceeds any reasonable context window
- **Category:** Data Quality
- **Severity:** High
- **Claim:** Patient `pid=20` has 530 encounters, 92 problems, 907 lab orders
  and 9 prescriptions — roughly 18x the median encounter count — so assembling
  that patient's full record into a prompt is not possible.
- **Evidence:**
  ```
  $ docker compose exec -T mysql mariadb -uopenemr -popenemr openemr -t -e "SELECT (SELECT COUNT(*) FROM form_encounter WHERE pid=20) AS encounters, (SELECT COUNT(*) FROM lists WHERE pid=20 AND type='medical_problem') AS problems, (SELECT COUNT(*) FROM procedure_order WHERE patient_id=20) AS lab_orders, (SELECT COUNT(*) FROM prescriptions WHERE patient_id=20) AS prescriptions;"
  +------------+----------+------------+---------------+
  | encounters | problems | lab_orders | prescriptions |
  +------------+----------+------------+---------------+
  |        530 |       92 |        907 |             9 |
  +------------+----------+------------+---------------+

  $ ... -e "SELECT MIN(c) mn, MAX(c) mx, ROUND(AVG(c),1) avg_c FROM (SELECT pid, COUNT(*) c FROM form_encounter GROUP BY pid) x;"
  |    1 |  530 |  43.1 |

  median encounters/patient = 29.0000
  ```
- **Correction:** problems = **92**, not 101. Encounters, lab orders and
  prescriptions confirmed as stated. Measured median is **29**, so 530 is ~18x
  median (the brief said ~20x); range across patients is 1 to 530.
- **Verified:** `first-hand`
- **Implication for the agent:** Whole-chart prompting is not a viable design at
  any patient volume. The co-pilot needs retrieval — time-windowed,
  problem-scoped or query-relevant selection — plus a hard token budget and a
  deterministic truncation policy that tells the user what was omitted.

### F-23 — Encounter dates span more than a century
- **Category:** Data Quality
- **Severity:** Low
- **Claim:** Encounter dates run from 1922-11-04 to 2026-09-12, including
  future-dated records relative to the audit date.
- **Evidence:**
  ```
  $ docker compose exec -T mysql mariadb -uopenemr -popenemr openemr -t -e "SELECT MIN(date) mn, MAX(date) mx FROM form_encounter;"
  +---------------------+---------------------+
  | mn                  | mx                  |
  +---------------------+---------------------+
  | 1922-11-04 18:15:55 | 2026-09-12 16:42:47 |
  +---------------------+---------------------+
  ```
- **Verified:** `first-hand`
- **Implication for the agent:** Relative date reasoning ("recent", "last
  year") must be computed against the encounter date, not assumed to fall in a
  plausible window. Date parsing and sorting must handle a 104-year span without
  overflow or two-digit-year ambiguity.

---

## PERFORMANCE

### F-24 — FHIR Patient read baseline: 0.375s under ideal conditions
- **Category:** Performance
- **Severity:** Informational
- **Claim:** A 5-record FHIR Patient query returned 200 in 0.375s locally on
  native arm64 with a single user, no contention and no LLM in the path — this
  is the performance floor, not a typical figure.
- **Evidence:** operator-reported, quoted verbatim:
  ```
  FHIR Patient?_count=5 → 200 in 0.375s. Local, native arm64, single user,
  no contention, no LLM. This is the floor.
  ```
- **Verified:** `operator-reported`
- **Implication for the agent:** A co-pilot turn that makes several FHIR calls
  before even reaching the model starts from a multi-second floor. Data
  retrieval must be parallelised and cached per turn, and the UI needs to stream
  or show progress rather than block.

### F-25 — Lab join drives from a full scan of `procedure_report`
- **Category:** Performance
- **Severity:** Medium
- **Claim:** The three-table lab join filtered by `patient_id` does not seek by
  patient first — the optimiser drives from `procedure_report` with a full table
  scan, then joins back to `procedure_order`, despite `procedure_order` being
  indexed on `patient_id`.
- **Evidence:**
  ```
  $ docker compose exec -T mysql mariadb -uopenemr -popenemr openemr -t -e "
    EXPLAIN SELECT po.procedure_order_id, pr.date_report, prs.result_code, prs.result, prs.units
    FROM procedure_order po
    JOIN procedure_report pr ON pr.procedure_order_id = po.procedure_order_id
    JOIN procedure_result prs ON prs.procedure_report_id = pr.procedure_report_id
    WHERE po.patient_id = 20;"
  +------+-------------+-------+--------+----------------------------+---------------------+---------+-------------------------------------+------+-------------+
  | id   | select_type | table | type   | possible_keys              | key                 | key_len | ref                                 | rows | Extra       |
  +------+-------------+-------+--------+----------------------------+---------------------+---------+-------------------------------------+------+-------------+
  |    1 | SIMPLE      | pr    | ALL    | PRIMARY,procedure_order_id | NULL                | NULL    | NULL                                |  596 | Using where |
  |    1 | SIMPLE      | po    | eq_ref | PRIMARY,patient_id         | patient_id          | 16      | const,openemr.pr.procedure_order_id |    1 | Using index |
  |    1 | SIMPLE      | prs   | ref    | procedure_report_id        | procedure_report_id | 8       | openemr.pr.procedure_report_id      |    1 |             |
  +------+-------------+-------+--------+----------------------------+---------------------+---------+-------------------------------------+------+-------------+
  ```
  `procedure_order` is indexed on `patient_id` and carries a composite
  `datepid(date_ordered, patient_id)`:
  ```
  $ ... -e "SHOW INDEX FROM procedure_order;"
  | procedure_order |          1 | datepid            |            1 | date_ordered       |
  | procedure_order |          1 | datepid            |            2 | patient_id         |
  | procedure_order |          1 | patient_id         |            1 | patient_id         |
  ```
- **Correction:** the brief described `procedure_report` as "scanning 561 index
  rows". The optimiser reports `type: ALL` with `key: NULL` — a **full table
  scan**, not an index scan — across **596** rows, not 561. This makes the
  finding stronger than stated, and the conclusion unchanged: the indexes exist,
  so this is a query-shape problem, not a missing-index problem.
- **Verified:** `first-hand`
- **Implication for the agent:** The co-pilot must not issue this join shape for
  lab retrieval. Select `procedure_order` by `patient_id` (optionally with
  `datepid` for a date window) into a narrow set first, then join reports and
  results to that — otherwise lab lookups degrade linearly with total lab volume
  rather than with the patient's own.

### F-26 — readyz loads the full framework on every probe
- **Category:** Performance
- **Severity:** Low
- **Claim:** The readiness endpoint bootstraps all of OpenEMR on every probe
  request, a cost its own source comment acknowledges.
- **Evidence:** `meta/health/index.php:52-58`:
  ```php
  if ($isInstalled) {
      // Bootstrap OpenEMR globals (this sets up database connection)
      // NOTE: This loads the full OpenEMR framework on each probe request.
      // For high-frequency probes, a lighter bootstrap path that only
      // initializes the database connection could improve performance.
      //
      // Skip authentication - health checks must work without a session
  ```
  Measured:
  ```
  $ for i in 1 2 3; do curl -sk -o /dev/null -w "run$i: %{time_total}s\n" https://localhost:9300/meta/health/readyz; done
  run1: 0.173452s
  run2: 0.143955s
  run3: 0.124710s
  $ for i in 1 2; do curl -sk -o /dev/null -w "livez run$i: %{time_total}s\n" https://localhost:9300/meta/health/livez; done
  livez run1: 0.139672s
  livez run2: 0.089061s
  ```
- **Note on magnitude:** at this data scale the measured gap between the
  full-bootstrap `readyz` and the minimal `livez` is small (tens of
  milliseconds) and overlaps across runs. The framework load is real per the
  source comment; the performance penalty is not demonstrated to be large here.
- **Verified:** `first-hand`
- **Implication for the agent:** If the co-pilot adds its own health or
  readiness endpoint, it should not copy this pattern — a probe that boots the
  whole framework will not stay cheap as the deployment grows. Low priority.

---

## ARCHITECTURE

### F-27 — Dual codebase: modern PSR-4 alongside legacy procedural
- **Category:** Architecture
- **Severity:** Informational
- **Claim:** The codebase runs two generations of code and two template engines
  side by side: `/src` is PSR-4 under the `OpenEMR\` namespace while `/library`
  is legacy procedural, with Laminas MVC plus Symfony components, Twig 3.x
  alongside legacy Smarty 4.5, and Doctrine DBAL 4.x presenting an ADODB surface
  API to legacy callers.
- **Evidence:** Documentation-sourced, from `CLAUDE.md` ("Technology Stack" and
  "Project Structure"). Directory existence confirmed first-hand: `src/` (36
  entries), `library/` (88), `templates/` (32), `interface/` (46).
- **Verified:** `first-hand` for directory structure; the framework/version
  claims are documentation-sourced from `CLAUDE.md`, not independently verified
  against `composer.json`.
- **Implication for the agent:** Co-pilot code belongs in `/src` under PSR-4
  with strict types, but it must interoperate with legacy callers that expect
  globals and ADODB-style results. Plan an explicit boundary layer rather than
  letting legacy patterns leak into new code.

### F-28 — Integration points relevant to this project all exist
- **Category:** Architecture
- **Severity:** Informational
- **Claim:** Every integration surface the co-pilot needs — FHIR services, REST
  controllers, the ACL layer, the event dispatcher, and a custom-module
  precedent for UI — is present in the tree.
- **Evidence:**
  ```
  $ ls src/Services/FHIR | wc -l      → 64   (Condition, DiagnosticReport, Document, DocumentReference, Enum, …)
  $ ls src/RestControllers | wc -l    → 33   (AllergyIntoleranceRestController.php, ApiApplication.php,
                                              AppointmentRestController.php, Authorization/, AuthorizationController.php, …)
  $ ls gacl | wc -l                   → 14
  $ ls src/Common/Acl                 → AccessDeniedException.php, AccessDeniedHelper.php,
                                        AccessDeniedResponseFormat.php, AclExtended.php, AclMain.php
  $ ls src/Events | wc -l             → 24   (AbstractBoundFilterEvent.php, Appointments/, Billing/, BoundFilter.php,
                                              CDA/, Codes/, Command/, Core/, …)
  $ ls interface/modules/custom_modules/
  README.md
  oe-module-claimrev-connect      oe-module-comlink-telehealth
  oe-module-dashboard-context     oe-module-dorn
  oe-module-ehi-exporter          oe-module-faxsms
  oe-module-prior-authorizations  oe-module-weno
  ```
- **Verified:** `first-hand`
- **Implication for the agent:** No new integration mechanism needs inventing.
  The co-pilot UI panel should follow the `oe-module-*` convention in
  `interface/modules/custom_modules/`, hook into `src/Events/` via the Symfony
  dispatcher rather than patching core files, read through `src/Services/FHIR/`,
  and gate every access through `src/Common/Acl/AclMain.php` rather than
  implementing its own permission checks.

### F-29 — Container health is independent of application readiness
- **Category:** Architecture
- **Severity:** Medium
- **Claim:** `docker compose up --wait` reported the container healthy while
  OpenEMR was not installed and readyz was returning `setup_required`.
- **Evidence:** operator-reported, quoted verbatim:
  ```
  Container reported Healthy by `docker compose up --wait` while OpenEMR was
  NOT installed; readyz returned status:setup_required. Container liveness and
  application readiness are independent.
  ```
- **Verified:** `operator-reported`
- **Implication for the agent:** The co-pilot's own startup must not treat
  container health as a signal that the EHR is usable. It needs an explicit
  application-level readiness gate — and given F-30, that gate cannot be the
  `installed` field.

### F-30 — `readyz` can never report `installed: true`
- **Category:** Architecture
- **Severity:** Medium
- **Claim:** The readiness endpoint's own bootstrap destroys the value its
  installation check reads, so `installed` reports `false` on a correctly
  installed system and `status` is permanently `setup_required`.
- **Evidence:** `meta/health/index.php:43-50` loads sqlconf and gates on it:
  ```php
  $sqlconfPath = __DIR__ . "/../../sites/{$siteId}/sqlconf.php";
  if (file_exists($sqlconfPath)) {
      require_once $sqlconfPath;
  }
  ...
  global $config;
  $isInstalled = isset($config) && $config === 1;
  ```
  That gate passes (`$config === 1`), so it proceeds to load
  `interface/globals.php`, which at `:384` requires `library/sql.inc.php`, whose
  line 59 reassigns the same global at file scope:
  ```php
  $config = DatabaseConnectionOptions::forSite($GLOBALS['OE_SITE_DIR']);
  ```
  `src/Health/Check/InstallationCheck.php` then reads it back at `:32` and
  compares strictly at `:42`:
  ```php
  global $config;
  ...
  if ($config !== 1) {
  ```
  `$config` is by then a `DatabaseConnectionOptions` object, so the comparison
  fails. Observed on a fully installed system with 284 tables and 65 patients:
  ```
  $ curl -sk https://localhost:9300/meta/health/readyz
  {"status":"setup_required","checks":{"installed":false,"database":true,"filesystem":true,"session":true,"oauth_keys":true,"cache":true}}
  ```
- **Verified:** `first-hand`
- **Implication for the agent:** The co-pilot must not gate startup, health
  checks or deployment automation on `readyz`'s `installed` field or top-level
  `status`. The sibling checks (`database`, `filesystem`, `session`,
  `oauth_keys`, `cache`) are meaningful and can be used; readiness itself needs
  an independent probe such as a table or row count.

---

## Verification gaps

Recorded so that absence of evidence is not later read as evidence of absence.

1. **Audit-log comparison set — blocked.** The audited-import path
   (`import-random-patients N false`) silently no-ops (F-15), so no audited
   import was ever completed. `audit_master` and `audit_details` are both 0
   rows. Whether audited import works at all is **unestablished**, not disproven.
2. **Origin of `documents` rows 2573–2577 — narrowed, unconfirmed.** Timestamps
   (2026-09-14 16:54:03–04) and filenames match five CCDA files observed
   stranded in `/tmp/synthea/output/ccda`, and the row signature is identical to
   F-15's. A prior `false`-mode run is **suspected, unconfirmed** — the
   invocation was not observed and no log of it was retained.
3. **The three `invalid_client` failure modes — not exercised.** F-02's claim
   that disabled-client, confidential-client and missing-grant failures all
   surface as the same opaque `invalid_client` is operator-reported. No token
   exchange was performed in this session, so the three causes were not
   individually distinguished.
4. **F-16's 4% generation loss — not reproduced.** The 5-patient run performed
   here reported 5 generated and 5 imported with no loss. The 50/48 gap and the
   `Person does not have insurance` exception are operator-reported only.
5. **F-09's token/JWT scope mismatch — not reproduced.** No token was minted
   first-hand, so neither the token response body nor the decoded JWT claims
   were inspected directly.
6. **F-27's framework and version claims — documentation-sourced.** Laminas MVC,
   Symfony components, Twig 3.x, Smarty 4.5 and Doctrine DBAL 4.x are taken from
   `CLAUDE.md` and were not verified against `composer.json` or the installed
   `vendor/` tree.
7. **F-21 counts rows, not patients.** 43 allergy rows across 65 patients; the
   number of distinct patients with at least one allergy record was not
   computed, so "most patients have none" is an inference from the row count.
