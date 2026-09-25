# Patient dashboard migration: PHP to React over FHIR

Work order 31 · branch `feat/w2-dashboard-port` · decision D59 ·
written 2026-09-24. Everything below that has a number was measured on the
local OpenEMR stack on that date. Where something was not measured, it
says so.

**The short version.** The OpenEMR patient dashboard is now a React +
TypeScript single-page app in `dashboard/`. It reads the patient's chart over
OpenEMR's existing FHIR R4 API and changes nothing in OpenEMR. A small
TypeScript backend-for-frontend (BFF) sits in front of it: it signs the
clinician in with OpenEMR's OAuth2/OpenID Connect and holds the tokens, so
the browser never does. Every required card renders live data, plus lab
results, including the ones Week 2's document pipeline writes back. An
end-to-end test logs in through OAuth and checks each card against the PHP
page for the same patient: 7 end-to-end and 80 unit tests pass. Where FHIR
cannot supply what the PHP page shows, the port says so rather than making
it up, and every such gap is in §5.

---

## 1. What was asked, and what changed on the way

The requirement: port the dashboard to a modern framework over OpenEMR's
REST/FHIR API. Don't touch the backend. Don't redesign. Include OAuth2/OIDC
login, the patient header, and the Allergies, Problem List, Medications,
Prescriptions and Care Team cards from live FHIR data, plus one more section
(lab results). Feature parity is the standard.

The work order also specified the login: a SMART **public** client (no
secret) with authorization code + PKCE, holding clinician read scopes, with
tokens in browser memory. **That client cannot exist on this OpenEMR.** Its
registration endpoint refuses clinician scopes to any client without a
secret:

```
$ curl -sk -X POST https://localhost:9300/oauth2/default/registration -H 'Content-Type: application/json' \
    --data '{"application_type":"public", ..., "token_endpoint_auth_method":"none", "scope":"openid fhirUser user/Patient.read"}'
{"error":"invalid_client_metadata","error_description":"system and user scopes are only allowed for confidential clients"}
```

A public client can only be a *patient* app (`patient/*` scopes, patient
portal login). Changing that rule means changing OpenEMR, which was out of
bounds. The inventory (`docs/dashboard/PARITY_INVENTORY.md` §4) put the
choice to the project owner, who chose a BFF. §3 explains why that is the
right answer on its merits, not only a workaround.

---

## 2. The choice: React + TypeScript + Vite, with a Hono BFF

```
 browser                         dashboard container                       OpenEMR (unchanged)
┌────────────────────┐  same    ┌─────────────────────────────┐  TLS,     ┌───────────────────────┐
│ React SPA          │  origin  │ Hono BFF (Node 24)          │  bearer   │ /oauth2/default/...   │
│ no token, no CDN   │ ───────► │ · serves the built SPA      │ ────────► │ /apis/default/fhir/.. │
│ strict CSP         │ cookie   │ · OAuth code+PKCE+secret    │  token    │ scopes + ACLs decide  │
│                    │ (opaque) │ · tokens in memory only     │           │                       │
└────────────────────┘          │ · GET-only FHIR allowlist   │           └───────────────────────┘
                                └─────────────────────────────┘
```

**React + TypeScript + Vite for the page.**
- The dashboard is one authenticated screen of independent cards. React's
  component model maps onto it one to one: `card_base.html.twig` became
  `Card.tsx`, and each Twig card became a component with the same markup
  and classes.
- TypeScript types the FHIR resources (`web/src/fhir/types.ts`) and the view
  models the cards draw. The mapping between them is a layer of pure
  functions (`web/src/mapping/`), which is where the parity rules live and
  what the unit tests pin.
- Vite gives a static, content-hashed build in under a second and a dev
  server that proxies to the BFF.
- The styling is **Bootstrap 4.6.2**, the exact release the PHP dashboard is
  built on, compiled with the variables OpenEMR's light theme overrides, and
  **Font Awesome Free 6.7.2**, OpenEMR's own version. Visual parity comes
  from the same design system rather than a copy of it.
- React-Bootstrap and Tailwind were left out: plain Bootstrap class names
  let the markup be compared line by line with the Twig templates.

