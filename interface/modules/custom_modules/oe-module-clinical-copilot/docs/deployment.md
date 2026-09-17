# Deployment — Clinical Co-Pilot module on the Vultr host

What is deployed to the OpenEMR host (`64.177.42.210`, Vultr), what was
verified, and the checks an operator still has to do in a browser. The
generic setup is in [`../README.md`](../README.md).

The module was first deployed and verified on the earlier DigitalOcean
droplet (§2.2). The migration to Vultr was a fresh OpenEMR install, so the
OpenEMR-side configuration (API client, settings, agent CA) did not carry
over and was recreated for this host.

## 1. State of the host (2026-09-17)

| What | State |
|---|---|
| Module files | In the bind-mounted checkout `/home/deploy/openemr-clinical-copilot` (at `951f839`), `interface/modules/custom_modules/oe-module-clinical-copilot/`. Every file outside `docs/` is byte-identical (SHA-256) to this branch. |
| Module registration | `modules` row `mod_id` 17, `oe-module-clinical-copilot`, `type` 0, `mod_active` 1, `sql_run` 1, `date` `2026-09-17 00:27:05`. |
| Immunization (`mod_id` 1) | Unharmed. Every column holds its seed value from `sql/database.sql`; `date` is `2026-09-16 23:48:31`, the install timestamp shared by rows 2–5. |
| API client | `client_id` `AMPCwe6N_9ptx8SjfCgHlAG_U4rHwIjHlNnMiYIUpRc`, "Clinical Co-Pilot (chart embed)", registered `2026-09-17 00:34:57` through `POST /oauth2/default/registration`. Confidential, `client_role` `user`, `authorization_code` only, scopes `openid user/Patient.read user/Condition.read user/MedicationRequest.read user/AllergyIntolerance.read`. Redirect URI `https://64.177.42.210:9300/interface/modules/custom_modules/oe-module-clinical-copilot/public/oauth-callback.php`. `is_enabled` 1 and `skip_ehr_launch_authorization_flow` 1. The two "A Private App" clients belong to other work and are not used by the module. |
| Skip-flow global | `oauth_ehr_launch_authorization_flow_skip` = `1`. |
| Module settings | The six `clinical_copilot_*` rows in `globals`, below. The secret is stored `encryptForDatabase`-encrypted, as the Config screen stores it. |
| Agent CA | The proxy's root (`CN=Caddy Local Authority - 2026 ECC Root`, not before `2026-09-17 00:33:59`, SHA-256 `C5:54:80:C1:…:81:89`), in the OpenEMR container at `sites/default/documents/certificates/clinical-copilot-agent-ca.pem`, owner `apache`, mode 644. Byte-identical to the proxy's `/data/caddy/pki/authorities/local/root.crt`. |
| Old host | The droplet's address appears in no `globals` row and no `oauth_clients` row. |
| Ports and bindings | Unchanged. Public: 22, 8300, 9300, 8443 (agent proxy). Loopback only: 18310 (phpMyAdmin), 18320 (MariaDB). OpenEMR reaches the agent at `https://64.177.42.210:8443` from inside its container. |
| Agent | Not touched by the module configuration. |

| Setting (`globals.gl_name`) | Value |
|---|---|
| `clinical_copilot_agent_url` | `https://64.177.42.210:8443` |
| `clinical_copilot_agent_ca_file` | `/var/www/localhost/htdocs/openemr/sites/default/documents/certificates/clinical-copilot-agent-ca.pem` |
| `clinical_copilot_oauth_client_id` | `AMPCwe6N_9ptx8SjfCgHlAG_U4rHwIjHlNnMiYIUpRc` |
| `clinical_copilot_oauth_client_secret` | (encrypted) |
| `clinical_copilot_oauth_redirect_uri` | as registered, above |
| `clinical_copilot_oauth_loopback_url` | `http://localhost` |

