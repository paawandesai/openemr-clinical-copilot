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

## 5. Follow-up conversation (branch `feat/embed-chat`): built, not deployed (2026-09-18)

The card's follow-up conversation (README, "Follow-up questions") is
committed on `feat/embed-chat`. **Nothing on the host was changed for it:**
the live module directory still holds the brief-only files (no
`public/conversation.php`), and no database row was written. The agent
already serves `POST /pre-visit-brief/conversation` and was not touched.

### 5.1 To deploy

1. **Give the API client the lab and visit scopes.** The client in §1 is
   registered with the four brief scopes, and OpenEMR grants only registered
   scopes. Until this is done, problem, medication and allergy questions
   answer, and every lab or visit question fails with "The co-pilot could not
   read this chart with your access…" (verified, §5.2). The row belongs to
   this module:

   ```sql
   UPDATE oauth_clients
      SET scope = 'openid user/Patient.read user/Condition.read user/MedicationRequest.read user/AllergyIntolerance.read user/Observation.read user/Encounter.read'
    WHERE client_id = 'AMPCwe6N_9ptx8SjfCgHlAG_U4rHwIjHlNnMiYIUpRc';
   ```

   Not yet verified on this host: that a token minted after the update
   carries all six scopes. Step 6 of §5.3 is that check.
2. **Put the branch's module files in the checkout**
   (`/home/deploy/openemr-clinical-copilot`, bind-mounted): the module
   directory only. New: `public/conversation.php`, `src/ChartRequest.php`,
   `src/ConversationNonce.php`, `src/ConversationSessionId.php`,
   `src/ConversationTurn.php`, `src/FollowUpQuestion.php`. Changed:
   `public/brief.php`, `public/assets/pre-visit-brief-card.js`,
   `templates/pre_visit_brief_card.html.twig`, `src/AgentClient.php`,
   `src/Bootstrap.php`, `src/BriefFailure.php`, `src/ClinicianTokenBroker.php`.
3. Nothing else. The module is already registered and enabled (`mod_id` 17),
   so **no module install step runs, and `bin/console openemr:zfc-module`
   must not be used** (§2.2, it acts on `mod_id` 1). No new settings, no
   agent change or restart. The card script's URL carries its mtime, so
   browsers fetch the new one.
