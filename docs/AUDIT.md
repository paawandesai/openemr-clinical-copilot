# Stage 3 Audit — Clinical Co-Pilot on OpenEMR

**Source of record:** `docs/audit/findings.md` (F-01–F-54). This document is
the narrative built from that ledger, not a replacement for it — every claim
below traces to a numbered finding there, and the raw commands, query output,
and log excerpts behind each one live in that file, not here.

**Environments audited:** a local development stack (`docker/development-easy`,
Colima) and the deployed DigitalOcean droplet (`201.79.25.22`), both running
OpenEMR 8.2.0-dev with the same Compose file. Two commits are relevant: `c4db157`
(the commit this ledger's evidence was gathered against — confirmed live and
unmodified on the droplet, F-53) and `b0a0bcc` (the port-binding fix committed
and pushed as part of resolving F-54).

**Reading the verification tags:** every finding below is marked **first-hand**
(re-run or re-read directly in an audit session) or **operator-reported**
(supplied by the operator, not independently re-verified). These are preserved
exactly as findings.md recorded them — neither is upgraded to the other here.
Where a finding was corrected between the initial brief and a later pass, that
correction is summarized, not hidden.

---

## Executive Summary

This audit produced 54 findings across two live environments. The eight below
are not a table of contents — they are the findings that most directly decide
whether this codebase is safe to build a Clinical Co-Pilot on, ranked by actual
impact rather than by category.

1. **Docker's port-publishing bypasses the host firewall entirely, and this was
   live (F-54).** Docker inserts its own NAT rules ahead of UFW: published-port
   traffic is DNAT'd in `PREROUTING` and reaches the `FORWARD` chain through
   Docker's own chains before any `ufw-*` chain runs, and the `DOCKER-USER`
   hook where an operator could intercept it was empty. UFW's default-deny
   policy never saw this traffic. As a direct result, MariaDB (default
   `root`/`root`) and phpMyAdmin sat reachable from the public internet for a
   bounded window of 3 h 48 min to 18 h 09 min. A first remediation attempt —
   an *additive* Compose override — made it worse (dual bindings, repeated
   failed container starts) until the base file itself was edited. The fix is
   now confirmed from three vantage points, but every future service (the
   agent process, its logging backend) inherits this exact blind spot unless
   its bindings are checked directly, never inferred from firewall rules.
2. **A remotely-triggerable PHP debugger is loaded into the live web process
   (F-35).** Xdebug runs in production Apache/mod_php with
   `discover_client_host=1` and no trigger value required — by its own
   documentation, anybody who can reach the webserver can open a debugging
   session, on a host already public on two ports.
3. **Lab results carry no reference ranges or abnormal flags, ever (F-19).**
   Every one of 7,259–29,701 result rows across both environments has a value
   and a LOINC code but a permanently blank `range`/`abnormal` field — a
   co-pilot cannot honestly call a value "high," "low," or "normal" from this
   record alone.
4. **Five demo accounts still authenticate with their own username as the
   password, one of them an Administrator (F-32).** This was still true at the
   last check and is a distinct, ongoing exposure from F-54's now-closed port
   issue.
5. **Authorization has no concept of "this patient" (F-50).** Both the ACL and
   OAuth-scope layers gate by section/action only; any clinician token with
   general chart access can read any patient's FHIR record, and per-patient
   scoping does not exist anywhere in the stack the co-pilot could inherit.
6. **One real patient record is 974 KB of raw rows (F-22).** An outlier with
   530 encounters — 18x the local median — proves whole-chart prompting is
   architecturally impossible at real patient volumes; retrieval and
   truncation are not optional.
7. **Reads generate no audit trail, and the trail that exists is ~80% noise
   (F-10, F-11).** `audit_events_query` ships disabled, and even the write-side
   categories that are enabled are 80–81% generic insert/delete rows —
   OpenEMR's own log cannot answer "who looked at this chart."
8. **The live deployment is the development image, not the hardened build
   (F-48).** Dev tooling, Xdebug, and a writable bind-mount of the git checkout
   into the web container are root causes feeding directly into findings 1 and
   2, and a full dependency build runs against upstream registries on every
   container boot.

---

## Security

### F-01 — GitHub token committed in three encodings
**Severity:** Medium · **Verified:** first-hand
`docker/development-easy/docker-compose.yml:75-77` carries a GitHub PAT in
plaintext, base64, and space-separated ASCII decimal simultaneously, so
scanning for any one form misses the other two. Confirmed live in the
droplet's running container environment as well (variable names present,
values not printed), not only in git history. Likely an already-revoked
inherited credential — the finding is the committed *pattern*, which any
secret the co-pilot introduces must not repeat.