**The CA file is tied to the proxy's `caddy_data` volume.** If that volume is
recreated, Caddy mints a new root and every brief fails TLS ("The co-pilot
service is unavailable…") until the new root is exported into the file above
again.

## 2. Verified

### 2.1 On the Vultr host, from the command line (2026-09-17)

`curl` from the operator's laptop against `https://64.177.42.210:9300`,
logged in through OpenEMR's login page as the demo `physician` account
(F-32), opening each dashboard with `demographics.php?set_pid=N` and posting
to `brief.php` with the card's own CSRF token. Database and log reads on the
host. No browser, so the card's JavaScript rendering was not re-checked here
(§2.2 covers it; the module code has not changed since).

| Check | Result |
|---|---|
| OpenEMR container → agent `/health` with the configured CA | 200, `ssl_verify_result` 0. Leaf issued by `Caddy Local Authority - ECC Intermediate`, SAN `IP:64.177.42.210`. |
| Same, system trust store only | Refused: curl exit 60, "unable to get local issuer certificate". The trust rests on the configured CA file. |
| Agent `/ready` from the container | 200, `fhir` and `llm_provider` healthy |
| pid 1 dashboard | 200; the Co-Pilot card is present, ahead of Demographics |
| pid 1 (Phil Belford), `brief.php` | 200 in 7 s. Active problems: HTN, Chronic Renal Insuficiency. Medications: Norvasc, Lisinopril. Allergies: (unspecified allergen). 5 of 5 claims verified. The allergy line is defect F-57, not a clean result: his penicillin allergy is uncoded, so the name is lost and the placeholder still passes verification. |
| pid 49 (Tonja658 Grant908), `brief.php` | 200. Two active problems, ten coded allergies by name (Bee venom, Aspirin, Shellfish, …), 16 of 16 claims verified. Each of her two current medications is listed twice: the chart holds each both in the medication list and as a prescription. |
| pid 3 (Wanda Moore) | 200. All three sections not recorded; 0 claims. |
| pid 5 (Agnus790 Reinger292) | 200. Active problems none active (14 on file), medications none active (4 on file), allergies not recorded. |
| Server-side token flow | One `api_token` row per brief: `client_id` = the client above, user = `physician`, scope exactly `openid` plus the four reads, expiry 1 hour. (The token value was not read.) |
| Agent log | Each run's `patient_session_key` starts with the physician's user UUID. |
| Audit | One `patient-record` row in `log` per brief, `success` 1, with the patient id and correlation id. |
| `receptionist` (no `patients`/`med`) | Dashboard 200 **without** the card; POST to `brief.php` with the dashboard's valid CSRF token → 403 `forbidden` |
| OpenEMR afterwards | Login page, FHIR `metadata` and OIDC discovery 200; all containers healthy; no Co-Pilot errors in Apache's error log |

### 2.2 On the earlier droplet, from the command line and headless Chrome (2026-09-16)

Same module code. A real, headless Chrome on the operator's laptop, logged in
through OpenEMR's login page as `physician`. It opened each patient's
dashboard in the tabbed UI the way the patient finder does
(`top.RTop.location = …demographics.php?set_pid=N`) and read the card's DOM.
Patient ids differ between the two installs' demo data.

| Check | Result |
|---|---|
| Module files lint (`php -l`), PHPCS (`phpcs.xml.dist`), module PHPUnit | clean; 33 tests, 79 assertions pass |
| PHPStan level 10 with the repository config and custom rules, **scoped to the module** (not the full codebase) | no errors |
| Chart before enabling vs after, same session, pid 1 | 200 both times. The same 20 cards render. The card is added at the top of the main column, above Demographics. No fatal errors. The same three pre-existing `safe_href()` warnings appear in the log both times. |
| Server-side token flow (spike, then through `brief.php`) | Authorize hop 1 → 200 with an OAuth session cookie. Hop 2 → 302 with code and matching `state`. Token → 200. JWT `sub` = the physician's user UUID; scopes exactly `openid` plus the four reads; expires in 3600 s. FHIR `Patient` read with it → 200. |
| OpenEMR container → agent | `/health` → 200, **certificate verified** against the exported root (`host.docker.internal:8443` is not reachable from that network) |
| pid 1 (Phil Belford), live | Brief rendered about 6 s after opening the chart, with the same content as in §2.1. "All 5 items verified against the chart". No JS errors. |
| pid 3 (Wanda Moore), live | Active problems, Current medications and Allergies each show **Not recorded**; "No chart items to verify" |
| A patient with problems and medications on file but none active, live | Active problems and Current medications **None active** ("N on file, none with active status"), Allergies **Not recorded** |
| Unverified item plus the summary warning | **Rendering only**. The agent cannot be made to produce an unverified claim on demand. Verified by stubbing just `brief.php`'s response in the browser with text in `render_brief()`'s exact format: **Unverified** badge with reason, the warning, and "2 of 3 items verified…; 1 unverified". Also covered by a Node run of the card script's classifier. |
| `receptionist` (no `patients`/`med`) | Dashboard 200 **without** the card; POST to `brief.php` with a valid CSRF token → 403 `forbidden` |
| No session / GET / bad CSRF / pid ≠ session patient | OpenEMR's own empty 400 (the module never runs) / 400 / 400 / 409 `patient_context_changed` |
| Response to the browser | Only `status`, `brief`, `verification`, `correlation_id`. No token, secret or session id. |
| Audit | One `patient-record` row in `log` per request, `success` 1 or 0, with the patient id. The comment carries only outcome and correlation id. |
| Agent failure path, live | During testing, the agent's Anthropic key briefly ran out of credit (`BadRequestError: credit balance is too low`). The card showed "The co-pilot could not generate a brief for this patient. No partial brief is shown." with the correlation id; the chart was unaffected. Credit was back by the final checks. |

**Incident during the droplet deployment, repaired there.** The first
attempt to install and enable used `bin/console openemr:zfc-module`. Its
`InstallerController::getModuleId()` returns `bool`, so it acted on
`mod_id` 1 (Immunization) rather than this module. It rewrote only that
row's `date`, which was restored to the install timestamp held by sibling
rows 2–5. Do not use that command; on the Vultr host row 1 is intact (§1).

## 3. Operator verification in a browser

What still needs a person: a normal (non-headless) browser, the admin
screens (no admin login was available), and a visual check in your theme.

1. Open `https://64.177.42.210:9300/` and accept the self-signed certificate
   warning (F-36).
2. Log in as a clinician with medical-record access, e.g. `physician`.
3. Top menu **Finder** → search `Belford` → click **Phil Belford**. The
   **Dashboard** tab opens.
4. The first card in the left (wide) column, above **Demographics**, is
   **Clinical Co-Pilot: Pre-Visit Brief**. Expect in order:
   - "Generating the Pre-Visit Brief..." with a spinner, immediately;
   - within about 10 s: **Active problems** HTN, Chronic Renal
     Insuficiency; **Current medications** Norvasc, Lisinopril;
     **Allergies** (unspecified allergen);
   - a green check with "All 5 items verified against the chart", and
     "Generated at HH:MM" above the brief.

   The wording comes from the LLM draft and may vary. The counts and the
   verified state should not. **"(unspecified allergen)" marked verified is
   a known defect (F-57), not a correct result:** the chart records a
   penicillin allergy without a code, and the brief loses its name. Step 7
   shows allergies rendered correctly.
5. Click **Regenerate**. The spinner shows again, then the brief with a new
   time.
6. Finder → **Wanda Moore**. All three sections show a dark **Not recorded**
   badge, and the summary reads "No chart items to verify".
7. Finder → **Tonja658 Grant908**. **Allergies** lists ten allergens by
   name (Bee venom, Aspirin, Shellfish, …), and the summary reads "All 16
   items verified against the chart". Fexofenadine and the epinephrine
   auto-injector each appear twice under **Current medications**, because the
   chart holds each one both in the medication list and as a prescription.
8. Finder → **Agnus790 Reinger292**. **Active problems** and **Current
   medications** show a blue **None active** badge with "14 on file, none
   with active status" and "4 on file, none with active status";
   **Allergies** shows **Not recorded**.
9. Click the card title to collapse the card, then reload the dashboard
   (the tab's reload icon). The card stays collapsed and no "Generating…"
   appears. Expand it, and the brief generates then.
10. Patient-switch guard. With a chart open, open a *different* patient in a
    second browser tab of the same login. Return to the first tab and click
    **Regenerate**. Expect a red box: "A different patient is now open in
    this session. Reload this chart…". No brief for the wrong patient.
11. Log out, log in as `receptionist`, open Phil Belford. There is **no**
    Clinical Co-Pilot card.
12. Admin screens, as `admin`:
    - *Modules → Manage Modules*: **Clinical Co-Pilot** is listed as
      enabled.
    - *Administration → Config*: a **Clinical Co-Pilot** section with six
      settings, the secret masked.
    - *Administration → System → API Clients*: "Clinical Co-Pilot (chart
      embed)" is enabled, with *Skip EHR Launch Authorization Flow* on.
13. Optional, what the agent sees: on the host, `docker logs
    clinical-copilot-agent-agent-1 2>&1 | grep pre_visit_brief` shows the
    run. Its `patient_session_key` starts with the logged-in user's UUID.

If step 4 shows an error instead, read the reference id and use the
Troubleshooting table in the README. The rest of the chart should look
exactly as it did before the module.

## 4. Roll back

- **Card off:** *Modules → Manage Modules → Disable*. Without an admin login:
  `UPDATE modules SET mod_active = 0 WHERE mod_directory = 'oe-module-clinical-copilot' AND type = 0;`
- **Token issuance off:** disable the "Clinical Co-Pilot (chart embed)" API
  client.
- **Full removal:** unregister the module, delete its directory from the
  checkout, and delete the six `clinical_copilot_*` rows from `globals` and
  the certificate file.