**Hono on Node for the BFF.**
- It is small (the BFF is 855 lines across nine files, comments included) and typed.
- It has no dependencies of its own. The production image carries exactly
  two npm packages, `hono` and `@hono/node-server`.
- It can be tested without a network: `app.request()` drives the real app
  in-process, which is how 42 BFF tests run in about 50 ms.
- It keeps the dashboard in one language and one toolchain, which the
  project owner required.

### Alternatives seriously considered

| | Why not |
|---|---|
| **Next.js** | The strongest alternative, because its server could have been the BFF. Rejected for four reasons. (1) Its server renders pages, so PHI would be rendered and possibly cached server-side, a surface a static SPA does not have. (2) A strict CSP is harder: Next injects inline scripts, which need per-request nonces; the port's CSP has no `unsafe-inline` and no nonces. (3) Its runtime and conventions (RSC, caching, middleware) are a lot to audit for one screen with no SEO or public pages. (4) A static SPA plus a small explicit BFF is easier to reason about: every request path is visible in one file. |
| **Vue 3** | Technically equivalent for this screen. React won on the typed-JSX tooling for the view models, the team's familiarity from Week 1/2 embed work, and the work order's default. No capability was lost by not choosing Vue. |
| **Svelte / SvelteKit** | Smaller bundles, and SvelteKit has a server like Next's. But bundle size is not this page's constraint (see §4.3), and the smaller ecosystem and hiring pool make it harder to hand over. SvelteKit has the same SSR-and-PHI objection as Next. |
| **Angular** | Strong typing and structure, but heavy (RxJS, DI, zone.js) for six cards, a larger bundle, and more framework to learn than application to write. It suits a large multi-screen app, not a port of one dashboard. |
| **Stay in PHP, refactor the templates** | The honest null option. It keeps one codebase and OpenEMR's session and ACLs, but it cannot meet the requirement, which is to consume the API as the data layer. It would also leave the page coupled to the database and the 2,080-line `demographics.php`. |

---

## 3. Why a BFF, and not a token in the browser

Three designs were possible without touching OpenEMR:

1. **A SPA holding the token, registered as a confidential client but never
   sending the secret.** This works today, but only because OpenEMR redeems a
   confidential client's code without the secret, and without PKCE (audit
   **F-65**, found and verified during this work). Building on a defect means
   the dashboard breaks the day it is fixed, and the design has to defend
   depending on it. Rejected.
2. **A patient-role public client.** The wrong user: a patient portal login,
   not a clinician. Rejected.
3. **A BFF.** A confidential client that keeps its secret server-side, and
   tokens that never reach the browser. **Chosen.**

The IETF's *OAuth 2.0 for Browser-Based Applications* guidance ranks the BFF
as the most secure of its browser patterns, for the reason that matters for
a clinical record. With a token in the browser, any script that runs in the
page (an XSS bug, a compromised dependency, a hostile extension) can read it
and use it from anywhere until it expires. With a BFF, such a script can only
make requests through the page's own session, while the page is open, and
only the GET requests the allowlist permits.

What the BFF enforces (`dashboard/bff/src/`):
- **Login.** Authorization code with PKCE (S256), `state`, `nonce`, and the
  client secret at the token endpoint. The ID token's issuer, audience, nonce
  and expiry are checked. The issuer is configured rather than discovered,
  because the deployed host advertises `localhost` (audit F-49).
- **Session cookie.** One opaque id, `HttpOnly; Secure; SameSite=Strict;
  Path=/`, with the `__Host-` prefix on https. The login-state cookie is
  `SameSite=Lax`, because it must survive the top-level redirect back from
  OpenEMR, and it is single-use and holds only a lookup key.
- **Tokens.** Kept in process memory only, never written to disk or logged.
  A restart signs everyone out, deliberately. Sessions end after 30 minutes
  idle, after 8 hours total, or when a refresh fails.
- **Refresh.** A refresh is shared between the parallel card reads (one
  token request, not six). It asks for exactly the scopes the user granted,
  which works around **F-66**: OpenEMR stores the `nonce` as a scope, so a
  refresh without an explicit `scope` fails every time. The live test found
  that. The unit tests alone would not have.
