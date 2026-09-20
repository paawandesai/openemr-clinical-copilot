# Stage 3 Instrumentation — Raw Findings Ledger

Evidence file. `AUDIT.md` will be written from this; this is not that document.
One entry per finding, no summary, no narrative.

**System state at time of audit:** OpenEMR 8.2.0-dev (`v_database` 541), 284
tables, `docker/development-easy` stack (mysql, openemr, phpmyadmin only).

**Pass 2 (2026-09-14T22:20Z – 2026-09-15T00:12Z):** adds the deployed droplet as
a second audited environment, entries F-31–F-53, and in-place corrections
marked **Correction (pass 2):** or **Cross-check (pass 2):**. All pass-2 SSH
access to the droplet was as `root@201.79.25.22` from the operator workstation
(egress IP `192.63.199.50`, confirmed by matching this session's own probe
requests in the droplet access log at 22:24:35Z). All timestamps are UTC unless
stated; droplet host clock, MariaDB (`@@system_time_zone` UTC) and PHP
(`date_default_timezone_get()` UTC) were confirmed to agree.

| | Local | Droplet |
|---|---|---|
| URL | `https://localhost:9300/` | `https://201.79.25.22:9300/` (also `http://201.79.25.22:8300/`) |
| Commit checked out | `c4db1578b53293611665d9190fe13f1458312590` | `c4db1578b53293611665d9190fe13f1458312590` (clean tree, 0 untracked; see F-53) |
| `README.md:11` | `**Deployed URL:** TBD` | `**Deployed URL:** TBD` (same commit — the README does not yet record the URL) |
| Host | Colima profile `default`: 6 CPU, 12 GiB, aarch64, on Apple M5 Pro | DigitalOcean Droplet: 4 vCPU AMD EPYC 9555P (KVM), 12,248,264 kB RAM, 48 G disk, Ubuntu 24.04.4, metadata region `mem1` |
| Stack | `docker/development-easy`, PHP 8.5.6 | same compose file, same image digests, PHP 8.5.6 (see F-48) |

**Data census re-run 2026-09-14T18:25:02Z** and used throughout pass-1 entries:

```
patients 65 · encounters 2800 · problems 1669 · allergies 43 · med_list 309
prescriptions 305 · lab_orders 7417 · lab_results 7259 · vitals 65
appointments 11 · documents 7 · log 2295 · audit_master 0 · audit_details 0
```

Counts differ from earlier instrumentation notes because a 5-patient Synthea
import was run during this session (60→65 patients, 2561→2800 encounters,
6833→7259 lab results). Entries carrying corrected figures are marked
**Correction:**.

**Census re-run in pass 2 against both databases with identical SQL** —
local 2026-09-14T22:31:14Z, droplet 2026-09-14T22:31:16Z. Every local figure
reproduces the 18:25:02Z census exactly.

| Label | Definition (as run) | Local | Droplet |
|---|---|---|---|
| patients | `COUNT(*) FROM patient_data` | 65 | 53 |
| encounters | `form_encounter` | 2800 | 3331 |
| problems | `lists WHERE type='medical_problem'` | 1669 | 2384 |
| allergies | `lists WHERE type='allergy'` | 43 | 59 |
| med_list | `lists WHERE type='medication'` | 309 | 377 |
| prescriptions | `prescriptions` | 305 | 373 |
| lab_orders ⚠ | `procedure_order` (**all** order types) | 7417 | 11250 |
| — of which `procedure_order_type='laboratory_test'` | | 596 | 1177 |
| lab_reports | `procedure_report` | 596 | 1177 |
| lab_results | `procedure_result` | 7259 | 29701 |
| vitals | `form_vitals` | 65 | 53 |
| immunizations | `immunizations` | 934 | 806 |
| appointments | `openemr_postcalendar_events` | 11 | 11 |
| documents | `documents` | 7 | 0 |
| log | `log` | 2295 | 2305 |
| audit_master | `audit_master` | 0 | 0 |
| patients with ≥1 allergy row | `COUNT(DISTINCT pid) FROM lists WHERE type='allergy'` | 14 | 9 |
| patients with ≥1 vitals row | `COUNT(DISTINCT pid) FROM form_vitals` | 65 | 53 |

**Correction (pass 2) to the census label:** `lab_orders` counts every
`procedure_order` row. Only 596 (local) / 1177 (droplet) are laboratory orders;
the rest are Synthea procedure orders with no report — see F-42. The
`log` count has since risen locally to 2321 because this session's API probes
wrote rows (see "Audit side-effects of pass 2").

All pass-1 database commands below were run from `docker/development-easy/`.
Pass-2 droplet database commands were run as
`ssh root@201.79.25.22 'docker exec -i development-easy-mysql-1 mariadb … openemr'`.

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
- **Cross-check (pass 2):** the running droplet container carries all three
  variables in its environment (`docker exec … printenv` listed the names
  `GITHUB_COMPOSER_TOKEN`, `GITHUB_COMPOSER_TOKEN_ENCODED`,
  `GITHUB_COMPOSER_TOKEN_ENCODED_ALTERNATE`; values not printed). The pattern
  is therefore live on an internet-facing host, not only in git. `first-hand`.
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
- **Cross-check (pass 2):** OpenEMR's own `log` table records the registration
  INSERTs with their bound values. Decoding `comments` (base64 SQL) shows every
  registration on both hosts inserted `grant_types='authorization_code'` —
  local log ids 2118 (16:49:14), 2224 (17:38:20), 2233 (17:40:34); droplet ids
  2118 (21:10:43), 2196 (22:12:17). On the droplet **both** current rows are
  `is_enabled=1` with `authorization_code,password,refresh_token`, and on
  neither host does any `log` row reference an `UPDATE` to `oauth_clients`. The
  as-registered defaults claimed above are thus confirmed on a second
  environment; the enabling was done by a path that writes nothing to the
  audit log (F-40). `first-hand`.
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
- **Cross-check (pass 2):** same layout on the droplet — `oaprivate.key`
  (1886 B, `-rw-r-----`) and `oapublic.key` (451 B, `-rw-rw----`) created
  21:10, i.e. at the first OAuth registration (21:10:43), not at install
  (20:51:57). `first-hand`.
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
- **Cross-check (pass 2):** the forged-token row was reproduced first-hand on
  both hosts:
  ```
  $ curl -sk -H 'Authorization: Bearer garbage' -w '\nHTTP %{http_code} total=%{time_total}\n' "https://localhost:9300/apis/default/fhir/Patient?_count=5"
  {"error":"An error occurred","message":"The resource owner or authorization server denied the request.","code":0}
  HTTP 401 total=0.183900
  $ (same against https://201.79.25.22:9300)
  {"error":"An error occurred","message":"The resource owner or authorization server denied the request.","code":0}
  HTTP 401 total=0.328752
  ```
  The scope-refusal path was traced in source (see F-08 correction and F-51):
  `src/RestControllers/Subscriber/AuthorizationListener.php:189-192` throws
  `AccessDeniedException` when `requestHasScopeEntity()` is false. The
  in-scope 200 and the two out-of-scope 401s remain unreproduced (no token).
- **Verified:** `operator-reported` (overall). Forged-token refusal and the
  enforcement code path are `first-hand`.
- **Implication for the agent:** The co-pilot cannot widen its own data access
  by reusing a narrow token — scope must be requested up front for every
  resource type it intends to read. Scope enforcement is a real boundary and can
  be relied on as one.

### F-08 — Out-of-scope and invalid-token responses share a status code but not a body
- **Category:** Security
- **Severity:** Low
- **Claim (as corrected in pass 2):** A request refused for insufficient scope
  returns 401 — the same status as a request bearing a garbage token, and never
  the RFC 6750 403 — but the response **bodies differ**: out-of-scope yields
  `"message":"Unauthorized"`, an invalid token yields
  `"message":"The resource owner or authorization server denied the request."`.
  The two failures can therefore be told apart, but only by string-matching
  the message.
- **Evidence:** operator-reported. From the F-07 probe set, the scoped-out
  resources and the forged token produce the same status:
  ```
    MedicationRequest?patient=20  → 401 {"error":"An error occurred","message":"Unauthorized","code":0}
    Observation?patient=20        → 401
    Bearer garbage                → 401
  ```
  Per RFC 6750 an insufficient-scope refusal would be 403; no `403` was observed
  in the probe set.