### F-02 — OAuth2 self-registration produces an unusable client
**Severity:** Medium · **Verified:** first-hand (schema defaults, compose
setting, live table state); operator-reported (that all three failure modes
surface as an identical opaque `invalid_client`)
`register-oauth2-client` inserts `is_enabled=0`, `is_confidential=1`, and
`grant_types='authorization_code'` only (`sql/database.sql:14118,14126`;
devtools registration payload, `/root/devtools:802-810` — droplet-only
artifact, not independently re-verified this session), while the deployment
globally advertises `oauth_password_grant: 3`, a grant no as-registered client
holds. On both hosts, one client was later enabled with extra grants through a
path that wrote nothing to OpenEMR's own audit log (ties to F-40) — the
as-shipped registration path has never produced a working client.

### F-03 — Client secret printed to stdout on registration
**Severity:** Low · **Verified:** first-hand
`/root/devtools:811-814` echoes the client secret in cleartext (droplet-only
artifact; confirmed via direct execution in the local container this session).
Any secret produced this way must be treated as already disclosed and rotated,
never reused from shell history or CI logs.

### F-04 — OAuth signing keys generated into the patient-document tree
**Severity:** Medium · **Verified:** first-hand
`src/Common/Auth/OAuth2KeyConfig.php:63-64` writes the OAuth private/public
keys to `sites/default/documents/certificates/` — the same parent directory as
`00/` (patient documents) and `procedure_results/` (lab documents). Confirmed
on both hosts, keys created at first OAuth use, not install. Any document-tree
traversal, backup, or indexing the co-pilot performs must explicitly exclude
`certificates/`.

### F-05 — POSITIVE: client secrets are not stored in plaintext
**Severity:** Positive · **Verified:** first-hand
Stored secrets carry a `007` CryptoGen version prefix and are 219 characters —
verified not to match the 88-character plaintext secret printed at
registration (`src/Common/Crypto/KeyVersion.php:30-31,39,64-67`). No
compensating control needed here.

### F-06 — POSITIVE: private key passphrase is CSPRNG-random per install
**Severity:** Positive · **Verified:** first-hand
`src/Common/Auth/OAuth2KeyConfig.php:215` generates a 60-character passphrase
from a 62-character alphabet via `random_int()`
(`src/Common/Utils/RandomGenUtils.php:33-41`), PHP's CSPRNG. No key-material
weakness to design around.

### F-07 — POSITIVE: FHIR scope enforcement holds
**Severity:** Positive · **Verified:** operator-reported (the in-scope 200 and
out-of-scope 401s); first-hand (forged-token refusal, reproduced identically
on both hosts, and the enforcement code path,
`src/RestControllers/Subscriber/AuthorizationListener.php:189-192`)
A token scoped to `openid api:fhir user/Patient.read` reads Patient
successfully and is refused on every resource outside that scope; a forged
bearer token is refused identically. Scope enforcement is a real boundary the
co-pilot can rely on — but it cannot widen access by reusing a narrow token.

### F-08 — Out-of-scope and invalid-token responses share a status but not a body
**Severity:** Low · **Verified:** operator-reported (out-of-scope responses);
first-hand (invalid-token responses and both source paths)
Both cases return 401 (never the RFC 6750 403 for insufficient scope), but the
JSON `message` differs by cause — `"Unauthorized"` versus
`"The resource owner or authorization server denied the request."`
(`src/Common/Http/HttpRestRouteHandler.php:112`;
`src/RestControllers/Subscriber/ExceptionHandlerListener.php:44-45,51`). *This
corrects the original brief*, which described the two failures as identical
and indistinguishable — they are distinguishable, but only by string-matching
a library-owned message, not a status code.

### F-09 — Token response `scope` disagrees with the JWT `scopes` claim
**Severity:** Medium · **Verified:** operator-reported (not reproduced
first-hand — no token was minted this session)
The token endpoint's `scope` field omitted `api:fhir`, which the issued JWT's
`scopes` claim contained. The co-pilot must derive its effective permissions
by decoding the JWT, never by trusting the response field, or it will
understate what it actually holds.