- **Read-only allowlist.** GET only; any other method on `/fhir/*` is 405.
  Eight FHIR types, six search parameters, and no `_include`, `_revinclude`
  or `_has`. Upstream errors become the BFF's own codes; OpenEMR's error
  text is never passed on.
- **Logs.** Method, route template, status and latency only. There is no
  patient id, query string, token or cookie in any line, and the logger's
  signature leaves no place to put one. Checked on the real log of the e2e
  run: 132 lines, three line shapes, zero matches for UUIDs, `Bearer`,
  `eyJ`, `patient=` or patient names.
- **Logout.** Destroys the server session and clears the cookie. OpenEMR
  has no token-revocation endpoint, so the browser is then sent to
  OpenEMR's own sign-out to end the OAuth session there.
- **Same origin and CSP.** The BFF serves the SPA, so there is no CORS. The
  CSP is `default-src 'none'; script-src 'self'; style-src 'self'; img-src
  'self' data:; font-src 'self'; connect-src 'self'; base-uri 'none';
  form-action 'self'; frame-ancestors 'none'`. No third party is ever
  contacted: no analytics, no CDN, no web fonts.

**The cost is one extra service.** It is a container to build, deploy,
monitor and patch, and a holder of live clinician tokens (§6). Measured
locally it uses 19.5 MiB of memory idle and 24.4 MiB after the full e2e
suite, from a 244 MB image.

---

## 4. What leaving PHP gained, with evidence

### 4.1 Tests where there were none

None of the five in-scope PHP cards has a test. `tests/` has render tests
for the appointments card only. The port has:

| Suite | What it proves | Result |
|---|---|---|
| `test/unit/mapping.test.ts` (31) | The FHIR-to-card mapping, run against real responses captured from the local API for four synthetic patients. The expected values are what the PHP page showed for the same patients. Also: age and deceased wording ported from PHP, the SSN never used as the MRN, units never mixed in a trend. | pass |
| `test/unit/bff.test.ts` (42) | The allowlist refuses other types, methods and parameters. Cookie flags. PKCE and state, nonce and origin checks. Token refresh, including the parallel share and the refused-refresh path. Logout destroys the session. Logs carry no ids, tokens or queries. CSP and hardening headers. Path traversal on static files. | pass |
| `test/unit/dashboard.test.tsx` (7) | All six reads start in the first render (in parallel). A failed card shows its own error and every other card still renders. Each card's empty state. | pass |
| `test/e2e/parity.spec.ts` (6) | Real OAuth login on the local stack, then every card compared with the PHP dashboard for the same patient, for three patients. D54 write-back rows. No token in the browser. Sign-out. | pass |
| `test/e2e/refresh.spec.ts` (1) | OpenEMR accepts the BFF's code redemption and refresh. | pass |

The unit tests were mutation-checked. Nine deliberate breakages were planted
one at a time: allowing `Encounter`, making the session cookie
`Lax`, removing the refresh share, letting the MRN come from the SSN,
keeping the session after logout, hiding the error state, adding a log
field, inverting the medication split, and dropping `scope` from the refresh. Every one turned a test red. Two
mutants survived the first round and got a new test each.

### 4.2 Type safety and structure

- `strict` TypeScript with `noUncheckedIndexedAccess` covers both the SPA
  and the BFF. FHIR's optional elements are typed as optional, so the
  compiler forces a decision about every missing field. That is where the
  PHP page's `(1)` and `()` severity strings came from: a missing lookup
  printed raw.
- The PHP page is one 2,080-line procedural file, `demographics.php`, that
  queries the database, checks ACLs and prints HTML. Its prescriptions card
  renders a Smarty template inside a Twig template through output buffering;
  the source comment calls this "a hacky way". The port separates reading
  (`api.ts`), mapping (`mapping/`), and drawing (`components/`).

### 4.3 Performance: measured, and a wash

These are warm loads: 7 runs after 2 warm-ups, same machine, same data, same
minute (`docs/dashboard/perf.json`).

