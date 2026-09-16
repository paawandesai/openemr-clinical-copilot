# Clinical Co-Pilot — OpenEMR module

Puts the agent's Pre-Visit Brief (`docs/USERS.md` Use Case 1) on the patient
chart: a **Clinical Co-Pilot: Pre-Visit Brief** card at the top of the
patient dashboard's main column, for the patient whose chart is open.

Droplet deployment record and operator verification steps:
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
endpoint checks the same ACL again.

## How it works

```
browser: patient dashboard card
  │ POST public/brief.php   (session cookie, CSRF token, pid the card was rendered for)
  ▼
OpenEMR (Apache/PHP): brief.php
  │ globals.php authenticates the session and refuses the script if the module is disabled
  │ CSRF check · patients/med ACL · pid must equal the session's current patient
  │
  │ ClinicianTokenBroker ── loopback http://localhost/oauth2/<site> ──▶ OpenEMR authorization server
  │   GET  /authorize (EHR launch, core session cookie)       → auto-submit page + OAuth session cookie
  │   GET  /authorize?autosubmit=1 (both cookies)             → 302 to redirect URI with code
  │   POST /token (code, client secret, PKCE verifier)        → access token for this clinician
  │
  │ AgentClient ── HTTPS, certificate verified against the agent's CA ──▶ agent POST /pre-visit-brief
  │                                                                        Authorization: Bearer <token>
  ▼
JSON to the card: brief text, verification counts, correlation id (never the token)
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
the droplet (F-49), so no remote browser could complete it. Running it on
the server also keeps the token, the code and the client secret out of the
browser.

OpenEMR still makes every decision. The client must be enabled and its
redirect URI registered. The launch token must decrypt with this server's
key. The session must be live. The code is redeemed with the client secret
and a PKCE verifier.

The resulting token (verified live on the droplet):

| Property | Value |
|---|---|
| `sub` | The logged-in clinician's user UUID. The agent's `patient_session_key` is attributed to that user. |
| Scopes | Exactly the client's registered scopes: `openid user/Patient.read user/Condition.read user/MedicationRequest.read user/AllergyIntolerance.read`. In this flow OpenEMR grants the registered scopes, not the requested ones, so **the registration is the ceiling**. |
| Lifetime | 1 hour. One token is minted per brief request and never reused. |
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
     "scope": "openid user/Patient.read user/Condition.read user/MedicationRequest.read user/AllergyIntolerance.read"
   }
   ```

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
| "The co-pilot could not generate a brief…" | The agent's LLM call failed. Look up the reference id in the agent's log. |

## Tests

Isolated (no database). Run from the OpenEMR root, inside the container:

```bash
vendor/bin/phpunit -c interface/modules/custom_modules/oe-module-clinical-copilot/phpunit.xml
```

They cover:

- the broker's two-hop exchange: cookies, `state`, PKCE, the secret never
  sent to authorize, and every way the exchange can break;
- the agent client's contract and its error mapping;
- configuration parsing, including the loopback restriction.