### F-31 — Default `admin`/`pass` was live on the internet for 1 h 22 min 21 s
**Severity:** High · **Verified:** first-hand
Reconstructed precisely from the droplet's container log (Apache started
20:51:58Z), `users_secure.last_update_password` (changed 22:14:19Z), and the
access log's single matching `user_info_ajax.php` request. Every fresh
deployment of this stack is born with a known credential and serves it
publicly within ~2 minutes of container start.

### F-32 — Five demo accounts with username-as-password remain live (ongoing)
**Severity:** High (exposure ongoing at 2026-09-15T00:12:00Z) · **Verified:**
first-hand (stored-credential state, group membership, reachability); not
verified (an actual interactive login — deliberately not attempted)
`accountant`, `clinician`, `physician`, `receptionist`, `zhportal` all
authenticate with their own username; `zhportal` sits in the gacl
**Administrators** group, and `physician`/`clinician` are authorized
providers. Login-throttling globals (`password_expiration_days`,
`password_max_failed_logins`, `secure_password`) are all disabled or
permissive. Re-checked twice ~90 minutes apart with identical results.
Changing `admin` alone does not secure a demo-seeded instance.

### F-33 — Docker-published ports bypass UFW; all four service ports were reachable
**Severity:** High · **Verified:** first-hand (bindings, rule sets,
operator-vantage reachability, UFW statistics); not tested (reachability of
*local* ports from another LAN host)
Point-in-time pass-2 evidence: 8300/9300/8310/8320 all bound on `0.0.0.0`/`[::]`,
`DOCKER-USER` empty, no cloud firewall filtering (corroborated by 338 distinct
scanning IPs in the first ~2 h alone), and one external host
(`45.79.207.252`) reaching OpenEMR directly. This is the state F-54 traces to
root cause and resolves for 8310/8320; 8300/9300 remain public by design.

### F-34 — Database reachable with default `root`/`root`; its TLS private keys are committed
**Severity:** High · **Verified:** first-hand; not attempted (any remote
database or phpMyAdmin login)
`root@'%'` and `openemr@'%'` both authenticate with the Compose defaults;
plaintext connections are permitted; the MariaDB TLS CA and server private key
are tracked in git (`docker/library/sql-ssl-certs-keys/easy/server-key.pem`,
confirmed `-----BEGIN RSA PRIVATE KEY-----`). MariaDB's `general_log` is off,
so successful external connections during any exposure window — including
F-54's — cannot be ruled out from logs.

### F-35 — Xdebug is trigger-activatable by any internet client
**Severity:** High · **Verified:** first-hand (configuration, SAPI loading,
log state); not tested (exploitability — no trigger was sent)
`xdebug.mode=debug,profile`, `start_with_request=trigger`, empty
`trigger_value`, `discover_client_host=1`, loaded into the Apache worker
process (not only CLI). Xdebug's own documentation: *"anybody who can connect
to the webserver will then be able to start a debugging session."* No log
evidence of use was found. Development debugging extensions make every other
control in this ledger moot on an exposed host; any deployment reachable
beyond localhost must assert Xdebug is off at startup.

### F-36 — Self-signed `CN=localhost` certificate on a bare IP; plaintext HTTP also served
**Severity:** Medium · **Verified:** first-hand
The droplet's HTTPS certificate is self-signed, marked as a CA, and its subject
doesn't match the server's actual address; the identical login page is also
served over plaintext HTTP on 8300. Clients will be pushed toward disabling
TLS verification entirely, which also disables protection against
interception — the co-pilot's own HTTP client must pin an explicit CA instead.

### F-37 — `readyz` returns raw exception messages to unauthenticated callers
**Severity:** Low · **Verified:** first-hand (source); not exercised (the
leak itself — no triggering error was captured)
`meta/health/index.php:91-95` catches every `Throwable` and returns
`$e->getMessage()` with HTTP 200 — directly contradicting this repo's own
`CLAUDE.md:465` rule against exposing exception messages in user-facing
output.