4. Roll back by restoring the previous module files (§1's commit) and, if
   wanted, the four-scope `scope` value.

### 5.2 Verified without deploying (2026-09-18)

The branch's module was copied to `/tmp` inside the OpenEMR container
(outside the web root, removed afterwards) for the PHP toolchain and for a
command-line spike that ran the branch's own `ClinicianTokenBroker`,
`ConversationSessionId` and `AgentClient` against the live authorization
server and the live agent, with a session obtained by logging in as
`physician` over loopback (logged out afterwards).

| Check | Result |
|---|---|
| `php -l` on every module file; PHPCS (`phpcs.xml.dist`); PHPStan level 10 with the repository config, **scoped to the module** | clean |
| Module PHPUnit | 76 tests, 148 assertions pass (33 before this branch) |
| Server-side token flow, then `POST /pre-visit-brief/conversation` with that token, Tonja658 Grant908 | "Which of her medications are active?" → `answered`, turn 1 `started`, 12 of 12 claims verified, 8.9 s. "When was she diagnosed with atopic dermatitis?" → `answered`, turn 2 `continued` with 12 verified facts as context, 3 of 3 verified (onset 2014-07-28). |
| Synthesis refusal | "Is her kidney function worsening?" → `refused_unverifiable`, 0 claims, 0.3 s |
| Patient switch, **same login and the same browser nonce**, Phil Belford's chart, "Is she still taking those?" | A different agent session id. Turn 1 `started`, context 0 turns / 0 claims, `not_in_record` with "nothing verified in an earlier turn identifies it". The answer names none of Tonja's medications and cites only Phil's counts (2 problems, 2 medications, 1 allergy). |
| Lab question with the client as registered today (four scopes), Dirk334 Crist667 | The token carries the four registered scopes although six were requested; the agent answers 401 `fhir_token_rejected`; the card would show `agent_rejected_token`. This is §5.1 step 1. The same question with a six-scope token (password grant, another client) answers: creatinine 1.17 mg/dL, 2025-09-19, 4 of 4 verified. |
| Real agent responses through `ConversationTurn` | Four live responses (answered ×3, refused) parse, and the card payload holds only `status`, `answer`, `turn_status`, `conversation`, `verification`, `claims`, `retrieval`, `total_seconds`, `correlation_id`. |
| Card script in headless Chrome, the real template and script with `conversation.php` stubbed by those payloads, at 800 px and 420 px | Turns render as in the README; an unverified claim shows the badge and reason; refusal and expired-context notes render; no JS errors; no horizontal overflow; nothing in `localStorage` or `sessionStorage`. Every POST carries `pid`, the CSRF token and the nonce, never a session id. **New conversation** ends the old nonce and uses a new one. On `patient_context_changed` the input, **Ask**, **New conversation** and the suggestions are disabled. |
| `conversation.php` over HTTP (CSRF, ACL, `pid` ≠ session patient → 409) | **Not run**: the file is not on the host. Its checks are `ChartRequest`, shared with `brief.php` and covered by `ChartRequestTest`, including "another patient is open in the session" → `patient_context_changed`. §5.3 step 8 is the live check. |
| `modules` table afterwards | Immunization (`mod_id` 1) unharmed: `mod_active` 1, `sql_run` 1, `type` 1, `date` `2026-09-16 23:48:31`, the install timestamp rows 2 and 3 share. Row 17 (this module) unchanged from §1. No row was written. |

Side effects on the host: one `physician` login and logout in the audit log,
`api_token` / `oauth_trusted_user` rows for the tokens minted, and three
agent conversations, each ended explicitly.

### 5.3 Operator verification in a browser, after deploying

Log in at `https://64.177.42.210:9300/` as `physician`.

1. Finder → **Tonja658 Grant908** (pid 49). Wait for the brief ("All 16
   items verified against the chart"). Under it: **Follow-up questions**,
   "No questions yet", an input, **Ask** and four suggestions.
2. Ask **"Which of her medications are active?"**. Expect, within about
   10 s: your question on the right; **Turn 1**; a green banner "Answered
   from the records retrieved for this question — N of N items verified";
   Fexofenadine and the epinephrine auto-injector with status active, each
   line with `MedicationRequest · status · source <uuid>` under it; chips
   "Problem records: 21", "Medication records: 14", "Allergy records: 10".
   The chips above the thread now read "1 turn(s)", "Remembers verified
   facts only", "Context kept 30 min if idle".
3. Ask **"When was she diagnosed with atopic dermatitis?"**. Expect **Turn
   2**, a grey note "Context given to this question: … verified fact(s) from
   1 earlier turn(s)…", and the claim `Problem "Atopic dermatitis
   (disorder)", onset date: 2014-07-28T10:26:04Z`, verified.
4. Ask **"What were her most recent hemoglobin results?"**. Expect a "Lab
   results: 2 of 24 · asked for by the question" chip and verified claims for
   LOINC 718-7 of 2025-02-07: a value near 15.04, unit g/dL, "[most recent
   of 2 results with this code]". If instead a red box says "The co-pilot could not read
   this chart with your access…", §5.1 step 1 has not been done.
5. Ask **"Is her kidney function worsening?"**. Expect a dark **Refused**
   banner ("a judgment, comparison or trend cannot be verified against the
   chart"), the agent's sentence explaining why, no claims, and an answer in
   under a second.
6. Finder → **Dirk334 Crist667** (pid 22). After the brief, ask **"What was
   her most recent creatinine?"**. Expect `value: 1.17`, `unit: mg/dL`,
   date 2025-09-19, "[most recent of 5 results…]", 4 of 4 verified.
7. **Patient-switch safety.** Finder → Tonja658 Grant908, ask "Which of her
   medications are active?" and wait for the answer. Then Finder → **Phil
   Belford** (pid 1). His card shows an empty conversation ("No questions
   yet"). Ask **"Is she still taking those?"**. Expect **Turn 1** (not Turn
   2), no "Context given…" note, the grey banner "That information is not in
   the retrieved record", and the sentence "…nothing verified in an earlier
   turn identifies it…". Nothing of Tonja's appears: no Fexofenadine, no
   epinephrine, and the closing line counts Phil's records (2 problem, 2
   medication, 1 allergy).
8. **Stale card.** Open Tonja in one browser tab and ask a question. In a
   second browser tab of the same login open Phil Belford. Back in the first
   tab, ask anything. Expect a red box "A different patient is now open in
   this session…", and the input, **Ask** and the suggestions disabled.
9. Click **New conversation** on any chart with turns. The thread clears,
   the chips read "No questions yet", and the next answer is **Turn 1**.
10. Optional, on the host: `docker logs clinical-copilot-agent-agent-1 2>&1
    | grep conversation_turn` shows each turn's `patient_session_key` as
    `<physician's user UUID>:<patient UUID>:<32 hex characters>`, and the
    last part differs between Tonja and Phil.
