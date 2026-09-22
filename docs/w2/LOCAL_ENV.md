# Local environment: making the laptop match the deployment

How the local `docker/development-easy` stack was turned into a working copy of
the Vultr deployment — same patients, same UUIDs, a working OAuth2 server, a
working chart embed and a local agent — and how to do it again from scratch.

Written for a session that has only the repo and a shell. It contains no
secrets, no tokens and no patient data; everything sensitive is named by
variable, table or column.

> **Why this exists.** W2 work (the document factory, the golden set) has to
> reference the same patients the deployed agent sees. Before this, the two
> instances were separate Synthea imports with disjoint populations, so a
> spec written against a local `pid` meant a different person — or nobody — on
> the deployment. See [Step 1](#1-restore-the-deployment-database-locally).

## What you end up with

| | |
|---|---|
| Patients | 52, identical UUIDs to the deployment |
| OpenEMR login | `physician` / `physician` (**not** `admin`/`pass` — see step 2) |
| OAuth2 / FHIR | working locally, with two locally-registered clients |
| Chart embed | loads for a patient and reaches a local agent |
| Agent | `https://host.docker.internal:8443`, published on host loopback only |

---

## 1. Restore the deployment database locally

Back up first — **this is the only copy of the original local seed.**

```bash
docker exec development-easy-mysql-1 mariadb-dump -uroot -proot \
  --single-transaction --routines --triggers --events --databases openemr \
  | gzip > ~/openemr-local-backup-$(date +%Y%m%d-%H%M%S).sql.gz
```

Take the snapshot (read-only on the droplet) and restore it:

```bash
ssh root@<droplet> 'docker exec development-easy-mysql-1 mariadb-dump -uroot -proot \
  --single-transaction --routines --triggers --events --databases openemr | gzip -9' \
  > ~/vultr-snapshot.sql.gz

docker exec development-easy-mysql-1 mariadb -uroot -proot -e "DROP DATABASE IF EXISTS openemr;"
gzcat ~/vultr-snapshot.sql.gz | docker exec -i development-easy-mysql-1 mariadb -uroot -proot
```

Delete the snapshot afterwards. Keep the backup.

Notes that cost time if you do not know them:

* The mysql container is **MariaDB** — there is no `mysql` binary, use `mariadb`
  and `mariadb-dump`. Credentials are `root`/`root`, published in
  `docker/development-easy/docker-compose.yml`; the port is `127.0.0.1:18320`.
* `documents` and `onsite_documents` are empty on both instances, so a
  DB-only copy carries everything. If that ever changes, a DB-only copy would
  leave those rows pointing at files that are not on this machine.
* **Identify a patient by UUID, never by `pid` or `pubpid`.** OpenEMR assigns
  both per instance at import time.

## 2. Logins after the restore

The restored database carries the deployment's password hashes, so the local
dev default `admin`/`pass` stops working. Use `physician`/`physician` — the
same account `agent/evals/harness.py` uses for its password grant.

Check outcomes in the audit log rather than guessing from a redirect:

```bash
docker exec development-easy-mysql-1 mariadb -uroot -proot -N -B -e "
  SELECT CONCAT(date,' | ',user,' | ',LEFT(CONVERT(FROM_BASE64(comments) USING utf8mb4),60))
  FROM openemr.log WHERE event LIKE 'login%' ORDER BY date DESC LIMIT 5;"
```

(`log.comments` is base64 on this version.)

## 3. Regenerate the site encryption key

**Symptom.** Every OAuth2 request returns HTTP 500
`"Security error - problem with authorization server keys"`.

**Cause.** OpenEMR splits its site encryption key across two places:

| half | where |
|---|---|
| drive | `sites/default/documents/logs_and_misc/methods/sevena`, `sevenb` |
| database | `keys` table rows `sevena`, `sevenb` |

These are not independent. `CryptoGen::collectDriveKey()` reads the on-disk
file and *unwraps it with the database key*. A DB-only restore therefore pairs
**local** disk files with the **snapshot's** DB rows, the HMAC check fails, and
every caller of the site key dies with
`CryptoGenException: Key in drive is not compatible ... with key in database`.
The OAuth server is just the loudest caller: `OAuth2KeyConfig::configKeyPairs()`
cannot decrypt `keys.oauth2key`, and reports the 500 above.

Confirm before changing anything — the audit log names the failing check:

```bash
docker exec development-easy-mysql-1 mariadb -uroot -proot -N -B -e "
  SELECT CONCAT(date,' | ',LEFT(CONVERT(FROM_BASE64(comments) USING utf8mb4),80))
  FROM openemr.log WHERE event LIKE 'oauth2%' ORDER BY date DESC LIMIT 3;"
```

**Fix — regenerate locally. Never copy key material off the deployment.**

```bash
docker exec development-easy-mysql-1 mariadb -uroot -proot -e "
  DELETE FROM openemr.\`keys\` WHERE name IN ('sevena','sevenb','oauth2key','oauth2passphrase');
  DELETE FROM openemr.oauth_clients;
  DELETE FROM openemr.api_token;
  DELETE FROM openemr.api_refresh_token;"

SD=/var/www/localhost/htdocs/openemr/sites/default/documents
docker exec -u apache development-easy-openemr-1 sh -c "rm -f \
  $SD/logs_and_misc/methods/sevena $SD/logs_and_misc/methods/sevenb \
  $SD/certificates/oaprivate.key $SD/certificates/oapublic.key"
```

All four regenerate on the next OAuth request (step 4 triggers it).

**Nothing recoverable is lost.** The snapshot never carried the source
instance's drive keys, so its site-key ciphertext was already undecryptable the
moment it landed. Columns that held such ciphertext, for the record:

| table.column | what it was |
|---|---|
| `keys.value` (`oauth2key`, `oauth2passphrase`) | regenerated here |
| `oauth_clients.client_secret` | the deployment's clients, discarded |
| `globals.gl_value` (`clinical_copilot_oauth_client_secret`) | re-set in step 5 |

To tell real ciphertext from coincidence, scan text columns for the version
prefix and then filter by length — `log.checksum` (40, SHA-1),
`log_comment_encrypt.checksum*` (128, SHA-512) and `list_options.notes` (3,
county FIPS codes) all start `00x` without being encrypted.

## 4. Register local OAuth clients

Two clients are registered locally. Both are created through OpenEMR's own
dynamic registration endpoint, so neither reuses anything from the deployment.

```bash
curl -sk -X POST "https://localhost:9300/oauth2/default/registration" \
  -H 'Content-Type: application/json' -d '{
    "application_type": "private",
    "client_name": "<name>",
    "redirect_uris": ["<https redirect>"],
    "token_endpoint_auth_method": "client_secret_post",
    "contacts": ["local-dev@example.invalid"],
    "scope": "<space separated scopes>"
  }'
```

The response carries `client_id` and `client_secret`. **This is the only time
the secret is shown** — capture it before moving on.

| client | grant | scopes |
|---|---|---|
| `w2-doc-factory-local-test` | `authorization_code` | `openid` + `user/{Patient,Condition,MedicationRequest,AllergyIntolerance,Observation,Encounter}.read` |
| `Clinical Co-Pilot (chart embed) - local` | `authorization_code` | same six |

Those six match what the deployment's embed client is registered with. OpenEMR
silently drops scopes a client was not registered for, so a client registered
for the brief alone still yields a working token — lab and visit follow-ups
then fail instead.

Two things a freshly registered client needs, both normally done by an admin in
**Admin → System → API Clients**:

```bash
docker exec development-easy-mysql-1 mariadb -uroot -proot -e "
  UPDATE openemr.oauth_clients
     SET is_enabled = 1,
         skip_ehr_launch_authorization_flow = 1
   WHERE client_name = '<name>';"
```

### `skip_ehr_launch_authorization_flow = 1` is not optional for the embed

Dynamic registration defaults this column to `0`. With `0`, the module's token
broker gets `307 → /oauth2/default/provider/login` on its first loopback hop
instead of the `200` it needs, and the embed fails with `token_unavailable`.

`AuthorizationController::shouldSkipAuthorizationFlow()` gates on **two**
things, and both must be true:

* the global `oauth_ehr_launch_authorization_flow_skip` = `1` (already set), and
* the per-client `oauth_clients.skip_ehr_launch_authorization_flow` = `1`.

The broker is a server-side loopback flow using the clinician's core session
(`ClinicianTokenBroker::issueFor()`); it can never complete an interactive
provider login, so a redirect to one is fatal rather than merely slow.

Verify a client works without printing the token:

```bash
# token endpoint should be 200; then GET /apis/default/fhir/Patient?_count=1 -> 200
```

## 5. Point the chart embed at local

The module reads its secret with `CryptoGen::decryptFromDatabase()`, so it must
be written with `CryptoGen::encryptForDatabase()` — the same call
`interface/super/edit_globals.php` makes for an `encrypted` field. Writing the
raw secret into `globals` will not work.

Run inside the container as `apache`, reading the registration response from a
file so the secret never appears in a command line or a log:

```php
<?php
$_GET['site'] = 'default'; $ignoreAuth = 1;
require_once '/var/www/localhost/htdocs/openemr/interface/globals.php';
use OpenEMR\Common\Crypto\CryptoGen;

$reg    = json_decode(file_get_contents('/tmp/reg.json'), true);
$crypto = new CryptoGen();

foreach ([
    'clinical_copilot_oauth_client_id'     => $reg['client_id'],
    'clinical_copilot_oauth_client_secret' => $crypto->encryptForDatabase($reg['client_secret']),
    'clinical_copilot_oauth_redirect_uri'  => 'https://localhost:9300/interface/modules/custom_modules/oe-module-clinical-copilot/public/oauth-callback.php',
    'clinical_copilot_oauth_loopback_url'  => 'http://localhost',
    'clinical_copilot_agent_url'           => 'https://host.docker.internal:8443',
] as $name => $value) {
    sqlStatement("UPDATE `globals` SET `gl_value` = ? WHERE `gl_name` = ?", [$value, $name]);
}
// round-trip check; prints a boolean, never the secret
$stored = sqlQuery("SELECT `gl_value` FROM `globals` WHERE `gl_name` = 'clinical_copilot_oauth_client_secret'")['gl_value'];
var_dump($crypto->decryptFromDatabase($stored) === $reg['client_secret']);
```

Delete `/tmp/reg.json` from the container afterwards.

Constraints worth knowing (`ModuleConfig::fromGlobals()`):

* `clinical_copilot_agent_url` and `..._redirect_uri` **must be https** — an
  `http` value is rejected outright, which is why the local agent needs TLS.
* `clinical_copilot_oauth_loopback_url` may be `http`, and its host must be one
  of `localhost`, `127.0.0.1`, `[::1]`.
* `clinical_copilot_agent_ca_file`, if non-empty, must be readable.
* The `redirect_uri` is never navigated to — the broker redeems the code
  server-side — but it must match the registered client exactly.

## 6. Local agent stack

The module requires https, so the agent runs behind its Caddy proxy exactly as
deployed. Put the env file **outside the repo** (it holds an API key):

```
ANTHROPIC_API_KEY=<key>
AGENT_PUBLIC_HOST=host.docker.internal
FHIR_BASE_URL=https://host.docker.internal:9300/apis/default/fhir
FHIR_VERIFY_TLS=false
AGENT_STUB_LLM_DRAFTING=false
```

`AGENT_PUBLIC_HOST` sets both the certificate's name and the default SNI, and
`host.docker.internal` is the one name that is both reachable from the OpenEMR
container and valid on the certificate.

### Keep the port on loopback

The committed compose publishes `0.0.0.0:8443` on purpose — on the deployed
host the eval suite, load tests and graders call the agent from outside. On a
laptop the only client is a container on the same machine, so override it.
`agent/deploy/docker-compose.override.yml` (gitignored):

```yaml
services:
  proxy:
    ports: !override
      - "127.0.0.1:8443:8443"
```

The base file is `docker-compose.agent.yml`, **not** `docker-compose.yml`, so
compose does not auto-load the override — pass it as a second `-f`:

```bash
cd agent/deploy
docker compose --env-file <env-file> \
  -f docker-compose.agent.yml -f docker-compose.override.yml up -d --build --wait
```

Docker Desktop for Mac does let a container reach a host-loopback-bound
published port through `host.docker.internal`; the LAN address refuses, which
is the point. Confirm both.

### Export the proxy's CA

Caddy mints its own root. The module needs it at the path
`clinical_copilot_agent_ca_file` names:

```bash
docker exec clinical-copilot-agent-proxy-1 cat /data/caddy/pki/authorities/local/root.crt \
  > /tmp/agent-ca.pem
docker cp /tmp/agent-ca.pem \
  development-easy-openemr-1:/var/www/localhost/htdocs/openemr/sites/default/documents/certificates/clinical-copilot-agent-ca.pem
docker exec -u root development-easy-openemr-1 \
  chown apache:apache /var/www/localhost/htdocs/openemr/sites/default/documents/certificates/clinical-copilot-agent-ca.pem
```

The root lives in the `caddy_data` volume. If that volume is ever recreated,
Caddy mints a new root and the embed fails TLS until this is redone.

## 7. Verify end to end

`brief.php` is a POST endpoint taking `pid` and `csrf_token_form`, and it
answers only for the patient the session currently has open
(`ChartRequest::fromRequest()`). So: log in, open the chart, scrape the CSRF
token from that page, then post.

```bash
curl -sk -c ck.txt -X POST \
  "https://localhost:9300/interface/main/main_screen.php?auth=login&site=default" \
  -d "authUser=physician&clearPass=physician&languageChoice=1&new_login_session_management=1"

curl -sk -b ck.txt -c ck.txt -o chart.html \
  "https://localhost:9300/interface/patient_file/summary/demographics.php?set_pid=<pid>"
# extract csrf_token_form from chart.html

curl -sk -b ck.txt -X POST \
  "https://localhost:9300/interface/modules/custom_modules/oe-module-clinical-copilot/public/brief.php" \
  -d "pid=<pid>&csrf_token_form=<token>"
```

Expect `HTTP 200` and `{"status":"ok", ...}` with a `verification` block. If it
returns `502`, read the reason rather than guessing — the module reports a
`BriefFailure` value, and the two layers below it log the detail:

| where | what it tells you |
|---|---|
| response `error` | `token_unavailable` (broker), `brief_generation_failed` (agent) |
| apache error log, lines matching `Co-Pilot` | broker `stage` and `http_status` |
| `docker logs clinical-copilot-agent-agent-1` | retrieval events, then the failing step |

`AGENT_STUB_LLM_DRAFTING=true` exercises everything except the model call,
which is the right setting when checking wiring or when the API account has no
credit. A stubbed claim is a verbatim copy of the source record, never a
model's output, so it must never be used to judge brief quality.

## What is deliberately not reproduced

* **The deployment's OAuth clients.** They belong to that instance and their
  secrets cannot be decrypted here.
* **Its site keys.** Regenerated locally; never copied.
* Patient UUIDs are the only identifiers that carry across, which is why
  `agent/evals/w2/factory/specs/*.yaml` key on them.