### F-38 — POSITIVE: droplet SSH is key-only
**Severity:** Positive · **Verified:** first-hand; inference, not confirmed
with DigitalOcean (the one anomalous login's Droplet-Console attribution)
Password and keyboard-interactive SSH authentication are disabled; every login
used a public key; the one non-operator login came from a DigitalOcean-owned
address consistent with the web Droplet Console, not an intrusion. Shell
access is not this deployment's weak point — the application-layer exposures
above are.

### F-39 — POSITIVE (bounded): no non-operator client reached the app during the admin/pass window
**Severity:** Positive · **Verified:** first-hand
Across the full access log, only the operator's IP, in-container healthchecks,
and the droplet's own address touched OpenEMR or phpMyAdmin during the
`admin`/`pass` exposure window. The bound does not extend to the database:
`general_log=0` means successful MariaDB connections in that window are
unobservable.

### F-54 — RESOLVED: MariaDB and phpMyAdmin were publicly reachable through Docker's UFW bypass
**Severity:** High (resolved — loopback-only replacements running from
2026-09-15T15:17:54Z) · **Verified:** first-hand (pre-fix bindings and
reachability, chain layout, default credentials, UFW statistics, the fix
commit and its local verification, droplet deployment state, and post-fix
`ss`/external `nc`); operator-reported (pre-fix `ss -tlnp`, the dual-binding
`docker compose ps`, the independent port-checker result)

**Mechanism.** Docker publishes container ports via its own `nat` table:
`iptables -t nat -S PREROUTING` shows `-A PREROUTING -m addrtype --dst-type
LOCAL -j DOCKER`, which DNATs traffic to the container before the packet ever
reaches an INPUT-chain rule UFW controls. Forwarded traffic then hits
`-A FORWARD -j DOCKER-USER` and `-j DOCKER-FORWARD` *ahead of*
`-j ufw-before-forward` in the `FORWARD` chain, and `DOCKER-USER` itself held
no rules (`-N DOCKER-USER` only). UFW's default-deny, 22/80/443-only policy
therefore never evaluated Docker-published traffic at all — a restrictive
firewall gave false confidence while MariaDB (default `root`/`root`,
plaintext connections permitted) and phpMyAdmin sat bound to `0.0.0.0`/`[::]`
on 8320/8310.

**Failed first remediation.** A `docker-compose.override.yml` adding
loopback-only alternates (`127.0.0.1:18320`, `127.0.0.1:18310`) did not fix
this: Compose merges `ports` lists additively rather than replacing them, so
`docker compose ps` showed *both* the original public binding and the new
loopback one simultaneously. The droplet's dockerd journal independently
corroborates the resulting conflict — five container-start failures logging
`"Failed to allocate port" error="failed to bind host port 127.0.0.1:8320/tcp:
address already in use"` — and a local reproduction of the same override
pattern hit the identical error.

**Working fix.** Commit `b0a0bcc` edited the *base* file directly —
`docker/development-easy/docker-compose.yml:7,150` now read
`"127.0.0.1:18320:3306"` and `"127.0.0.1:18310:80"` — replacing the
all-interfaces defaults rather than layering an addition on top of them.

**Resolution verified from three vantage points:** (1) the droplet's own
`ss -tlnp` shows listeners only on `127.0.0.1:18320`/`18310`, with no
`0.0.0.0`/`[::]` binding on either service; (2) an external `nc` from the
operator's laptop timed out on 18320/18310 while 9300 connected, corroborated
by matching UFW-block log entries for those exact probes; (3) an independent
external port-checking service (a network under neither the droplet's nor the
laptop's control) reported 18310 closed.

**Exposure window — bounded, not exact.** Reconstructed from the droplet
kernel log's container network attach/detach events: **at least 3 h 47 min 48
s** established public (the original stack, 20:49:36Z–00:37:24Z), **at most
18 h 09 min 12 s** if every unlogged interstitial stack restart is assumed
still public. The precise duration is **blocked**: intermediate restarts'
bindings were never logged, the override file's creation time is gone (it has
since been deleted), and UFW's log structurally cannot show contact with a
published port — only rejected traffic to *unpublished* ones. Whether the
exposure was actually used is separately unestablishable, since MariaDB never
logged successful connections.

**Implication.** Host firewall state is not evidence of container network
exposure. A Compose override cannot remove a base file's port mapping unless
explicitly marked to replace it (`!override`/`!reset`). Every future service
this project adds — the agent sidecar, the observability backend — must have
its actual bound listen address checked directly (`ss -tlnp`, resolved
`docker compose config`) from a genuinely external vantage point before being
called internal-only.

---

## Performance

### F-24 — FHIR Patient read baseline: 0.375 s under ideal conditions
**Severity:** Informational · **Verified:** operator-reported; first-hand
(unauthenticated reference points only — no token available this session)
A 5-record authenticated FHIR Patient query returned 200 in 0.375 s locally,
single-user, no contention, no LLM in the path. This is a floor, not a typical
figure — a co-pilot turn making several such calls starts from a
multi-second baseline before the model even runs.

### F-25 — Lab join plan is cost-based, not systematically broken
**Severity:** Low (was Medium — corrected in pass 2) · **Verified:**
first-hand
The original claim — that the optimizer always full-scans `procedure_report`
instead of seeking by patient — held only for one outlier patient locally,
whose order count (907) exceeded the entire report table (596 rows); every
other patient measured, and the droplet's own worst case, get the
patient-first plan by default. Measured cost delta was ~0.1 ms, within noise.
The query shape the original brief warned against exists in the codebase only
as dead code (`src/Services/ProcedureService.php:558-576`
`searchProcedureReports()`, no callers anywhere in the tree).

### F-26 — readyz loads the full framework on every probe
**Severity:** Low · **Verified:** first-hand
`meta/health/index.php:52-58`'s own comment acknowledges bootstrapping the
entire framework per health check. A pass-2 remeasurement (5 runs instead of
2-3) found the gap versus the minimal `livez` real and non-overlapping — a
consistent ~81 ms / 2.6x, not the "noise-level" first estimate — though still
low priority in absolute terms.

### F-44 — Droplet compute profile: architecture and tenancy differ from local
**Severity:** Informational · **Verified:** first-hand (guest-visible CPU,
memory, steal, iowait); operator-reported (Shared vs. Dedicated plan type,
unconfirmable from inside the guest)
Droplet: 4 vCPU AMD EPYC (KVM), x86_64. Local: 6 vCPU Colima on an M5 Pro,
aarch64. This corrects the brief's "burstable CPU credit" framing — DigitalOcean
documentation confirms shared vCPUs are subject to hypervisor steal time, not
an AWS-style credit mechanism; measured cumulative steal was near zero.
Cross-environment latency comparisons must record steal/iowait, not just wall
time.

### F-45 — Synthea import was slower on the droplet; the gap depends on the normalizer
**Severity:** Informational · **Verified:** first-hand (DB-timestamp
reconstruction); operator-reported (that both runs used the same invocation)
9.00 s/patient droplet vs. 1.41 s/patient local by insert-timestamp proxy
(6.4x) — but droplet patients carry ~5x the lab-result volume each, so
normalized per clinical row the gap is 1.3x–4.3x. A resourcing observation,
not a defect.

### F-46 — Context assembly for the worst-case patient: ~5-7 ms server-side, ~1 MB of rows
**Severity:** Informational · **Verified:** first-hand
Ten `SELECT`s covering one patient's full chart (demographics through lab
results) run in 5.1-6.9 ms server-side and return 974,140 bytes locally
(749,934 on the droplet). Database latency is not the bottleneck for context
building — data volume and the need to summarize it are, and lab results plus
procedure orders alone are ~97% of the raw bytes.