| | pid 20 PHP | pid 20 port | pid 49 PHP | pid 49 port |
|---|---|---|---|---|
| Time until the clinical cards are visible (median) | 702 ms | 657 ms | 650 ms | 853 ms |
| Server time (PHP: page; port: slowest parallel FHIR read) | 692 ms | 430 ms | 638 ms | 429 ms |
| Bytes transferred | 215 KB | 99 KB | 201 KB | 159 KB |
| Requests | 25 | 15 | 25 | 12 |

The port is not faster, and this document does not claim it is. Rendering
server-side in one request is hard to beat with six API reads. The port
transfers roughly half the bytes for pid 20, and its reads run in parallel,
so the page shows the header and fast cards while the lab bundle (67 results
for pid 49) is still arriving. The PHP page shows nothing until everything
is rendered. What the numbers do show is that moving to the API cost no
meaningful speed.

The JS bundle is 172 KB (55 KB gzipped). The CSS is 201 KB (40 KB gzipped),
because it is all of Bootstrap, like OpenEMR's theme; it could be purged.
The build takes 0.7 s, the 80 unit tests 0.65 s, and the e2e suite 36 s.

### 4.4 Security posture

| | PHP dashboard | Port |
|---|---|---|
| Content-Security-Policy | none sent (checked: `demographics.php` returns only `Strict-Transport-Security`) | strict, `'self'` only, no inline |
| Inline scripts | 13 inline `<script>` blocks, 57 script tags, 172 KB of HTML | none |
| Data access | full session: every PHP include and every query the user's ACL allows | GET-only FHIR, eight types, scoped token |
| Credential in the browser | OpenEMR session cookie (full application) | opaque cookie for a read-only proxy; no OAuth token |
| Framing, referrer, sniffing | not set on this page | `frame-ancestors 'none'`/`X-Frame-Options: DENY`, `no-referrer`, `nosniff` |

The port also found two OpenEMR OAuth defects (F-65 security, F-66
availability) and one FHIR mapping defect (F-67). All three are recorded in
`docs/audit/findings.md` with reproduction steps.

---

## 5. Parity matrix

Match = same information, same place and form, verified live by the e2e
comparison unless noted. Partial = present but different in a stated way.
Gap = the PHP page shows it and FHIR does not supply it. G-numbers refer to
`docs/dashboard/PARITY_INVENTORY.md` §3.

### Patient header

| Element | Status | Why |
|---|---|---|
| Name (`fname lname`) | **Match** | `Patient.name[official]`: `given[0]` + `family`, so the middle name is left out as PHP leaves it out. |
| MRN `(pubpid)` | **Match** | `identifier` typed `PT`. Never the `SS` identifier, even when that is the only one (tested). |
| DOB + age (`DOB: 2004-08-24 Age: 22`) | **Match** | Same format. `getPatientAge()` is ported: months up to 24, then years. |
| Sex | **Match (moved)** | `Patient.gender`. The PHP bar does not show sex (it is on the Demographics card); the requirement puts it in the header. |
| Active status | **Partial** | OpenEMR hardcodes `Patient.active = true` (G2), so "Status: Active" carries no information except that the patient is not deceased. The tooltip says so. |
| Deceased banner | **Match (not observed live)** | Same wording and ago-buckets as `deceasedDays()`. Unit-tested. No local patient is deceased. |
| Photo | **Gap** | OpenEMR does not expose the photo document as `Patient.photo` (G12). The default silhouette is shown, as PHP shows it when there is no photo. |
| Encounter picker, new encounter | **Out of scope** | Encounter actions; not in the requirement. |

### Allergies