- **Correction (pass 2):** the original claim said the two cases return "the
  same status **and body**" and "cannot be told apart". The operator's probe
  set only showed a body for the out-of-scope case. First-hand, a forged bearer
  token (and no token at all) returns
  `{"error":"An error occurred","message":"The resource owner or authorization server denied the request.","code":0}`
  on both hosts (commands in F-07 cross-check). Source confirms two distinct
  messages reach the same status:
  - Out of scope: `AuthorizationListener.php:192` throws
    `AccessDeniedException` → caught at `src/Common/Http/HttpRestRouteHandler.php:99`
    → rethrown at `:112` as `new HttpException(Response::HTTP_UNAUTHORIZED, "Unauthorized", $exception)`.
  - Invalid token: `src/RestControllers/Authorization/BearerTokenAuthorizationStrategy.php:342-344`
    catches `OAuthServerException` and throws `new HttpException(401, $exception->getMessage(), $exception)`;
    the message text is league/oauth2-server's
    (`vendor/league/oauth2-server/src/Exception/OAuthServerException.php:244`
    inside the container's vendor volume; `league/oauth2-server` 8.5.5 per `composer.lock`).
  - Both are rendered by `src/RestControllers/Subscriber/ExceptionHandlerListener.php:44-45`
    (status from `HttpException::getStatusCode()`) and `:51` (`'error' => 'An error occurred'`),
    with `'message' => $exception->getMessage()`.
  Status-code equality and the absence of 403 stand; body equality does not.
- **Verified:** `operator-reported` (out-of-scope responses). Invalid-token
  responses and both source paths are `first-hand`.
- **Implication for the agent:** The co-pilot must not treat a 401 as "token
  expired, refresh and retry" — an infinite refresh loop against a
  permanently-out-of-scope resource is the failure mode. It needs its own record
  of which scopes it holds and must check that before requesting a resource.
  The message string can serve as a secondary discriminator, but it is
  library-owned text and should not be the primary control.

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

### F-31 — Default `admin`/`pass` credential was live on the public internet for 1 h 22 min 21 s
- **Category:** Security
- **Severity:** High
- **Claim:** On the droplet, the OpenEMR `admin` account accepted the default
  password `pass` from the moment Apache began serving at
  **2026-09-14T20:51:58Z** until the password was changed at
  **2026-09-14T22:14:19Z** — a window of **4,941 s (1 h 22 min 21 s)** —
  on ports reachable from the internet (F-33), including plaintext HTTP 8300.
- **Evidence:**
  - Default credential source: `docker/development-easy/docker-compose.yml:66-67`
    `OE_USER: admin` / `OE_PASS: pass`; the container env on the droplet shows
    `OE_USER=admin` and an `OE_PASS` variable (value not printed).
  - Start of window — droplet container log (`docker logs -t development-easy-openemr-1`,
    json-file driver, first line is the entrypoint at 20:49:47, so the log is complete):
    ```
    2026-09-14T20:51:29.679083661Z Running quick setup!
    2026-09-14T20:51:57.339428643Z OpenEMR configured.
    2026-09-14T20:51:57.719404307Z Setup Complete!
    2026-09-14T20:51:58.832066520Z Starting apache!
    2026-09-14T20:51:58.953860691Z [...] AH00163: Apache/2.4.67 (Unix) OpenSSL/3.5.7 configured -- resuming normal operations
    ```
    A demo-data reset ran at ~21:07 (readyz logged an SQL error at 21:07:11;
    demo users' `last_update_password` is 21:07:29) and reinstalled with the
    same `OE_PASS`.
  - End of window:
    ```
    users_secure: admin | last_update_password 2026-09-14 22:14:19 | last_update 2026-09-14 22:14:19
    access log:   2026-09-14T22:14:19.947969309Z 192.63.199.50 - - [14/Sep/2026:22:14:19 +0000] "POST /interface/usergroup/user_info_ajax.php HTTP/1.1" 200 65
    ```
    This is the only `user_info_ajax.php` request in the full access log.
  - The change moved away from `pass` — offline `password_verify()` inside the
    droplet container against stored hashes (no login attempted, hashes never
    printed; script reads `users_secure` over the Docker network):
    ```
    admin  current hash          → does not verify 'pass'
    admin  password_history1     → verifies 'pass'
    admin  password_history2..4  → empty
    ```
    (Local, for contrast: `admin` current hash verifies `'pass'`.)
  - Application logins during/after the window (OpenEMR `log`, comments base64-decoded):
    ```
    2026-09-14 22:13:12 login ''       success=0  "failure: 192.63.199.50. empty username or password"
    2026-09-14 22:13:22 login testuser success=0  "failure: 192.63.199.50. user not found"
    2026-09-14 22:13:52 login admin    success=1  "success: 192.63.199.50"
    ```
    These are the only login events dated 2026; the others are 2014/2017 rows
    from the imported demo dump. The successful login used port 9300 (Referer
    `https://201.79.25.22:9300/interface/login/login.php`).
  - Lower bound corroboration: the first request from outside the container
    was `2026-09-14T21:06:33Z 192.63.199.50 "GET /meta/health/readyz"`, proving
    public reachability by then from at least the operator's network.
- **Verified:** `first-hand` — window endpoints, credential state before and
  after, and login events all re-derived this session from droplet artifacts.
  Whether any *other* party used the credential is addressed in F-39.
- **Implication for the agent:** Every fresh deployment of this stack is born
  with a known admin credential and serves it to the internet within ~2 minutes
  of container start. Co-pilot deployment automation must set credentials
  *before* publishing ports, and must treat any instance that was ever reachable
  with defaults as potentially accessed.

### F-32 — Five demo accounts with username-as-password remain live on the internet, one in Administrators (ongoing)
- **Category:** Security
- **Severity:** High (exposure ongoing at 2026-09-15T00:12:00Z)
- **Claim:** The demo-data install created five active OpenEMR users whose
  password equals their username. They were not changed when `admin` was.
  `physician` and `clinician` are authorized providers, and `zhportal` is in the
  gacl **Administrators** group. The accounts have been exposed since
  ~21:07:29Z and still were at the last check.
- **Evidence:** offline `password_verify()` against stored hashes (no login
  attempted; hashes never printed), re-run 2026-09-14T22:47:38Z and
  2026-09-15T00:12:00Z with identical output:
  ```
  admin         active=1 authorized=1 algo=$2y$ default_match=none
  accountant    active=1 authorized=0 algo=$2a$ default_match=username-as-password
  clinician     active=1 authorized=1 algo=$2a$ default_match=username-as-password
  physician     active=1 authorized=1 algo=$2a$ default_match=username-as-password
  receptionist  active=1 authorized=0 algo=$2a$ default_match=username-as-password
  zhportal      active=1 authorized=0 algo=$2a$ default_match=username-as-password
  ```
  ACL group membership (`gacl_aro` → `gacl_groups_aro_map` → `gacl_aro_groups`):
  ```
  admin → admin (id 11, "Administrators")   zhportal → admin (id 11, "Administrators")
  accountant → back   clinician → clin   physician → doc   receptionist → front
  ```
  Login gates that would otherwise block these accounts are off:
  ```
  password_expiration_days 0 · password_grace_time 0 · password_max_failed_logins 20
  secure_password '' · login_into_facility 0
  ```
  `users_secure.last_update_password` for all five is `2026-09-14 21:07:29`
  (unchanged at 00:12Z). The login page is served publicly on 8300 and 9300
  (F-33). The same five accounts with the same passwords exist locally.
- **Verified:** `first-hand` for stored-credential state, group membership,
  global settings and reachability of the login page. **Not verified:** that an
  interactive login as these users succeeds end-to-end — deliberately not
  attempted (it would create a live session as a clinical user on the exposed
  host).
- **Implication for the agent:** Changing `admin` alone does not secure a demo-
  seeded instance. Any environment the co-pilot is evaluated on must enumerate
  *all* `users_secure` rows and verify none match a known default before it is
  exposed. Evaluation traffic should never run under shared demo identities,
  because audit attribution (F-11) becomes meaningless.

### F-33 — Docker-published ports bypass UFW; all four service ports are internet-reachable
- **Category:** Security
- **Severity:** High
- **Claim:** The droplet's UFW policy allows only 22/80/443, but the Compose
  stack publishes 8300 (HTTP), 9300 (HTTPS), 8310 (phpMyAdmin) and 8320
  (MariaDB) on `0.0.0.0`/`[::]`. Docker's DNAT rules take effect before UFW, no
  `DOCKER-USER` filter exists, and no upstream cloud firewall is filtering, so
  all four ports are reachable from the internet.
- **Evidence:**
  - Compose binds without a host address: `docker-compose.yml:7`
    `- "${WT_MYSQL_PORT:-8320}:3306"`, `:31` `- "${WT_HTTP_PORT:-8300}:80"`,
    `:32` `- "${WT_HTTPS_PORT:-9300}:443"`, `:150` `- "${WT_PMA_PORT:-8310}:80"`.
  - Droplet `docker ps`:
    ```
    development-easy-phpmyadmin-1   0.0.0.0:8310->80/tcp, [::]:8310->80/tcp
    development-easy-openemr-1      0.0.0.0:8300->80/tcp, [::]:8300->80/tcp, 0.0.0.0:9300->443/tcp, [::]:9300->443/tcp
    development-easy-mysql-1        0.0.0.0:8320->3306/tcp, [::]:8320->3306/tcp
    ```
  - Firewall state on the droplet:
    ```
    $ ufw status verbose
    Status: active
    Default: deny (incoming), allow (outgoing), deny (routed)
    22/tcp (OpenSSH) ALLOW IN Anywhere · 80/tcp ALLOW IN Anywhere · 443/tcp ALLOW IN Anywhere  (+ v6)
    $ iptables -S DOCKER-USER
    -N DOCKER-USER
    $ iptables -t nat -S DOCKER | grep -E "dport (8300|9300|8310|8320)"
    -A DOCKER ! -i br-6b2b9346cdfc -p tcp -m tcp --dport 8320 -j DNAT --to-destination 172.18.0.2:3306
    -A DOCKER ! -i br-6b2b9346cdfc -p tcp -m tcp --dport 8300 -j DNAT --to-destination 172.18.0.3:80
    -A DOCKER ! -i br-6b2b9346cdfc -p tcp -m tcp --dport 9300 -j DNAT --to-destination 172.18.0.3:443
    -A DOCKER ! -i br-6b2b9346cdfc -p tcp -m tcp --dport 8310 -j DNAT --to-destination 172.18.0.4:80
    ```
  - External probe from the operator workstation (22:24Z, re-run 00:12Z for 8300–8320):
    ```
    tcp/22 open · tcp/80 closed/filtered · tcp/443 closed/filtered
    tcp/8300 open · tcp/9300 open · tcp/8310 open · tcp/8320 open
    8300 login.php http=200 · 9300 login.php http=200 · 8310 → <title>phpMyAdmin</title>
    ```
  - No upstream cloud firewall: the droplet kernel log (current boot) holds
    **424** `UFW BLOCK` entries from **338** distinct source IPs to arbitrary
    ports between 20:25:27Z and 22:43:35Z (top: DPT=25565 ×9, 23 ×9, 5060 ×5,
    53 ×4, 1433 ×4). DigitalOcean Cloud Firewalls drop unlisted inbound traffic
    before it reaches the droplet, so unsolicited scans arriving at UFW mean
    none is in place. UFW logging is `low` (rate-limited), so these are lower
    bounds.
  - Direct third-party reach: the OpenEMR access log contains
    `2026-09-14T23:43:58Z 45.79.207.252 - - "GET / HTTP/1.0" 400 402 "-" "-"`
    (whois: `LINODE-US`, Akamai Technologies) — a non-operator internet host
    reaching the published Apache. The log format does not record which port.
  - Local environment: Colima's SSH mux (`ssh: …/.colima/_lima/colima/ssh.sock [mux]`,
    PID 23076) listens on `*:8300`, `*:8310`, `*:8320`, `*:9300` on the
    workstation — all interfaces, not loopback. macOS Application Firewall
    reports `enabled (State = 1)`.
- **Verified:** `first-hand` for bindings, rule sets, operator-vantage
  reachability, UFW block statistics and the 45.79.207.252 request.
  Reachability of *local* ports from another LAN host was **not tested**.
- **Implication for the agent:** Host firewall configuration is not evidence of
  exposure control on a Docker host. Any co-pilot service port must bind to
  `127.0.0.1` (or sit behind `DOCKER-USER` rules / a cloud firewall) and be
  verified from an external vantage point, never by reading `ufw status`.

### F-34 — Database reachable from the internet with default `root`/`root`; its TLS private keys are committed
- **Category:** Security
- **Severity:** High
- **Claim:** MariaDB (tcp/8320) and phpMyAdmin (tcp/8310) — both
  internet-reachable (F-33) — accept the Compose defaults: `root@'%'` with
  password `root` and `openemr@'%'` with password `openemr`. Plaintext
  connections are permitted, and the TLS CA and server private keys MariaDB
  uses are tracked in git.
- **Evidence:**
  - Defaults: `docker-compose.yml:14` `MYSQL_ROOT_PASSWORD: root`, `:63`
    `MYSQL_ROOT_PASS: root`, `:64-65` `MYSQL_USER: openemr` / `MYSQL_PASS: openemr`;
    phpMyAdmin `:152` `PMA_HOSTS: mysql`.
  - Account hosts and password equality, checked inside the droplet DB without
    printing hashes:
    ```
    SELECT user, host FROM mysql.user;
    openemr %  · root %  · healthcheck 127.0.0.1/::1/localhost · mariadb.sys localhost · root localhost
    SELECT user, host, authentication_string = PASSWORD("root") AS pw_is_root FROM mysql.user WHERE user="root";
    root localhost 1 · root % 1
    SELECT user, host, authentication_string = PASSWORD("openemr") AS pw_is_openemr FROM mysql.user WHERE user="openemr";
    openemr % 1
    SELECT @@have_ssl, @@require_secure_transport;   → YES  0
    ```
  - TLS key material in git: `docker-compose.yml:9-11` mounts
    `../library/sql-ssl-certs-keys/easy/{ca.pem,server-cert.pem,server-key.pem}`;
    `git ls-files docker/library/sql-ssl-certs-keys/easy/` lists `ca-key.pem`,
    `ca.pem`, `client-cert.pem`, `client-key.pem`, `client-req.pem`,
    `server-cert.pem`, `server-key.pem`, `server-req.pem`; `server-key.pem`
    begins `-----BEGIN RSA PRIVATE KEY-----`.
  - Observed external DB activity: MariaDB `log_warnings=2`, `general_log=0`.
    Counters at `Uptime` 5869 s: `Aborted_connects 3`, `Access_denied_errors 2`,
    `Connections 571`. Every access-denied warning is internal
    (`'openemr'@'172.18.0.3'` at 20:51:29 and 21:06:51); the only external
    aborted connection is this session's own port probe
    (`host: '192.63.199.50'` at 22:24:34, re-checked at 00:11Z). phpMyAdmin's
    container log holds one request in total, from this session's probe.
- **Verified:** `first-hand`. No remote database or phpMyAdmin login was
  attempted. Successful external connections would **not** be logged under
  `general_log=0`, so absence of access cannot be established.
- **Implication for the agent:** The co-pilot must assume the database of any
  environment built from this compose file is reachable and trivially
  credentialed until proven otherwise. It must never connect to MariaDB as
  `root`, and any PHI it caches must not live in this database under these
  settings.

### F-35 — Xdebug step debugger is trigger-activatable by any internet client
- **Category:** Security
- **Severity:** High
- **Claim:** The internet-facing Apache/mod_php runtime on the droplet loads
  Xdebug 3.5.0 with `xdebug.mode=debug,profile`,
  `xdebug.start_with_request=trigger`, empty `xdebug.trigger_value` and
  `xdebug.discover_client_host=1`. Any client that can send an HTTP request can
  make the server open a DBGp debugging session back to an address the client
  chooses — a known remote-code-execution vector.
- **Evidence:**
  - Environment: `XDEBUG_ON=1`, `XDEBUG_PROFILER_ON=1`,
    `XDEBUG_CLIENT_HOST=host.docker.internal` (from `docker-compose.yml:71-74`).
    Container log: `20:51:58.668 (1/1) Installing php85-pecl-xdebug (3.5.0-r0)`,
    `20:51:58.829 XDebug installed and configured`.
  - Effective settings (`/etc/php85/php.ini`, droplet container):
    ```
    1885:zend_extension=/usr/lib/php85/modules/xdebug.so
    1891:xdebug.start_with_request=trigger
    1900:xdebug.discover_client_host=1
    1903:xdebug.mode=debug,profile
    1909:xdebug.client_port=9003
    1911:xdebug.client_host=host.docker.internal
    ini_get: xdebug.trigger_value='' · xdebug.log='/tmp/xdebug.log' · xdebug.output_dir='/tmp'
    ```
    (`/etc/php85/conf.d/50_xdebug.ini` contains only commented lines; the
    active configuration is in `php.ini`.) Local container reports the same
    `debug,profile` / `discover=1`.
  - Loaded in the web SAPI, not only CLI: Apache loads
    `LoadModule php_module modules/mod_php85.so`, and `/tmp/xdebug.log` entries
    start `[1311] Log opened at 2026-09-14 20:52:08`, where PID 1311 is an
    `apache` `httpd` worker (`ps`).
  - Xdebug documentation (https://xdebug.org/docs/all_settings, fetched this
    session) on `discover_client_host`: *"Please note that there is no filter
    available, and anybody who can connect to the webserver will then be able
    to start a debugging session, even if their address does not match
    xdebug.client_host."* On `trigger_value`: *"If this setting is set to a
    non-empty string, then Xdebug will only trigger if the value matches the
    value of this setting."*
  - No evidence of use so far: `/tmp/xdebug.log` (1,797 lines at ~22:29Z)
    contains only `Trigger value for 'XDEBUG_…' not found, so not activating`
    messages; 0 lines match `connect|discover` (re-checked 00:11Z); 0
    `cachegrind` profiler files in `/tmp`.
- **Verified:** `first-hand` for configuration, SAPI loading and log state.
  Exploitability was **not** tested — no trigger was sent.
- **Implication for the agent:** Development debugging extensions make every
  other control in this ledger moot on an exposed host. The co-pilot's
  deployment must assert at startup that Xdebug is not loaded, or at minimum
  that `xdebug.mode=off`, in any environment reachable beyond localhost.

### F-36 — Self-signed `CN=localhost` certificate on a bare IP; plaintext HTTP login also served
- **Category:** Security
- **Severity:** Medium
- **Claim:** The droplet serves HTTPS on 9300 with a self-signed certificate
  generated at container start, whose subject is `CN=localhost` and which is
  marked as a CA. There is no domain and no publicly trusted certificate. The
  same login page is also served over plaintext HTTP on 8300.
- **Evidence:**
  ```
  $ echo | openssl s_client -connect 201.79.25.22:9300 2>/dev/null | openssl x509 -noout -issuer -subject
  issuer= /C=xx/ST=x/L=x/O=x/OU=x/CN=localhost
  subject= /C=xx/ST=x/L=x/O=x/OU=x/CN=localhost
  $ … | openssl x509 -noout -dates -fingerprint -sha256
  notBefore=Sep 14 20:49:55 2026 GMT
  notAfter=Sep 14 20:49:55 2027 GMT
  SHA256 Fingerprint=F0:63:40:44:7E:1C:F4:87:75:F6:C1:86:84:5B:5E:4C:D5:66:F6:EF:1A:73:FF:BC:C1:B2:93:99:05:FD:BA:46
  $ … | openssl x509 -noout -text   → RSA Public-Key: (4096 bit) · X509v3 Basic Constraints: critical CA:TRUE
  $ echo | openssl s_client -connect 201.79.25.22:9300 -servername 201.79.25.22 2>&1 | grep "Verify return code"
  Verify return code: 18 (self signed certificate)
  ```
  `notBefore` matches the container log line
  `2026-09-14T20:49:55.990656807Z Configuring self-signed certificate as default...`.
  Apache itself warns at start: `AH01906: … server certificate is a CA certificate (BasicConstraints: CA == TRUE !?)`
  and `AH01909: … server certificate does NOT include an ID which matches the server name`.
  The local stack serves a different self-signed `CN=localhost` certificate
  (SHA256 `1D:4F:78:7F:…:4E:DC`), confirming per-container generation.
  Plaintext: `curl http://201.79.25.22:8300/interface/login/login.php` → 200.
- **Verified:** `first-hand`
- **Implication for the agent:** Clients of the deployed co-pilot (browsers,
  SMART apps, the co-pilot's own server-to-server calls) cannot validate this
  endpoint and will be pushed toward `--insecure`/`verify=False`, which also
  disables protection against interception. Any co-pilot HTTP client must pin
  or trust an explicit CA, never disable verification globally. Credentials or
  PHI must never be sent to port 8300.

### F-37 — `readyz` returns exception messages to unauthenticated callers with HTTP 200
- **Category:** Security
- **Severity:** Low
- **Claim:** The unauthenticated `/meta/health/readyz` endpoint catches every
  `Throwable` and returns its raw message in the JSON body with status 200 —
  on the droplet, to the internet.
- **Evidence:** `meta/health/index.php:91-95`:
  ```php
  } catch (\Throwable $e) {
      // Even on error, return 200 with error details in body
      // This ensures the probe doesn't fail just because of an exception
      $response = new JsonResponse(['status' => 'error', 'message' => $e->getMessage()]);
      $response->send();
  }
  ```
  Circumstantial: during the droplet's demo reset the healthcheck probe got a
  422-byte 200 response instead of the usual 136–137 bytes, immediately after
  an SQL error was logged:
  ```
  2026-09-14T21:07:11.508205930Z [...] SQL Error with statement:query failed: SELECT gl_name, gl_index, gl_value FROM globals ORDER BY gl_name
  2026-09-14T21:07:11.510609525Z ::1 - - [14/Sep/2026:21:07:11 +0000] "GET /meta/health/readyz HTTP/1.1" 200 422 "-" "curl/8.19.0"
  ```
  That response body was not captured, so it is not known whether the error
  path at `:91-95` or the normal checker path produced it.
- **Verified:** `first-hand` (source). Leak not exercised.
- **Implication for the agent:** Contradicts this repo's own rule ("Never expose
  `$e->getMessage()` in user-facing output"). Any co-pilot health or error
  endpoint must log exceptions and return generic text.

### F-38 — POSITIVE: droplet SSH is key-only; the one non-operator SSH login is attributable to DigitalOcean's console path
- **Category:** Security
- **Severity:** Positive
- **Claim:** SSH password and keyboard-interactive authentication are disabled.
  Every SSH login since first boot used a public key, and every login except
  one came from the operator's key. That one came from a DigitalOcean-owned
  address with a key not present in `authorized_keys`, consistent with
  DigitalOcean's web Droplet Console.
- **Evidence:**
  ```
  $ sshd -T | grep -E "^(passwordauthentication|permitrootlogin|pubkeyauthentication|kbdinteractiveauthentication) "
  permitrootlogin yes · pubkeyauthentication yes · passwordauthentication no · kbdinteractiveauthentication no
  /root/.ssh/authorized_keys: 1 key, type ssh-ed25519

  /var/log/auth.log "Accepted":
  20:17:48 Accepted publickey for root from 198.211.111.194 port 57438 ssh2: ECDSA SHA256:qwUbdDi3JJgCQyYsDxkAR7leoBgWClk8M01ClO6CGNI   (disconnected 20:18:15)
  20:20:16, 20:20:57, 20:23:39, 20:50:09, 22:22:11, 22:24:06, 22:24:15 …
           Accepted publickey for root from 192.63.199.50 … ED25519 SHA256:wNJrbBibqKX…
  ```
  Operator workstation: `ssh-keygen -lf ~/.ssh/id_ed25519.pub` →
  `SHA256:wNJrbBibqKXwJ…`, matching. `whois 198.211.111.194` →
  `NetName: DIGITALOCEAN-198-211-96-0`, `OrgName: DigitalOcean, LLC`.
  `systemctl is-active droplet-agent` → `active`.
- **Verified:** `first-hand`. The Droplet Console attribution is an inference
  from address ownership, key absence and agent presence; it was not confirmed
  with DigitalOcean.
- **Caveat:** `PermitRootLogin yes`: all operator activity runs as root, and
  `deploy` (uid 1000) is in the `docker` group, which is root-equivalent.
- **Implication for the agent:** Shell access is not the weak point; the
  application-layer exposures above are. Co-pilot operations should still move
  to a non-root user, because the web container writes into `deploy`'s
  checkout (F-48).

### F-39 — POSITIVE (bounded): no non-operator client reached OpenEMR or phpMyAdmin during the `admin`/`pass` window
- **Category:** Security
- **Severity:** Positive
- **Claim:** Within the logs available on the droplet, no party other than the
  operator, the containers themselves and the droplet's own IP made an HTTP
  request to OpenEMR or phpMyAdmin between 20:51:58Z and 22:14:19Z, and no
  application login came from any address other than the operator's.
- **Evidence:** full access log from container creation, client-IP summary
  taken 2026-09-15T00:11:30Z:
  ```
  260 192.63.199.50  21:06:33Z – 00:09:52Z   (operator; includes this session's probes)
  201 ::1            20:52:08Z – 00:10:40Z   (in-container healthcheck curl/8.19.0)
    4 127.0.0.1      22:13:12Z – 22:14:05Z   (Apache "internal dummy connection")
    1 201.79.25.22   21:10:36Z               (droplet's own public IP, curl/8.5.0, readyz)
    1 45.79.207.252  23:43:58Z               ("GET / HTTP/1.0" 400 — after the admin window)
  ```
  phpMyAdmin container log: 1 request in total (`192.63.199.50`, this session).
  OpenEMR `log`: the only 2026 login events are the three in F-31, all from
  `192.63.199.50`. Since 22:50Z the operator IP's requests are only
  `dated_reminders.php` (77), `dated_reminders_counter.php` (5) and
  `background_service/$run` (5), i.e. an open browser tab polling.
  `api_token` rows: 0 (no API token has ever been issued on the droplet).
- **Verified:** `first-hand`
- **Limits:** (1) MariaDB successful connections are not logged
  (`general_log=0`), so direct DB access on 8320 cannot be ruled out (F-34).
  (2) The logs live on the exposed host and are not tamper-evident. (3) The
  demo accounts of F-32 remain exposed after this window; the single later
  third-party request (45.79.207.252) was a malformed `GET /` with no login.
- **Implication for the agent:** The operator can treat OpenEMR-layer access
  during the window as unobserved, but not the database. Any claim that "no
  PHI was accessed" must carry limit (1). The data is synthetic, which bounds
  real-world harm.

### F-54 — RESOLVED: MariaDB and phpMyAdmin were publicly reachable through Docker's UFW bypass; an additive override did not close them; a base-file fix did
- **Category:** Security
- **Severity:** High (resolved — loopback-only replacements running from 2026-09-15T15:17:54Z)
- **Claim:** MariaDB (container port 3306) and phpMyAdmin (container port 80)
  were reachable from the public internet at the droplet's IP on host ports
  8320 and 8310, despite UFW's default-deny policy and an allow-list of only
  22/80/443. Root cause: Docker publishes ports with its own nat-table DNAT
  rules. The rewritten packets traverse the FORWARD chain, where Docker's chains
  run ahead of UFW's, and never reach the INPUT chain, where UFW's allow-list is
  enforced. UFW never saw Docker-published traffic, so the restrictive policy
  gave false confidence while both services stayed publicly bound. MariaDB
  accepted the default `root`/`root` credential while exposed. During the
  exposure window UFW blocked 1,674 distinct external source IPs on *other*
  ports (338 of them in the first ~2 h), and one external host
  (`45.79.207.252`) reached OpenEMR directly. A first remediation via
  `docker-compose.override.yml` did not remove the public bindings, because
  Compose merges `ports` lists additively. The fix that worked edits the base
  file (commit `b0a0bcc`). The public exposure lasted at least **3 h 47 min 48 s**
  and at most **18 h 09 min 12 s** — see the reconstruction below.
- **Evidence:**
  - Pre-fix binding and reachability:
    - `first-hand` (pass 2, F-33): droplet `docker ps` showed
      `0.0.0.0:8320->3306/tcp, [::]:8320->3306/tcp` and
      `0.0.0.0:8310->80/tcp, [::]:8310->80/tcp`. `nc -z` from the operator
      workstation (egress `192.63.199.50`) found 8320 and 8310 open at
      2026-09-14T22:24Z and 2026-09-15T00:12:01Z, and 8310 served
      `<title>phpMyAdmin</title>`.
    - `operator-reported`: `ss -tlnp` on the droplet showed `LISTEN` on
      `0.0.0.0:8320` and `0.0.0.0:8310`, and `nc -zv <droplet-ip> 8320` / `8310`
      from an external laptop both succeeded.
  - Mechanism (`first-hand`; chain layout read 2026-09-15 ~15:29Z, and the fix
    does not change it):
    ```
    $ iptables -t nat -S PREROUTING
    -A PREROUTING -m addrtype --dst-type LOCAL -j DOCKER
    $ iptables -S FORWARD | head -8
    -P FORWARD DROP
    -A FORWARD -j DOCKER-USER
    -A FORWARD -j DOCKER-FORWARD
    -A FORWARD -j ufw-before-logging-forward
    -A FORWARD -j ufw-before-forward
    -A FORWARD -j ufw-after-forward
    -A FORWARD -j ufw-after-logging-forward
    -A FORWARD -j ufw-reject-forward
    $ iptables -S DOCKER-USER
    -N DOCKER-USER
    ```
    The pre-fix DNAT rules (`--dport 8320 -j DNAT --to-destination 172.18.0.2:3306`,
    `--dport 8310 -j DNAT --to-destination 172.18.0.4:80`) and
    `ufw status` (default deny incoming; allow 22/80/443) are recorded in F-33.
    UFW's own log is consistent with the bypass. Across the whole boot it holds
    `UFW BLOCK … DPT=8320` only at 2026-09-15T00:37:39Z (`SRC=192.63.199.50`,
    15 s after the original containers detached) and 15:27:50Z (this session's
    post-fix probe). The successful pre-fix probes at 22:24Z and 00:12:01Z left
    no UFW entry. UFW logging is `low` (rate-limited), so this absence is
    corroborating, not conclusive.
  - Default credentials (`first-hand`, pass 2 ~22:30Z, F-34):
    `authentication_string = PASSWORD("root")` → `1` for both `root@'%'` and
    `root@localhost`; plaintext connections allowed
    (`@@require_secure_transport` 0). Not re-checked for the later intervals
    (no `docker exec` in this task). No remote login was ever attempted.
  - Scanning and third-party reach (`first-hand`, droplet kernel log):
    `UFW BLOCK` entries from 2026-09-14T20:49:36Z to 2026-09-15T15:17:43Z:
    **3,335** entries from **1,674** distinct sources (first 20:49:58Z, last
    15:17:40Z). The pass-2 subset (20:25:27Z–22:43:35Z) had 424 entries from 338
    sources. Blocks aimed at MySQL's *default* port 3306 came from 7 external
    sources inside the window (`193.163.125.223`, `71.6.232.30`,
    `18.217.208.51`, `176.32.193.16`, `89.37.172.143`, `138.197.0.183`,
    `20.65.179.159`). These are refused attempts on unpublished ports.
    DNAT'd traffic to 8320/8310 is not logged by UFW, so these figures neither
    show nor exclude contact with the exposed services. Separately, the OpenEMR
    access log records
    `2026-09-14T23:43:58Z 45.79.207.252 - - "GET / HTTP/1.0" 400` (F-33).
  - Failed first remediation:
    - `operator-reported`: with an override adding loopback-only alternates
      (`127.0.0.1:18320`, `127.0.0.1:18310`), `docker compose ps` showed both
      `0.0.0.0:8320` (still public) and `127.0.0.1:18320` bound simultaneously.
    - `first-hand`, droplet `journalctl -u docker`: container starts failed at
      00:37:31Z, 00:39:16Z, 00:46:51Z, 00:52:08Z and 15:04:45Z, each with
      `level=warning msg="Failed to allocate port" error="failed to bind host port 127.0.0.1:8320/tcp: address already in use"`.
      The previous containers had already detached at 00:37:24Z, so this is
      consistent with a configuration that still carried the base file's
      all-interfaces 8320 mapping *plus* a loopback mapping on the same port.
    - `first-hand`, local reproduction of the merge behaviour (2026-09-15): a
      local override `127.0.0.1:8320:3306` plus the base file resolved in
      `docker compose config` to two entries for mysql (`published: "8320"`
      with no `host_ip`, and `host_ip: 127.0.0.1` `published: "8320"`). The
      container's `PortBindings` were
      `{"3306/tcp":[{"HostIp":"","HostPort":"8320"},{"HostIp":"127.0.0.1","HostPort":"8320"}]}`,
      and it failed with the same `address already in use`. A scratch override
      using `ports: !override` resolved to a single `127.0.0.1` entry.
  - Working fix (`first-hand`): commit `b0a0bcc122609ffedae5da0685036c388beef6b4`,
    message `(fix): docker setup`, changing only
    `docker/development-easy/docker-compose.yml:7`
    `- "${WT_MYSQL_PORT:-8320}:3306"` → `- "127.0.0.1:18320:3306"` and `:150`
    `- "${WT_PMA_PORT:-8310}:80"` → `- "127.0.0.1:18310:80"`. It was pushed to
    `origin` (`labs.gauntletai.com`) `main`. Verified locally before the commit:
    `docker compose config` resolved mysql to `host_ip: 127.0.0.1`
    `published: "18320"` and phpmyadmin to `host_ip: 127.0.0.1`
    `published: "18310"` only. `docker compose ps` showed
    `127.0.0.1:18320->3306/tcp` and `127.0.0.1:18310->80/tcp` with no
    `0.0.0.0` on either line, and the test stack was torn down afterwards.
    Droplet deployment, read-only: `docker-compose.yml` lines 7 and 150 carry
    the new mappings (mtime `2026-09-15 15:16:48Z`); `docker-compose.override.yml`
    is absent; the new `docker-proxy` processes started
    `Tue Sep 15 15:17:43 2026` (`-host-ip 127.0.0.1 -host-port 18320`) and
    `15:17:54` (`-host-ip 127.0.0.1 -host-port 18310`).
- **Remediation verified from three vantage points:**
  1. **Droplet, `ss -tlnp`** — `first-hand`, 2026-09-15T15:28:11Z (the operator
     reports the same result):
     ```
     LISTEN 0 4096 127.0.0.1:18310 0.0.0.0:* users:(("docker-proxy",pid=73497,fd=8))
     LISTEN 0 4096 127.0.0.1:18320 0.0.0.0:* users:(("docker-proxy",pid=73215,fd=8))
     LISTEN 0 4096   0.0.0.0:9300  0.0.0.0:* users:(("docker-proxy",pid=73407,fd=8))
     LISTEN 0 4096   0.0.0.0:8300  0.0.0.0:* users:(("docker-proxy",pid=73385,fd=8))
     LISTEN 0 4096      [::]:9300     [::]:* users:(("docker-proxy",pid=73414,fd=8))
     LISTEN 0 4096      [::]:8300     [::]:* users:(("docker-proxy",pid=73391,fd=8))
     ```
     No listener exists on 8320 or 8310, and neither service has a
     `0.0.0.0`/`[::]` binding.
  2. **External `nc`:** the operator reports that 18320 and 18310 timed out and
     9300 connected (`operator-reported`). The droplet's UFW log corroborates
     those probes as blocked at the host: `SRC=192.63.199.50 DPT=18320` at
     15:19:00Z and `DPT=18310` at 15:20:19Z. Re-run from the same egress IP at
     2026-09-15T15:27:50Z (`first-hand`): `tcp/8320`, `tcp/8310`, `tcp/18320`,
     `tcp/18310` closed/filtered; `tcp/9300` and `tcp/8300` open. The operator's
     laptop and this workstation share the egress `192.63.199.50`, so they are
     one network vantage point, not two.
  3. **Independent external port-checking service** (a network controlled by
     neither the droplet nor the laptop) reported 18310 closed —
     `operator-reported`. The service name, timestamp and raw output were not
     provided to this ledger, and the result for 18320 was not reported.
- **Exposure window — reconstruction (bounded, not exact):** container network
  attach/detach times come from the droplet kernel log
  (`journalctl -k`, `br-…: port N(veth…) entered forwarding/disabled state`),
  and failures from the dockerd journal. MariaDB, host port 8320:
  ```
  2026-09-14T20:49:36Z → 2026-09-15T00:37:24Z   3 h 47 min 48 s   original stack; 0.0.0.0:8320 observed first-hand   PUBLIC (established)
  2026-09-15T00:37:24Z → 00:54:04Z              16 min 40 s       mysql not running (4 failed starts, above)          closed
  2026-09-15T00:54:04Z → 15:04:39Z              14 h 10 min 35 s  stack started; bindings not logged                  public per operator's dual-binding report (inferred)
  2026-09-15T15:04:39Z → 15:05:39Z              1 min 00 s        not running (failed start 15:04:45Z)                closed
  2026-09-15T15:05:39Z → 15:16:28Z              10 min 49 s       stack started; bindings not logged                  unknown; base file still public until 15:16:48Z mtime
  2026-09-15T15:17:43Z →                                          127.0.0.1:18320 only (ss, 15:28:11Z)                closed
  ```
  Lower bound (established): **3 h 47 min 48 s**. Upper bound (all
  unlogged intervals public): **18 h 09 min 12 s**. phpMyAdmin (8310) follows
  the same intervals, starting ~11 s later (its bridge ports attached
  20:49:47Z, 00:54:15Z, 15:05:49–50Z) and ending within ~1 s of MariaDB's.
- **Blocked — exact exposure duration:** not precisely reconstructable. What is
  missing:
  1. The port bindings of the stacks that ran 00:54:04Z–15:04:39Z and
     15:05:39Z–15:16:28Z. The kernel log records only attach/detach, dockerd
     logs no bindings for successful starts, and the containers have since
     been removed, so their `HostConfig` cannot be inspected.
  2. The operator's dual-binding `docker compose ps` observation carries no
     timestamp.
  3. The override file has been deleted from the droplet, so its creation time
     is not recoverable from the filesystem.
  4. The suggested cross-check of UFW log timestamps against the override's
     creation time cannot bound exposure: UFW never logs DNAT'd traffic to
     published ports, so its log shows only when a port was *not* published.

  What would pin it: `docker events` or dockerd debug logs captured at the time,
  a timestamped `docker compose ps` / `ss -tlnp` from each stack, or a
  timestamped record of the operator's commands (none was available to this
  ledger). Whether anyone *used* the exposure is separately unestablishable:
  MariaDB does not log successful connections (`general_log=0`, F-34/F-39).
- **Correction to the brief:** "338 distinct external IPs scanning the host
  during this period" counts only the pass-2 sample (20:25:27Z–22:43:35Z).
  Across the exposure window the figure is 1,674 sources (3,335 entries). In
  both cases these are UFW-blocked attempts on unpublished ports, not observed
  scans of 8320/8310.
- **Verified:** `first-hand`: pre-fix bindings and reachability in the
  first interval, the chain layout, default credentials (22:30Z), UFW
  statistics, the `45.79.207.252` request, the dockerd bind failures and
  kernel attach/detach timeline, the local merge reproduction, the fix commit
  and its local verification, the droplet deployment state, and post-fix
  `ss` (15:28:11Z) plus external `nc` (15:27:50Z). `operator-reported`: the
  pre-fix droplet `ss -tlnp`, the droplet dual-binding `docker compose ps`,
  the operator's own post-fix laptop probes (corroborated by UFW log entries),
  and the independent port-checker result. F-33 and F-34 remain point-in-time
  pass-2 observations; their 8310/8320 exposure is closed as of this entry,
  while 8300/9300 remain public by design.
- **Implication for the agent:** Container network exposure cannot be assumed
  safe from host firewall state: Docker's port publishing operates beneath the
  firewall layer and is invisible to it. Compose override files cannot remove
  a base file's port mappings either (they merge additively unless the list is
  marked `!override`/`!reset`). Any deployment running Docker alongside a
  host-level firewall must check actual bound listen addresses directly
  (`ss -tlnp`, `docker compose ps`, and the resolved `docker compose config`),
  not firewall rule state, before treating a service as non-public. This
  applies directly to this project's future services — the agent sidecar and
  the observability backend (Langfuse). Each must have its bindings verified
  the same way, including from a genuinely external vantage point (a network
  other than the operator's egress), before being declared internal-only.

### F-56 — MITIGATED: LangGraph's bundled LangSmith tracing sends the graph's run state to a third-party service when `LANGSMITH_TRACING=true`
- **Category:** Security
- **Severity:** High (mitigated — two guard layers on the Pre-Visit Brief graph,
  covered by a regression test; guarding future graphs is still manual, see
  Remediation)
- **Claim:** LangGraph installs the `langsmith` package as a transitive
  dependency. If `LANGSMITH_TRACING=true` is set — a plausible accident: a
  copied `.env`, a stray default, a misconfigured deployment — each graph run
  is traced and its state POSTed to LangSmith. No API key is needed for the
  request to be sent, and the built-in destination is LangSmith Cloud
  (`https://api.smith.langchain.com`), a third-party SaaS with no BAA recorded
  anywhere in this project (contrast F-41). The Pre-Visit Brief graph's state
  holds the retrieved chart records (conditions, medications, allergies), the
  drafted claims and the rendered brief, so an unguarded run would send
  patient chart data off-host: PHI exfiltration with no attacker required,
  triggered by environment misconfiguration alone. The current dataset is
  synthetic (F-41), so today that would expose synthetic records; the
  mechanism is identical for real PHI.
- **Evidence:**
  - Dependency (`first-hand`, agent environment built from
    `agent/requirements.txt`, 2026-09-16): `langgraph` 1.2.11 requires
    `langchain-core` (1.6.3), which requires `langsmith` (0.12.5);
    `pip show langsmith` reports `Required-by: langchain-core`. Before commit
    `3cbd8a4`, `agent/requirements.txt` did not name `langsmith`.
  - Default destination (`first-hand`, installed source): with no endpoint
    variable set, `langsmith.utils.get_api_url(None)` returns
    `https://api.smith.langchain.com`.
  - Outbound POST of graph state (`first-hand`, 2026-09-16 ~18:06Z, local
    capture only): a one-node LangGraph graph whose node writes the marker
    string `SENTINEL-STATE-VALUE` into state, run with `LANGSMITH_TRACING=true`
    and `LANGSMITH_ENDPOINT` pointed at an HTTP recorder on `127.0.0.1`, each
    case in a fresh process:
    ```
    no guard, LANGSMITH_API_KEY set     → POST /runs/multipart, 4007 bytes, marker in body
    no guard, no API key in environment → POST /runs/multipart, 4007 bytes, marker in body, no x-api-key header
    tracing_context(enabled=False)      → no requests (recorder answered control GETs immediately before and after)
    ```
    First observed earlier the same day while building the graph (trace
    `01a0ab4e-b754-…`, UUIDv7 time 2026-09-16T17:40:57Z): with the endpoint set
    to a closed local port, `langsmith` logged
    `Failed to multipart ingest runs: … POST http://127.0.0.1:9/runs/multipart … Content-Length: 3983`.
    Inside that run's node, `langsmith.utils.tracing_is_enabled()` and
    langchain-core's `_tracing_v2_is_enabled()` both returned `True`; under
    `tracing_context(enabled=False)` both returned `False`.
  - The real graph's payload (`first-hand`, 2026-09-16 ~19:26Z, committed code
    at `39ccd52`, local capture, every non-loopback DNS lookup refused): the
    exported `PRE_VISIT_BRIEF_GRAPH.ainvoke` was called directly with
    `LANGSMITH_TRACING=true`, with FHIR retrieval and claim drafting replaced by
    fakes returning marker records. It sent `POST /runs/multipart`, 14,701
    bytes, holding five runs — `LangGraph` (root), `retrieve`, `draft`,
    `verify`, `render`. The markers were in the root run's outputs and in the
    inputs or outputs of every node run (condition marker 14 times,
    medication and allergy 5 each); the root and `retrieve` inputs were only
    `{"node_seconds":{}}`. The faked calls run inside nodes as plain
    coroutines and add no runs of their own, so real FHIR and Anthropic calls
    would change the values in this payload, not its shape.
  - Bearer token — checked, not present (`first-hand`, same run): a sentinel
    token passed in the runtime context (`PreVisitBriefContext`, the only place
    it lives — graph state has no field for it,
    `agent/app/graph/pre_visit_brief.py:113`) reached the FHIR layer on all 3
    calls and appeared 0 times in the captured bodies and headers. Also
    absent: the context's other fields (patient ID, session key), `Bearer `,
    `PreVisitBriefContext`, `__pregel_runtime`. Every run's metadata held
    `ls_integration`, `LANGSMITH_TRACING`, `LANGSMITH_ENDPOINT` and
    `revision_id`; node runs added LangGraph's `langgraph_node`,
    `langgraph_step`, `langgraph_path`, `langgraph_triggers` and
    `langgraph_checkpoint_ns`; SDK and platform details sat under `runtime`.
    None of it carries runtime context. LangSmith copies environment
    variables into metadata, but only `LANGCHAIN_*`/`LANGSMITH_*` ones minus
    its own keys (`langsmith/env/_runtime_env.py`,
    `get_langchain_env_var_metadata`); `FHIR_CLIENT_SECRET`, `FHIR_CLIENT_ID`
    and `ANTHROPIC_API_KEY` were set in that process and were not in the
    payload.
  - A tracer passed explicitly as a callback is not stopped by the guard
    (`first-hand`, 2026-09-16, one-node graph): under
    `tracing_context(enabled=False)` with `LANGSMITH_TRACING=true`, a
    `LangChainTracer` passed via `config={"callbacks": [...]}` was still
    attached to the node's run; without it, none was.
  - Environment at time of writing (`first-hand`, variable names only):
    `agent/.env` sets only `FHIR_*` and `ANTHROPIC_API_KEY`, and the operator
    shell had no `LANGSMITH_*` or `LANGCHAIN_*` variables.