### F-47 — Network-path latency to the droplet; authenticated read blocked
**Severity:** Informational · **Verified:** first-hand (all unauthenticated
timings); blocked (an authenticated FHIR read against the droplet — no bearer
token was obtainable without using a demo-default credential or performing a
write, neither of which this audit chose to do)
Unauthenticated round-trips run ~0.1-0.3 s slower than localhost, with TLS
setup alone costing ~110 ms per new connection. This is the first real
network-path number available, but it cannot yet be composed with the local
0.375 s authenticated floor (F-24) because no authenticated droplet read has
been measured.

---

## Architecture

### F-27 — Dual codebase: modern PSR-4 alongside legacy procedural
**Severity:** Informational · **Verified:** first-hand
`/src` (PSR-4, `OpenEMR\` namespace) and `/library` (legacy procedural)
coexist. Pass 2 verified every framework/version claim against
`composer.lock`/`composer.json`: Laminas MVC 3.8.0, Twig v3.27.1, Smarty
v4.5.6, Doctrine DBAL 4.4.3, `adodb/adodb-php` v5.22.11 (still a *direct*
dependency, not only a DBAL-emulated surface), `league/oauth2-server` 8.5.5.
Runtime PHP is 8.5.6 in both environments while Composer's platform pins 8.2
— code is tested against 8.2 constraints but executed on 8.5.

### F-28 — Every integration surface the co-pilot needs already exists — on disk
**Severity:** Informational · **Verified:** first-hand
64 FHIR services, 33 REST controller entries, a 5-file ACL facade, 24 event
namespaces, and 8 precedent custom modules are all present. *Correction:*
none of the 8 custom modules is registered active in the `modules` table in
either environment (`src/Core/ModulesApplication.php:141` loads only
`mod_active = 1 AND type != 1`, and no such row exists) — the "precedent" has
never been exercised at runtime.

### F-29 — Container health is decoupled from application readiness
**Severity:** Medium · **Verified:** operator-reported (the specific
not-installed episode); first-hand (mechanism)
`docker compose up --wait` reported the container healthy while OpenEMR was
not installed. Mechanism confirmed in source: the healthcheck's `curl --fail`
only reacts to HTTP≥400, and every branch of `readyz` — including its
`setup_required` and caught-exception paths — always returns 200
(`docker/development-easy/docker-compose.yml:112-125`; `meta/health/index.php:70,73-78,91-95`).
*Note: the healthcheck's `retries: 3` sits at line 125, one line past the
range the original evidence citation gave (112-124) — corrected here.*
A container can never fail this healthcheck on application state.

### F-30 — `readyz` can never report `installed: true`
**Severity:** Medium · **Verified:** first-hand
The endpoint's own bootstrap overwrites the `$config` global its installation
check depends on: `interface/globals.php:384` loads `library/sql.inc.php:59`,
which reassigns `$config` to a `DatabaseConnectionOptions` object, so
`src/Health/Check/InstallationCheck.php:42`'s strict `$config !== 1` always
fails — even on a correctly installed, 284-table system. Confirmed
byte-identical on both hosts. Deployment automation must not gate on this
field.

### F-48 — The deployment runs the development stack, not the hardened build
**Severity:** Medium · **Verified:** first-hand (topology, environment,
mounts, build log, on-disk alternatives); operator-reported (that a Stage 2
specification permits this — no such document exists in the repository;
confirmed via `grep -rniE "stage ?2" README.md docs/`, which returns nothing
outside `findings.md` itself)
The droplet runs `docker/development-easy/docker-compose.yml` verbatim: dev
mode, Xdebug with the profiler, a read-write bind-mount of the live git
checkout into the web container (so the web-server user can rewrite
application source), and a full composer/npm/webpack build on every container
boot (26 npm audit findings at last boot: 2 low, 11 moderate, 11 high, 2
critical). The hardened multi-stage build in `docker/release/` was not used.
This is the structural root cause behind F-35's exposed debugger and this
deployment's non-deterministic startup.

### F-49 — OAuth/SMART discovery advertises `localhost` on the public deployment
**Severity:** Medium · **Verified:** first-hand (discovery output and
configuration source); not verified (the `iss` claim inside an issued token —
no token was minted)
The droplet's OIDC and SMART discovery documents name
`https://localhost:9300` as the issuer and every endpoint, sourced from the
Compose default `site_addr_oath`. Any standards-following external client —
including an off-box co-pilot — doing discovery against the public droplet
will be pointed at its own localhost rather than the real server.

### F-50 — ACL layer has no per-patient scoping
**Severity:** Informational · **Verified:** first-hand
`AclMain::aclCheckCore()` (`src/Common/Acl/AclMain.php:166`) answers "may user
U perform action R on section S" — it takes no patient argument, and 531 call
sites across 305 files all follow this shape. The OAuth-scope layer
(`AuthorizationListener.php:189-192`) is equally section/resource-scoped, not
patient-scoped, except for the special-cased patient-context token path
(`apis/routes/_rest_routes_fhir_r4_us_core_3_1_0.inc.php:583`). Any clinician
token with `patients`/`demo` access can read any patient's chart through
`GET /fhir/Patient`; per-patient restriction does not exist in the platform
and must be built by the co-pilot itself if it needs to honor a care-team
boundary.

### F-51 — FHIR/REST routes wrap pre-existing domain services, constructed with `new`
**Severity:** Informational · **Verified:** first-hand (source); the
Observation fan-out's actual query cost was not measured (no authenticated
FHIR request could be made this session)
65 `GET /fhir/*` route closures in one 876-line file
(`apis/routes/_rest_routes_fhir_r4_us_core_3_1_0.inc.php`) build controllers
that `new`-construct `Fhir*Service` classes wrapping OpenEMR's existing
domain services — not container-injected. `Observation` fans out across ten
mapped sub-services (`src/Services/FHIR/FhirObservationService.php:64-73`)
unless the caller passes `category` and/or `code`
(`:117-130`,`:132-146`,`:148-150`, confirmed by direct read), which narrow the
service set before dispatch. Scope enforcement lives in a Symfony kernel
listener, not in this service layer.