| Element | Status | Why |
|---|---|---|
| Allergy names, order | **Match** | `code.coding[].display`. For an uncoded allergy the name is only in the narrative, so it is read from `text.div` (Phil Belford's "penicillin"). |
| Active filter | **Match (3 patients)**; partial at edges | FHIR says `active` only while there is no end date. A future-dated end date is `inactive` in FHIR but active in PHP (G7). A resolved outcome without an end date is active in FHIR but hidden in PHP (G8). |
| Severity in parentheses | **Gap → partial** | FHIR has only `criticality` (low/high/unable-to-assess), into which OpenEMR folds seven severities (G1). The port shows "(High Risk)" etc. when present. Local Synthea rows carry an invalid severity (`1`), which PHP prints raw as "(1)"; FHIR sends nothing, so the port shows nothing. |
| Highlight of severe/life-threatening/fatal | **Partial** | Highlighted when criticality is `high`, which also covers "moderate to severe" (G1). |
| Hover text | **Partial** | Same shape; carries criticality instead of severity. |
| Empty text | **Partial** | Always "Nothing Recorded". PHP says "No Known Allergies" when the list was touched; FHIR has no such flag (G9). The port never asserts NKA it cannot verify. |
| Edit pencil | **Gap (by design)** | Read-only port (G11). |

### Medical problems

| Element | Status | Why |
|---|---|---|
| Titles, order | **Match** | `code.text`, sorted by onset ascending with a missing onset first, as MySQL orders PHP's `begdate`. Same 2, 7 and 2 items for the three patients. |
| Active filter | **Match (3 patients)**; partial at edges | FHIR calls a first-occurrence problem `resolved` (F-67, G6). Future end dates behave as in G7. |
| Empty text | **Partial** | "Nothing Recorded" (G9). |

### Medications

| Element | Status | Why |
|---|---|---|
| Titles, order | **Match** | `MedicationRequest` with `intent = plan` (the `lists` table) and `status = active`. The same two items on each patient. |
| Dosage instructions | **Match (not observed)** | `dosageInstruction.text`. Every local row is empty on both sides. |
| Separation from prescriptions | **Partial (risk)** | OpenEMR emits one MedicationRequest per `lists` row **and** one per `prescriptions` row. The table is not in the resource, so `intent` is the only discriminator (plan = medication list, anything else = prescription). A `lists` row whose intent was edited to `order` would move to Prescriptions. |
| Order when start dates differ | **Partial** | `begdate` is not in the resource; the port keeps FHIR's order. Same on the test patients. |

### Prescriptions

| Element | Status | Why |
|---|---|---|
| Drug, Refills, Filled | **Match** | `coding.display`, `numberOfRepeatsAllowed`, `authoredOn` printed as `YYYY-MM-DD HH:MM:SS`. Seven rows and the same order for pid 20 and pid 49. |
| Which rows | **Match** | PHP shows `active > 0`, which is FHIR status `active` or `completed`. |
| Details | **Gap** | PHP prints `1.00`. OpenEMR's FHIR drops a numeric `dosage` (G3). The port prints whatever dose, text, route and timing FHIR has; here, nothing. |
| Qty | **Match** | `dispenseRequest.quantity`; empty on both sides. |
| Order | **Partial** | PHP sorts by `date_modified` first, which FHIR does not carry (G4). The port sorts by `authoredOn` descending. Same order on the test patients. |
| Empty text | **Match** | "None". |
| Hidden by `disable_prescriptions` or missing `patients/rx` | **Partial** | The port cannot read OpenEMR globals. OpenEMR's FHIR route checks `patients/med`, not `patients/rx` (G14). |

### Care Team

(Verified with a team entered on pid 20 through OpenEMR's own Care Team
form. No local patient had one before.)

| Element | Status | Why |
|---|---|---|
| Team name, status badge | **Match** | `CareTeam.name`, `status`, same badge colours. |
| Type badge (Provider / Related Person) | **Match** | From the member's reference type. |
| Role, Since | **Match** | `participant.role`, `period.start`. |
| Member name, Facility | **Gap** | They need `Practitioner` and `Organization` reads, which OpenEMR allows only with the `admin/users` ACL: a physician gets 403, the same wall D30 hit for `Binary`. The port shows "Not available to your role". An administrator would see names. |
| Member status, Note | **Gap** | Not emitted by OpenEMR's FHIR CareTeam (G10). The columns stay, empty, to keep the table the clinician knows. |
| Empty state | **Partial** | PHP shows headers only; the port adds "No care team recorded." (the work order requires an explicit empty state). |
| Edit mode | **Out of scope** | A write action. |

### Lab results (the additional section)

| Element | Status | Why |
|---|---|---|
| Most recent lab date | **Match** | The PHP Labs card names only the most recent procedure and date, and links to `labdata.php`. The port's newest row has the same date on all three patients. |
| Results table: value, unit, range, flag, date | **New (beyond the PHP card)** | What the requirement asked for, closer to `labdata.php` than to the card. Values are shown exactly as stored, never rounded. |
| Reference range | **Partial** | OpenEMR emits a range only when the stored one parses as two non-zero numbers (G5). D54 rows (`70-99`) come through; `0-5` or `<200` would not. |
| Flag | **Match (D54 rows)** | `interpretation`, labelled by its HL7 code, because OpenEMR sends `N` with the display "No". Synthea rows have no flags (F-19). |
| D54 write-back rows | **New** | All 43 rows the write-back filed for pid 49 render, badged "From document", with the extraction id on hover. Verified with a fresh upload of `lab-multipage-003` on 2026-09-24. |
| Per-analyte trend | **New** | A sparkline with the range band beside the latest result of each analyte measured at least twice. One series per LOINC code **and** unit string: Synthea's `10*3/uL` and the report's `K/uL` are never joined. |
| Empty text | **Match** | "No lab data documented." |

### Cross-cutting

| Element | Status | Why |
|---|---|---|
| Layout, classes, colours, type | **Match** | Same Bootstrap release and theme variables. Card chrome measured from the PHP page's computed styles. Side by side in `docs/dashboard/screenshots/side-by-side-pid{20,49}.png`. |
| Collapse per card | **Partial** | Same toggle and icons. Remembered per browser, not per user (G13). |
| Loading state | **Match** | `loader.html.twig`'s spinner. |
| Error state | **New** | Per card. PHP renders the page in one request, so a card cannot fail alone. |
| Translation (`xlt`) | **Gap** | The port is English only. OpenEMR's translation tables are not reachable over the API. |
| Date format global | **Partial** | The port uses Y-m-d, OpenEMR's default. PHP follows `date_display_format`. |
| Time zone | **Partial** | Times are shown as FHIR carries them (UTC offset `+00:00`). They equal PHP's only while the server runs in UTC, as both local and host do. |
| ACL-driven card hiding | **Partial** | PHP hides a card the user may not see. The port shows that card's error ("Your OpenEMR role does not permit reading this section") (G14). |

---

## 6. Tradeoffs and costs, honestly

- **Tokens are held by a service instead of a browser.** That is better
  than the browser, but the BFF is now a place where every active
  clinician's access token lives. A compromised BFF process yields all of
  them.
  - Mitigation: memory only; no disk; one small container that is read-only
    and drops all capabilities; tokens and PHI are absent from logs by
    construction; sessions end after 8 hours at most.
- **Refresh tokens last three months and cannot be revoked at logout.**
  OpenEMR issues a refresh token only with `offline_access`, sets it to P3M,
  and has no revocation endpoint. The BFF drops the refresh token on logout
  and at the 8-hour cap, but OpenEMR still considers it valid.
  - Mitigations: a clinician can untick offline access on OpenEMR's consent
    page (the BFF then ends the session at the one-hour token expiry), and an
    administrator can revoke the client's tokens in Admin > System > API
    Clients.
  - The alternative is to drop `offline_access` and accept a sign-in every
    hour. That is a policy call for the owner, and one line of
    configuration (`OAUTH_SCOPES`).
- **ACLs are enforced only by what OpenEMR's FHIR layer enforces.** That
  means scopes plus a section-level ACL per resource type (e.g.
  `patients/med` for MedicationRequest). The PHP page's finer per-card rules
  (prescriptions hidden without `patients/rx`, the `hiddenCards` user
  setting) do not carry over. Neither layer has a per-patient boundary
  (F-50): a clinician who can read one chart can read any. The BFF's
  allowlist narrows the types, not the patients.
- **API parity gaps are real.** §5 lists them: severity, prescription
  details, care-team names, member status, notes, translation, and the photo.
  Each is a place where FHIR, as OpenEMR implements it, carries less than
  the database. None can be closed from the client. Some can be closed in
  OpenEMR's FHIR mappers (F-67, G3).
- **A second codebase and toolchain.** Node 24, npm, TypeScript, Vite and
  Playwright sit next to PHP.
  - Runtime supply chain: two npm packages.
  - Build and test chain: the usual front-end set (`package-lock.json`,
    pinned). Bootstrap and Font Awesome are pinned exactly to OpenEMR's
    versions, and must be bumped with OpenEMR's.
  - An OpenEMR upgrade can change a FHIR mapping silently. The e2e
    comparison against the PHP page is the tripwire for that, and it needs
    the full stack, so it is not in CI (§8).
- **Two deploy units and one more origin.** The dashboard is served on its
  own origin (`:8444` on the host), which is one more port to firewall and
  check (F-54).
- **Found on the way, not caused by the port:** F-65 (secretless code
  redemption, High), F-66 (refresh broken by `nonce`), F-67 (a
  first-occurrence problem reported as resolved).

---

## 7. How to run, build and test

Prerequisites: the local OpenEMR dev stack (`docker/development-easy`, see
`docs/openemr-dev-guide.md`), Node 22 or newer, and Google Chrome (the e2e
tests use the installed browser).

**One-time: register the BFF's OAuth client on the local OpenEMR.**
1. Register it with `POST https://localhost:9300/oauth2/default/registration`:
   `application_type: private`, `token_endpoint_auth_method:
   client_secret_post`, `redirect_uris` `http://localhost:8787/auth/callback`
   and `http://localhost:5173/auth/callback`, `post_logout_redirect_uris`
   `http://localhost:8787/`, and scope `openid offline_access` plus the eight
   `user/<Type>.read` scopes in `dashboard/bff/src/config.ts`.
2. Enable it in Admin > System > API Clients. On the local stack, where the
   admin password is unknown, earlier sessions set `is_enabled=1` in SQL.
   That is an out-of-band edit (F-40).
3. Save the response under `~/.config/clinical-copilot/`: the JSON as
   `dashboard-bff-local.reg.json`, and the `client_secret` alone in
   `dashboard-bff-local.secret` (mode 600).
4. Export OpenEMR's certificate so TLS is verified, not skipped:
   `openssl s_client -connect localhost:9300 </dev/null | openssl x509 > ~/.config/clinical-copilot/openemr-local-ca.pem`.

**Build, run and test:**
```bash
cd dashboard
npm ci
npm run typecheck                 # strict TS, SPA + BFF
npm test                          # 80 unit tests, no network
npm run build                     # dist/web (SPA) + dist/bff
scripts/run-local.sh              # BFF + SPA on http://localhost:8787
npm run test:e2e                  # 7 e2e tests against the local stack (starts the BFF if needed)
SCREENSHOTS=1 npm run test:e2e -- screenshots && python3 scripts/compose-screenshots.py   # regenerate docs/dashboard/screenshots/
PERF=1 npm run test:e2e -- perf                 # regenerate docs/dashboard/perf.json
```

**Dev loop:** run `npm run dev:bff` with `PUBLIC_ORIGIN=http://localhost:5173`
and the variables from `scripts/run-local.sh`, plus `npm run dev:web`. Vite
proxies `/auth` and `/fhir` to the BFF, so the browser still sees one origin.

**Container:** `docker build -t patient-dashboard:local dashboard/`.
Configuration is in `dashboard/.env.example`. The BFF refuses to start
without its client id and secret.

---

## 8. What Saturday's deploy needs (not done here)

Nothing was deployed. The host needs the following, in this order:

1. **The OAuth client, registered on the host's OpenEMR.**
   - Confidential (`application_type: private`), `client_secret_post`.
   - Redirect URI `https://64.177.42.210:8444/auth/callback`.
   - Post-logout URI `https://64.177.42.210:8444/`, exactly one: OpenEMR
     compares the stored list as a single string.
   - Scopes: `openid offline_access user/Patient.read
     user/AllergyIntolerance.read user/Condition.read
     user/MedicationRequest.read user/CareTeam.read user/Observation.read
     user/Practitioner.read user/Organization.read`.
   - An administrator enables it in Admin > System > API Clients, not in
     SQL. `offline_access` requires that approval anyway.
   - Scopes are immutable per client, so a mistake means a new client.
2. **The secret.** Written to `/etc/patient-dashboard/client_secret`, owned
   by uid 1000 (the image's `node` user), mode 0400. It is mounted as a
   Docker secret, never set as an environment variable, and never in the repo.
3. **OpenEMR's certificate.** Exported from the host to
   `/etc/patient-dashboard/openemr-ca.pem`. The BFF verifies OpenEMR's
   `CN=localhost` certificate for `localhost` while dialing
   `host.docker.internal` (`OPENEMR_TLS_SERVERNAME`). Unlike the agent's
   `FHIR_VERIFY_TLS=false`, verification stays on.
4. **`/etc/patient-dashboard/dashboard.env`:**
   - `PUBLIC_ORIGIN=https://64.177.42.210:8444`.
   - `OAUTH_CLIENT_ID`.
   - `OPENEMR_BROWSER_OAUTH_BASE=https://64.177.42.210:9300/oauth2/default`.
   - `OPENEMR_ISSUER` and `OPENEMR_FHIR_AUDIENCE`: read both from
     `https://64.177.42.210:9300/oauth2/default/.well-known/openid-configuration`
     on the day. If the host still advertises `localhost` (F-49), set them to
     what it advertises.
5. **The container.** `dashboard/deploy/docker-compose.dashboard.yml`.
   - No published port. It joins the agent's network,
     `clinical-copilot-agent_default`, as `bff:8787`.
   - Limits: `mem_limit: 256m` (measured use is 20–25 MiB), read-only,
     `cap_drop: ALL`, `no-new-privileges`, healthcheck on `/healthz`.
6. **The proxy route.** This is an edit to `agent/deploy/`, which work order
   31 did not make:
   - Add a site block `https://{$AGENT_PUBLIC_HOST}:8444 { tls internal;
     request_body { max_size 16KB }; reverse_proxy bff:8787 }`.
   - Publish `8444` on the proxy service.
   - The port gets its own origin, so the dashboard's `Path=/` cookies and
     CSP never mix with the agent UI on 8443.
7. **The firewall.** Open 8444 on the host firewall. Then confirm the real
   bindings with `ss -tlnp` (F-54): 8444 served by Caddy only, and nothing
   on 8787 outside Docker.
8. **The smoke test.** Sign in as a clinician, open a patient with labs, and
   check that `/fhir/Encounter?patient=…` answers 403 and a POST to
   `/fhir/Patient` answers 405.

**The CI job** `dashboard-tests` was added by work order 33 (D61), in a gate
session (`.gitlab-ci.yml` is a gate file). Image `node:24`. It is created when
`dashboard/**` or `.gitlab-ci.yml` changes, and runs
`cd dashboard && npm ci && npm run typecheck && npm test && npm run build`,
with the unit results as a JUnit report. It takes about 40 s from a cold image
and needs no credentials. The e2e suite stays a local, pre-merge step,
because it needs the OpenEMR stack.

---

## 9. Where things are

| | |
|---|---|
| SPA | `dashboard/web/src/`: `components/` (cards, header), `mapping/` (FHIR to view model), `fhir/types.ts`, `api.ts` |
| BFF | `dashboard/bff/src/`: `app.ts` (routes, headers, refresh), `oauth.ts`, `fhirProxy.ts` (allowlist), `sessions.ts`, `log.ts`, `upstream.ts`, `static.ts` |
| Tests | `dashboard/test/unit/`, `dashboard/test/e2e/`, fixtures in `dashboard/test/fixtures/` (synthetic patients only, SSN replaced) |
| Container, deploy proposal | `dashboard/Dockerfile`, `dashboard/deploy/docker-compose.dashboard.yml` |
| Phase 1 inventory, screenshots, measurements | `docs/dashboard/PARITY_INVENTORY.md`, `docs/dashboard/inventory/`, `docs/dashboard/screenshots/`, `docs/dashboard/perf.json` |
| Decision | `docs/w2/DECISIONS.md` D59 |
| Audit findings from this work | `docs/audit/findings.md` F-65, F-66, F-67 |