- **Remediation:**
  - Commit `3cbd8a4`: `run_pre_visit_brief()` ran the graph inside
    `tracing_context(enabled=False)`, and `agent/requirements.txt:11` declares
    `langsmith` explicitly.
  - Closed 2026-09-16, in the change that revised this entry:
    1. **Exported graph object** (was residual gap 1). `PRE_VISIT_BRIEF_GRAPH`
       is now an `UntracedGraph` (`agent/app/graph/pre_visit_brief.py:199`), not
       a LangGraph object. Its only way to run the graph, `ainvoke`, forces
       tracing off (`:216`) and accepts no `RunnableConfig`, so the explicit
       tracer callback above has no way in. `stream`, `astream_events`,
       `batch`, `with_config` and the rest do not exist on it.
       `run_pre_visit_brief()` goes through it, so that guard lives in one
       place. Second layer: every node is wrapped (`_untraced`, `:187`, applied
       at `:367` onward) and raises `TracingEnabledError` before doing
       anything if a `LangChainTracer` is attached to its run
       (`_refuse_if_traced`, `:166`). The compiled graph reached another way
       (`PRE_VISIT_BRIEF_GRAPH._compiled`, `_build_graph()`) therefore fails
       before any chart record is retrieved or the token is used.
       `agent/requirements.txt:16` declares `langchain-core`, now imported
       directly.
    2. **Automated test** (was residual gap 3).
       `agent/tests/test_langsmith_tracing_guard.py`, 8 tests, in the suite
       (124 passed). With `LANGSMITH_TRACING=true`, `LANGSMITH_ENDPOINT` on a
       local recorder and every non-loopback DNS lookup refused, it asserts
       that `run_pre_visit_brief()` and `PRE_VISIT_BRIEF_GRAPH.ainvoke` send
       nothing and look up no `smith.langchain.com` host. It also asserts
       that the compiled graph reached directly refuses before any retrieval
       — whether the tracer comes from the environment or an explicit
       callback — and that what the refused run traces holds neither the token
       nor the chart markers. The exported object must expose no
       other execution method, and no `app/graph` module may hold a raw
       compiled graph. Control tests turn both layers off and assert the
       recorder then receives the chart markers, so a silent recorder cannot
       pass. `first-hand` checks that it fails when it should:
       - Against the committed code at `39ccd52`,
         `test_exported_graph_object_sends_nothing` failed with
         `Lists differ: ['GET /info (0 bytes)', 'POST /runs/multipart (16458 bytes)'] != []`,
         all 11 execution-method subtests failed, and the module scan found
         `['PRE_VISIT_BRIEF_GRAPH']`.
       - Against deliberately broken copies of the fixed code: removing the
         `tracing_context` guard failed both "sends nothing" tests; removing
         the node check failed both refusal tests (`TracingEnabledError not
         raised`); adding the token to graph state failed the token test.
    3. **Bearer token** (was "not checked" under Evidence): checked, not
       present — see Evidence. The token test fails if it ever becomes
       reachable.
  - Residual gaps, still open:
    1. Any future graph or entry point still needs both layers applied by
       hand. The module scan catches a raw compiled graph held at module level
       anywhere in `agent/app/graph/`. It does not catch one compiled and
       invoked inside a function, or nodes added without `_untraced`.
    2. Python has no private access: code that assembles its own graph from
       this module's node functions without `_untraced`, or calls them
       directly, is outside both layers.
    3. The node check recognises LangSmith's `LangChainTracer` only. Another
       callback handler that exports run data would not be refused.