### F-52 — No event fires on a read; zero custom modules are actually installed
**Severity:** Informational · **Verified:** first-hand
A single Symfony `EventDispatcher` (`src/Core/Kernel.php:62`) backs both
patient-dashboard UI hooks (two of the eight on-disk modules subscribe to
`RenderEvent::EVENT_SECTION_LIST_RENDER_BEFORE`/`AFTER`,
`interface/patient_file/summary/demographics.php:1354,1533`) and REST-route
extension (`RestApiCreateEvent::EVENT_HANDLE`). No event exists for "a FHIR,
REST, or service-layer record was read" — only a UI-level
`patientDemographics.view`. The co-pilot cannot delegate its own access
logging to an existing event; it must write its own records at the point it
reads (directly reinforcing F-11).

### F-53 — POSITIVE: the code live on the droplet is exactly the audited commit
**Severity:** Positive · **Verified:** first-hand
`git rev-parse HEAD` on the droplet returns the same commit
(`c4db1578b53293611665d9190fe13f1458312590`) as the local audit checkout and
`origin/main`, with a clean, zero-untracked working tree. Every source
citation in this document applies to code that was actually running. This
guarantee depends on the checkout staying read-only from the application's
perspective — which F-48 shows is not currently the case.

---

## Data Quality

### F-15 — `import-random-patients N false` is a success-shaped silent failure
**Severity:** High · **Verified:** first-hand
The non-dev-mode import path exits 0 and prints per-record success messages
while creating zero patients. Root cause traced to source: devtools never
passes `--auth_name` to two downstream console commands
(`contrib/util/ccda_import/import_ccda.php:195-207`), whose `exec()` failures
are never checked, leaving orphan `documents` rows behind. Exit code 0 is not
evidence of work done anywhere in this codebase — every write path the
co-pilot depends on must be verified by reading back the resulting rows.

