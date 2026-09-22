# Clinical Co-Pilot — OpenEMR module

Puts the agent's Pre-Visit Brief (`docs/USERS.md` Use Case 1) on the patient
chart: a **Clinical Co-Pilot: Pre-Visit Brief** card at the top of the
patient dashboard's main column, for the patient whose chart is open. Below
the brief, the same card holds the **follow-up conversation** (Use Case 3):
the physician asks questions about the open patient and each answer is
retrieved and verified per turn by the agent's
`POST /pre-visit-brief/conversation`.

Deployment record for the live host and operator verification steps:
[`docs/deployment.md`](docs/deployment.md).

## What the card shows

- It asks for the brief when the chart opens (the Use Case 1 trigger). If the
  user has collapsed the card, it waits until they expand it. **Regenerate**
  asks again.
- It shows the agent's rendered brief (`render_brief()` in
  `agent/app/graph/pre_visit_brief.py`), styled line by line and never
  re-derived:
  - verified items as plain list items;
  - `UNVERIFIED` items with an **Unverified** badge and the agent's reason;
  - a section with no records gets a **Not recorded** badge;
  - a section with records but none active gets a **None active** badge and
    the "N on file" detail;
  - the agent's "could not be verified" summary as a warning.
- Under the brief: "All N items verified against the chart", or "V of N
  items verified against the chart; U unverified", or "No chart items to
  verify".
- On failure: a message for the physician and the agent's correlation id as
  a reference. An earlier brief is hidden, so a stale one never looks
  current. The rest of the chart is unaffected.

Only users with the `patients`/`med` ACL (medical records) see the card. The
endpoints check the same ACL again.

### Whose brief, whose answer

The brief and every follow-up answer open with a dark bar naming the patient:
"Pre-Visit Brief for Phil Belford · DOB 1972-02-09", "Answer for Phil
Belford · …". A question can name anyone ("which of Tonja's medications are
active?"); the co-pilot answers only for the open chart and does not try to
notice the difference (`docs/audit/findings.md` F-62), so the card says whose
answer it is, every time, for the physician to catch a mismatch.

The name comes from the server: `brief.php` and `conversation.php` build it
(`src/OpenPatient.php`) from the `patient_data` row of the pid `ChartRequest`
has just matched to the session, the same row that supplies the UUID sent to
the agent. It is never read from the page or the question. A card payload
cannot be built without it, `AgentClient` rejects an agent response whose
`patient_id` is not the UUID it sent, and the card refuses to render a
response that has no patient name.

### Follow-up questions

Shown under the brief once a brief has rendered. Treatment matches the
standalone UI (`agent/deploy/ui/index.html`):

- The question, then **Turn N** and a banner by `turn_status`: answered,
  partly answered, not in the retrieved record, or one of the two refusals
  (outside what the co-pilot retrieves; a judgment, comparison or trend).
  With claims, the banner adds "V of N items verified".
- One line per claim from the agent's structured `claims`: a green check for
  verified, an **Unverified** badge with the agent's reason otherwise. Under
  each, its attribution: resource type · field · source record id.
- Every other line of the agent's answer (gap and refusal sentences, the
  "could not be verified" warning, the closing statement of what was
  retrieved) is shown as the agent wrote it, in order. Nothing is re-derived.