- **Verified:** `first-hand` (transitive install; default endpoint; POST of
  graph state with and without an API key; suppression by the guard; chart
  records in the real graph's trace payload, with marker records in place of
  FHIR data; bearer token and the other runtime-context fields absent from
  that payload; explicit tracer callbacks surviving `tracing_context`; both
  guard layers and the token check exercised by
  `agent/tests/test_langsmith_tracing_guard.py`, which fails on the pre-fix
  code and on each deliberate breakage) · not captured (delivery to LangSmith
  Cloud, deliberately never attempted) · inferred (the same payload shape
  when the records come from the droplet's real FHIR API).
- **Implication for the agent:** This is one instance of a general risk: an
  LLM orchestration framework can bundle telemetry or tracing integrations
  whose defaults are more permissive than a PHI-handling system can tolerate.
  Dependencies must be audited for outbound network calls, not only reviewed
  for their primary function — the egress counterpart of F-54's rule that
  listening ports are checked directly rather than assumed. A guard needs the
  same treatment: `tracing_context(enabled=False)` reads as "tracing off" but
  does not detach a tracer passed in explicitly, which only surfaced by
  testing it.

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
- **Cross-check (pass 2):** the identical query against the droplet returns
  the identical 14 rows and values. `first-hand`.
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
- **Correction (pass 2):** pass-2 API probes added 26 local rows (13 `api`,
  13 `http-request-select`, 22:32:55–22:36:18). Re-measured 2026-09-15T00:11Z:
  total **2321**, `other-*` **1862** → **80.2%**. The claim holds at ≈80%.
  The drift itself illustrates the finding: diagnostic traffic, not clinical
  access, is what the log accumulates.
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
- **Cross-check (pass 2):** both empty on the droplet too. With no ATNA export,
  the only copy of the droplet's audit trail sits in a database that is
  reachable from the internet with default root credentials (F-34). `first-hand`.
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
- **Cross-check (pass 2):** `1` on the droplet too. `first-hand`.
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

### F-40 — Out-of-band database edits leave no trace in OpenEMR's audit log
- **Category:** Compliance
- **Severity:** Medium
- **Claim:** Security-relevant rows were changed after creation on both hosts
  (OAuth clients enabled and granted `password`/`refresh_token`) with no
  corresponding `log` entry, while their creation *was* logged. Changes made
  directly against the database — which F-34 makes possible from the internet
  on the droplet — are invisible to the audit trail.
- **Evidence:** droplet, all `log` rows whose decoded SQL mentions `oauth_clients`:
  ```
  $ … mariadb -uroot -proot openemr -N < (SELECT id, date, CAST(FROM_BASE64(comments) AS CHAR) FROM log WHERE CAST(FROM_BASE64(comments) AS CHAR) LIKE '%oauth_clients%' ORDER BY id;)
  2118  2026-09-14 21:10:43  INSERT INTO `oauth_clients` (… `grant_types`, …) VALUES (…) ('<redacted>','user','A Private App',…,'authorization_code',…
  2196  2026-09-14 22:12:17  INSERT INTO `oauth_clients` (… `grant_types`, …) VALUES (…) ('<redacted>','user','A Private App',…,'authorization_code',…
  ```
  (long secret/token values were redacted from output before display). Current rows:
  ```
  A Private App | is_enabled 1 | is_confidential 1 | authorization_code,password,refresh_token | register_date 2026-09-14 22:12:17
  A Private App | is_enabled 1 | is_confidential 1 | authorization_code,password,refresh_token | register_date 2026-09-14 21:10:43
  ```
  No `UPDATE` row exists. Locally the same query returns only the three
  INSERTs (ids 2118, 2224, 2233), yet client `773KrZky…` is enabled with
  `password,refresh_token` (F-02). The access log shows no OpenEMR admin-UI
  request that could have edited clients on the droplet (the operator's only
  admin-side POST was `user_info_ajax.php`, F-31).
- **Verified:** `first-hand` for the logged INSERTs, the current row state and
  the absence of logged updates. *How* the rows were changed (direct SQL,
  phpMyAdmin, or a `…NoLog` code path) was not established.
- **Implication for the agent:** OpenEMR's audit log is an application-layer
  record, not a database-layer one. If the co-pilot's compliance story depends
  on detecting tampering with its own configuration or access tables, it needs
  DB-level controls (restricted DB accounts, binlog/audit plugin) rather than
  OpenEMR's `log`.

### F-41 — POSITIVE (with caveats): the database stays inside a HIPAA-eligible Droplet rather than Managed Databases
- **Category:** Compliance
- **Severity:** Positive
- **Claim:** MySQL/MariaDB runs as a container on the same droplet as OpenEMR,
  not on DigitalOcean Managed Databases. DigitalOcean's published
  HIPAA-eligible product list includes Droplets and omits Managed Databases, so
  this placement keeps ePHI-bearing data on a covered product. The operator
  reports the choice was deliberate for this reason.
- **Evidence:**
  - Topology (first-hand): droplet `docker ps` shows `development-easy-mysql-1`
    (`mariadb:11.8.8`) on the same host; the OpenEMR container env has
    `MYSQL_HOST=mysql` (the Compose service name).
  - Vendor list (documentation-sourced, fetched 2026-09-14 from
    https://www.digitalocean.com/trust/hipaa-at-do): the page directs customers
    to upload ePHI "exclusively to the following DigitalOcean's HIPAA Eligible
    products", listing *Backups & Snapshots* (Droplet Backups, Volumes Block
    Storage Snapshots), *Compute* (Droplets, GPU Droplets, Kubernetes),
    *Management Tools* (Monitoring), *Inference Engine* (1-Click Models),
    *Networking* (Firewalls, Load Balancers, Reserved IPs, VPC), *Storage*
    (Spaces Object Storage, Volume Block Storage), *Containers & Images*
    (Container Registry, Custom Images). Managed Databases does not appear on
    the list. The page carries no "last updated" date.
  - Intent: `operator-reported`.
- **Caveats:** (1) Eligibility requires an executed BAA with DigitalOcean; no
  evidence of one was available to this audit. (2) The placement is right, but
  the database is currently internet-exposed with default credentials (F-34),
  which a BAA does not cure. (3) The dataset is synthetic.
- **Verified:** `first-hand` (topology) · documentation-sourced (product list) ·
  `operator-reported` (deliberate intent; BAA status unknown).
- **Implication for the agent:** Any co-pilot datastore (vector index, cache,
  conversation log) holding PHI must follow the same rule — no Managed
  Databases, and no other product off DigitalOcean's eligible list, unless that
  list changes.

### F-59 — Logged Metric 2 cannot detect a render step that hides failed claims
- **Category:** Compliance / Observability
- **Severity:** Medium (monitoring gap, not a live bug)
- **Claim:** `KEY_METRICS.md` Metric 2 (Unverified-Claim Disclosure Rate) is the
  share of failed claims actually surfaced to the physician, and "anything
  under 100% here is a defect in the render step". As implemented it reads
  `disclosed_as_unverified` from `claim_verified_event`, which the verifier
  sets to `True` for every failed claim before rendering runs. Nothing after
  rendering records what was shown, so the metric reports 100% whatever the
  renderer does.
- **Evidence:**
  - Source (`first-hand`): `agent/app/verification/checker.py:155-161`
    `_failed()` always sets `disclosed_as_unverified=True` and has no parameter
    to do otherwise; `agent/scripts/compute_metrics.py:220` `metric_2`
    computes the rate from that field alone.
  - Eval case C5 (`first-hand`, run 2026-09-16 22:45–22:48Z): in-process
    pipeline with live FHIR retrieval, the model's real draft replayed plus 4
    injected failing claims, and the renderer replaced for the run by one that
    drops failed claims.
    ```
    suite's check of the rendered text:  5 violation(s), e.g. Active problems: failed claim "severe" not disclosed as UNVERIFIED (1x)
    compute_metrics.py, same run's own events:  result: 100.0%  (4 disclosed of 4 failed claims) — meets the 100% absolute
    ```
  - The real renderer discloses correctly (`first-hand`, same run): B2
    (failed claims shown as UNVERIFIED across 5 live briefs) and C1–C4
    (injected failures shown as UNVERIFIED with their reason) pass.
- **Remediation:** not done. Record disclosure from the render step — one
  event per failed claim, emitted after the brief text is built, saying
  whether its UNVERIFIED line is present — and compute Metric 2 from that.
- **Verified:** `first-hand`
- **Implication for the agent:** Metric 2 is the production monitor for §5's
  "never silently dropped" promise, and it would stay at 100% through exactly
  the regression it exists to catch. Until it measures the rendered output,
  the evidence that failures reach the physician is the eval suite (B2,
  C1–C4), not the metric.

### F-61 — No BAA covers the deployment host since the move from DigitalOcean to Vultr
- **Category:** Compliance
- **Severity:** Low for this exercise (synthetic data only); blocking before
  any real PHI
- **Claim:** Late in the build the deployment moved from the DigitalOcean
  droplet `201.79.25.22` (the audited host: F-38, F-41, F-44) to a Vultr host
  at `64.177.42.210`, because of a payment constraint. F-41's positive
  finding rests on DigitalOcean's HIPAA-eligible product list and does not
  carry over. No BAA has been executed with Vultr, so the host that now holds
  OpenEMR's database and runs the agent is not under any BAA. This is a
  documented tradeoff for a synthetic-data exercise, not a claim of coverage.
- **Evidence:**
  - Provider (`first-hand`, `whois 64.177.42.210`, 2026-09-17 ~00:55Z):
    ```
    NetRange:       64.176.0.0 - 64.177.255.255
    NetName:        CHOOP-1
    OrgName:        The Constant Company, LLC
    ```
    The Constant Company, LLC is Vultr's operating company.
  - Migration window (`first-hand`, repository records): the eval run at
    2026-09-16T22:47:45Z still targeted `https://201.79.25.22:8443`
    (`agent/evals/RESULTS.md`); commit `e81fdfc` (2026-09-17T00:16:40Z)
    repointed the deployed references to `64.177.42.210`.
  - Reason for the move (`operator-reported`): a payment constraint on the
    DigitalOcean account.
  - BAA status (`operator-reported`): no BAA with Vultr has been executed.
    The operator also reports that Vultr does not offer one. **That second
    point is not confirmed:** Vultr's compliance and trust-center pages
    (`https://www.vultr.com/legal/compliance/`,
    `https://www.vultr.com/trust-center/`) returned HTTP 403 to this
    session's fetches (2026-09-17 ~00:57Z). Web-search summaries of those
    pages describe Vultr supporting customers as a Business Associate
    through executed BAAs. Whether Vultr would sign one for this account is
    unestablished. Either way, none is in place.
  - Dataset: the populations audited on the DigitalOcean droplet and locally
    are OpenEMR demo data plus Synthea imports (`first-hand`: F-15–F-18,
    F-43). The Vultr host is a fresh install, not a copy: FHIR ids from the
    DigitalOcean host return nothing there, and the same patients carry new
    ids (Phil Belford is `a2c36e54-af42-4c38-b86e-ade616ed5494` on Vultr). Its
    population mixes OpenEMR demo patients with Synthea-named ones, such as
    Tonja658 Grant908. That was checked live on 2026-09-17 by a separate
    session (branch `fix/embed-vultr-config`, commit `45e6710`) and not
    re-run for this entry. That the Vultr host holds only synthetic data,
    per the case study brief's demo-data constraint, is `operator-reported`.
    This session did not inspect the Vultr database.
- **Remediation:** not done, and not needed while every record is synthetic.
  Before any real PHI, move to a substrate under an executed BAA, such as
  DigitalOcean's HIPAA-eligible products under a signed BAA (F-41), or
  another provider's covered products under a signed agreement. Then
  re-apply the placement rules to that provider's covered-product list:
  database off any uncovered managed product (F-41), agent co-located or on
  a covered product (`ARCHITECTURE.md` §1), observability backend on a
  covered product (`agent/docs/observability.md` §9, written for
  DigitalOcean).
- **Verified:** `first-hand` (IP registrant; migration window from repository
  records) · `first-hand` in a separate session, not re-run here (fresh
  install with new FHIR ids) · `operator-reported` (reason for the move; no
  executed BAA; synthetic-only contents of the Vultr host) · not verified (whether Vultr
  offers a BAA — its pages were not retrievable, and search summaries
  suggest it does; Verification gaps item 25).
- **Implication for the agent:** `ARCHITECTURE.md` §1's placement decisions
  were argued from F-41 and keep the right shape. On the current host,
  though, they rest on the synthetic-data constraint alone, not on BAA
  coverage. As deployed, neither the host nor the LLM provider (Verification
  gaps item 23) is under an executed BAA. The deployment must not receive
  real PHI in this state.

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
- **Cross-check (pass 2):** the droplet's `documents` table has **0** rows and
  its `audit_master` is 0, consistent with only development-mode imports
  having been run there. `first-hand`.
- **Verified:** `first-hand`
- **Implication for the agent:** Exit code 0 and success-shaped log output are
  not evidence of work done anywhere in this codebase. Every ingestion or write
  path the co-pilot depends on must be verified by reading back the resulting
  rows, not by trusting the tool's own report.
- **Correction (later session; recorded 2026-09-15):** the identical command
  (`import-random-patients 5 false`) was run again in a later session and
  imported **4 of 5** patients, not 0. The observation above is not disputed —
  it is what happened in that run — but the finding's framing should be read
  as **intermittent, not deterministic**: the same root cause (`$authName`
  never set by the devtools wrapper, per
  `contrib/util/ccda_import/import_ccda.php:195-207`) may still be the
  mechanism, but it does not fail on every invocation, nor does it fail
  completely when it does fail. This is `operator-reported` to this ledger,
  not independently re-run in this session. Severity is **not** downgraded
  from High: a command that silently succeeds somewhere between 0% and 80% of
  the time on a supposedly all-or-nothing operation is arguably worse than a
  guaranteed no-op, because partial success is far harder to notice — a
  4-of-5 import looks like success unless every expected patient is
  individually checked, and no existing tooling in this codebase does that
  check.

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
- **Correction (pass 2) — flag, not refutation:** the 50→48 run cannot be
  matched to either database. Patient rows grouped by insert time:
  - Local: one batch of **57** Synthea patients (pids 4–60, inserted
    16:51:53–16:53:12) plus the 5-patient batch (pids 61–65, 18:15:12–18:15:15).
  - Droplet: one batch of **50** (pids 4–53, 21:11:52–21:19:13), with **50**
    CCDA files still present in the container's `/tmp/synthea/output/ccda` —
    50 written, 50 imported, no loss.

  Neither host holds a 48-patient batch. The loss may have occurred in a run
  whose output was later superseded, but that is unobserved. The claim stays
  `operator-reported` and is now also **unreconciled with observed state**.
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
  citation look like they work when neither has been exercised. (The same dump
  also carries the default-password demo users of F-32.)

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
- **Cross-check (pass 2):** droplet `procedure_result`: total **29701**,
  `range_blank` 29701, `abnormal_blank` 29701, `has_result` 29701, `has_units`
  29701. The property holds independently of population. `first-hand`.
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
- **Cross-check (pass 2):** local `COUNT(DISTINCT pid) FROM form_vitals` = 65
  (every patient exactly one row); droplet `vitals 53` = `patients 53` =
  distinct pids 53, against 3331 encounters. `first-hand`.
- **Verified:** `first-hand`
- **Implication for the agent:** The co-pilot cannot show vitals trends over
  time, and must not imply a vitals reading belongs to a specific encounter.
  Any "vitals at this visit" feature has no data behind it.

### F-21 — Allergies present for a minority of patients
- **Category:** Data Quality
- **Severity:** Medium
- **Claim (as corrected in pass 2):** 43 allergy rows exist across 65 patients, and only **14** patients
  (21.5%) have any allergy record — 51 of 65 have none.
- **Evidence:** From the census of 2026-09-14T18:25:02Z: `allergies 43`,
  `patients 65`. Row count is of `lists` where `type='allergy'`.
- **Correction:** 43 rows, not 39 — count rose with this session's import.
- **Correction (pass 2):** the original entry noted that "43 rows may cover
  fewer than 43 distinct patients" and left "most patients have none" as an
  inference. Measured:
  `SELECT COUNT(DISTINCT pid) FROM lists WHERE type='allergy'` → local **14**
  of 65; droplet **9** of 53 (59 rows). The claim is now measured rather than
  inferred, and stronger than stated. Closes verification gap 7. The worst-case
  patient pid=20 has **0** allergy rows (F-46).
- **Verified:** `first-hand`
- **Implication for the agent:** "No known allergies" and "allergies were never
  captured" are different clinical statements and this data cannot distinguish
  them. The co-pilot must say "no allergies recorded", never "no known
  allergies", and must not treat an empty allergy list as safe to prescribe
  against.

### F-22 — Outlier patient exceeds any reasonable context window
- **Category:** Data Quality
- **Severity:** High
- **Claim (as corrected in pass 2):** Patient `pid=20` has 530 encounters, 92 problems, 907 procedure
  orders (130 of them laboratory orders) and 9 prescriptions — roughly 18x the
  median encounter count — so assembling that patient's full record into a
  prompt is not possible.
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
- **Correction (pass 2):** the column labelled `lab_orders` above is
  `COUNT(*) FROM procedure_order`, which includes non-laboratory procedure
  orders (F-42). For pid=20:
  `SELECT procedure_order_type, COUNT(*) FROM procedure_order WHERE patient_id=20 GROUP BY 1`
  → `laboratory_test 130`, `order 777`. Those 130 lab orders have 130 reports
  and **3,040** result rows. The outlier claim stands, and is now quantified:
  a raw SQL dump of this patient's record is **974,140 bytes** (F-46). The
  droplet's worst case is pid=27 with 410 encounters (range 1–410, mean 62.8).
- **Verified:** `first-hand`
- **Implication for the agent:** Whole-chart prompting is not a viable design at
  any patient volume. The co-pilot needs retrieval — time-windowed,
  problem-scoped or query-relevant selection — plus a hard token budget and a
  deterministic truncation policy that tells the user what was omitted.

### F-23 — Encounter dates span more than a century
- **Category:** Data Quality
- **Severity:** Low
- **Claim (as corrected in pass 2):** Encounter dates run from 1922-11-04 to
  2026-09-12 locally (a 104-year span); the newest records are two days before
  the audit date.
- **Evidence:**
  ```
  $ docker compose exec -T mysql mariadb -uopenemr -popenemr openemr -t -e "SELECT MIN(date) mn, MAX(date) mx FROM form_encounter;"
  +---------------------+---------------------+
  | mn                  | mx                  |
  +---------------------+---------------------+
  | 1922-11-04 18:15:55 | 2026-09-12 16:42:47 |
  +---------------------+---------------------+
  ```
- **Correction (pass 2):** the original claim said the range included
  "future-dated records relative to the audit date". The maximum shown in the
  evidence above (2026-09-12 16:42:47) precedes the audit date (2026-09-14),
  and the droplet maximum (2026-09-10 13:55:13) does too, so no future-dated
  encounter was observed; the claim is withdrawn. The span itself is
  population-specific: droplet `form_encounter` runs **1945-04-25 01:27:11** to
  **2026-09-10 13:55:13** (81 years). `first-hand`.
- **Verified:** `first-hand`
- **Implication for the agent:** Relative date reasoning ("recent", "last
  year") must be computed against the encounter date, not assumed to fall in a
  plausible window. Date parsing and sorting must handle a 104-year span without
  overflow or two-digit-year ambiguity.

### F-42 — `procedure_order` is 92% non-laboratory; "lab orders" figures overstate labs about 12x
- **Category:** Data Quality
- **Severity:** Medium
- **Claim:** Of 7,417 local `procedure_order` rows only **596** are laboratory
  orders; the other **6,821** are Synthea procedure orders (depression
  screening, renal dialysis, dental care, etc.) that never have a report or
  result. The same split holds on the droplet (1,177 of 11,250). Every
  `procedure_report` belongs to a `laboratory_test` order. Questionnaire
  instruments are also stored as laboratory tests.
- **Evidence:**
  ```
  $ … -e "SELECT po.procedure_order_type, COUNT(*) orders, SUM(EXISTS(SELECT 1 FROM procedure_report pr WHERE pr.procedure_order_id=po.procedure_order_id)) with_report FROM procedure_order po GROUP BY po.procedure_order_type ORDER BY orders DESC;"
  LOCAL                                   DROPLET
  order            6821   with_report 0   order            10073   with_report 0
  laboratory_test   596   with_report 596 laboratory_test   1177   with_report 1177

  -- top procedure_name for type 'order' (local):
  Depression screening (procedure) 649 · Renal dialysis (procedure) 410
  Assessment of health and social care needs (procedure) 366 · Medication reconciliation (procedure) 303
  Dental consultation and report (procedure) 273 · Oral health education (procedure) 269 …

  -- top procedure_name for type 'laboratory_test' (local):
  Generalized anxiety disorder 7 item (GAD-7) 110 · Basic metabolic panel - Blood 104
  CBC panel - Blood by Automated count 84 · Lipid panel with direct LDL - Serum or Plasma 62 …
  ```
  `procedure_order_code.procedure_type` shows the identical split.
- **Verified:** `first-hand`
- **Implication for the agent:** "Lab" retrieval must filter
  `procedure_order_type='laboratory_test'`; otherwise it returns hundreds of
  result-less procedures per patient. Conversely, procedures such as dialysis
  are clinically important and are *only* discoverable through this table. GAD-7
  scores arriving as "lab results" must not be characterised with lab-range
  logic (cf. F-19).

### F-43 — Local and droplet hold different synthetic populations
- **Category:** Data Quality
- **Severity:** Informational
- **Claim:** The two environments were seeded by separate Synthea runs and do
  not share patients. Per-patient volume differs markedly — the droplet has
  fewer patients but about 4x the lab results — so any count, outlier or timing
  measured in one environment does not transfer numerically to the other.
- **Evidence:** census table above (2026-09-14T22:31Z). Derived: lab results
  per Synthea patient — local batch 1 6,833/57 = **120**, droplet 29,701/50 =
  **594**; encounters per patient mean — local **43.1**, droplet **62.8**; worst
  case — local pid=20 (530 encounters), droplet pid=27 (410). Demo-dump
  patients pids 1–3 (dated 2014) are present in both.
- **Verified:** `first-hand`
- **Implication for the agent:** Evaluation cases, golden answers and
  performance baselines must name the environment and seed they were built
  against. A test that references `pid=20` is meaningless on the droplet.

### F-55 — POSITIVE: medication records carry machine-checkable RxNorm codes
- **Category:** Data Quality
- **Severity:** Positive
- **Claim:** 311 of 312 prescriptions (99.7%) carry a non-empty
  `rxnorm_drugcode` value. Medication claims can be attributed to a standard,
  machine-checkable code, not just free text.
- **Evidence:** droplet, post-reseed — a fresh `dev-reset-install-demodata`
  followed by `import-random-patients 50` (development mode on) — in a later
  session than the pass-2 census above. No separate timestamp was recorded
  for this specific query; the closest anchor in this ledger is F-54's
  `2026-09-15T00:54:04Z – 15:04:39Z` interval, the only droplet stack restart
  between the incident's discovery and its fix whose container ran long
  enough for this kind of session but whose bindings went unlogged — offered
  as the nearest available session context, not a confirmed timestamp for
  this query.
  ```
  $ (droplet) SELECT COUNT(*) total, SUM(rxnorm_drugcode IS NOT NULL AND rxnorm_drugcode <> '') coded FROM prescriptions;
  +-------+-------+
  | total | coded |
  +-------+-------+
  |   312 |   311 |
  +-------+-------+
  ```
  This population (312 prescriptions) matches neither the pass-2 droplet
  census (373, taken 2026-09-14T22:31:16Z) nor the local census (305),
  confirming it reflects a distinct, later reseed rather than a re-read of
  previously recorded data.
- **Verified:** `first-hand`
- **Implication for the agent:** Medication claims in the verification layer
  can bind to `source_id` + RxNorm code — a stronger attribution surface than
  most other clinical fields in this dataset. Contrast explicitly with F-19:
  lab results carry zero reference ranges and zero abnormal flags across
  7,259 (local) / 29,701 (droplet) `procedure_result` rows. Medications and
  labs warrant different verification-strength assumptions in
  `ARCHITECTURE.md` — the agent can cite a medication against a standard code
  with high confidence, but cannot do the equivalent for a lab value's
  clinical significance.

### F-57 — Uncoded allergy renders as "(unspecified allergen)" and passes verification
- **Category:** Data Quality / Verification
- **Severity:** High
- **Claim:** An allergy that OpenEMR stores without a coded allergen reaches the
  physician as `(unspecified allergen)`, marked verified. OpenEMR's FHIR
  mapping sends such a record with `code` set to the data-absent-reason
  placeholder and the allergen name only in the resource narrative
  (`text.div`). The agent's FHIR client reads only `code` and falls back to a
  fixed string; the model copies that string; the verifier compares it with
  the same parsed record and passes it. The API collection's own demo patient,
  Phil Belford (`a2c0e096-23d5-4cf9-ad5b-4895597363a7`), has a penicillin
  allergy that renders this way.
- **Evidence:**
  - OpenEMR mapping (`first-hand`, source): `src/Services/FHIR/FhirAllergyIntoleranceService.php:217`
    sets `code` to `UtilsService::createDataAbsentUnknownCodeableConcept()`
    when the record has no diagnosis code; `:221` puts the record's `title`
    into the narrative (`createNarrative($dataRecord['title'], "additional")`).
  - Raw resource (`first-hand`, droplet FHIR API, 2026-09-16 ~21:40Z),
    `AllergyIntolerance/a2c0e096-2141-41eb-94a3-8295a93183e8`:
    ```
    "text": {"status": "additional", "div": "<div xmlns='http://www.w3.org/1999/xhtml'>penicillin</div>"}
    "code": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/data-absent-reason", "code": "unknown", "display": "Unknown"}]}
    ```
  - Agent parsing (`first-hand`, source): `agent/app/fhir/client.py:139`
    `_display_text` tries `code.text`, then coding displays, skipping the
    data-absent system (`:154`), then returns the caller's fallback;
    `get_allergy_intolerances` passes `fallback="(unspecified allergen)"`
    (`:348`). The narrative is never read.
  - Live brief (`first-hand`, deployed agent `POST /pre-visit-brief`, same
    result in every run 2026-09-16 21:40Z–22:48Z):
    ```
    Allergies:
      - (unspecified allergen)
    verification: {"claims": 5, "verified": 5, "unverified": 0}
    ```
  - Eval cases (`first-hand`, run 2026-09-16 22:45–22:48Z, `agent/evals/RESULTS.md`):
    ```
    A5 FAIL  allergy a2c0e096-2141: chart names it ['penicillin'], brief shows ['(unspecified allergen)']
    B1 FAIL  a2c0e096-23d5-4cf9-ad5b-4895597363a7: verified claim "(unspecified allergen)" on
             AllergyIntolerance/a2c0e096-2141: the chart record does not hold that value (it holds ['penicillin'])
    ```
    Caught because the suite reads raw FHIR JSON with its own parser
    (`agent/evals/oracle.py`, no imports from `agent/app/`). A check built on
    the agent's parsed records would have passed.
  - Extent (`first-hand`, same population): the only uncoded record among the
    50 AllergyIntolerance records visible to the test clinician.
- **Remediation:** done 2026-09-18 on `fix/f57-uncoded-values`, **not
  deployed**. Built and tested locally against the live Vultr FHIR API.
  - *Name recovery* (`agent/app/fhir/client.py` `_record_name`): a record's
    name is its code text, else its first non-data-absent coding display,
    else its narrative, else `None`. This applies to all three brief types
    (Condition, MedicationRequest, AllergyIntolerance). The three fixed
    placeholders (`(unspecified allergen|problem|medication)`) are gone.
  - *The narrative is untrusted HTML.* `UtilsService::createNarrative`
    (`src/Services/FHIR/UtilsService.php:374-383`) concatenates the chart
    title into the `div` unescaped, so anything typed as a title arrives as
    markup. `_narrative_name` never pattern-matches it. It tokenizes it with
    the standard library's `html.parser.HTMLParser` and keeps text nodes
    only; attributes are never read. It refuses the whole narrative (`None`,
    no repair) if the narrative has status `empty`, or contains any element
    outside FHIR's narrative formatting set (constraint txt-1: so `script`,
    `style`, `iframe`, `object`, `svg`, `form`, unknown tags), or a CDATA
    section, processing instruction or doctype. It also refuses if the
    extracted text still contains `<` or `>` (escaped markup such as
    `&lt;script&gt;`, or a stray bracket), or is longer than 255 characters
    (`lists.title` is `varchar(255)`). Every name the client returns, coded or
    narrative, is reduced to one line: control, format (zero-width,
    bidirectional override) and surrogate characters are dropped, and
    whitespace, including line breaks, is collapsed. A name can therefore
    neither form a tag in any downstream HTML context nor start a new line
    of the plain-text brief.
  - *A record nothing names is visibly uncertain, never verified.* `display`
    is `str | None` on all three models. A record with no name is withheld
    from the model (`draft_claims.drafted_records`), so there is no
    placeholder to copy. The pipeline states it itself as a `display` claim
    with value `None` (`unnamed_record_claims`). The verifier gained one
    rule that is stricter, never looser: a `display` claim never verifies
    against a record whose `display` is `None`
    (`FailureReason.NO_NAME_ON_SOURCE`), even though other absent fields
    accept a `None` claim. The brief shows
    `UNVERIFIED: "(name not available)" (the chart record has no usable name;
    review it in the chart)`, the footer and API `verification` count it
    as unverified, and both front-ends already flag that line shape. The
    follow-up renderer uses the same wording. The verifier otherwise still
    checks only that a claim matches the parsed record.
  - *Live result* (Vultr, 2026-09-18, the same patient re-seeded as Phil
    Belford `a2c36e54-af42-4c38-b86e-ade616ed5494`, allergy
    `a2c36e54-abdd-4e69-911c-11516235801e`, same raw shape as above; local
    agent, real model): before, `Allergies: - (unspecified allergen)`,
    5/5 verified; after, `Allergies: - penicillin`, claim value
    `penicillin` verified against the narrative, 5/5 verified.
  - *Tests:* `agent/tests/test_uncoded_names.py` (47 cases: the live
    record's exact shape, all three types, an injection matrix and 5,000
    seeded fuzz narratives, and the API end to end with FHIR transport
    faked), plus additions to `test_verifier.py`,
    `test_pre_visit_brief_render.py`, `test_draft_claims_stub.py` and
    `test_follow_up.py`. Evals: A5 and B1 now pass, and A5 requires the name
    to be the narrative's and no placeholder anywhere. B1 also flags a coded
    record shown as nameless, with a control. New group H mutates live
    records in flight: an uncoded condition (H1) and an uncoded medication
    (H2) named from the narrative; placeholder code with no narrative shown
    UNVERIFIED (H3); markup-bearing narratives (H4). Full eval runs
    (`first-hand`, 2026-09-18), same setup both times (local agent, live
    Vultr FHIR, real model): at `f6f85a6`, before the fix, 22 PASS, 4 FAIL
    (A5, A6, B1, C5), 1 SKIP (D6). With the fix: 28 PASS, 2 FAIL (A6, which
    is F-58, and C5), 1 SKIP (D6), of 31 cases. H1–H4 run against
    `f6f85a6` all fail, each with a verified `(unspecified …)` placeholder.
  - *Extent on Vultr* (`first-hand`, 2026-09-18): 1 of 36 AllergyIntolerance,
    0 of 1,816 Condition and 0 of 592 MedicationRequest records lack a coded
    name. OpenEMR sends no narrative at all for Condition or
    MedicationRequest. Its mappers put an uncoded problem's title in
    `code.text` (`FhirConditionTrait.php:153-156`) and an uncoded drug's name
    in `medicationCodeableConcept.text` (`FhirMedicationRequestService.php:463-466`),
    which the client already read. The narrative fallback for those two
    types is therefore proven by mutation (H1, H2), not by a live record.
  - *Residual, not changed:* when a problem's `title` is null, OpenEMR
    itself sets `code.text` to the literal `"Problem"`
    (`FhirConditionTrait.php:155`). That placeholder cannot be told apart
    from a real title and still renders as a name. No live record has it.
- **Verified:** `first-hand` (mapping, raw resource, parsing path, live brief,
  eval failures) · `operator-reported` (that this is the patient-safety failure
  the case study brief opens with; the brief is not in the repository) ·
  inferred (that allergies entered by hand without choosing a code take the
  same path; this population holds one such record).
- **Implication for the agent:** Verification (`ARCHITECTURE.md` §5)
  guarantees that a claim matches the record as the agent parsed it, not what
  OpenEMR stored. A parsing gap upstream of verification produces a verified,
  wrong claim, and here a "5 of 5 verified" result hides a drug allergy. This
  is §5's "source data accuracy" limitation observed live, on the demo
  patient. Checking the brief needs a chart reader that shares no code with
  the agent.

### F-58 — Allergy clinical status is discarded before drafting; a resolved allergy would render as current
- **Category:** Data Quality
- **Severity:** Medium (latent — no live allergy is resolved)
- **Claim:** OpenEMR's FHIR `AllergyIntolerance` carries `clinicalStatus`
  (`active`, `resolved`, `inactive`), but the agent's allergy model has no
  status field, so status never reaches the model or the renderer. A resolved
  allergy would be listed under "Allergies:" as current, and the "none active"
  wording the brief uses for all-inactive sections can never appear for
  allergies.
- **Evidence:**
  - OpenEMR mapping (`first-hand`, source): `FhirAllergyIntoleranceService.php:117-121`
    emits `resolved` when the record has an end date and outcome `1`,
    `inactive` for any other end date, and `active` otherwise.
  - Agent (`first-hand`, source): `agent/app/models/fhir_resources.py:125`
    `AllergyIntolerance` holds `source_id`, `display` and `recorded_date` only;
    `agent/app/reasoning/draft_claims.py:83` ("AllergyIntolerance has no status
    field, so every allergy record counts"), so `brief_records` keeps every
    allergy and the section's `in_brief` always equals `retrieved`.
  - Eval case A6 (`first-hand`, run 2026-09-16 22:45–22:48Z): a real patient's
    real Bundle (`a2c0e47b-ec57-40e5-be47-9844f7ae98a1`, 10 allergies) with
    only `clinicalStatus` changed in-test to OpenEMR's `resolved` coding — no
    write to the chart. Drafting was replaced by a stand-in that follows the
    system prompt, since no model can act on a field it is not given.
    ```
    A6 FAIL  10 resolved allergies rendered under 'Allergies:' as current, e.g. ['Allergic disposition (finding)',
             'Mold (organism)', 'House dust mite (organism)']; the model's input for AllergyIntolerance has only
             ['display', 'recorded_date', 'source_id'], so no status can reach the brief
    ```
  - Live state (`first-hand`, droplet FHIR API, 2026-09-16 ~21:30Z): all 50
    AllergyIntolerance records, across the 10 patients with any, report
    `clinicalStatus` `active`. Nothing renders wrongly today.
- **Remediation:** not done. Carry clinical status on the allergy model and
  apply the same active filter used for conditions and medications, so
  "none active" is reachable for allergies.
- **Verified:** `first-hand` (mapping, model, eval result, live census) · not
  observed on a real resolved allergy (none exists in this population).
- **Implication for the agent:** The brief's "not recorded" / "none active"
  distinction (`ARCHITECTURE.md` §5) holds for conditions and medications
  only. For allergies it fails silently the first time a clinician records an
  end date; that no live record has one is a property of this seed (F-43),
  not of the code.

### F-62 — A question that names another patient is answered for the open chart without saying so
- **Category:** Verification / Presentation (comprehension; **not** data exposure)
- **Severity:** Medium
- **Claim:** A follow-up question that names a person other than the patient
  whose chart is open is answered as if it had named the open patient. With
  Phil Belford's chart open, "which of Tonja's medications are active?"
  returned Phil's medications, verified against Phil's chart, with nothing
  saying that the name in the question is not the open patient. Every
  statement in the answer is true of the chart it cites, and the answer
  counts Phil's records. What it never says is *whose* they are, so a
  physician who asks about patient X with patient Y's chart open gets a
  confident, fully verified answer about Y. That is a wrong-patient
  comprehension risk. In kind it is F-21's and F-57's: the system is honest
  about the data it used, and a rushed reader can still take the wrong thing
  from it. Here the wrong thing is which patient the answer is about.
- **What this is not:** a cross-patient leak. Isolation held. Only the open
  patient's records were retrieved, drafted from, verified and shown; none of
  Tonja658 Grant908's data was read or appeared. The defence against
  conversation state following the clinician to another chart (F-50;
  `agent/app/conversation/state.py`; eval I1; the embed's
  `ConversationSessionId`) is a different mechanism, was not involved, and is
  intact. Nobody saw data they should not have; somebody could misread whose
  data they saw.
- **Evidence:**
  - Found (`operator-reported`, 2026-09-19): live browser test of the
    deployed chart embed, operator-driven. Phil Belford's chart (pid 1,
    `a2c36e54-af42-4c38-b86e-ade616ed5494`) open; question "which of Tonja's
    medications are active?"; answer Norvasc and Lisinopril, verified.
  - Reproduced (`first-hand`, deployed agent `POST /pre-visit-brief/conversation`,
    2026-09-20T04:10Z, correlation `ff10bdf3-e7f5-4b02-945b-489f389e17f1`),
    same question, `patient_id` Phil's:
    ```
    turn_status: answered   verification: {"claims": 4, "verified": 4, "unverified": 0}
    refusals: []   gaps: []   patient_id: a2c36e54-af42-4c38-b86e-ade616ed5494
      - Medication: Norvasc
      - Medication "Norvasc", status: active
      - Medication: Lisinopril
      - Medication "Lisinopril", status: active
    Answered only from what was retrieved for this turn: 2 problem records (2 active),
    2 medication records (2 active), 1 allergy records, ...
    claims cite MedicationRequest a2c36e54-abe0-41e5-993f-775c51e0b2ad (Norvasc)
                and             a2c36e54-abe3-4cd7-8db9-8b4bf50931f0 (Lisinopril)
    retrieval: Condition 2, MedicationRequest 2, AllergyIntolerance 1
    ```
    Both source ids are Phil's records (the same ids his brief cites). The
    answer contains neither "Tonja" nor either of her medications
    (Fexofenadine, the epinephrine auto-injector).
  - Why (`first-hand`, source). Every layer binds the turn to the open chart,
    and none reads the question for whom it is about:
    - the embed sends the patient from the session, never from the browser
      beyond a `pid` that must match it (`ChartRequest`, module
      `public/conversation.php`). That guard compares the *request's* pid
      with the open chart. It does not, and cannot, look at the question;
    - the agent retrieves only for the request's `patient_id`
      (`agent/app/graph/conversation.py` `_retrieve`);
    - routing refuses questions about other patients only for generic
      wording: `agent/app/conversation/routing.py:81-85`, category
      `other_patients`, matches "another patient", "someone else", "patient
      list" and the like. A proper name matches nothing: `route("Which of
      Tonja's medications are active?", frozenset())` returns no refusal and no expansion;
    - the drafting prompt (`agent/app/reasoning/draft_conversation_turn.py`)
      gives the model one chart's records and a question. The model is never
      told the patient's name (the agent discards demographics unread,
      `agent/app/fhir/client.py` `ensure_patient_exists`), so it has nothing
      to compare "Tonja" with, and it answers from the records it holds.