### F-16 — Synthea generator/importer count mismatch, unreconciled
**Severity:** Medium · **Verified:** operator-reported; not reproduced (a
5-patient run this session showed no loss)
A reported 50-generated/48-imported Synthea run (two CCDA export exceptions)
has no reconciliation mechanism in the pipeline. Pass 2 additionally found
that neither database contains a matching 48-patient batch at all — the claim
is now unmatched to any observed state, a flag rather than a refutation. The
patient population cannot be assumed complete.

### F-17 — Shipped example patient data is demographics-only
**Severity:** Informational · **Verified:** first-hand
`sql/example_patient_data.sql` contains exactly 14 `INSERT INTO patient_data`
statements and nothing else — no encounters, problems, medications, or
results. Useless as a co-pilot fixture; test data must come from Synthea or be
hand-authored.

### F-18 — Demo dataset is 2014-dated and clinically empty
**Severity:** Medium · **Verified:** first-hand (measured immediately after
the reset; superseded by later Synthea seeding)
`dev-reset-install-demodata` imports a 5.0.0-era dump and migrates it forward,
yielding 3 patients, 3 same-day encounters, and zero lab orders/results — with
many post-5.0.0 columns left null. Never evaluate the co-pilot against demo
data alone; it will make retrieval and citation look exercised when neither
has been.

### F-19 — Lab results carry no reference ranges or abnormal flags
**Severity:** High · **Verified:** first-hand
Every result row across both environments (7,259 local, 29,701 droplet) has a
value, units, and a LOINC code, but the `range`/`abnormal` columns — which the
schema explicitly supports — are 100% blank. This is the single hardest
constraint on the co-pilot: it can cite a value but cannot characterize it as
normal, high, or low from the record alone.

### F-20 — Vitals recorded once per patient, never per encounter
**Severity:** Medium · **Verified:** first-hand
Exactly one vitals row per patient exists on both hosts (65/65 local, 53/53
droplet) against thousands of encounters. The co-pilot cannot show vitals
trends over time and must not imply a reading belongs to a specific visit.

### F-21 — Allergies are recorded for a minority of patients
**Severity:** Medium · **Verified:** first-hand
Only 14/65 (local) and 9/53 (droplet) patients have any allergy row at all.
"No allergies recorded" and "no known allergies" are different clinical
statements this data model cannot distinguish, and the co-pilot must never
collapse the former into the latter.

### F-22 — An outlier patient exceeds any reasonable context window
**Severity:** High · **Verified:** first-hand
The local worst case (530 encounters, 92 problems, 130 lab orders, 3,040
result rows) is ~18x the median and serializes to 974,140 raw bytes. The
droplet's own worst case is a smaller but still substantial 410 encounters.
Whole-chart prompting is not viable at any real patient volume the co-pilot
will encounter.

