# Deploying Week 2 onto a Week 1 host

The ordered procedure for taking a host that runs the Week 1 deployment
(`agent/docs/deployment.md`, the module's `docs/deployment.md`) to the Week 2
build: what to run, in what order, how to undo each step, and what to check when
something breaks.

**Rehearsed on the live host** (`64.177.42.210`, Vultr, 4 vCPU, 7.2 GiB RAM,
hostname `vultr`) on 2026-09-22, deploying `chore/w2-deploy-rehearsal@35f76ae`
(which is `main@d65dac4` plus the Tesseract change to `agent/Dockerfile`). Every
command below ran there, every number below was measured there, and the
rehearsal's test records were deleted afterwards (§7.4).

**Follow-up the same day:** the branch was rebased onto `main@cf182df` (which
adds `feat/w2-extractor`), `/documents` was routed through the proxy (§2.1)
and the rerank budget raised to 4 s (D41). The host now runs **`9922314`**.

Background, not repeated here: [`deploy-writeback.md`](deploy-writeback.md)
(why the ledger and the client re-registration), [`retrieval-deploy.md`](retrieval-deploy.md)
(why the models are baked), [`LOCAL_ENV.md`](LOCAL_ENV.md) (the same stack on a
laptop).

---

## 0. What Week 2 on `main` actually contains

Read this before promising a demo. As of `d65dac4`, these pieces exist and
**deploy and work**:

| Piece | Where it runs | Rehearsal result |
|---|---|---|
| Guideline retrieval (corpus, hybrid BM25 + dense, cross-encoder) | agent, warmed at startup | preloads in ~2.6 s from baked files, no network |
| OCR + text-layer word boxes (D3 stage 1) and the locate step (stage 3) | agent image (library code) | runs in the container; per-page numbers in §8 |
| Lab write-back endpoint, ledger, idempotent replay | OpenEMR module | 200, replay returns the same ids, audited, reads back over FHIR |
| Source-document preview route (`document.php`, D28) | OpenEMR module | 200 PDF for the open chart, 404 for any other |
| `POST /documents`, `GET /documents/{id}/extraction` | agent, **through the proxy** since `9922314` | **still a stub** on `main@cf182df`: takes the bytes, hashes them, throws them away, walks `pending → in_progress → complete` on a timer, never returns a payload (`app/api/documents.py`) — and **accepts any non-empty bearer string** without checking it with OpenEMR |

And these **do not exist on `main` yet**, so there is nothing to deploy for them:

- **Vision-LLM extraction reachable from an upload.** `feat/w2-extractor` is
  merged (the intake-extractor worker, vision + locate + LOINC coding), and it
  is in the deployed image as library code, but it changed no route:
  `/documents` does not call it. Its cost and latency per page are measured
  once `feat/w2-graph` lands (§10).
- **A supervisor, and guideline evidence in an answer.** The retriever is
  preloaded into `app.state.guideline_retriever` and nothing reads it. A
  follow-up question today is the Week 1 conversation graph: chart citations
  only.
- **The module's upload route and the source-documents card.**
  `templates/source_documents_card.html.twig` and `public/assets/w2/*` ship, but
  `Bootstrap` registers only the Week 1 card, so no clinician sees any of it.
- **Anything that calls the write-back.** `app/writeback/client.py` is a
  library; no route or graph calls it, and the chart-embed broker never asks
  for the write scopes (`ClinicianTokenBroker::SCOPES` / `CONVERSATION_SCOPES`).
- **The Week 2 eval gate** (`CLAUDE.md`: not implemented).

When those land, §10 lists the deployment steps each will add.

---

## 1. Before anything: snapshot, and know the rollback

```bash
ssh root@<host>
B=/root/w2-deploy-$(date +%Y%m%d); mkdir -p $B; chmod 700 $B; cd $B
docker exec development-easy-mysql-1 mariadb-dump -uroot -proot \
  --single-transaction --routines --triggers --events --databases openemr \
  < /dev/null | gzip > openemr-pre-w2.sql.gz
tar -C /home/deploy/openemr-clinical-copilot/interface/modules/custom_modules \
  -czf module-pre-w2.tgz oe-module-clinical-copilot
tar -C /opt/clinical-copilot -czf agent-tree-pre-w2.tgz agent DEPLOYED_COMMIT
docker tag clinical-copilot-agent:local clinical-copilot-agent:pre-w2-$(cat /opt/clinical-copilot/DEPLOYED_COMMIT)
docker exec development-easy-mysql-1 mariadb -uroot -proot -N -B -e \
  "SELECT gl_name, gl_value FROM openemr.globals WHERE gl_name LIKE 'clinical_copilot%'" \
  < /dev/null > globals-pre-w2.tsv
chmod 600 *
```

`globals-pre-w2.tsv` holds the old client secret **as ciphertext**; writing it
back byte-for-byte is the embed rollback (§5), and it never needs decrypting.
Keep the directory root-only.

Also record the Week 1 baseline you will compare against at the end: a brief and
a follow-up through the chart embed (§7.1), `ss -tlnp`, and
`docker stats --no-stream`.

**Full rollback of everything below, in one place:** §6.

---

## 2. Agent image and container

**Order matters:** build under a new tag while the old container keeps serving;
swap only once the new image has passed an offline cold start.

```bash
# On the laptop: an archive of exactly the commit being deployed.
git archive <commit> agent | gzip > agent-<commit>.tgz
scp agent-<commit>.tgz root@<host>:$B/

# On the host: build from a scratch copy, not from the live tree.
mkdir -p /root/w2-build && tar -C /root/w2-build -xzf $B/agent-<commit>.tgz
cd /root/w2-build/agent && docker build -t clinical-copilot-agent:w2-<commit> .
```

The build downloads both ONNX models from Hugging Face (it needs egress to
`huggingface.co` **at build time only**), runs `app.retrieval.build --check`,
warms the vector cache, and fails if Tesseract's `eng` data is missing or
`app.extraction.words` cannot see the binary. **222 s** on this host.

Cold start with no network at all — this is the proof that nothing is fetched
at runtime:

```bash
docker run -d --name w2-coldstart --network none --read-only --tmpfs /tmp \
  --cap-drop ALL --security-opt no-new-privileges:true \
  -e ANTHROPIC_API_KEY=dummy -e AGENT_PRELOAD_RETRIEVAL=true \
  clinical-copilot-agent:w2-<commit>
docker logs w2-coldstart 2>&1 | grep retrieval_preload_event   # vector_cache_prebuilt: true
docker rm -f w2-coldstart
```

Swap:

```bash
tar -C /opt/clinical-copilot -xzf $B/agent-<commit>.tgz      # in place: keeps the proxy's bind mounts valid
cd /opt/clinical-copilot/agent/deploy
docker tag clinical-copilot-agent:w2-<commit> clinical-copilot-agent:local
docker compose --env-file /etc/clinical-copilot/agent.env \
  -f docker-compose.agent.yml up -d --no-deps --no-build --wait agent
echo <commit> > /opt/clinical-copilot/DEPLOYED_COMMIT
```

- **Extract in place, do not swap directories.** The proxy bind-mounts
  `deploy/Caddyfile` and `deploy/ui/`; renaming the directory out from under a
  running container leaves it serving the old inode.
- `--no-deps` so the proxy is **not** recreated: recreating it is harmless, but
  `caddy_data` must survive or the module's pinned agent CA stops matching
  (§11).
- Expect **~7 s of 502s** from the proxy while the agent restarts (13 polls at
  0.5 s in the rehearsal). `up --wait` returned healthy after 13 s.

Verify:

```bash
curl -sk https://<host>:8443/health                     # {"status":"alive"}
curl -sk https://<host>:8443/ready                      # 200, fhir + llm_provider healthy
docker compose -f docker-compose.agent.yml logs agent | grep retrieval_preload_event
ss -tlnp                                                # unchanged; 8000 never published (F-54)
```

**Rollback:** `docker tag clinical-copilot-agent:pre-w2-<old> clinical-copilot-agent:local`,
`tar -C /opt/clinical-copilot -xzf $B/agent-tree-pre-w2.tgz`, then the same
`up -d --no-deps --no-build --wait agent`. The Week 1 image is 360 MB and still
tagged; nothing about it was deleted. Each Week 2 image stays tagged as
`clinical-copilot-agent:w2-<commit>`, so rolling back one Week 2 step is a
re-tag to the previous one.

### 2.1 The proxy: route `/documents`, and size its body cap

Uploads are the Week 2 entry point and go through Caddy like everything else.
`agent/deploy/Caddyfile` scopes the request-body cap per route:

| matcher | cap | why |
|---|---|---|
| everything but `/documents*` | 16 KB (16,000 bytes) | unchanged: briefs and conversation turns carry a few short strings |
| `/documents*` | **25 MiB** (26,214,400 bytes) | equal to the agent's `MAX_UPLOAD_BYTES` and the card's `MAX_BYTES`, so no layer refuses what another accepts. ~80 pages at the factory's scan density (150–310 KB/page), ~25 pages of a 1 MB/page 300 dpi colour office scan |

The two matchers are **disjoint on purpose**: `request_body` stacks, so a
site-wide 16 KB cap would still bind inside a larger `/documents` one.

The proxy bind-mounts the Caddyfile as a file, and extracting the tree
replaces the file's inode, so the running container keeps the old config.
`admin off` means there is no hot reload. Validate, then recreate the proxy
**after** the agent is healthy:

```bash
docker run --rm -e AGENT_PUBLIC_HOST=<host> -v "$PWD/Caddyfile:/etc/caddy/Caddyfile:ro" \
  caddy:2.11.4-alpine@sha256:5f5c8640aae01df9654968d946d8f1a56c497f1dd5c5cda4cf95ab7c14d58648 \
  caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile      # "Valid configuration"
docker exec clinical-copilot-agent-proxy-1 cat /data/caddy/pki/authorities/local/root.crt \
  | openssl x509 -noout -fingerprint -sha256                             # note it
docker compose --env-file /etc/clinical-copilot/agent.env \
  -f docker-compose.agent.yml up -d --no-deps --no-build --force-recreate proxy
# same fingerprint again: caddy_data is a named volume and survives a recreate
```

Rehearsal: fingerprint unchanged (`C5:54:80:…:81:89`, equal to the CA file
OpenEMR pins), recreate 3 s, Week 1 embed 200 afterwards. Verified from
outside:

| request through `:8443` | result |
|---|---|
| 2-page scanned factory sample `intake-scanned-light-med-005.pdf`, 625,765 bytes | 200 `pending`, `byte_length` equal to the file, 335 ms; `GET …/extraction` polled through Caddy walks `pending → in_progress → complete` |
| same, no bearer | 401 from the agent |
| exactly 25 MiB | 200 from the agent |
| 25 MiB + 1 byte | **413 from Caddy** |
| 20 KB body to `/pre-visit-brief` | 413 from Caddy (the small cap still binds) |

**Exposure to know about:** until the route checks the token with OpenEMR,
anyone who can reach `:8443` can POST up to 25 MiB to `/documents` with a
made-up bearer. The agent reads the whole body into memory, hashes it and
discards it. Nothing is stored or extracted, but that is a memory and CPU
cost per request with no authentication behind it (§10).

**Rollback:** restore `Caddyfile-<old>` from the backup directory into
`deploy/` and recreate the proxy the same way.

---

## 3. OpenEMR module files

The OpenEMR code tree is `/home/deploy/openemr-clinical-copilot`, bind-mounted
into the container at `/var/www/localhost/htdocs/openemr`, owned by
`deploy` (uid 1001). Between the Week 1 deploy (`e7bb0dc`) and `d65dac4`
nothing outside the module changed on the PHP side, so this is a module-only
copy, and it only adds files (no deletions — check with
`git diff --diff-filter=D --name-only <old> <new> -- interface/modules/custom_modules/oe-module-clinical-copilot`).

```bash
git archive <commit> interface/modules/custom_modules/oe-module-clinical-copilot | gzip > module-<commit>.tgz
scp module-<commit>.tgz root@<host>:$B/
ssh root@<host> "cd /home/deploy/openemr-clinical-copilot \
  && tar -xzf $B/module-<commit>.tgz --no-same-owner \
  && chown -R 1001:1001 interface/modules/custom_modules/oe-module-clinical-copilot"
docker exec development-easy-openemr-1 sh -c 'cd /var/www/localhost/htdocs/openemr/interface/modules/custom_modules/oe-module-clinical-copilot \
  && for f in $(find src public -name "*.php"); do php -l $f >/dev/null || echo FAIL $f; done'
```

The module is already registered and enabled from Week 1 (`modules.mod_id`
17 on this host), so the new bootstrap code — the write-back route and the
`user/lab-writeback.c` scope — is live on the next request. No Apache restart.
58 files lint clean.

**What changes for a clinician:** nothing visible. The dashboard card is the
Week 1 card; the source-documents card is not registered (§0).

**Rollback:** `rm -rf` the module directory and untar `module-pre-w2.tgz` in
its place, `chown -R 1001:1001`. The route and scope vanish with the code.

---

## 4. The ledger table — the working install path

**Do not use `bin/console openemr:zfc-module --modaction=install_sql`.** It
installs module 1 (Immunization) whatever `--modname` says
(`InstallerController::getModuleId()` is `: bool`; see
[`deploy-writeback.md`](deploy-writeback.md) §1). It exits 0.

Call the installer with the real id, as the web user:

```php
<?php // install-module-sql.php
$fileRoot = '/var/www/localhost/htdocs/openemr';
require_once "$fileRoot/vendor/autoload.php";
$_GET['site'] = 'default'; $ignoreAuth = true; $sessionAllowWrite = true;
require_once "$fileRoot/interface/globals.php";

use Installer\Controller\InstallerController;
use Installer\Model\InstModuleTable;
use OpenEMR\Core\OEGlobalsBag;

$table = OEGlobalsBag::getInstance()->get('modules_application')
    ->getServiceManager()->build(InstModuleTable::class);
$modId = null;
foreach ($table->allModules() as $module) {
    if ($module['mod_directory'] === 'oe-module-clinical-copilot') {
        $modId = (int) $module['mod_id'];
    }
}
echo "mod_id=" . var_export($modId, true) . "\n";
if ($modId === null) { exit(1); }
var_dump((new InstallerController($table))->InstallModuleSQL($modId));
```

```bash
docker cp install-module-sql.php development-easy-openemr-1:/tmp/
docker exec -u apache development-easy-openemr-1 php /tmp/install-module-sql.php < /dev/null
docker exec development-easy-openemr-1 rm -f /tmp/install-module-sql.php
docker exec development-easy-mysql-1 mariadb -uroot -proot -e \
  "SHOW CREATE TABLE openemr.module_clinical_copilot_writeback_ledger\G" < /dev/null | grep UNIQUE
#   UNIQUE KEY `idempotency_key` (`idempotency_key`),
```

Rehearsal output: `mod_id=17`, `bool(true)`, two `Undefined property:
InstModule::$mod_nick_name` / `$mod_enc_menu` warnings (core noise — both columns
were already empty, and the row was compared against the dump: only
`sql_version` `0 → 0.1.0` and its date changed).

**Rollback:** leave the table. It is empty until something writes, and if
anything has written, it is what stops a re-run duplicating chart rows
([`deploy-writeback.md`](deploy-writeback.md) §5). If it must go and
`SELECT COUNT(*)` is 0: `DROP TABLE openemr.module_clinical_copilot_writeback_ledger`
and `UPDATE openemr.modules SET sql_version='0' WHERE mod_id=<id>`.

---

## 5. OAuth clients

Scopes are immutable per client through every OpenEMR interface, so a client
that needs a new scope is **a new registration**, never an edit.

### 5.1 Prove the scope exists

```bash
reg() { curl -sk -X POST https://127.0.0.1:9300/oauth2/default/registration \
          -H 'Content-Type: application/json' -d "$1"; }
reg '{"application_type":"private","client_name":"scope-probe","redirect_uris":["https://<host>:9300/x"],
      "token_endpoint_auth_method":"client_secret_post","contacts":["ops@example.invalid"],
      "scope":"openid api:oemr user/lab-writeback-nonesuch.c"}'
# -> {"error":"invalid_scope", ...}   and no client row is created
```

and the real registration below succeeds. If both fail, the module is not
enabled: the scope is registered from its bootstrap.

### 5.2 Replace the chart-embed client

| | old | new |
|---|---|---|
| name | `Clinical Co-Pilot (chart embed)` | `Clinical Co-Pilot (chart embed) v2` |
| id on this host | `AMPCwe6N…` | `2ps8qgz4…` |
| scopes | `openid` + six `user/*.read` | the same seven **+ `api:oemr` + `user/lab-writeback.c`** |
| grant | `authorization_code` | `authorization_code` |
| `skip_ehr_launch_authorization_flow` | 1 | 1 (**required** for the embed) |

Register with the response written straight to a root-only file, so the secret
never reaches a terminal:

```bash
umask 077
reg '{"application_type":"private","client_name":"Clinical Co-Pilot (chart embed) v2",
      "redirect_uris":["https://<host>:9300/interface/modules/custom_modules/oe-module-clinical-copilot/public/oauth-callback.php"],
      "token_endpoint_auth_method":"client_secret_post","contacts":["<contact>"],
      "scope":"openid api:oemr user/Patient.read user/Condition.read user/MedicationRequest.read user/AllergyIntolerance.read user/Observation.read user/Encounter.read user/lab-writeback.c"}' \
  > $B/reg-embed-v2.json
docker exec development-easy-mysql-1 mariadb -uroot -proot -e "
  UPDATE openemr.oauth_clients SET is_enabled=1, skip_ehr_launch_authorization_flow=1
   WHERE client_name='Clinical Co-Pilot (chart embed) v2';" < /dev/null
```

Point the module at it. The secret goes through `CryptoGen::encryptForDatabase()`;
the script prints two booleans and nothing else:

```php
<?php // set-embed-client.php
$_GET['site'] = 'default'; $ignoreAuth = 1;
require_once '/var/www/localhost/htdocs/openemr/interface/globals.php';
use OpenEMR\Common\Crypto\CryptoGen;
$reg = json_decode(file_get_contents('/tmp/cc-reg.json'), true);
$crypto = new CryptoGen();
sqlStatement("UPDATE `globals` SET `gl_value` = ? WHERE `gl_name` = 'clinical_copilot_oauth_client_id'", [$reg['client_id']]);
sqlStatement("UPDATE `globals` SET `gl_value` = ? WHERE `gl_name` = 'clinical_copilot_oauth_client_secret'", [$crypto->encryptForDatabase($reg['client_secret'])]);
$id     = sqlQuery("SELECT `gl_value` FROM `globals` WHERE `gl_name` = 'clinical_copilot_oauth_client_id'")['gl_value'];
$stored = sqlQuery("SELECT `gl_value` FROM `globals` WHERE `gl_name` = 'clinical_copilot_oauth_client_secret'")['gl_value'];
echo 'id_matches=' . var_export($id === $reg['client_id'], true)
   . ' secret_roundtrip=' . var_export($crypto->decryptFromDatabase($stored) === $reg['client_secret'], true) . "\n";
```

```bash
docker cp $B/reg-embed-v2.json development-easy-openemr-1:/tmp/cc-reg.json
docker exec -u root development-easy-openemr-1 chown apache /tmp/cc-reg.json
docker cp set-embed-client.php development-easy-openemr-1:/tmp/
docker exec -u apache development-easy-openemr-1 php /tmp/set-embed-client.php < /dev/null
docker exec development-easy-openemr-1 rm -f /tmp/cc-reg.json /tmp/set-embed-client.php
shred -u $B/reg-embed-v2.json          # the secret now lives only encrypted in globals
```

Then **verify the embed** (§7.1) — brief, a lab question and a visit question.
Rehearsal: all four 200, every claim verified, and `api_token.scope` for the new
client shows the six read scopes. **The token carries only what the broker asks
for**, and the broker never asks for `api:oemr` or `user/lab-writeback.c`; the
registration is a ceiling for a write path that is not built yet (§0).

**Old client:** leave `AMPCwe6N…` registered and enabled until the demo is over.
Nothing points at it, and keeping it makes the rollback a two-row write. After
that, delete it with its `api_token`, `api_refresh_token` and
`oauth_trusted_user` rows.

**Rollback:** write the two rows from `globals-pre-w2.tsv` back —
`clinical_copilot_oauth_client_id` and `clinical_copilot_oauth_client_secret`
(the ciphertext exactly as saved; same site key, so it decrypts). Optionally
disable v2 (`is_enabled = 0`). No restart; the next chart open uses the old
client.

### 5.2.1 Where the embed client's credentials are

There is deliberately **no plaintext copy at rest**. The client id is the
`clinical_copilot_oauth_client_id` global. The secret is
`clinical_copilot_oauth_client_secret`, encrypted with the site key
(`CryptoGen`), and OpenEMR holds its own encrypted copy in
`oauth_clients.client_secret`. The registration response that showed it once
was shredded.

To retrieve both, as root on the host, with
`/root/w2-rehearsal-20260922/embed-client-credentials.php` (root-only; its
source is below):

```bash
F=/root/w2-rehearsal-20260922/embed-client-credentials.php
docker cp $F development-easy-openemr-1:/tmp/cc-creds.php
docker exec -u root development-easy-openemr-1 chown apache /tmp/cc-creds.php
docker exec -u apache development-easy-openemr-1 php /tmp/cc-creds.php < /dev/null
#   check only: client_name, client_id, is_enabled, secret_len,
#   module_copy_matches_oauth_clients=true — never the secret
docker exec -u apache development-easy-openemr-1 php /tmp/cc-creds.php /tmp/cc-client.json < /dev/null
docker cp development-easy-openemr-1:/tmp/cc-client.json /root/embed-client.json && chmod 600 /root/embed-client.json
docker exec development-easy-openemr-1 rm -f /tmp/cc-creds.php /tmp/cc-client.json
# copy it where it belongs, then: shred -u /root/embed-client.json
```

```php
<?php // embed-client-credentials.php — run as apache in the OpenEMR container
$_GET['site'] = 'default'; $ignoreAuth = 1;
require_once '/var/www/localhost/htdocs/openemr/interface/globals.php';
use OpenEMR\Common\Crypto\CryptoGen;
$crypto = new CryptoGen();
$id = sqlQuery("SELECT gl_value FROM globals WHERE gl_name = 'clinical_copilot_oauth_client_id'")['gl_value'];
$fromModule = $crypto->decryptFromDatabase(sqlQuery("SELECT gl_value FROM globals WHERE gl_name = 'clinical_copilot_oauth_client_secret'")['gl_value']);
$client = sqlQuery("SELECT client_name, client_secret, is_enabled FROM oauth_clients WHERE client_id = ?", [$id]);
$fromOAuth = $client ? $crypto->decryptFromDatabase($client['client_secret']) : false;
echo 'client_name=' . ($client['client_name'] ?? 'MISSING') . ' client_id=' . $id
   . ' is_enabled=' . ($client['is_enabled'] ?? '?')
   . ' secret_len=' . (is_string($fromModule) ? strlen($fromModule) : 'DECRYPT_FAILED')
   . ' module_copy_matches_oauth_clients=' . var_export(is_string($fromModule) && $fromModule === $fromOAuth, true) . "\n";
if (isset($argv[1])) {
    umask(077);
    file_put_contents($argv[1], json_encode(['client_id' => $id, 'client_secret' => $fromModule]));
    echo "written to {$argv[1]} (mode " . decoct(fileperms($argv[1]) & 0777) . ")\n";
}
```

Checked 2026-09-22 (check mode only): `client_name=Clinical Co-Pilot (chart
embed) v2`, `is_enabled=1`, `secret_len=86`,
`module_copy_matches_oauth_clients=true`.

**The old client, for rollback:** `Clinical Co-Pilot (chart embed)`,
`AMPCwe6N…`, still registered and enabled, unused. Its id and its secret's
ciphertext are in `/root/w2-rehearsal-20260922/globals-pre-w2.tsv`; the
rollback is above. A site-key regeneration (the `LOCAL_ENV.md` §3 fix)
makes **both** secrets undecryptable, at which point the only route back is a
new registration.

### 5.3 Test client (smoke test only — delete afterwards)

Password grant, so the endpoint can be driven without the embed. **Never used to
write on a clinician's behalf.**

```bash
reg '{"application_type":"private","client_name":"Clinical Co-Pilot lab write-back (test)",
      "redirect_uris":["https://<host>:9300/clinical-copilot/writeback-callback"],
      "token_endpoint_auth_method":"client_secret_post","contacts":["<contact>"],
      "scope":"openid api:oemr user/lab-writeback.c user/Observation.read user/Patient.read"}' > $B/reg-writeback-test.json
docker exec development-easy-mysql-1 mariadb -uroot -proot -e "
  UPDATE openemr.oauth_clients SET is_enabled=1, grant_types='authorization_code,password,refresh_token'
   WHERE client_name='Clinical Co-Pilot lab write-back (test)';" < /dev/null
```

Delete it after §7 (with its tokens) and `shred -u` the file.

---

## 6. Rolling everything back, in order

1. Embed client: restore the two globals (§5.2 rollback).
2. Module: restore `module-pre-w2.tgz` (§3 rollback). The route and scope go
   with it; the ledger table stays.
3. Agent: re-tag and restart the previous image, restore the tree (§2 rollback).
4. Last resort, **destroys every write since the snapshot**, demo data
   included: `DROP DATABASE openemr` and load `openemr-pre-w2.sql.gz`. A
   DB-only restore onto the *same* host keeps the site key consistent (both
   halves come from this instance) — unlike a restore onto another host
   ([`LOCAL_ENV.md`](LOCAL_ENV.md) §3).

---

## 7. Smoke test

Name the patient by UUID. The rehearsal used
`a2c373ec-57bf-41cb-8767-94410dcf663d` (pid 38 on this host), the subject of
the committed factory sample `lab-clean-001`.

### 7.1 Chart embed (Week 1 surface, and the OAuth check)

Log in as `physician`, open `demographics.php?set_pid=<pid>`, scrape
`data-csrf-token` from the card, then POST `brief.php` (`pid`,
`csrf_token_form`) and `conversation.php` (`pid`, `csrf_token_form`,
`action=ask`, `conversation=<nonce matching [A-Za-z0-9-]{8,64}>`, `question`).
Expect 200 and `"status":"ok"`. Ask one lab and one visit question — they are
the ones that fail if the client's scopes are wrong.

### 7.2 Agent

```bash
curl -sk https://<host>:8443/health
curl -sk https://<host>:8443/ready    # fhir healthy, llm_provider healthy, observability_backend "disabled" (not required)
```

`/documents` goes through the proxy since `9922314` (§2.1):
`POST https://<host>:8443/documents?kind=lab_pdf` with the PDF as the body →
200 `pending`; the same bytes again → the same `document_id`; no bearer → 401;
polling `GET /documents/{id}/extraction` walks `pending → in_progress →
complete` with `extraction_id: null` and no payload.

### 7.3 Write-back, replay, read-back, audit, preview

Run inside the agent container so the agent's own client code does the POST
(`app/writeback/client.py`, URL derived from `FHIR_BASE_URL`). Inputs go in
`/tmp` (tmpfs); delete them afterwards — one of them is the test client's
secret. Build the request with `convert_ground_truth()` on the sample's ground
truth, assign LOINC codes to the rows you write (the factory draws none, and a
row without one is skipped with `no_loinc_code`), then `plan_write_back()`.

Rehearsal results:

| check | expected | got |
|---|---|---|
| token without `user/lab-writeback.c` | 401 | 401 |
| token without `api:oemr` | 403 | 403 |
| write (glucose `2345-7`, creatinine `2160-0`) | 200, real ids | 200 — document 2474, order 8198, reports 762/763, results 14849/14850 — 500 ms |
| identical replay | 200, same ids, `duplicate_ignored` | same ids, all `duplicate_ignored` — 384 ms |
| `GET /fhir/Observation?patient=<uuid>&category=laboratory` | note = extraction id | 2 matches, value, range and interpretation present |
| `openemr.log` | `lab-results-insert` rows + module entry | 4 rows + `procedure_order 8198 committed with 2 result(s)` |
| `procedure_report.review_status` | `received` (Pending Review) | `received` |
| `document.php?document_id=2474`, that chart open | 200 PDF | 200 `application/pdf`, bytes identical to the upload, 354 ms |
| another chart open / unknown id | 404 | 404 / 404 |

### 7.4 Cleanup — from the ledger, and prove it

Everything the endpoint wrote is keyed from the ledger, including the
`uuid_registry` rows OpenEMR mints for each new row and for the stored file:

```sql
START TRANSACTION;
CREATE TEMPORARY TABLE cc_l AS
  SELECT * FROM module_clinical_copilot_writeback_ledger
   WHERE extractor_version = '<the version your test used>' AND patient_uuid = '<uuid>';
DELETE FROM uuid_registry WHERE uuid IN (SELECT uuid FROM procedure_result WHERE procedure_result_id IN (SELECT procedure_result_id FROM cc_l));
DELETE FROM uuid_registry WHERE uuid IN (SELECT uuid FROM procedure_report WHERE procedure_report_id IN (SELECT procedure_report_id FROM cc_l));
DELETE FROM uuid_registry WHERE uuid IN (SELECT uuid FROM procedure_order WHERE procedure_order_id IN (SELECT procedure_order_id FROM cc_l));
DELETE FROM uuid_registry WHERE uuid IN (SELECT uuid FROM documents WHERE id IN (SELECT document_id FROM cc_l));
DELETE FROM uuid_registry WHERE document_drive = 1 AND uuid IN
  (SELECT UNHEX(REPLACE(SUBSTRING_INDEX(url, '/', -1), '-', '')) FROM documents WHERE id IN (SELECT document_id FROM cc_l));
DELETE FROM procedure_result      WHERE procedure_result_id IN (SELECT procedure_result_id FROM cc_l);
DELETE FROM procedure_report      WHERE procedure_report_id IN (SELECT procedure_report_id FROM cc_l);
DELETE FROM procedure_order_code  WHERE procedure_order_id  IN (SELECT procedure_order_id FROM cc_l);
DELETE FROM procedure_order       WHERE procedure_order_id  IN (SELECT procedure_order_id FROM cc_l);
DELETE FROM categories_to_documents WHERE document_id IN (SELECT document_id FROM cc_l);
DELETE FROM documents             WHERE id IN (SELECT document_id FROM cc_l);
DELETE FROM module_clinical_copilot_writeback_ledger WHERE id IN (SELECT id FROM cc_l);
COMMIT;
```

Read `documents.url` **before** the transaction and delete that encrypted file
(`sites/default/documents/<pid>/<uuid>`), and the `<pid>` directory if the
write created it and it is now empty.

**Proof of zero leftovers:** load the §1 dump into a scratch schema (rename
the `CREATE DATABASE` / `USE` lines to `w2snap`), compare `COUNT(*)` for every
table, then drop it. Rehearsal: the only differing tables were `log`,
`log_comment_encrypt`, `api_log` (audit — append-only by design, never
deleted), `api_token`/`api_refresh_token`/`oauth_trusted_user` (the v2 embed
client's tokens from ordinary chart opens), `oauth_clients` +1 (v2, intended),
`session_tracker` (test logins) and `contact` +2 (OpenEMR creates a contact row
the first time a chart's demographics page is opened — pids 38 and 40, not the
write). `documents`, `procedure_*` and the ledger were back to their exact
pre-change counts.

---

## 8. Measured on the host

All on `64.177.42.210` (4 vCPU, 7.2 GiB), 2026-09-22.

**Image and process**

| | Week 1 (`e7bb0dc`) | Week 2 (`35f76ae`; `9922314` is the same size) |
|---|---|---|
| image size | 360 MB | **1.18 GB** — pip layer 412 MB (PyMuPDF 64 MB, onnxruntime 67 MB, numpy 70 MB…), baked models + vectors 160 MB, Tesseract 104 MB |
| build time | — | 222 s (176 s for `9922314`, apt and pip layers cached) |
| cold start to `/health` 200 (page cache dropped, `--network none`) | — | **7.0 s**, of which retrieval preload 2.86 s |
| preload on the live restart | — | 2.63 s, `vector_cache_prebuilt: true` |
| resident memory, idle | **50 MiB** | **760–900 MiB** |

**Extraction, D3 stage 1 only** (stage 2 does not exist on `main`), best of 3:

| page kind | per page |
|---|---|
| text layer (5 pages across 3 clean PDFs) | p50 6 ms, max 14 ms |
| OCR, Tesseract 5.5.0 at 300 dpi (3 pages across 2 scans) | p50 **5.7 s**, max 6.2 s |

**Retrieval** — 30 dev queries, top-5, inside the agent container:

| | p50 | p95 | max |
|---|---|---|---|
| hybrid, no rerank (n=60) | 7.8 ms | 9.4 ms | 9.8 ms |
| rerank stage, idle host (n=90) | 1.38 s | 2.08 s | 2.28 s — 0% over 2.5 s, 0% over 4 s |
| rerank stage, **OCR running in the same container** (n=60) | 2.45 s | 3.39 s | 4.09 s — **43.3% over 2.5 s, 1.7% over 4 s** |

Each rerank row resets the circuit before every query, so every query
really reranks. **The first rehearsal's "9 of 60 over 2.5 s" was a
miscount**: one rerank ran over (2.75 s), opened the circuit, and eight
skipped turns were counted as over budget too, because
`RerankOutcome.over_budget` is `True` for both. D41 records this, the move
to 4 s, and two defects 4 s does not fix: the circuit never closes (after
the one 4.09 s rerank, 35 of the next 60 turns ran without reranking), and
the log's `rerank_over_budget` cannot tell a slow rerank from a skipped one.

**End-to-end ask** — through the chart embed, brief + the four questions from
`scripts/measure_session_cost.py`, three patients (pids 5, 38, 40 — 22, 170
and 3,226 lab results):

| | p50 | max |
|---|---|---|
| Pre-Visit Brief | 19.7 s | 22.8 s |
| follow-up turn | 9.2 s | 18.2 s |

**Cost per encounter** (brief + 4 turns, `claude-opus-5` at $5 / $25 per MTok
input / output, `docs/COST_ANALYSIS.md` §1.2; tokens from `llm_usage_event`;
no cache hits):

| patient | input tok | output tok | cost |
|---|---|---|---|
| pid 5 (small chart) | 27,944 | 1,629 | $0.180 |
| pid 38 | 52,003 | 4,589 | $0.375 |
| pid 40 (largest) | 101,103 | 5,735 | $0.649 |

These are Week 1 surfaces. Week 2 adds no model call yet (no vision
extraction, no supervisor), and retrieval is local, so its marginal cost is
CPU. A vision extraction per uploaded page is the number still missing.

---

## 9. Environment variables

**Agent env file** — `/etc/clinical-copilot/agent.env`, root, mode 600, outside
the code tree (`--env-file`, interpolated by `docker-compose.agent.yml`):

| variable | required | default | notes |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | yes | — | the agent's only credential |
| `AGENT_PUBLIC_HOST` | yes | — | the certificate's name and default SNI; `64.177.42.210` here |
| `FHIR_BASE_URL` | no | `https://host.docker.internal:9300/apis/default/fhir` | the write-back URL is derived from it (`…/apis/default/api/clinical-copilot/lab-writeback`) |
| `FHIR_VERIFY_TLS` | no | `false` | OpenEMR's self-signed cert (F-36); also used by the write-back client |
| `AGENT_STUB_LLM_DRAFTING` | no | `false` | load tests only; never for a real brief |
| `AGENT_PRELOAD_RETRIEVAL` | no | **`true` in compose** (code default off) | **new in W2** — warm retrieval at startup |
| `AGENT_CONVERSATION_TTL_SECONDS`, `AGENT_CONVERSATION_MAX_AGE_SECONDS` | no | code defaults | conversation store expiry |
| `LANGFUSE_BASE_URL`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | no | unset | unset → `/ready` reports `observability_backend: disabled` |
| `COHERE_API_KEY` | no | unset | only for the unmeasured Cohere rerank adapter; the deployed path does not use it |

**Baked into the image** (`agent/Dockerfile`; do not set them in the env file):
`AGENT_MODEL_CACHE_DIR=/opt/models`, `HF_HUB_OFFLINE=1`, `HOME=/tmp`,
`PYTHONDONTWRITEBYTECODE=1`, `PYTHONUNBUFFERED=1`.

**OpenEMR globals** (`globals` table; Administration → Config → Clinical
Co-Pilot):

| global | value here | changed in W2 |
|---|---|---|
| `clinical_copilot_agent_url` | `https://64.177.42.210:8443` | no |
| `clinical_copilot_agent_ca_file` | `…/sites/default/documents/certificates/clinical-copilot-agent-ca.pem` | no |
| `clinical_copilot_oauth_client_id` | v2 client | **yes** |
| `clinical_copilot_oauth_client_secret` | encrypted with `CryptoGen` | **yes** |
| `clinical_copilot_oauth_redirect_uri` | `https://64.177.42.210:9300/…/public/oauth-callback.php` | no |
| `clinical_copilot_oauth_loopback_url` | `http://localhost` | no |
| `oauth_ehr_launch_authorization_flow_skip` | `1` | no |

---

## 10. Steps the unbuilt pieces will add

| when this lands | add this step |
|---|---|
| module upload route (server-side call to the agent's `/documents`) | the proxy route and cap are done (§2.1). Still needed: a real token check in the agent route — the stub accepts any bearer string, and it is now public |
| source-documents card | register it in `Bootstrap`; confirm Apache serves `.mjs` as `text/javascript` (D32) |
| `/documents` wired to the extractor, and `feat/w2-graph` (supervisor) | redeploy; measure vision extraction cost and latency per page on this host, and one question answered with both a document citation and a guideline citation; decide where extraction runs relative to the ~0.9 GiB agent, given that OCR in the same container more than doubles rerank p95 (§8) |
| agent-driven write-back | a broker scope set that includes `api:oemr user/lab-writeback.c`; the v2 client already allows it |
| W2 eval gate | nothing on the host — it runs on recorded fixtures (D6) |

---

## 11. When it breaks

| symptom | look at | likely cause / fix |
|---|---|---|
| build fails at `tesseract --list-langs … grep -qx eng` | build log | `tesseract-ocr-eng` not installed, or the apt layer was cached from an older Dockerfile |
| build fails in the fastembed `RUN` | build log | no egress to `huggingface.co` during the build; it is needed then and only then |
| `retrieval_preload_event` shows `vector_cache_prebuilt: false` | agent log | image did not bake the `.npz`; every restart re-embeds 693 chunks. Rebuild |
| `model_cache_dir: null`, or preload fails with a download error | agent log | `AGENT_MODEL_CACHE_DIR` not reaching the process; `HF_HUB_OFFLINE=1` turns the missing weight into a startup error by design |
| `retrieval_preload_failed_event` | agent log | the service still serves Week 1; retrieval is off. The event names the exception type only |
| steady `rerank_over_budget: true` in `evidence_retrieval_event` | agent log | **first check whether it is one slow rerank followed by an open circuit**: the flag is also `True` on every skipped turn, and the circuit never closes until the process restarts (D41). A restart closes it. A stream of genuinely slow reranks means OCR or another load is contending for the CPU (§8) |
| 413 on an upload | response has no JSON body → Caddy; JSON → the agent | over 25 MiB. Both caps are the same number (§2.1); if only one refuses, they have drifted |
| 413 on a brief or a question | Caddy | body over 16 KB; the small cap applies to everything but `/documents*` |
| `OcrUnavailableError` | agent log | image built without Tesseract — i.e. from before `35f76ae` |
| agent OOM-killed / host swapping | `docker stats`, `dmesg` | the W2 agent idles at ~0.9 GiB; OCR at 300 dpi adds to it per page |
| `/ready` 503 `fhir` | `/ready` detail | OpenEMR down, or `FHIR_BASE_URL` wrong |
| embed: "OpenEMR did not issue an access token" | Apache log, `Co-Pilot` lines: `stage`, `http_status` | new client not enabled, `skip_ehr_launch_authorization_flow` = 0 (307 to login), redirect URI mismatch, or the secret was written in plaintext instead of through `CryptoGen` |
| embed: lab/visit questions refused, others fine | `api_token.scope` for the client | client registered without `user/Observation.read user/Encounter.read` |
| embed: "service unavailable", TLS error | Apache log | `caddy_data` was recreated → new Caddy root. Re-export `/data/caddy/pki/authorities/local/root.crt` to the CA file |
| write-back 500 on every request | Apache log | ledger table missing — §4 |
| write-back **401** | Apache log: `scope user/lab-writeback.c not in access token` | token lacks the scope |
| write-back **403** | response | token lacks `api:oemr` (checked before any resource scope), or the user lacks `patients/docs` / `patients/lab` write |
| registering with `user/lab-writeback.c` → `invalid_scope` | — | module disabled or its files not deployed; the scope comes from its bootstrap |
| `install_sql` "succeeded" but no table | — | you used `bin/console openemr:zfc-module`; use §4 |
| preview 404 for a document that exists | — | a different chart is open in that session, or the document is another patient's — by design (D28) |
| preview 403 | — | user lacks `patients/docs` |
| written result not in *Patient Results* | Procedures → Pending Review | by design: unreviewed until signed (D29) |
| a new port appears in `ss -tlnp` | compose files | F-54: only `0.0.0.0:8443` is the agent's; 8000 is never published |