- **Remediation:** mitigated 2026-09-20 on `feat/answer-patient-header`,
  **not deployed**. A display change, not detection.
  - *Every answer names its patient, first.* In the chart embed each
    follow-up turn, and the brief itself, now opens with a full-width bar:
    "Answer for Phil Belford · DOB …" / "Pre-Visit Brief for …". The
    asked-about-X, answered-for-Y mismatch is then in front of the physician
    on every turn, for them to catch.
  - *The name is the server's.* `brief.php` and `conversation.php` build it
    (`OpenPatient`) from the `patient_data` row of the pid `ChartRequest` has
    just matched to the session, in the same request, from the same row that
    supplies the UUID sent to the agent. It is never taken from the browser
    or the question. `PreVisitBrief::toCardPayload()` and
    `ConversationTurn::toCardPayload()` now *require* an `OpenPatient`, so a
    payload cannot be built unnamed; a patient row with no name fails the
    request; and the card treats a response without `patient.name` as a
    failure rather than rendering it. `AgentClient` additionally rejects an
    agent response whose `patient_id` is not the UUID it sent, so the name
    shown is the patient the answer was computed against.
  - *Standalone UI:* each turn opens with "Answer for patient `<uuid>`",
    from the response's own `patient_id`, not the form. That page identifies
    patients by id throughout and has no name to show: the agent never reads
    demographics, and this change keeps it that way. An id is a weaker
    at-a-glance cue than a name; the embed, where charts are switched, is
    the surface that matters.
  - *Why not detect the name.* Deciding whether a question refers to someone
    other than the open patient is a fuzzy language problem: first names,
    nicknames, misspellings, "my last patient", pronouns, a relative named in
    a family-history question, a drug or condition that is also a name. It
    would also mean giving the patient's name to the agent, which today
    never holds it. A detector that catches "Tonja" and misses the next
    phrasing buys false confidence: the physician learns that the co-pilot
    objects when the patient is wrong, and trusts its silence. The header
    makes no such promise. It is deterministic, costs no model call, cannot
    be wrong about who the answer is for, and leaves the judgment with the
    person who knows whom they meant.
  - No retrieval, routing, drafting, verification or conversation-state code
    changed. Tests: agent 351 passed (344 before), including "a question
    naming someone else is answered for the open chart and says whose" and
    "every turn states the patient it was answered for", and a source check
    that the standalone page's turn opens with the response's `patient_id`;
    module PHPUnit 89 tests, 167 assertions (76 before), PHPStan level 10 and
    PHPCS clean. Both surfaces were rendered in headless Chrome with this
    finding's question: the embed with stubbed endpoints, the standalone page
    served locally against the live FHIR API.