### F-23 — Encounter dates span more than a century
**Severity:** Low · **Verified:** first-hand
Encounters run 1922-2026 locally (1945-2026 on the droplet). *Correction:* the
original claim of future-dated records is withdrawn — both environments'
maximum dates precede the audit date. Date logic must still handle a
104-year span without two-digit-year ambiguity.

### F-42 — "Lab orders" figures overstate actual labs by ~12x
**Severity:** Medium · **Verified:** first-hand
Only 596/7,417 (local) and 1,177/11,250 (droplet) `procedure_order` rows are
`laboratory_test`; the rest are Synthea procedures (dialysis, screenings) that
never get a report or result. "Lab" retrieval must filter on
`procedure_order_type='laboratory_test'` explicitly, or it returns hundreds of
result-less rows per patient.

### F-43 — Local and droplet hold different, non-overlapping synthetic populations
**Severity:** Informational · **Verified:** first-hand
Separate Synthea seedings mean per-patient volume differs sharply (droplet:
fewer patients, ~4x the lab results each). Any evaluation case or performance
baseline must name the environment it was built against — a test referencing
a specific `pid` is meaningless on the other host.

---

## Compliance & Regulatory

### F-10 — Read/query audit logging ships disabled
**Severity:** High · **Verified:** first-hand
`audit_events_query` and `audit_events_cdr` are blank while eight other
categories (patient-record, order, lab-results, etc.) are enabled by default —
record *reads* generate no audit trail out of the box, the opposite of what a
chart-reading co-pilot needs. Confirmed identical on both hosts.

### F-11 — The audit log that exists is ~80% uninformative noise
**Severity:** High · **Verified:** first-hand
81.1% (initially) / 80.2% (after this session's own probe traffic) of all
`log` rows are generic `other-insert`/`other-delete`; genuine `view` events
are 10 rows total. Even the enabled write-side categories cannot answer "who
looked at what" — the co-pilot must emit its own structured access record
into a purpose-built table, not depend on this one.

### F-12 — Audit records are unencrypted at rest, with no external export
**Severity:** Medium · **Verified:** first-hand
`enable_auditlog_encryption` and `enable_atna_audit` are both off on both
hosts. Combined with F-34, the only copy of this plaintext audit trail sat in
a database that was itself internet-reachable with default credentials during
F-54's exposure window. The co-pilot must not log PHI-bearing prompt content
into this trail under current settings.

### F-13 — POSITIVE: break-glass logging is forced on
**Severity:** Positive · **Verified:** first-hand
`gbl_force_log_breakglass=1` on both hosts — emergency-access logging cannot
be silently suppressed and should not be disabled by the co-pilot.

### F-14 — BLOCKED: the audited-import comparison set could not be produced
**Severity:** Informational · **Verified:** first-hand
The only code path that would populate `audit_master`/`audit_details`
silently no-ops (F-15), so both tables read 0 on both hosts. This is recorded
so the empty tables are not later misread as proof that audited import
*fails* — it has simply never been successfully exercised.

### F-40 — Out-of-band database edits leave no trace in OpenEMR's audit log
**Severity:** Medium · **Verified:** first-hand (the logged INSERTs, current
row state, and absence of any logged UPDATE); not established (exactly how
the rows were changed — direct SQL, phpMyAdmin, or a `…NoLog` code path)
On both hosts, `oauth_clients` rows were enabled and granted extra scopes
after their INSERT, with zero corresponding `UPDATE` row in OpenEMR's own
log. Given F-34's and F-54's finding that the database and phpMyAdmin were
directly internet-reachable, this means changes made straight against the
database are invisible to the application-layer audit trail entirely — a
compliance story that depends on detecting tampering needs DB-level controls,
not this log.

### F-41 — POSITIVE (with caveats): the database stays on a HIPAA-eligible product
**Severity:** Positive · **Verified:** first-hand (topology);
documentation-sourced (DigitalOcean's HIPAA-eligible product list, which
includes Droplets and omits Managed Databases); operator-reported (that this
placement was deliberate)
MySQL runs as a container on the same Droplet as OpenEMR rather than on
DigitalOcean's Managed Databases product, keeping ePHI-bearing data on a
covered product. Caveats: an executed BAA with DigitalOcean was not confirmed,
and this correct placement was exposed to the internet anyway during F-54's
window — the right infrastructure choice does not substitute for the access
controls above.

---

*End of findings. This document and `docs/audit/findings.md` were assembled
during Stage 3 instrumentation of the OpenEMR-based Clinical Co-Pilot fork.*
