# Deploying the lab write-back endpoint

What has to happen on an instance before `POST /api/clinical-copilot/lab-writeback`
answers: the module's SQL, an OAuth client that can reach it, and the chart
embed's client re-registered so the agent can write under the clinician.

Decisions behind it: [`DECISIONS.md`](DECISIONS.md) D1 (superseding), D23, D24,
D25. Endpoint reference, including the request shape and every refusal:
[the module README](../../interface/modules/custom_modules/oe-module-clinical-copilot/README.md).

> **Nothing here has been run against Vultr.** Both work orders were LOCAL
> only, by instruction. The steps below are the local procedure with the
> deployment's paths substituted, and the "verify" step at the end exists
> because that substitution is the part that has not been tested.

---

## 1. The ledger table

The module's `sql/install.sql` creates `module_clinical_copilot_writeback_ledger`
— D1's idempotency record, `UNIQUE` on the key over (document SHA3-512, test
code, collection date). Without it every request 500s.

**Do not use `bin/console openemr:zfc-module --modaction=install_sql`.** It
installs the wrong module. `InstallerController::getModuleId()` is declared
`: bool` in a file with no `declare(strict_types=1)`, so the module id is
coerced to `true` and every later lookup reads `mod_id = 1` (Immunization, a
zend module). It prints `module [oe-module-clinical-copilot] was found` and then
`install script does not exist`, and exits 0. Pre-existing upstream defect —
that file has a single commit, `chore: initial import from OpenEMR master`.

Two paths that do work:

**A. Module Manager (preferred — no scripting, and it is what an admin would
do).**
Log in as an administrator → **Modules → Manage Modules** → *Clinical Co-Pilot*
→ **Install SQL**. Afterwards `modules.sql_version` for that row reads `0.1.0`.

**B. From a shell, calling the installer with the right id.**

```php
<?php // /tmp/install-module-sql.php, run as the web user
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
var_dump((new InstallerController($table))->InstallModuleSQL($modId));
```

```bash
docker cp /tmp/install-module-sql.php <openemr-container>:/tmp/
docker exec -u apache <openemr-container> php /tmp/install-module-sql.php
docker exec <mysql-container> mariadb -uroot -p<pw> -e \
  "SHOW CREATE TABLE openemr.module_clinical_copilot_writeback_ledger\G"
```

Expect `UNIQUE KEY \`idempotency_key\``. Two `PHP Warning: Undefined property:
InstModule::$mod_nick_name` lines are core noise on this path and are harmless
if those columns were already empty — check them first if the module has a
nickname or a menu setting you care about.

`install.sql` is `#IfNotTable`-guarded, so running it twice is safe.

## 2. Scopes, and why the client has to be replaced

Two scopes are needed, and **an OpenEMR client's scopes cannot be edited**:
`ClientRepository` exposes `insertNewClient`, `saveIsEnabled`,
`saveSkipEHRLaunchFlow` and `remove` and no scope-update method, the RFC 7592
client endpoint is read-only, and `interface/smart/admin-client.php` has no
scope field. Changing scopes means registering a new client.

| scope | why |
|---|---|
| `api:oemr` | `BearerTokenAuthorizationStrategy::isValidRequestForUserRole()` rejects **every** `/api/` request whose token lacks it, before any resource scope is considered. Without it the call is a 403 that reads like an ACL problem. FHIR routes are exempt, which is why the read-only agent never needed it. |
| `user/lab-writeback.c` | The endpoint's own scope, registered by the module through `RestApiScopeEvent`. `HttpRestParsedRoute` derives the resource from the route's last path segment and POST maps to the `c` permission, so the route path and this string are two spellings of one thing. |

The clinician also needs the **`patients`/`docs`** and **`patients`/`lab`** ACLs
with `write` or `addonly`. A scope says what the client application may ask for;
an ACL says what this clinician may do. Both are checked.

### Which client

Writes are attributed to the user the token belongs to —
`procedure_order.provider_id`, `procedure_report.source`, `documents.owner`, and
`authUser` on every audit row. So the token is **the clinician's**, brokered by
the chart embed from their OpenEMR session exactly as for the Pre-Visit Brief.
That means the **chart-embed client** is the one that needs the two scopes
added. The password-grant client below is a test fixture for driving the
endpoint directly; it must not be used to write on a clinician's behalf.

### Re-registering the chart-embed client