- **Verified:** `first-hand` (reproduction, source) · original observation
  `operator-reported`.
- **Residual risk:** the header depends on being read. A physician who does
  not look at it is where they were before. The agent's plain-text `answer`
  (and any other client of the API) still does not name the patient; only
  `patient_id` in the response does.

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
- **Cross-check (pass 2):** an authenticated read could not be reproduced (no
  token; see F-47). Unauthenticated reference points on the same endpoint,
  local, 5 runs: `Patient?_count=5` with no token → 401 in median **0.116 s**;
  `fhir/metadata` → 200 in median **0.324 s**. These bracket framework
  overhead, not an authenticated read. `first-hand`.
- **Verified:** `operator-reported`
- **Implication for the agent:** A co-pilot turn that makes several FHIR calls
  before even reaching the model starts from a multi-second floor. Data
  retrieval must be parallelised and cached per turn, and the UI needs to stream
  or show progress rather than block.

### F-25 — Lab join plan is cost-based: it scans `procedure_report` only when that table is smaller than the patient's own orders
- **Category:** Performance
- **Severity:** Low (was Medium — corrected in pass 2, see below)
- **Claim (as corrected in pass 2):** For the worst-case local patient
  (pid=20), the optimiser drives the three-table lab join from a full scan of
  `procedure_report` rather than seeking `procedure_order` by `patient_id`. This
  is a cost-based choice specific to that data shape — pid=20's 907 orders
  outnumber the entire 596-row report table. For typical local patients, and
  for the droplet's worst case, the optimiser seeks by `patient_id` first. The
  measured cost difference is ~0.1 ms, within noise.
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
- **Correction (pass 2):** re-verified, and the earlier conclusion was
  over-generalised.
  1. The 596 rows are the **entire** `procedure_report` table (census
     `lab_reports 596`; `information_schema.tables.table_rows` 596). Statistics
     are not stale.
  2. Sizes for pid=20: 907 orders (`patient_id` index entries), 130 reports,
     3,040 results. Scanning 596 report rows is cheaper than probing 907 index
     entries, so the plan is reasonable here.
  3. For other local patients the plan seeks by patient first:
     `pid=18` (220 orders) and `pid=4` (133 orders) → `po ref patient_id`, then
     `pr ref procedure_order_id`, then `prs ref procedure_report_id`.
  4. On the droplet (1,177 reports) the worst-case patient pid=27 (711 orders)
     gets the patient-first plan by default.
  5. Measured with `ANALYZE FORMAT=JSON` (executes the SELECT; `r_total_time_ms`), 3 runs each:
     ```
     LOCAL pid=20  default (pr ALL 596 → po eq_ref → prs ref):         1.121 · 1.093 · 1.148 ms
     LOCAL pid=20  STRAIGHT_JOIN (po ref 907 → pr ref → prs ref):      1.038 · 1.056 · 1.013 ms
     DROPLET pid=27 default (already po ref 711 → pr ref → prs ref):   1.415 · 1.418 · 1.403 ms
     DROPLET pid=27 STRAIGHT_JOIN (identical plan):                    1.256 · 1.286 · 1.367 ms
     ```
     On the droplet the *same* plan varies by 0.05–0.16 ms between the two
     statements, so the local ~0.1 ms gap is within noise.

  The claim "does not seek by patient first" is true only for pid=20 locally.
  The severity is lowered to Low because no material cost was measured. The
  scaling concern survives only as a conditional: if one patient's order count
  exceeds the global report count at larger scale, the scan grows with global
  lab volume.
  This exact join shape exists in the codebase only as dead code —
  `src/Services/ProcedureService.php:558-576` `searchProcedureReports()` builds
  the `$query` string but never executes or returns it, and no caller exists
  (`grep -rn searchProcedureReports src interface library apis` finds only the
  declaration).
- **Verified:** `first-hand`
- **Implication for the agent (as corrected in pass 2; previously "must not issue
  this join shape … otherwise lab lookups degrade linearly with total lab volume
  rather than with the patient's own"):** No special query shape is needed for lab
  retrieval at current scale. Filter by `patient_id` plus
  `procedure_order_type='laboratory_test'` (F-42) and a date window, and
  re-check the plan with `ANALYZE` if order volume per patient approaches the
  global report count.

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
- **Correction (pass 2) to the note on magnitude:** re-measured with 5 runs per
  endpoint (2026-09-14 ~22:32Z), the distributions do **not** overlap:
  ```
  localhost readyz: 0.136 0.133 0.132 0.121 0.117  (median 0.132 s)
  localhost livez:  0.065 0.053 0.046 0.048 0.051  (median 0.051 s)
  droplet   readyz: 0.417 0.281 0.282 0.372 0.302  (median 0.302 s)
  droplet   livez:  0.232 0.238 0.226 0.272 0.228  (median 0.232 s)
  ```
  Locally, readyz's fastest run (0.117 s) is slower than livez's slowest
  (0.065 s); the median gap is ~81 ms (~2.6x). The earlier 2-run livez sample (0.140/0.089 s)
  was likely warm-up affected. The penalty is still modest in absolute terms,
  but it is consistent. `livez` short-circuits before any bootstrap at
  `meta/health/index.php:30-34`; `readyz` requires `interface/globals.php` at
  `:66`. The Compose healthcheck invokes readyz every minute
  (`docker-compose.yml:120` `https://localhost/meta/health/readyz`, `:123`
  `interval: 1m`), which the droplet access log confirms (`::1 … "GET /meta/health/readyz" … "curl/8.19.0"` once per minute).
- **Verified:** `first-hand`
- **Implication for the agent:** If the co-pilot adds its own health or
  readiness endpoint, it should not copy this pattern — a probe that boots the
  whole framework will not stay cheap as the deployment grows. Low priority.

### F-44 — Droplet compute profile: plan type not verifiable from inside, and DigitalOcean shared CPU has no credit throttling
- **Category:** Performance
- **Severity:** Informational
- **Claim:** The droplet presents 4 vCPU (AMD EPYC 9555P, KVM) and ~12 GB RAM.
  The operator reports a Shared (non-Dedicated) plan. That cannot be confirmed
  from inside the guest. On DigitalOcean, shared vCPUs are subject to
  hypervisor contention, observable as steal time — not to AWS-style
  CPU-credit throttling. Cumulative steal since boot, including the Synthea
  import, is effectively zero.
- **Evidence:**
  ```
  $ lscpu → Model name: AMD EPYC 9555P 64-Core Processor · Thread(s) per core: 1 · Core(s) per socket: 4 · Socket(s): 1 · Hypervisor vendor: KVM
  $ grep MemTotal /proc/meminfo → 12248264 kB     $ docker info → NCPU=4 MemTotal=12542222336 Arch=x86_64
  $ dmidecode -s system-product-name → Droplet    $ metadata /v1/region → mem1
  $ getconf CLK_TCK → 100
  $ awk '/^cpu /' /proc/stat (uptime 7740 s) → steal 12 jiffies (0.12 s) · iowait 29381 (293.8 s) · user 35640 · system 28897
  $ vmstat 1 5 → st column 0 in all samples
  ```
  The DigitalOcean metadata service exposes no size/plan field (keys: `id`,
  `hostname`, `user-data`, `vendor-data`, `public-keys`, `region`, `interfaces/`,
  `dns/`, `floating_ip/`, `reserved_ip/`, `tags/`, `features/`, `virtual_ips/`),
  and `doctl` is not installed on the workstation.
  DigitalOcean documentation (https://docs.digitalocean.com/products/droplets/concepts/choosing-a-plan/,
  fetched this session): "With shared CPU Droplets, the hyper-thread allocated
  to the Droplet may be shared between multiple other Droplets"; "shared CPU
  Droplets *can* have access to full hyper-threads, but it's not guaranteed."
  The page describes no CPU-credit mechanism.
  Local comparison: Colima `default` profile — 6 CPUs, 12 GiB, aarch64, docker
  runtime — on an Apple M5 Pro host (`hw.ncpu` 15, `hw.memsize`
  25,769,803,776). Colima vCPUs are not multi-tenant, but they share the host
  with macOS workloads.
- **Correction to the brief:** "burstable CPU credit throttling" is not a
  DigitalOcean mechanism per the cited documentation. The relevant caveat for
  load-test interpretation is neighbour-driven steal time on shared vCPUs,
  which should be recorded (`/proc/stat` steal, `vmstat st`) during any load
  test. The environments also differ in CPU architecture (x86_64 vs aarch64)
  and core count (4 vs 6), and the droplet shows substantial cumulative iowait.
- **Verified:** `first-hand` (guest-visible CPU, memory, steal, iowait;
  DigitalOcean docs fetched). `operator-reported`: the plan type
  (Shared, "4 vCPU/12GB").
- **Implication for the agent:** Latency comparisons between local and droplet
  confound architecture, core count, I/O and tenancy. Load-test results must
  capture steal and iowait alongside latency, so application regressions are
  not confused with host effects.

### F-45 — Synthea import was slower per patient on the droplet; the size of the gap depends on the normaliser
- **Category:** Performance
- **Severity:** Informational
- **Claim:** Measured by patient-row insert timestamps, the droplet imported
  Synthea patients at **9.00 s** per patient against **1.41 s** locally (6.4x).
  The runs were not identical, though: droplet patients carry ~5x more lab
  results each. Normalised by clinical row volume, the droplet is **1.3x–4.3x**
  slower. This is a resourcing observation, not a defect.
- **Evidence:** `patient_data.date` (= `regdate`) is written at insert, and
  consecutive inserts approximate per-patient import time:
  ```
  batch    host     patients  pids   first insert  last insert  span   s/patient-interval  encounters  lab_results  list rows
  batch1   local         57  4–60   16:51:53Z     16:53:12Z    79 s   1.41                2558        6833         1846
  batch2   local        5  61–65  18:15:12Z     18:15:15Z     3 s   0.75                 239         426          166
  batch1   droplet     50  4–53   21:11:52Z     21:19:13Z   441 s   9.00                3328       29701         2811

  inter-insert gaps:  local batch1 min 0 / max 28 / mean 1.41 s, 0 gaps ≥30 s
                      droplet      min 0 / max 127 / mean 9.00 s, 4 gaps ≥30 s
  ```
  (Encounters, lab results and list rows are counted for the pid range of each
  batch; lab results via `procedure_result → procedure_report → procedure_order.patient_id`.)
  Normalisations, local batch1 → droplet:
  ```
  per patient interval     1.41 s    → 9.00 s     6.4x
  per encounter            30.9 ms   → 132.5 ms   4.3x
  per list row             42.8 ms   → 156.9 ms   3.7x
  per 1,000 lab results    11.6 s    → 14.8 s     1.3x
  ```
  Host context: cumulative steal on the droplet since boot is 0.12 s, while
  cumulative iowait is 293.8 s (F-44), so contention for CPU is not a
  plausible explanation; I/O and per-core speed remain candidates, unmeasured.
  The droplet's uuid-registry pass followed at 21:19:15–21:19:23Z
  (`security-administration-insert` rows in `log`).
- **Limits of the proxy:** timestamps have 1-second resolution; the span covers
  N−1 inter-insert intervals and excludes the last patient's clinical rows and
  the uuid pass; wall-clock from the importer's own output was not available
  for either run.
- **Verified:** `first-hand` (DB-timestamp reconstruction on both hosts).
  `operator-reported`: that the two runs used the same Synthea invocation, and
  that the local VM is "6 vCPU/12GB dedicated" (the 6 CPU / 12 GiB figure is
  confirmed first-hand via `colima list`; "dedicated" is not a Colima term).
- **Implication for the agent:** Import or indexing jobs the co-pilot adds
  (embeddings, chart summaries) will scale with clinical row volume, not with
  patient count. Budgets and progress reporting should be expressed per row
  type, and timing comparisons across environments must normalise for data
  volume.

### F-46 — Worst-case SQL context assembly takes ~5–7 ms server-side; the ~1 MB payload is the constraint
- **Category:** Performance
- **Severity:** Informational
- **Claim:** A full SQL-level assembly of the worst-case local patient (pid=20)
  — demographics, encounters, problems, allergies, medication list,
  prescriptions, immunizations, vitals, procedure orders and lab results —
  runs in **5.1–6.9 ms** of server time and returns **974,140 bytes**. Database
  latency is not the bottleneck for context building; volume is.
- **Definition of "context assembly":** no co-pilot code exists yet, so this
  measures the ten `SELECT *` queries a naive assembler would issue, in one
  MariaDB session. It is not a FHIR-level assembly (blocked, F-47), and raw TSV
  bytes are an upper bound on prompt size, not a token count.
  ```sql
  SELECT * FROM patient_data WHERE pid=@pid;
  SELECT * FROM form_encounter WHERE pid=@pid ORDER BY date;
  SELECT * FROM lists WHERE pid=@pid AND type='medical_problem';
  SELECT * FROM lists WHERE pid=@pid AND type='allergy';
  SELECT * FROM lists WHERE pid=@pid AND type='medication';
  SELECT * FROM prescriptions WHERE patient_id=@pid;
  SELECT * FROM immunizations WHERE patient_id=@pid;
  SELECT * FROM form_vitals WHERE pid=@pid;
  SELECT po.*, poc.procedure_code, poc.procedure_name FROM procedure_order po JOIN procedure_order_code poc ON poc.procedure_order_id=po.procedure_order_id WHERE po.patient_id=@pid;
  SELECT po.procedure_order_id, po.date_ordered, pr.date_report, pr.report_status, prs.* FROM procedure_order po JOIN procedure_report pr ON pr.procedure_order_id=po.procedure_order_id JOIN procedure_result prs ON prs.procedure_report_id=pr.procedure_report_id WHERE po.patient_id=@pid;
  ```
- **Evidence (local, pid=20):**
  ```
  wall-clock via `docker compose exec -T mysql mariadb --batch`, 5 runs each (Python perf_counter):
    baseline SELECT 1:      0.205 0.082 0.098 0.089 0.080   median 0.089 s
    context assembly:       0.105 0.095 0.101 0.089 0.091   median 0.095 s
  server-side (SET profiling=1; SHOW PROFILES), 3 passes over the 10 queries:
    pass1 6.92 ms · pass2 5.21 ms · pass3 5.13 ms   (lab-results join is the largest single query: 3.02 / 2.43 / 2.39 ms)
  output: 974,140 bytes · 4,612 lines   (bytes/4 heuristic ≈ 243,535 tokens — heuristic only, not tokenised)

  per section (rows / bytes):
    patient_data 1 / 2,182 · form_encounter 530 / 145,985 · problems 92 / 24,028 · allergies 0 / 0
    medications(lists) 9 / 2,638 · prescriptions 9 / 3,020 · immunizations 14 / 2,865 · form_vitals 1 / 480
    procedure_order+code 907 / 221,453 · lab results join 3,040 / 571,489
  ```
  **Droplet, pid=27 (worst case there):**
  ```
  wall-clock via ssh + docker exec: baseline SELECT 1 median 0.942 s (0.968 0.942 0.775 0.847 0.979)
                                    context assembly median 1.049 s (1.102 1.045 1.049 1.049 1.050)
  server-side: 6.83 · 6.16 · 6.02 ms
  output: 749,934 bytes · 3,521 lines
  ```
  The wall-clock deltas over baseline (~6 ms local, ~107 ms droplet over SSH)
  are dominated by transport; server-side time is comparable across hosts.
- **Verified:** `first-hand`
- **Implication for the agent:** Retrieval latency from the database is
  negligible; the design pressure is entirely on selection and summarisation.
  Lab results alone are 59% of pid=20's raw bytes, and encounters plus procedure
  orders another 38%. Those three sections are where windowing and truncation
  (F-22) must act first. A time-to-first-token budget should be spent on the
  model, not the query.

### F-47 — First network-path latency to the droplet (unauthenticated); authenticated FHIR read blocked
- **Category:** Performance
- **Severity:** Informational
- **Claim:** From the operator workstation over the public internet
  (~53 ms RTT), unauthenticated requests to the droplet complete in
  ~0.23–0.59 s median, ~0.1–0.3 s slower than the same endpoints on localhost.
  TLS setup alone costs ~110 ms per new connection. An authenticated FHIR read
  against the droplet could **not** be performed.
- **Evidence:** `curl -sk -o /dev/null -w "code=%{http_code} dns=%{time_namelookup} tcp=%{time_connect} tls=%{time_appconnect} ttfb=%{time_starttransfer} total=%{time_total} bytes=%{size_download}"`, 5 sequential runs each, new connection per run, ~22:32Z:
  ```
  $ ping -c 5 201.79.25.22 → round-trip min/avg/max/stddev = 49.825/52.847/56.732/2.279 ms

  endpoint                              droplet totals (median)                       localhost totals (median)
  /meta/health/livez          200  0.232 0.238 0.226 0.272 0.228 (0.232)   0.065 0.053 0.046 0.048 0.051 (0.051)
  /meta/health/readyz         200  0.417 0.281 0.282 0.372 0.302 (0.302)   0.136 0.133 0.132 0.121 0.117 (0.132)
  /apis/default/fhir/metadata 200  0.588 0.762 0.581 0.586 0.693 (0.588)   0.493 0.327 0.310 0.324 0.306 (0.324)   35,811 B
  /apis/default/fhir/Patient?_count=5 (no token)
                              401  0.403 0.307 0.304 0.395 0.307 (0.307)   0.119 0.113 0.108 0.118 0.116 (0.116)   113 B
  Bearer garbage, Patient     401  0.329 (1 run)                             0.184 (1 run)

  droplet connection phases, typical run: tcp ≈ 0.050–0.053 s · TLS complete ≈ 0.110–0.115 s
  (outliers: tcp 0.150 / 0.115, TLS 0.241 / 0.183 on first-in-series runs)
  ```
- **Blocked — authenticated FHIR read on the droplet:** no bearer token was
  obtainable within this task's constraints. `api_token` has **0** rows on the
  droplet (no token has ever been issued there). Both registered clients permit
  the `password` grant (F-40), but a password grant needs a user credential.
  The changed `admin` password is not held by this audit. Using a demo account's
  default password (F-32) would mean logging in to an exposed host with a
  known-weak credential, and creating a new client or user would be a write.
  None was done. OAuth discovery also advertises `https://localhost:9300/oauth2/default/token`
  as the token endpoint (F-49), so a standards-following client would not reach
  the droplet's token endpoint without overriding discovery.
- **Verified:** `first-hand` for all unauthenticated timings; authenticated
  read **blocked** (reason above).
- **Implication for the agent:** A co-pilot running off-box pays ~110 ms TLS
  setup plus ~50 ms RTT per round-trip before OpenEMR does any work, so it must
  reuse connections (keep-alive / HTTP/2) and batch FHIR reads. The operator's
  local 0.375 s authenticated floor (F-24) should be expected to rise by at
  least the ~0.19 s localhost→droplet gap seen on the 401 path, before any
  load.

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
- **Correction (pass 2) — versions now verified against the lockfile:**
  ```
  composer.json:14 "php": ">=8.2.0" · :244 platform "php": "8.2"
  composer.json:49 "adodb/adodb-php": "^5.22.11"   → composer.lock v5.22.11
  composer.json:54 "doctrine/dbal": "^4.4"         → composer.lock 4.4.3
  composer.json:75 "laminas/laminas-mvc": "^3.8.0" → composer.lock 3.8.0
  composer.json:115 "smarty/smarty": "^4.5.6"      → composer.lock v4.5.6
  composer.json:122 "symfony/event-dispatcher": "^7.3" → composer.lock v7.4.9
  composer.json:128 "symfony/http-kernel": "^7.3"  → composer.lock v7.4.13  (symfony/dependency-injection v7.4.13)
  composer.json:133 "twig/twig": "^3.22.2"         → composer.lock v3.27.1
  package.json:72 "bootstrap": "4.6.2" · :94 "jquery": "3.7.1"
  ```
  All `CLAUDE.md` framework claims hold, with two refinements. (1) ADOdb is
  still a *direct* dependency (`adodb/adodb-php` 5.22.11), not only a surface
  emulated over DBAL. (2) The runtime PHP in both environments is **8.5.6**
  (`php -r 'echo PHP_VERSION;'` local; `php -v` droplet), while Composer
  resolves dependencies against a **platform of 8.2** — code is tested against
  8.2 constraints but executed on 8.5. Angular's version was not found under a
  top-level `"angular…"` key in `package.json` and remains documentation-sourced.
  Closes verification gap 6.
- **Verified:** `first-hand` for directory structure and (pass 2) dependency
  versions and runtime PHP; Angular 1.8 remains documentation-sourced.
- **Implication for the agent:** Co-pilot code belongs in `/src` under PSR-4
  with strict types, but it must interoperate with legacy callers that expect
  globals and ADODB-style results. Plan an explicit boundary layer rather than
  letting legacy patterns leak into new code. Any co-pilot PHP dependency must
  install under the 8.2 platform constraint and be tested on the 8.5 runtime.

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
- **Correction (pass 2):** all counts re-verified identical (64 / 33 / 14 / 24;
  same 5 ACL files; same 8 modules). Two refinements:
  1. `gacl/` holds phpGACL's **admin UI, docs and config** (`admin/`, `docs/`,
     `Cache_Lite/`, `gacl.ini.php`, `profiler.inc.php`, licence/readme files) —
     not the ACL engine. The engine is `src/Gacl/Gacl.php:38` `class Gacl`
     (with `GaclApi.php:24`, `GaclAdminApi.php:26`), which
     `AclMain.php:123` imports as `OpenEMR\Gacl\Gacl`. See F-50.
  2. The module "precedent" exists **on disk only**. In both environments the
     `modules` table holds just the five Laminas modules (`type=1`:
     Immunization, Syndromicsurveillance, Documents, Ccr, Carecoordination);
     none of the eight custom modules is registered, so none is bootstrapped
     (`src/Core/ModulesApplication.php:141` loads only
     `mod_active = 1 AND type != 1`). No custom module code path has been
     exercised at runtime in this audit. See F-52.