- What the turn read, as chips ("Lab results: 5 of 79 · asked for by the
  question"), the raw answer text, the time taken and the correlation id.
- Notes when the agent discarded earlier context (expired, or a different
  patient) and when a turn used verified facts from earlier turns.
- **New conversation** clears the thread and starts a new one; the old one is
  ended on the agent (best effort; it expires after 30 idle minutes anyway).
- Reloading the chart starts a new conversation. Turns live in the page only:
  nothing clinical is written to browser storage.

### The conversation's session, and switching patients

The browser never holds or sends the agent's `session_id`. It names its
conversation with a random nonce, made when the card loads and again on **New
conversation**. `conversation.php` derives the agent's session id on every
request as `HMAC-SHA256(key: login session id, "…" + patient UUID + nonce)`
(`src/ConversationSessionId.php`), and takes the patient from the session's
open chart, never from the browser beyond the `pid` it must match. So:

- patient A's conversation cannot be presented with patient B's chart open,
  whatever the browser sends: the same nonce under B is a different session,
  which the agent has never seen. The agent's own reset-on-patient-change
  (F-50) is a second line, not the one relied on;
- a card left open for patient A while the session moves to patient B gets
  `patient_context_changed` (409) on its next question and locks its form;
- a conversation cannot be resumed from another login.

## How it works

```
browser: patient dashboard card
  │ POST public/brief.php          (session cookie, CSRF token, pid the card was rendered for)
  │ POST public/conversation.php   (the same, plus the conversation nonce and the question)
  ▼
OpenEMR (Apache/PHP): brief.php / conversation.php
  │ globals.php authenticates the session and refuses the script if the module is disabled
  │ ChartRequest: POST · CSRF check · patients/med ACL · pid must equal the session's current patient
  │
  │ ClinicianTokenBroker ── loopback http://localhost/oauth2/<site> ──▶ OpenEMR authorization server
  │   GET  /authorize (EHR launch, core session cookie)       → auto-submit page + OAuth session cookie
  │   GET  /authorize?autosubmit=1 (both cookies)             → 302 to redirect URI with code
  │   POST /token (code, client secret, PKCE verifier)        → access token for this clinician
  │
  │ AgentClient ── HTTPS, certificate verified against the agent's CA ──▶ agent POST /pre-visit-brief
  │                                                                        or  POST /pre-visit-brief/conversation
  │                                                                        Authorization: Bearer <token>
  ▼
JSON to the card: the open patient's name and date of birth (from OpenEMR, not the agent),
brief text or the turn (answer, claims, retrieval), verification counts, correlation id
(never the token, the agent session id or patient_session_key)
```

### Where the clinician's token comes from

The agent requires the clinician's own OpenEMR access token
(`docs/ARCHITECTURE.md` §2). OpenEMR's session can already be exchanged for
one: in a **SMART EHR launch**, a client flagged *Skip EHR Launch
Authorization Flow* (with the `oauth_ehr_launch_authorization_flow_skip`
global on) receives an authorization code for whoever holds a live core
session, with no second login or consent screen
(`AuthorizationController::getLoggedInCoreUserUuid()`,
`processAuthorizeFlowForLaunch()`).

The module runs that launch **server side**. The browser version posts its
auto-submit form to `site_addr_oath`, which is `https://localhost:9300` on
the deployed host (F-49), so no remote browser could complete it. Running it on
the server also keeps the token, the code and the client secret out of the
browser.

OpenEMR still makes every decision. The client must be enabled and its
redirect URI registered. The launch token must decrypt with this server's
key. The session must be live. The code is redeemed with the client secret
and a PKCE verifier.

The resulting token (verified live on the deployed host):

| Property | Value |
|---|---|
| `sub` | The logged-in clinician's user UUID. The agent's `patient_session_key` is attributed to that user. |
| Scopes | The brief asks for `openid user/Patient.read user/Condition.read user/MedicationRequest.read user/AllergyIntolerance.read`; a follow-up question asks for those plus `user/Observation.read user/Encounter.read` (labs and visits). OpenEMR grants only what the client was registered with (`ScopeRepository::finalizeScopes()`), so **the registration is the ceiling**: against a client registered with the four brief scopes, problem, medication and allergy questions answer, and lab or visit questions fail with "The co-pilot could not read this chart with your access…". |
| Lifetime | 1 hour. One token is minted per brief or question and never reused. |
| Where it goes | Held in PHP memory for one request and sent only to the agent. It is not stored, logged or returned to the browser. |

## Security properties and limits

- **Loopback only.** The broker sends the session cookie and the client
  secret to the OAuth loopback URL. `ModuleConfig` refuses any host other
  than `localhost`, `127.0.0.1` or `[::1]`.
- **Agent TLS always verified.** The card has no option to skip it. The
  agent's certificate comes from its proxy's local CA, so that CA's root is
  configured as the trust anchor.
- **Patient bound on the server.** The endpoint answers only for the
  session's current patient. If the physician opened another patient
  elsewhere in the same session, the card gets `patient_context_changed`
  (409) instead of a brief.
- **Audit.** Each request writes one `patient-record` event to OpenEMR's
  `log`: user, patient, success or failure, and the agent's correlation id.
  The comment carries no clinical content. No core event fires on the FHIR
  reads the agent makes (F-52).
- **Fault isolation.** If adding the card fails at runtime (database,
  configuration, duplicate card), the error is logged and the card is
  omitted; the dashboard renders normally. A misconfigured module shows no
  card and logs which setting is wrong. `\Error`s are not caught, per the
  project's `ForbiddenCatchTypeRule`.
- **Not closed:**
  - Every chart open costs a token (rows in `api_token` and
    `oauth_trusted_user`) and an LLM call. There is no caching, and the
    agent has no rate limiting.
  - Tokens are not revoked after use; they expire after an hour.
  - OpenEMR has no per-patient scoping (F-50). A token can read any patient
    the clinician can. The launch's patient context does not narrow it.
  - Anyone with the database and OpenEMR's encryption key can decrypt the
    client secret. But minting a token still needs a live clinician session
    id, which is already enough to read that clinician's charts.
  - The agent → OpenEMR FHIR hop still skips certificate verification
    (`agent/docs/deployment.md` §1).

## Setup

All steps use OpenEMR's own admin screens, except exporting the agent's CA.

1. **The agent's CA certificate.** On the host, export the proxy's root
   (`agent/docs/deployment.md` §5). Copy it into the OpenEMR container at
   `sites/default/documents/certificates/clinical-copilot-agent-ca.pem`
   (owner `apache`, mode 644). `sites/` is a persistent volume, and this is
   OpenEMR's own certificate directory. The file is a public certificate,
   not a secret.
2. **Register the API client.** POST to
   `https://<openemr>/oauth2/default/registration` with
   `Content-Type: application/json`:

   ```json
   {
     "application_type": "private",
     "client_name": "Clinical Co-Pilot (chart embed)",
     "redirect_uris": ["https://<openemr>/interface/modules/custom_modules/oe-module-clinical-copilot/public/oauth-callback.php"],
     "token_endpoint_auth_method": "client_secret_post",
     "grant_types": ["authorization_code"],
     "response_types": ["code"],
     "scope": "openid user/Patient.read user/Condition.read user/MedicationRequest.read user/AllergyIntolerance.read user/Observation.read user/Encounter.read"
   }
   ```

   The last two scopes are what lets follow-up questions read lab results
   and visits. A client registered before the conversation existed has only
   the first four: see `docs/deployment.md` §5.

   Keep the returned `client_id` and `client_secret`; the secret cannot be
   retrieved again (F-05). Nothing serves the redirect URI: the broker reads
   the code from the redirect itself. The URI only has to match the
   registration.
3. **Enable it.** In *Administration → System → API Clients*, edit the
   client: enable it, and turn on *Skip EHR Launch Authorization Flow*.
   Clients with `user/` scopes are registered disabled (F-02). The switch
   only appears while *Administration → Config → Connectors → OAuth2
   EHR-Launch Authorization Flow Skip Enable App Setting* is on.
4. **Register, install and enable the module.** In *Modules → Manage
   Modules → Unregistered*: **Register**, then **Install**, then **Enable**.
   Do **not** use `bin/console openemr:zfc-module`: its
   `InstallerController::getModuleId()` is declared `: bool`, so the
   command acts on module id 1 (Immunization) whatever `--modname` says.
5. **Configure.** In *Administration → Config → Clinical Co-Pilot*:

   | Setting | Value |
   |---|---|
   | Agent base URL | `https://<agent-host>:8443` |
   | Agent CA certificate file | `/var/www/localhost/htdocs/openemr/sites/default/documents/certificates/clinical-copilot-agent-ca.pem` |
   | OAuth client ID / secret | from step 2 (the secret is stored encrypted) |
   | OAuth redirect URI | exactly as registered in step 2 |
   | OAuth loopback base URL | `http://localhost` (the default) |

**Disable instantly:** *Modules → Manage Modules → Disable*. The card
disappears and `brief.php` is refused. To also stop token issuance, disable
the API client.

## Troubleshooting

| Symptom | Where to look |
|---|---|
| No card at all | The user lacks `patients`/`med`; or the module is not enabled; or it is not configured. The log then says `Clinical Co-Pilot is enabled but not configured; card not shown` with the setting name. |
| "OpenEMR did not issue an access token…" | The log line `Clinical Co-Pilot did not obtain an access token` gives `stage` and `http_status`. `authorize_session` or `authorize_code`: the client is disabled, its skip flag is off, the skip global is off, the redirect URI does not match, or the session expired. `token`: the secret is wrong. |
| "The co-pilot service is unavailable…" | Agent unreachable, a TLS failure (wrong CA file), or the agent reports FHIR down. Check `GET /ready` on the agent. |
| "The co-pilot could not generate a brief…" / "…could not answer that question…" | The agent's LLM call failed. Look up the reference id in the agent's log. |
| Lab or visit questions fail with "The co-pilot could not read this chart with your access…" while other questions answer | The API client is registered without `user/Observation.read user/Encounter.read` (`docs/deployment.md` §5). |
| Every question fails with "The co-pilot service is unavailable…" but the brief works | The deployed agent predates `POST /pre-visit-brief/conversation` (it answers a bare 404). |

## Lab write-back endpoint

`POST /api/clinical-copilot/lab-writeback` — one lab document plus the results
extracted from it, written into the chart in a single transaction, with real ids
returned. Decided in [`docs/w2/DECISIONS.md`](../../../../docs/w2/DECISIONS.md)
D1 (superseding), D23 and D25, on the evidence in
[`docs/w2/spikes/writeback.md`](../../../../docs/w2/spikes/writeback.md).
Installing it on an instance:
[`docs/w2/deploy-writeback.md`](../../../../docs/w2/deploy-writeback.md).

Why it exists: OpenEMR 8.2 exposes **no API that writes a lab result**. Every
FHIR write route 404s and no `Observation` write scope is defined even in
principle, so the four-table
`procedure_order -> procedure_order_code -> procedure_report ->
procedure_result` chain has to be written from inside the fork. It goes through
`QueryUtils`, which is what keeps it in OpenEMR's audit log, and the document
goes through `\Document::createDocument()`, which is what keeps it encrypted at
rest.

### What it writes, and what it refuses

- **One document and its analytes.** A panel is one request: the results new to
  it share one `procedure_order` and get a `procedure_order_code` +
  `procedure_report` + `procedure_result` each, so every analyte keeps its own
  LOINC code and printed name in the Procedure Results screen (D25). At most 100
  results per request — a bound on one transaction, not a contract rule.
- **Lab reports only.** `document_kind` must be `lab_pdf`. D2 forbids intake
  medications and allergies reaching the chart, and the type check is where
  that is enforced.
- **Idempotently, per result.** The ledger table
  `module_clinical_copilot_writeback_ledger` is `UNIQUE` on D1's key over
  (document SHA3-512, test code, collection date). The same request twice
  returns the same ids and writes nothing the second time; a request that
  repeats some results and adds others writes only the new ones and reports
  `created` / `duplicate_ignored` row by row. The endpoint recomputes every key,
  every result's extraction id, and the run's `extraction_id` (which is
  `document_extraction_id` of the document digest and `extractor_version`) from
  their components, and refuses a request whose values do not match.
- **All of it or none of it.** Document and results commit together. A failure
  part-way rolls the whole thing back and deletes the encrypted file
  `\Document::createDocument()` had already written — a filesystem write is not
  part of a database transaction, so that cleanup is explicit. Verified against
  the live stack by making the ledger insert fail on purpose: zero rows, no
  orphaned file.
- **Under a race.** Two identical requests arriving together produce exactly one
  chain. The loser's transaction rolls back and it answers with the winner's
  ids.
- **`procedure_result.comments` carries an extraction id and nothing else.** It
  surfaces as `Observation.note[0].text`; the ledger maps that id to the
  document, page and bounding box.
- **An `indeterminate` abnormal flag is refused.** `procedure_result.abnormal`
  has no value meaning "undecidable", and writing `no` would record a normal
  the data does not support.

### Setting it up

```bash
# 1. Ledger table, through the module's own install SQL.
#    NOTE: `bin/console openemr:zfc-module --modaction=install_sql` does NOT
#    work — InstallerController::getModuleId() is declared `: bool` in a file
#    without strict_types, so the module id is coerced to true and mod_id 1 is
#    installed instead. Use Module Manager (Modules > Manage Modules >
#    Clinical Co-Pilot > Install SQL), or call InstallModuleSQL() with the real
#    mod_id.

# 2. A dedicated OAuth client. Scopes are immutable per client, so this is a
#    new registration, not an edit of the read client.
curl -sk -X POST "https://localhost:9300/oauth2/default/registration" \
  -H 'Content-Type: application/json' -d '{
    "application_type": "private",
    "client_name": "Clinical Co-Pilot lab write-back - local",
    "redirect_uris": ["https://localhost:9300/clinical-copilot/writeback-callback"],
    "token_endpoint_auth_method": "client_secret_post",
    "contacts": ["local-dev@example.invalid"],
    "scope": "openid api:oemr user/lab-writeback.c"
  }'

# 3. Enable it (an admin action, as for every dynamically registered client).
#    skip_ehr_launch_authorization_flow is NOT needed here - that flag is for
#    the chart embed's loopback broker.
UPDATE oauth_clients SET is_enabled = 1 WHERE client_name = '...';
```

**`api:oemr` is not optional.** `BearerTokenAuthorizationStrategy::
isValidRequestForUserRole()` rejects every `/api/` request whose token lacks
it, before the resource scope is considered, and the failure looks like a
permissions problem rather than a missing scope.

The caller also needs the `patients`/`docs` and `patients`/`lab` ACLs, with
`write` or `addonly`. The scope says what the client application may ask for;
the ACL says what this clinician may do.

### Auditing

Core audits the `procedure_report` and `procedure_result` inserts through
`EventAuditLogger::auditSQLEvent()`. It does **not** audit `procedure_order`:
`LOG_TABLES` maps it to the `lab-order` event and `audit_events_lab-order` is
not a global `library/globals.inc.php` defines, so the check is false on every
instance. The module writes its own entry for that, from **outside** the
transaction — OpenEMR's audit inserts do not roll back with the work they
describe, so an entry written inside one can outlive rows that were undone. An
attempt that wrote nothing is recorded too, with `success = 0`.

### Source-document preview

`GET <module>/public/document.php?document_id=<int>` streams the stored PDF for
the patient whose chart is open, decrypting through `\Document::get_data()`.

This replaces `GET /fhir/Binary/:id` for clinicians (D24). That route requires
the `admin`/`users` ACL, which a physician does not have, so it 403s for exactly
the people the preview is for — the write-back spike only saw it work because it
ran as `admin`.

It checks the session, the `patients`/`docs` ACL, and that the document belongs
to the open chart. Missing, another patient's and deleted all answer **404**
with the same text, because a document id that exists for a patient is itself a
fact about that patient. Non-PDF documents are **415**: streaming arbitrary
stored types inline from OpenEMR's own origin would be a scripting vector.

### Where the result appears

**Procedures > Pending Review**, with the patient's chart open — not Patient
Results. A written-back result is created unreviewed, and
`interface/orders/orders_results.php` skips `review_status = 'received'` rows
in patient-results mode. Signing the result moves it to Patient Results, which
is the same path a result from a real lab interface takes.

## Tests

Isolated (no database). Run from the OpenEMR root, inside the container:

```bash
vendor/bin/phpunit -c interface/modules/custom_modules/oe-module-clinical-copilot/phpunit.xml
```

They cover:

- the broker's two-hop exchange: cookies, `state`, PKCE, the secret never
  sent to authorize, and every way the exchange can break;
- the agent client's contract and its error mapping, for the brief and for
  a conversation turn, and that a turn's payload to the browser carries no
  session id, `patient_session_key` or patient id;
- that a brief or turn payload always names the open patient, that the name
  and the UUID sent to the agent come from one `patient_data` row, and that an
  agent response for any other patient is rejected;
- the request guard both endpoints share (`ChartRequest`): method, CSRF,
  pid, ACL, and rejection when the session's open patient is not the card's;
- the conversation session derivation: stable across turns, different for
  another patient under the same nonce, another nonce, or another login;
- configuration parsing, including the loopback restriction.

The write-back tests are isolated too, and cover:

- the idempotency key, the per-result extraction id and the run id, pinned
  against values produced by the committed Python contract, so a drift between
  the two derivations fails here rather than as a 400 in production;
- the abnormal-flag and value mappings onto OpenEMR's columns, and every
  refusal the request parser makes;
- the orchestration, with the four database collaborators faked
  (`tests/Fakes/`): a panel written as one call, a mixed request that writes
  only the new rows, a transaction that fails after the document was stored
  (nothing written, the ciphertext removed, nothing claimed in the audit log),
  and the losing side of an idempotency race answering with the winner's ids.
  The last two are also verified against a live database — see the session
  report — but only here can the cleanup be observed, since after a real
  rollback there is nothing left to look at but the absence of rows;
- the preview's guard: method, id shape, ACL before chart, and a
  `Content-Disposition` filename that cannot carry a path, a quote or a
  newline.