Current scopes (local and Vultr both, `docs/w2/LOCAL_ENV.md` §4 and the
module's `docs/deployment.md` §5.1):

```
openid user/Patient.read user/Condition.read user/MedicationRequest.read
user/AllergyIntolerance.read user/Observation.read user/Encounter.read
```

Register a replacement with those six plus the two new ones:

```bash
curl -sk -X POST "<base>/oauth2/default/registration" \
  -H 'Content-Type: application/json' -d '{
    "application_type": "private",
    "client_name": "Clinical Co-Pilot (chart embed) v2",
    "redirect_uris": ["<base>/interface/modules/custom_modules/oe-module-clinical-copilot/public/oauth-callback.php"],
    "token_endpoint_auth_method": "client_secret_post",
    "contacts": ["<contact>"],
    "scope": "openid api:oemr user/Patient.read user/Condition.read user/MedicationRequest.read user/AllergyIntolerance.read user/Observation.read user/Encounter.read user/lab-writeback.c"
  }'
```

**Capture `client_id` and `client_secret` from that response — the secret is
shown once and is stored encrypted.**

Then, as an admin action (Admin → System → API Clients, or directly):

```sql
UPDATE oauth_clients
   SET is_enabled = 1,
       skip_ehr_launch_authorization_flow = 1
 WHERE client_name = 'Clinical Co-Pilot (chart embed) v2';
```

`skip_ehr_launch_authorization_flow = 1` **is** required for the embed — with
`0` the broker's first loopback hop gets a `307` to the provider login it can
never complete, and the card fails with `token_unavailable`
(`docs/w2/LOCAL_ENV.md` §4). It is **not** needed for a password-grant client.

Point the module at the new client — the secret must be written with
`CryptoGen::encryptForDatabase()`, not as plaintext; the procedure is
`docs/w2/LOCAL_ENV.md` §5 and the module's `docs/deployment.md` §5.

Leave the old client in place until the new one is confirmed working, then
delete it and its `api_token` / `api_refresh_token` / `oauth_trusted_user` rows.

### A test client, for driving the endpoint directly

Password grant, no embed involved. Useful for a smoke test; **not** for writing
on a clinician's behalf.

```bash
curl -sk -X POST "<base>/oauth2/default/registration" \
  -H 'Content-Type: application/json' -d '{
    "application_type": "private",
    "client_name": "Clinical Co-Pilot lab write-back (test)",
    "redirect_uris": ["<base>/clinical-copilot/writeback-callback"],
    "token_endpoint_auth_method": "client_secret_post",
    "contacts": ["<contact>"],
    "scope": "openid api:oemr user/lab-writeback.c"
  }'
```

```sql
UPDATE oauth_clients
   SET is_enabled = 1, grant_types = 'authorization_code,password,refresh_token'
 WHERE client_name = 'Clinical Co-Pilot lab write-back (test)';
```

Newly registered clients start `is_enabled = 0` and `authorization_code`-only,
so both columns have to change before a password grant will mint a token.

## 3. Agent configuration

The agent derives the endpoint from `FHIR_BASE_URL` by replacing the trailing
`/fhir` — one setting, so an instance that moves moves both. Nothing else is
needed: `app/writeback/client.py` holds no credential and takes the bearer token
on every call.

```
FHIR_BASE_URL=https://<host>:9300/apis/default/fhir
→ POST https://<host>:9300/apis/default/api/clinical-copilot/lab-writeback
```

## 4. Verify, in this order

1. **The scope exists.** Registering a client with `user/lab-writeback.c`
   succeeds, and registering one with `user/lab-writeback-nonesuch.c` fails with
   `invalid_scope`. If the first fails too, the module is not enabled — the
   scope is registered from its bootstrap.
2. **The route exists and is protected.** A token *without*
   `user/lab-writeback.c` gets **401**, and the error log says
   `scope user/lab-writeback.c not in access token`. A token without `api:oemr`
   gets **403** instead — different failure, different fix.
3. **A write lands.** POST one document and one result; expect **200** with real
   ids. Then post it again byte-for-byte: expect **200**, the *same* ids, and
   `duplicate_ignored` throughout, with no new rows.
4. **It reads back.** `GET /fhir/Observation?patient=<uuid>&category=laboratory`
   shows the value, unit, reference range, interpretation, and
   `note[0].text` equal to the result's extraction id.
5. **It is audited.** `openemr.log` has `lab-results-insert` rows for
   `procedure_report` and `procedure_result`, plus the module's own
   `Clinical Co-Pilot write-back: procedure_order N committed …` entry.
   (`log.comments` is base64 on this version.)
6. **A clinician can see it.** With the patient's chart open: **Procedures →
   Pending Review**. Not *Patient Results* — a written-back result is created
   unreviewed and moves there when a clinician signs it (D23).
7. **The preview works.** `<module>/public/document.php?document_id=<id>`, in a
   logged-in session with that patient's chart open, returns the PDF. A
   different chart open, or an id that is not that patient's, returns 404.

## 5. Rolling back

The module can be disabled from Module Manager: the route and the scope are
registered from its bootstrap, so both disappear and the endpoint 404s.
Existing rows are ordinary OpenEMR lab results and stay readable — the ledger is
what stops a re-run duplicating them, so **leave the table in place** even if
the module is off.

To remove test data, delete by ledger row: every chain the endpoint wrote is
recorded there, so the ledger is the safe thing to drive a cleanup from, and
nothing outside it gets touched.