- **Verified:** `first-hand`
- **Implication for the agent:** No new integration mechanism needs inventing.
  The co-pilot UI panel should follow the `oe-module-*` convention in
  `interface/modules/custom_modules/`, hook into `src/Events/` via the Symfony
  dispatcher rather than patching core files, read through `src/Services/FHIR/`,
  and gate every access through `src/Common/Acl/AclMain.php` rather than
  implementing its own permission checks. Registration in `modules` is a
  separate, required step — having the directory present does nothing.

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
- **Cross-check (pass 2) — mechanism verified in source and config:** the
  healthcheck *cannot* fail on application state, because readyz never returns
  a failing HTTP status:
  - `docker/development-easy/docker-compose.yml:112-124`: test is
    `/usr/bin/curl --fail --insecure --location --show-error --silent https://localhost/meta/health/readyz`
    (`--fail` only reacts to HTTP ≥ 400), `interval: 1m`, `retries: 3`.
  - `meta/health/index.php:70` `new JsonResponse($checker->getResultsArray())`
    and `:73-78` the `setup_required` response are both constructed without a
    status argument (default 200). `:91-95` returns 200 even on an exception
    ("Even on error, return 200 with error details in body").
  - Observed on both hosts: `curl -sk https://…:9300/meta/health/readyz` →
    HTTP 200 with body
    `{"status":"setup_required","checks":{"installed":false,…}}`, and both
    containers report `(healthy)` in `docker ps`.
  The operator's observation is the expected consequence. `first-hand`.
- **Verified:** `operator-reported` (the specific not-installed episode);
  mechanism `first-hand`.
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
- **Cross-check (pass 2):** all cited lines re-read this session and
  unchanged. The droplet (284 tables, 53 patients) returns the byte-identical
  body at `https://201.79.25.22:9300/meta/health/readyz`. Its once-a-minute
  healthcheck responses are logged as 136–137 bytes — this body — except a
  single 422-byte response during the demo reset at 21:07:11Z (F-37).
  `first-hand`.
- **Verified:** `first-hand`
- **Implication for the agent:** The co-pilot must not gate startup, health
  checks or deployment automation on `readyz`'s `installed` field or top-level
  `status`. The sibling checks (`database`, `filesystem`, `session`,
  `oauth_keys`, `cache`) are meaningful and can be used; readiness itself needs
  an independent probe such as a table or row count.

### F-48 — The deployment runs the development-easy stack verbatim, not the `docker/release/` build
- **Category:** Architecture
- **Severity:** Medium
- **Claim:** The droplet runs `docker/development-easy/docker-compose.yml`
  unmodified. That means development mode, developer tools, Xdebug with the
  profiler, a writable bind mount of the git checkout into the web container,
  and a composer/npm/webpack build at every container start. The hardened
  multi-stage build in `docker/release/` was not used. The operator reports
  this is permitted by the Stage 2 specification; it is nonetheless a real
  security and performance tradeoff (F-31–F-37).
- **Evidence:**
  - Compose project labels on the droplet container:
    `com.docker.compose.project.working_dir=/home/deploy/openemr-clinical-copilot/docker/development-easy`,
    `config_files=…/docker/development-easy/docker-compose.yml`; the checkout is
    clean at `c4db157` (F-53), so the file in use is the committed one.
  - Running container environment (selected; secrets not printed):
    ```
    EASY_DEV_MODE=yes · EASY_DEV_MODE_NEW=yes · DEVELOPER_TOOLS=yes
    XDEBUG_ON=1 · XDEBUG_PROFILER_ON=1 · XDEBUG_CLIENT_HOST=host.docker.internal
    OPENEMR_DOCKER_ENV_TAG=easy-dev-docker · MYSQL_HOST=mysql · OE_USER=admin
    OPENEMR_SETTING_site_addr_oath=https://localhost:9300 · OPENEMR_SETTING_oauth_password_grant=3
    OPENEMR_SETTING_rest_api=1 · …rest_fhir_api=1 · …rest_portal_api=1 · …rest_system_scopes_api=1
    SELENIUM_USE_GRID=true · SELENIUM_HOST=selenium · SELENIUM_BASE_URL=http://openemr
    secret-bearing variable names present: MYSQL_ROOT_PASS, MYSQL_PASS, OE_PASS, GITHUB_COMPOSER_TOKEN(+_ENCODED, +_ENCODED_ALTERNATE),
                                           OPENEMR_SETTING_couchdb_pass, OPENEMR_SETTING_SMTP_PASS
    ```
    These match `docker-compose.yml:60-90`.
  - Images: `openemr/openemr:flex` (`:29` pinned `sha256:1d2c8345a320…`),
    `mariadb:11.8.8` (`:4`, `be1ef4fe5f14…`), `phpmyadmin:5.2.3` (`:148`,
    `b16dc88d6e62…`); `docker images` on the droplet shows IDs `1d2c8345a320`,
    `be1ef4fe5f14`, `b16dc88d6e62`.
  - Source bind-mounted read-write: `:34` `${OPENEMR_DIR:-../..}:/openemr:ro`,
    `:35` `${OPENEMR_DIR:-../..}:/var/www/localhost/htdocs/openemr:rw`. Container
    log `20:49:47Z Adopting host uid/gid: HOST_UID=1000 HOST_GID=1000`, and on
    the host `id deploy` → `uid=1000(deploy) … groups=…,987(docker)`. The
    web server therefore writes into `deploy`'s git checkout as `deploy`.
  - Build at boot (container log): `apk` installs 155 packages including
    Chromium (from 20:49:47Z); composer install (20:49:56–20:50:10Z); npm
    `added 1174 packages … 26 vulnerabilities (2 low, 11 moderate, 11 high, 2 critical)`
    (20:50:33Z); webpack production theme build (20:50:33–20:51:02Z);
    `EASY_DEV_MODE_NEW enabled: Using local repository from /openemr`
    (20:49:55Z). Setup settings are echoed to the log, including
    `Set couchdb_pass to password` and `Set SMTP_PASS to openemr` (20:51:57Z).
    Start to "Starting apache!" took 131 s.
  - The untaken path exists: `docker/release/` contains `Dockerfile`,
    `openemr.sh`, `ssl.sh`, `xdebug.sh`, `auto_configure.php`, `php.ini`, etc.;
    `Dockerfile:193` `FROM base AS production` and `:430` `FROM production AS final`
    (multi-stage, build-time assets; the Dockerfile still copies `xdebug.sh` at
    `:263`, so its default Xdebug state was not verified). Separately,
    `docker/production/docker-compose.yml:13` also sets
    `MYSQL_ROOT_PASSWORD: root` and publishes `80:80`/`443:443` (`:30-31`).
    Switching compose files alone would therefore not remove the default
    credentials.
  - Spec permission: no Stage 2 specification exists in the repository
    (`grep -rniE "stage ?2"` over `README.md` and `docs/` returns nothing).
- **Verified:** `first-hand` (topology, env, mounts, build log, alternatives on
  disk). `operator-reported`: that the Stage 2 specification permits this
  topology.
- **Implication for the agent:** The co-pilot will be evaluated on a host whose
  startup is non-deterministic (network-fetched dependencies, 26 npm audit
  findings at build) and whose web user can rewrite the application source.
  Before the co-pilot handles real PHI, the deployment must move to a built,
  immutable image (`docker/release/` or equivalent) with dev tooling removed and
  credentials injected, not defaulted.

### F-49 — OAuth/SMART discovery advertises `https://localhost:9300` on the public deployment
- **Category:** Architecture
- **Severity:** Medium
- **Claim:** The droplet's OAuth2/OIDC and SMART discovery documents name
  `https://localhost:9300` as the issuer and as every endpoint, because
  `site_addr_oath` is set from the Compose default. Standards-following external
  clients (SMART apps, the co-pilot if it runs off-box) will be pointed at
  their own localhost.
- **Evidence:**
  ```
  $ curl -sk https://201.79.25.22:9300/oauth2/default/.well-known/openid-configuration | python3 -c '…'
  issuer = https://localhost:9300/oauth2/default
  authorization_endpoint = https://localhost:9300/oauth2/default/authorize
  token_endpoint = https://localhost:9300/oauth2/default/token
  jwks_uri = https://localhost:9300/oauth2/default/jwk
  registration_endpoint = https://localhost:9300/oauth2/default/registration
  grant_types_supported = ['authorization_code', 'password', 'refresh_token']
  $ curl -sk https://201.79.25.22:9300/apis/default/fhir/.well-known/smart-configuration | python3 -c '…'
  smart issuer = https://localhost:9300/apis/default/fhir
  smart token_endpoint = https://localhost:9300/oauth2/default/token
  smart authorization_endpoint = https://localhost:9300/oauth2/default/authorize
  ```
  Source of the value: `docker-compose.yml:83`
  `OPENEMR_SETTING_site_addr_oath: 'https://localhost:${WT_HTTPS_PORT:-9300}'`;
  container log `20:51:57Z Set site_addr_oath to https://localhost:9300`;
  droplet `globals.site_addr_oath` = `https://localhost:9300`. Both registered
  clients' `redirect_uri` is `https://localhost:9300/swagger/oauth2-redirect.html`
  (from the decoded INSERTs, F-40).
- **Verified:** `first-hand` for discovery output and configuration source.
  **Not verified:** the `iss` claim inside an issued token (no token, F-47).
- **Implication for the agent:** An off-box co-pilot doing SMART/OIDC discovery
  against the droplet will fail, or silently talk to its own host. Either
  `site_addr_oath` must be set to the public origin at deploy time, or the
  co-pilot must be configured with explicit endpoints and must validate `iss`
  against a configured value rather than the discovered one.

### F-50 — ACL layer: section-level phpGACL behind a static facade, with an implicit session dependency and no patient scoping
- **Category:** Architecture
- **Severity:** Informational
- **Claim:** Authorization is phpGACL, ported into `src/Gacl/` and wrapped by the
  static `AclMain`. A check answers "may user U perform action R on section S"
  — it takes no patient argument, so there is no per-patient or care-team
  scoping in the core ACL. When no user is passed it reads the user from the
  active session. REST routes invoke it explicitly per route.
- **Evidence:**
  - Facade and engine: `src/Common/Acl/AclMain.php:123` `use OpenEMR\Gacl\Gacl;`,
    `:125` `class AclMain`, `:128` `private static $gaclObject;`, `:136`
    `self::$gaclObject = new Gacl();` (one shared instance per request).
    Engine `src/Gacl/Gacl.php:38` `class Gacl {`, `:229` `acl_check(…)`,
    `:307` `acl_query($aco_section_value, $aco_value, $aro_section_value, $aro_value, …)`.
    `gacl/` contains admin UI/docs/config only (F-28 correction).
  - Check signature and semantics — `AclMain.php:166`
    `public static function aclCheckCore($section, $value, $user = '', $return_value = ''): bool`;
    `:170` `$user = $session->get('authUser') ?? '';` when `$user` is empty;
    `:174` superuser short-circuit (`admin`/`super` grants everything);
    `:181` `$gacl_object->acl_query($section, $value, 'users', $user, null, null, null, null, null, true);`;
    `:234` `if (!$deny && $access) {` (an explicit deny wins).
  - Reach: 531 `AclMain::aclCheckCore` call lines across 305 files in `src`,
    `interface`, `library`, `apis` (grep; custom modules not counted).
  - REST bridge: `src/RestControllers/Config/RestConfig.php:180-183`
    `request_authorization_check()` passes the request session's `authUser` to
    `authorization_check()` (`:185`), which calls `aclCheckCore` at `:187` and
    throws at `:192` `AccessDeniedHttpException("Organization policy does not have permit access resource")`.
    Example FHIR use:
    `apis/routes/_rest_routes_fhir_r4_us_core_3_1_0.inc.php:590`
    `RestConfig::request_authorization_check($request, "patients", "demo");`
    inside `"GET /fhir/Patient"` (`:578`), skipped for patient-context requests
    (`:583` `if ($request->isPatientRequest())` binds `_id` to the token's
    patient instead). The FHIR route file contains 64
    `request_authorization_check` calls.
  - Dead code: `RestConfig::scope_check()` (`:201`) has no callers anywhere in
    `src apis interface library modules`. OAuth scope enforcement actually
    happens in `AuthorizationListener.php:189-192` (F-51).
  - Seeded groups, droplet: `admin` and `zhportal` → Administrators;
    `physician` → `doc`; `clinician` → `clin`; `receptionist` → `front`;
    `accountant` → `back` (F-32).
- **Verified:** `first-hand`
- **Implication for the agent:** Two independent gates apply to every co-pilot
  API read: OAuth scope (F-51) and gacl section ACL (`patients`/`demo`,
  `patients`/`med`, …) evaluated for the token's user. Neither `aclCheckCore`
  nor a `user/…` scope restricts *which patient*. Other patient-filtering
  mechanisms exist elsewhere (e.g. the Laminas `PatientFilter` module on the UI
  `ViewEvent`, F-52), but none was found on the FHIR `GET /fhir/Patient` path
  read in this audit. If the co-pilot must limit itself to the current
  encounter's patient, it has to enforce that itself or use patient-context
  tokens. Background or CLI
  co-pilot code must always pass `$user` explicitly, because the session
  fallback at `:170` silently evaluates as the empty user.

### F-51 — FHIR/REST layer: route files dispatch to controllers wrapping service classes; scope enforcement lives in a kernel listener
- **Category:** Architecture
- **Severity:** Informational
- **Claim:** FHIR R4 endpoints are closures in one route file. Each builds a
  controller that delegates to a `Fhir*Service`, and those mostly wrap the
  pre-existing OpenEMR domain services. The services are instantiated with
  `new` rather than injected. OAuth scope checks run in a Symfony kernel event
  listener before the route closure. Several FHIR resources fan out to many
  underlying services.
- **Evidence:**
  - Routes: `apis/routes/` holds `_rest_routes_fhir_r4_us_core_3_1_0.inc.php`
    (876 lines, **65** `"GET /fhir/…"` routes), `_rest_routes_standard.inc.php`
    (717), `_rest_routes_portal.inc.php` (49). Examples:
    `:73` `"GET /fhir/AllergyIntolerance"`, `:167` `"GET /fhir/Condition"`,
    `:297` `"GET /fhir/Encounter"`, `:470` `"GET /fhir/MedicationRequest"`,
    `:493` `"GET /fhir/Observation"`, `:578` `"GET /fhir/Patient"`
    (`:581` `$controller = new FhirPatientRestController();`).
  - Controllers: `src/RestControllers/` 33 entries / 92 PHP files recursively;
    `src/RestControllers/FHIR/` 38 entries; route discovery dispatches a
    `RestApiCreateEvent` in `FHIR/Finder/FhirRouteFinder.php:31`,
    `Finder/StandardRouteFinder.php:37` and `Finder/PortalRouteFinder.php:33`.
  - Services: `src/Services/FHIR/` 64 entries / 103 PHP files recursively.
    `FhirServiceBase.php:36` `abstract class FhirServiceBase`, with `:223`
    `getOne($fhirResourceId, $puuidBind = null)` and `:240`
    `getAll($fhirSearchParameters, $puuidBind = null)`; abstract
    `parseOpenEMRRecord` (`:157`) and `searchForOpenEMRRecords` (`:303`).
    `FhirPatientService.php:49` extends it, `:58` `private PatientService $patientService;`,
    `:115` `$this->patientService = new PatientService();`.
  - Fan-out: `FhirObservationService.php:41` registers **ten** mapped
    services at `:64-73` (SocialHistory, Vitals, Laboratory, ObservationForm,
    HistorySdoh, Patient, Employer, AdvanceDirective,
    TreatmentInterventionPreference, CareExperiencePreference). Laboratory
    observations resolve through `Observation/FhirObservationLaboratoryService.php:72`
    `$this->service = new ProcedureService();` (an `ObservationLabService`
    alternative is commented out at `:66`). `ProcedureService::search()` (`:76`)
    builds a derived-table query starting
    `FROM procedure_order WHERE activity = 1` (`:188-189`) with `LEFT JOIN`s to
    report and result subqueries.
  - Scope enforcement:
    `src/RestControllers/Authorization/BearerTokenAuthorizationStrategy.php:209-211`
    builds the scope-validator array from the token's `oauth_scopes` and attaches
    it to the request. `src/RestControllers/Subscriber/AuthorizationListener.php:189-192`:
    ```php
    // check access token scopes
    $scopeEntity = ScopeEntity::createFromString($scope);
    if (!$restRequest->requestHasScopeEntity($scopeEntity)) {
        throw new AccessDeniedException($scopeType, $restRequest->getResource() ?? '', "scope " . $scope . " not in access token");
    ```
    Error mapping as in F-08. Subscribers present: `ApiResponseLoggerListener`,
    `AuthorizationListener`, `CORSListener`, `ExceptionHandlerListener`,
    `OAuth2AuthorizationListener`, `RoutesExtensionListener`,
    `SessionCleanupListener`, `SiteSetupListener`, `TelemetryListener`,
    `ViewRendererListener`.
- **Verified:** `first-hand` (source). The Observation fan-out's query cost was
  **not** measured, because no authenticated FHIR request could be made (F-47).
