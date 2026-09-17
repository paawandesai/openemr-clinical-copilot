# Deployment — Clinical Co-Pilot module on the droplet

What is deployed to the OpenEMR droplet (`201.79.25.22`), how it was done,
what was verified, and the checks an operator still has to do in a browser.
The generic setup is in [`../README.md`](../README.md).

## 1. State of the droplet (2026-09-16)

| What | State |
|---|---|
| Module files | Module directory copied into the bind-mounted checkout, owned `deploy:deploy`: `/home/deploy/openemr-clinical-copilot/interface/modules/custom_modules/oe-module-clinical-copilot/`. `git status` there shows only this directory, untracked, until this branch is pulled. |
| Module registration | `modules` row `mod_id` 6, `oe-module-clinical-copilot`, `type` 0, `mod_active` 1. Registered, installed and enabled through Module Manager's own `InstModuleTable::register()` and `InstallerController::InstallModule()` / `EnableModule()`, called from a one-off PHP CLI script. |
| API client | `client_id` `dWxJ-c5-CxNMkiw_NWNbHtLQUIqNDfDdRH_NqivwTp0`, "Clinical Co-Pilot (chart embed)". Registered through `POST /oauth2/default/registration` (confidential, `authorization_code` only, the four read scopes plus `openid`). Enabled, with *Skip EHR Launch Authorization Flow* on, by a direct `UPDATE oauth_clients`; the admin-UI equivalent is setup step 3, and no admin login was available. The two pre-existing "A Private App" clients were not touched. |
| Skip-flow global | `oauth_ehr_launch_authorization_flow_skip` was already `1`; not changed. |
| Module settings | The six `clinical_copilot_*` rows in `globals`. The client secret is stored `encryptForDatabase`-encrypted, the same as the Config screen stores it; a decrypt round-trip was checked. |
| Agent CA | The proxy's root (`CN=Caddy Local Authority - 2026 ECC Root`, SHA-256 `AF:32:C5:AD:…:12:35`), in the OpenEMR container at `sites/default/documents/certificates/clinical-copilot-agent-ca.pem`. |
| Ports and bindings | Unchanged. OpenEMR reaches the agent at `https://201.79.25.22:8443`, the proxy's existing public port, from inside its container. No new listener; F-54 loopback bindings untouched. |
| Agent | Not touched: no code, configuration or container changes. |

**Incident during deployment, repaired.** The first attempt to install and
enable used `bin/console openemr:zfc-module`. Its
`InstallerController::getModuleId()` returns `bool`, so it acted on
`mod_id` 1 (Immunization) rather than this module. It rewrote only that
row's `date`: every other column already held the seed values from
`sql/database.sql`. `date` was restored to `2026-09-15 17:20:25`, the
install timestamp still held by sibling rows 2–5. Row 1 now matches its
siblings again, which also restores module load order
(`ORDER BY mod_ui_order, date`).

Temporary scripts, the spike's token and the client secret copies were
removed from the container and the host afterwards.

## 2. Verified from the command line and headless Chrome

A real, headless Chrome on the operator's laptop, logged in through
OpenEMR's login page as the demo `physician` account (F-32). It opened each
patient's dashboard in the tabbed UI the way the patient finder does
(`top.RTop.location = …demographics.php?set_pid=N`) and read the card's DOM.

| Check | Result |
|---|---|
| Module files lint (`php -l`), PHPCS (`phpcs.xml.dist`), module PHPUnit | clean; 33 tests, 79 assertions pass |
| PHPStan level 10 with the repository config and custom rules, **scoped to the module** (not the full codebase) | no errors |
| Chart before enabling vs after, same session, pid 1 | 200 both times. The same 20 cards render. The card is added at the top of the main column, above Demographics. No fatal errors. The same three pre-existing `safe_href()` warnings appear in the log both times. |
| Server-side token flow (spike, then through `brief.php`) | Authorize hop 1 → 200 with an OAuth session cookie. Hop 2 → 302 with code and matching `state`. Token → 200. JWT `sub` = the physician's user UUID; scopes exactly `openid` plus the four reads; expires in 3600 s. FHIR `Patient` read with it → 200. |
| OpenEMR container → agent | `https://201.79.25.22:8443/health` → 200, **certificate verified** against the exported root (`host.docker.internal:8443` is not reachable from that network) |
| pid 1 (Phil Belford), live | Brief rendered about 6 s after opening the chart. Active problems: HTN, Chronic Renal Insuficiency. Medications: Norvasc, Lisinopril. Allergies: (unspecified allergen). "All 5 items verified against the chart". No JS errors. |
| pid 3 (Wanda Moore), live | Active problems, Current medications and Allergies each show **Not recorded**; "No chart items to verify" |
| pid 23 (Esteban536 Koch169), live | Active problems **None active** ("11 on file, none with active status"), Current medications **None active** ("6 on file…"), Allergies **Not recorded** |
| Unverified item plus the summary warning | **Rendering only**. The agent cannot be made to produce an unverified claim on demand. Verified by stubbing just `brief.php`'s response in the browser with text in `render_brief()`'s exact format: **Unverified** badge with reason, the warning, and "2 of 3 items verified…; 1 unverified". Also covered by a Node run of the card script's classifier. |
| `receptionist` (no `patients`/`med`) | Dashboard 200 **without** the card; POST to `brief.php` with a valid CSRF token → 403 `forbidden` |
| No session / GET / bad CSRF / pid ≠ session patient | OpenEMR's own empty 400 (the module never runs) / 400 / 400 / 409 `patient_context_changed` |
| Response to the browser | Only `status`, `brief`, `verification`, `correlation_id`. No token, secret or session id. |
| Audit | One `patient-record` row in `log` per request, `success` 1 or 0, with the patient id. The comment carries only outcome and correlation id. |
| Agent failure path, live | During testing, the agent's Anthropic key briefly ran out of credit (`BadRequestError: credit balance is too low`). The card showed "The co-pilot could not generate a brief for this patient. No partial brief is shown." with the correlation id; the chart was unaffected. Credit was back by the final checks. |

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
   verified state should not.
5. Click **Regenerate**. The spinner shows again, then the brief with a new
   time.
6. Finder → **Wanda Moore**. All three sections show a dark **Not recorded**
   badge, and the summary reads "No chart items to verify".
7. Finder → **Esteban536 Koch169**. **Active problems** and **Current
   medications** show a blue **None active** badge with "N on file, none
   with active status"; **Allergies** shows **Not recorded**.
8. Click the card title to collapse the card, then reload the dashboard
   (the tab's reload icon). The card stays collapsed and no "Generating…"
   appears. Expand it, and the brief generates then.
9. Patient-switch guard. With a chart open, open a *different* patient in a
   second browser tab of the same login. Return to the first tab and click
   **Regenerate**. Expect a red box: "A different patient is now open in
   this session. Reload this chart…". No brief for the wrong patient.
10. Log out, log in as `receptionist`, open Phil Belford. There is **no**
    Clinical Co-Pilot card.
11. Admin screens, as `admin`:
    - *Modules → Manage Modules*: **Clinical Co-Pilot** is listed as
      enabled.
    - *Administration → Config*: a **Clinical Co-Pilot** section with six
      settings, the secret masked.
    - *Administration → System → API Clients*: "Clinical Co-Pilot (chart
      embed)" is enabled, with *Skip EHR Launch Authorization Flow* on.
12. Optional, what the agent sees: on the droplet, `docker logs
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