- **Implication for the agent:** `Observation?patient=X` is not one query — it
  is up to ten service searches, and the lab path uses a derived-table query
  shape different from F-25's. A co-pilot that wants labs should always pass
  `category` (and/or `code`) on Observation searches: `FhirObservationService.php:84`
  declares `category` as a token search parameter, `:117-130` narrows the mapped
  services by category, `:132-146` narrows by code, and only when neither is
  given does `:148-150` fall back to all mapped services. (The exact category
  token each mapped service accepts was not enumerated.) Because services are `new`-constructed
  inside controllers, in-process reuse from a module means constructing the
  same classes, not resolving them from a container.

### F-52 — Event dispatcher and module mechanism: patient-dashboard and REST-route hooks exist; zero custom modules are installed; no read/access event exists
- **Category:** Architecture
- **Severity:** Informational
- **Claim:** The kernel exposes a single Symfony `EventDispatcher`. Custom
  modules subscribe from an `openemr.bootstrap.php`, but only when registered
  and active in the `modules` table, and no custom module is registered in
  either environment. Hooks exist for injecting UI into the patient dashboard
  and for adding REST routes. There are save-events for services, but no event
  for "a patient record was read".
- **Evidence:**
  - Dispatcher: `src/Core/Kernel.php:62`
    `$definition = new Definition(EventDispatcher::class, [new Reference('service_container')]);`
    (with `RegisterListenersPass` at `:61`); `:226-235` `getEventDispatcher()`
    returns the container's `event_dispatcher` or throws.
  - Module loading: `src/Core/ModulesApplication.php:39`
    `const CUSTOM_MODULE_BOOSTRAP_NAME = 'openemr.bootstrap.php';`, `:141`
    `SELECT mod_name, mod_directory FROM modules WHERE mod_active = 1 AND type != 1 …`,
    `:155` sets `mod_active = 0` if the bootstrap file is unreadable.
  - Installed modules, both hosts (`SELECT … FROM modules`): only mod_id 1–5,
    `type=1`, `mod_active=1`: Immunization, Syndromicsurveillance, Documents,
    Ccr, Carecoordination. No `type != 1` rows exist.
  - On-disk custom modules and what each contains: all eight have
    `openemr.bootstrap.php` and `info.txt`; `moduleConfig.php` is absent from
    `oe-module-dorn` and `oe-module-ehi-exporter`. Example bootstrap
    (`oe-module-dashboard-context/openemr.bootstrap.php`): registers a PSR-4
    namespace through `ModulesClassLoader`, fetches
    `OEGlobalsBag::getInstance()->getKernel()->getEventDispatcher()`, then
    `(new Bootstrap($eventDispatcher))->subscribeToEvents()`.
  - Patient dashboard hook: `src/Events/PatientDemographics/RenderEvent.php:30`
    `EVENT_SECTION_LIST_RENDER_TOP`, `:37` `…_BEFORE`, `:44` `…_AFTER`, `:51`
    `EVENT_RENDER_POST_PAGELOAD`; dispatched with the patient id at
    `interface/patient_file/summary/demographics.php:1354` (`BEFORE`) and
    `:1533` (`AFTER`). Subscribers on disk:
    `oe-module-weno/src/Bootstrap.php:168` (`BEFORE`) and
    `oe-module-claimrev-connect/src/Bootstrap.php:153` (`AFTER`, only if
    `CONFIG_ENABLE_ELIGIBILITY_CARD`, `:152`). `oe-module-dashboard-context`
    defines a dashboard widget at `src/Bootstrap.php:60-62`, but its
    registration is commented out at `:57`
    (`//$this->registerDashboardWidget(); …`); the module actually wires a
    page-heading widget (`:78-81`, `PageHeadingRenderEvent::EVENT_PAGE_HEADING_RENDER`)
    and a menu item.
  - REST extension: `src/Events/RestApiExtend/RestApiCreateEvent.php:10`
    `EVENT_HANDLE = 'restConfig.route_map.create'` (dispatch sites in F-51);
    also `RestApiScopeEvent`, `RestApiResourceServiceEvent`,
    `RestApiSecurityCheckEvent` (`:24` `'api.route.security.check'`).
  - Data events: `src/Events/Services/ServiceSaveEvent.php:24`
    `EVENT_PRE_SAVE = 'service.save.pre'`, `:30` `EVENT_POST_SAVE = 'service.save.post'`,
    dispatched e.g. at `src/Services/EncounterService.php:381` and `:408`.
    A grep for `const EVENT_.*(READ|VIEW|ACCESS)` under `src/Events` finds only
    UI events: `PatientDemographics/ViewEvent.php:28`
    `'patientDemographics.view'` (dispatched at `demographics.php:1055`,
    consumed by the Laminas `PatientFilter` module at
    `interface/modules/zend_modules/module/PatientFilter/Module.php:79`),
    `PatientDocumentViewCCDAEvent` and `PatientDocumentTreeViewFilterEvent`.
    Nothing fires on a FHIR/REST or service-layer read.
  - Dispatch breadth: 182 `dispatch(` occurrences in `src`, `interface`,
    `library` PHP files (45 files under `src`).
- **Verified:** `first-hand`
- **Implication for the agent:** The co-pilot panel can be delivered as a
  custom module subscribing to `patientDemographics.render.section.before/after`
  (the weno/claimrev precedent), and co-pilot API endpoints can be added via
  `restConfig.route_map.create` without touching core. Installing it is a
  `modules` table registration, which no existing custom module has exercised
  in these environments. Because no read/access event exists, the co-pilot
  cannot delegate its access auditing to an event subscriber; it must write its
  own access records (F-11) at the point it reads.

### F-53 — POSITIVE: the code live on the droplet is exactly the audited commit
- **Category:** Architecture
- **Severity:** Positive
- **Claim:** The droplet serves an unmodified checkout of commit
  `c4db1578b53293611665d9190fe13f1458312590` — the same commit as the local
  audit checkout and as `origin/main` — so every source citation in this ledger
  applies to the deployed code.
- **Evidence:**
  ```
  droplet$ G="git -C /home/deploy/openemr-clinical-copilot -c safe.directory=… --no-optional-locks"
  droplet$ $G rev-parse HEAD                     → c4db1578b53293611665d9190fe13f1458312590
  droplet$ $G branch -vv                         → * main c4db157 [origin/main] audit: raw findings ledger from stage 3 instrumentation
  droplet$ $G status --porcelain=v1 --untracked-files=no | head   → (empty)
  droplet$ $G status --porcelain=v1 | grep -c "^??"               → 0
  droplet$ $G reflog                             → c4db157 HEAD@{2026-09-14 20:46:42 +0000}: clone: from https://labs.gauntletai.com/paawandesai/openemr-clinical-copilot.git
  droplet$ $G remote -v                          → origin https://labs.gauntletai.com/paawandesai/openemr-clinical-copilot.git
  droplet$ grep -n "Deployed URL" README.md      → 11:**Deployed URL:** TBD
  local$   git rev-parse HEAD                    → c4db1578b53293611665d9190fe13f1458312590
  local$   git ls-remote origin refs/heads/main  → c4db1578b53293611665d9190fe13f1458312590
  local$   git ls-remote github refs/heads/main  → ef3d4909fadab9b3280452d08a1aa746e3fa7502
  ```
  Re-checked 2026-09-15T00:11Z: HEAD unchanged, 0 porcelain lines. The web
  container's bind mount (F-48) serves this directory directly.
- **Notes:** (1) `README.md:11` still reads `**Deployed URL:** TBD` at this
  commit; the live URL is `https://201.79.25.22:9300/`. (2) The `github` remote's
  `main` is still at the initial import `ef3d490`; only `origin` carries the
  Stage 1/3 commits. (3) The working tree is writable by the web server (F-48),
  so "clean now" does not prove "clean throughout".
- **Verified:** `first-hand`
- **Implication for the agent:** Deployed-code provenance can currently be
  asserted by commit hash. Once the co-pilot ships, the deploy procedure should
  record the hash at deploy time and verify a clean tree, so later audits can
  repeat this check. That guarantee disappears if the bind-mounted, writable
  checkout is kept.

### F-60 — `/ready` reports the model provider healthy while an exhausted credit balance fails every brief
- **Category:** Architecture / Observability
- **Severity:** Medium
- **Claim:** `/ready`'s LLM check calls `models.retrieve`, which needs a valid
  key but spends no tokens, so it cannot see a billing failure. On 2026-09-16
  the Anthropic account's credit balance ran out during an eval run: briefs
  failed while `/ready` stayed `ready` with `llm_provider: healthy`. The
  failure itself was contained: fast 502s, no partial or empty brief.
- **Evidence:**
  - Source (`first-hand`): `agent/app/health.py:116`
    `await client.models.retrieve(MODEL)`, documented at `:16` as "no tokens
    consumed"; results cached for 15 s (`:63`).
  - Outage (`first-hand`; the eval run that started 22:03Z — the later
    22:45–22:48Z run was clean):
    ```
    22:03–22:05Z  eval G2: two concurrent POST /pre-visit-brief → HTTP 502 brief_generation_failed, in 0.7 s and 1.0 s
                  eval E3 control (in-process, local key) → anthropic.BadRequestError
    22:05:44Z     direct Messages API call, local key → 400 invalid_request_error "Your credit balance is too low
                  to access the Anthropic API. ..." (request_id req_011Cf7sqvpom1efycJbP6GVd)
    ~22:06Z       GET https://201.79.25.22:8443/ready → 200 {"status":"ready", ... "llm_provider":{"status":"healthy", ...}}
    22:08–22:11Z  next eval run: briefs succeed again
    ```
  - Contained failure (`first-hand` + source): the failed requests carried an
    error body with no `brief` field (eval G1 read one); the 502 path in
    `agent/app/api/pre_visit_brief.py` returns `ErrorResponse` only, with "No
    partial brief is returned".
  - Not recoverable afterwards: the 22:03Z run's results file was overwritten
    by the next run (its observations were read in-session first), and the
    droplet agent container was recreated at 2026-09-16T22:17:46Z by a separate
    deployment, so its logs for the window are gone.
- **Remediation:** not done. Either a readiness check that spends a minimal
  token budget on a slower cadence than `/ready` polling, or readiness that
  also reflects recent `llm_call_failed` events (for example, consecutive
  `invalid_request_error` / billing failures → `not_ready`).
- **Verified:** `first-hand` (probe code; local credit error; `/ready` healthy
  during the outage; droplet 502s) · inferred (that the droplet's 502s came from
  the same account's credit exhaustion — the droplet's key and logs were not
  available; see Verification gaps item 24).
- **Implication for the agent:** A readiness probe blind to billing reports
  healthy while the agent serves nothing, so an orchestrator keeps routing to
  it and an operator watching `/ready` sees no incident. The failure mode
  itself is a positive datapoint for the case study's failure-mode
  requirement — fast, explicit, nothing partial — but it was visible only in
  the failed requests.

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
4. **F-16's 4% generation loss — not reproduced, and now unmatched.** The
   5-patient run performed here reported 5 generated and 5 imported with no
   loss. The 50/48 gap and the `Person does not have insurance` exception are
   operator-reported only. Pass 2: neither database contains a 48-patient batch
   (local 57 + 5; droplet 50 with 50 CCDA files present).
5. **F-09's token/JWT scope mismatch — not reproduced.** No token was minted
   first-hand, so neither the token response body nor the decoded JWT claims
   were inspected directly.
6. **~~F-27's framework and version claims — documentation-sourced.~~ Closed
   in pass 2.** Verified against `composer.json`/`composer.lock` (F-27
   correction). Angular 1.8 alone remains documentation-sourced.
7. **~~F-21 counts rows, not patients.~~ Closed in pass 2.** Distinct patients
   with an allergy row measured: 14/65 local, 9/53 droplet.
8. **Authenticated FHIR read against the droplet — blocked** (F-47). No token
   obtainable without the changed admin password, a demo-default login, or a
   write; `api_token` is empty.
9. **Droplet plan type (Shared vs Dedicated) — not determinable from the
   guest** (F-44). No size field in metadata; `doctl` absent. Operator-reported.
10. **Demo-account interactive login — deliberately not attempted** (F-32).
    Stored-hash matches and disabled login gates were verified; an end-to-end
    session was not.
11. **Xdebug exploitability — not tested** (F-35). Configuration and SAPI
    loading verified; no trigger sent.
12. **Successful external MariaDB connections — unobservable** (F-34, F-39).
    `general_log=0`; only failed/aborted connections are logged.
13. **Local port reachability from another LAN host — not tested** (F-33).
    Colima's mux listens on all interfaces; the macOS firewall is enabled but
    its effect on these sockets was not probed.
14. **DigitalOcean BAA execution — unknown** (F-41).
15. **"Spec-permitted for Stage 2" — operator-reported** (F-48). No
    specification document exists in the repository.
16. **`iss` claim inside an issued token — not inspected** (F-49).
17. **Synthea import timing — DB-timestamp proxy only** (F-45). 1-second
    resolution; importer wall-clock output not available; "same invocation on
    both hosts" is operator-reported.
18. **How the `oauth_clients` rows were modified — not established** (F-40).
19. **Body of the 422-byte readyz response at 21:07:11Z — not captured** (F-37).
20. **Default Xdebug state of the `docker/release/` image — not verified** (F-48).
21. **Query cost of the FHIR Observation fan-out — not measured** (F-51).
22. **Independent-vantage probe — attempted, not independent.** A fetch via the
    session's web-fetch tool egressed from the same `192.63.199.50` (logged
    22:43:00Z as `"\x16\x03\x01\x05\xa7\x01" 400`). Internet reachability is
    instead evidenced by the 338-source UFW block log and the
    `45.79.207.252` request (F-33).
23. **LLM-provider BAA — none executed or recorded** (added 2026-09-16; the
    counterpart of item 14). Every Pre-Visit Brief sends retrieved chart
    records to Anthropic's API. For this submission that flow is covered
    only by the case study brief's instruction to act as if a signed BAA
    exists with all LLM providers — `operator-reported`, since the brief is
    not in the repository — which is an assumption, not an agreement. Flow,
    coverage and the real-deployment requirement: `ARCHITECTURE.md` §6.
24. **Cause of the droplet agent's 502s during the 2026-09-16 credit outage —
    inferred** (F-60). The droplet's Anthropic key was not inspected, and the
    agent container was recreated at 22:17:46Z, removing its logs for
    22:03–22:06Z. That the same exhausted account caused them is inferred from
    matching timing and the `brief_generation_failed` code.
25. **Vultr BAA availability — not established** (F-61; added 2026-09-17).
    No BAA with Vultr is executed (operator-reported). Whether Vultr offers
    one was not confirmed: its compliance pages returned HTTP 403, and
    search summaries of them point the other way from the operator's
    report.

---

## Audit side-effects of pass 2

Recorded so that artifacts created by the audit are not later mistaken for
operator or attacker activity.

1. **Droplet file outside the repository: `/root/.gitconfig`.** Created
   2026-09-14T22:24:47Z by an unintended `git config --global --add safe.directory /home/deploy/openemr-clinical-copilot`
   included in a command meant to be read-only. Contents (58 bytes):
   `[safe]` / `directory = /home/deploy/openemr-clinical-copilot`. The file
   holds only this entry, so it was most likely created by that command
   (prior absence cannot be proven). **Not reverted**, to avoid a further write;
   all later git reads used `-c safe.directory=… --no-optional-locks`.
   Re-checked unchanged at 00:11Z. See R-8.
2. **Database rows written by probe traffic** (no SQL writes were issued):
   - Droplet: 15 `api` rows in `log` and 15 `api_log` rows (22:32:49–22:36:19Z,
     `192.63.199.50`) from unauthenticated `fhir/metadata`, `fhir/Patient` and
     discovery requests; an unknown share of `http-request-*` rows in the same
     period (not separable from the operator's open browser session).
   - Local: 13 `api`, 13 `http-request-select` and 13 `api_log` rows
     (22:32:55–22:36:18Z); `log` total 2295 → 2321.
3. **Log lines:** droplet OpenEMR access/error log entries for every probe
   (including PHP warnings `Undefined array key "version"` at 22:32:49–52Z and
   `RestConfig->verifyAccessToken` errors for token-less requests); a 400 entry
   at 22:43:00Z from the web-fetch attempt; MariaDB
   `Aborted connection 529 … host: '192.63.199.50'` at 22:24:34Z (TCP port
   probe); one phpMyAdmin access-log line; probable `/tmp/xdebug.log` entries
   from CLI `php` invocations inside the droplet container (offline hash checks,
   `ini_get` reads); SSH session records in droplet `wtmp`/`auth.log` from
   22:22:11Z. Equivalent access-log lines exist in the local container.
4. **Not done:** no application or phpMyAdmin logins; no remote MariaDB login;
   no `INSERT`/`UPDATE`/`DELETE`/`ANALYZE TABLE` (only `SELECT`, `SHOW`,
   `EXPLAIN`, `ANALYZE [FORMAT=JSON] SELECT` — which executes the SELECT and
   reports runtime statistics without updating table statistics — and
   `SET profiling`); no container, compose, config or repository file changed on
   either host. Local `git status --porcelain` shows only this file; droplet
   porcelain output is empty.
5. **Outside both hosts:** helper scripts, a copy of the droplet container log
   and query outputs were kept in the session scratchpad (outside the
   repository). DigitalOcean and Xdebug documentation were fetched; `whois` was
   run for `198.211.111.194` and `45.79.207.252`.

---

## Recommendations requiring changes outside this file (not performed)

This task permitted edits to `docs/audit/findings.md` only. Each item below
needs a change elsewhere and was deliberately left undone.

**Urgent — exposure verified ongoing at 2026-09-15T00:12:00Z**

- **R-1 (F-32):** On the droplet, deactivate or re-password `accountant`,
  `clinician`, `physician`, `receptionist` and `zhportal`, and remove `zhportal`
  from Administrators. Afterwards re-run the offline hash check across all
  `users_secure` rows.
- **R-2 (F-33, F-34):** Close 8310 and 8320 to the internet: drop the
  `phpmyadmin` service from the droplet stack, and bind MariaDB to
  `127.0.0.1`/the Docker network only (Compose override, `DOCKER-USER` rules,
  or a DigitalOcean Cloud Firewall allowing only 22 and the chosen HTTPS port).
  Verify from a vantage point other than `192.63.199.50`.
- **R-3 (F-34):** Rotate the MariaDB `root` and `openemr` passwords, drop
  `root@'%'`, and stop mounting the git-tracked TLS keys.
- **R-4 (F-35):** Disable Xdebug on the droplet (`XDEBUG_ON`/`XDEBUG_PROFILER_ON`
  unset, or `xdebug.mode=off`) and confirm via `php -r 'var_dump(extension_loaded("xdebug"));'`
  under Apache, not just the CLI.

**Follow-up**

- **R-5 (F-34, F-39):** Treat the droplet database as potentially accessed from
  MariaDB container start (2026-09-14T20:49:36Z) until R-2/R-3 are complete.
  The dataset is synthetic.
- **R-6 (F-36, F-49):** Put the deployment behind a domain with a publicly
  trusted certificate, stop serving plaintext 8300, and set `site_addr_oath` to
  the public origin.
- **R-7 (F-48):** Move to a built, immutable image (`docker/release/` or
  equivalent) without developer tooling or a writable source bind mount, with
  injected credentials. Note `docker/production/docker-compose.yml` also
  defaults `MYSQL_ROOT_PASSWORD: root`.
- **R-8 (side-effect 1):** Remove `/root/.gitconfig` on the droplet
  (`rm /root/.gitconfig`), which this audit created.
- **R-9 (F-10, F-11):** Enable `audit_events_query` before any co-pilot read
  path touches real data.
- **R-10 (F-53):** In a separate reviewed change, record the deployed URL
  `https://201.79.25.22:9300/` and commit `c4db1578b53293611665d9190fe13f1458312590`
  at `README.md:11`, which currently reads `TBD`.
