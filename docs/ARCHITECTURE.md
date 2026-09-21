# Stage 5 Architecture — Clinical Co-Pilot Agent Integration

**Source of record:** every capability below traces to one of the three use
cases in `docs/USERS.md`, and every specific number or finding cited traces
to `docs/AUDIT.md` or `docs/audit/findings.md` — both re-read in full this
session, not recalled from earlier in this conversation. One note on
staleness: `AUDIT.md` was finalized at F-01–F-54; `findings.md` has since
grown to F-55 (RxNorm coding coverage), later entries including F-61
(hosting provider and BAA status), and an amendment to F-15 (the
silent-import finding). Where those entries are cited below, the
citation is to `findings.md` directly, not `AUDIT.md`, and is marked as such.

---

## Summary

Eight decisions carry the most weight in this plan, in order of how much
they constrain everything else.

1. **The agent reads patient data only through OpenEMR's FHIR R4 API, under
   the requesting clinician's own OAuth2 token — never direct database
   access, never a broader-than-user service account.** This was tested, not
   assumed: a token scoped to `user/Patient.read` correctly got 401 on
   `MedicationRequest` and `Observation` reads (F-07). Authorization is never
   reimplemented agent-side; OpenEMR's own scope enforcement is the trust
   boundary, because an agent-side ACL could silently drift from the source
   system's actual permission model.
2. **Tool scope is exactly the five FHIR resource types USERS.md's three use
   cases name — `Condition`, `MedicationRequest`, `AllergyIntolerance`,
   `Encounter`, `Observation` — and nothing else.** No resource is added
   because it "seems useful"; each one traces to a specific use case's stated
   data requirement.
3. **Verification is a deterministic code check, not a second model call.**
   The reasoning model drafts claims bound to `source_id` + field + value;
   a separate step confirms the source_id was actually retrieved this turn
   and the value matches, before anything renders. Cheaper and more legible
   than a second opinion with its own uncertainty.
4. **Verification strength is stated per field, not assumed uniform.**
   Medication claims bind to `source_id` and a standard RxNorm code — 99.7%
   coverage, 311 of 312 prescriptions (F-55) — a strong, checkable
   attribution. Lab claims bind to `source_id` and a LOINC code but the
   dataset carries zero reference ranges or abnormal flags (F-19): the agent
   can verifiably cite a lab value, never verifiably characterize it.
5. **Multi-turn conversation state exists for exactly one of the three use
   cases, and is deployed.** Only Follow-Up Drilling needs it (USERS.md);
   Pre-Visit Brief and Change Detection are each single-turn by the use
   case's own definition. The brief and the constrained single-turn
   follow-up keep no conversation state at all: each stores nothing between
   requests, and for them the `(user, patient, session)` key is a
   per-request attribution label on log events. The multi-turn conversation
   stores only the claims earlier turns verified, never question or answer
   text; keys them to user + session, bound to one patient; and deletes them
   the moment a turn names a different patient, as the mitigation against
   cross-patient leakage within the agent's own state. Every turn re-reads
   the chart and verifies every claim against that turn's records, exactly
   as the brief does. It does not fix OpenEMR's own per-patient
   authorization gap (F-50; §4).
6. **MySQL stays self-hosted on the application host, not on a managed
   database product.** The rule was set on DigitalOcean, whose BAA covers
   Droplets, Kubernetes, Spaces, and Volumes but explicitly excludes
   Managed Databases (F-41); ePHI-bearing data must stay on a covered
   product. This is a compliance constraint on infrastructure, not a
   convenience choice. **The deployment has since moved to Vultr, and no
   BAA covers the Vultr host** (§1; `findings.md` F-61). That is acceptable
   for this exercise only because every record is synthetic.
7. **The Docker/UFW port-exposure incident (F-54) sets the verification
   standard for every future service this project adds.** Docker's own
   NAT rules run ahead of UFW's filter chain, so a restrictive host firewall
   gave false confidence while two services stayed genuinely public. The
   fix was a loopback bind at the compose-file level, not an override file
   (overrides merge additively, they don't replace). The generalizable
   lesson — check actual bound listen addresses, never firewall rule state
   — applies directly to the agent process and any observability backend
   added later.
8. **The verification layer's core justification is architectural, not
   hypothetical.** This codebase's own demo-import tooling produces
   divergent outcomes on the identical command — 0 of 2 patients imported in
   one run, 4 of 5 in another, success messages printed regardless (F-15,
   amended). "Printed success" and "actual success" diverge in this
   codebase's own tools; the agent's verification layer exists precisely to
   stop that class of failure from reaching a physician-facing claim.

---

## 1. Deployment Topology

A single cloud host running Docker Compose, hosting OpenEMR and its
MySQL/MariaDB database as containers on the same host — mirroring the
audited deployment (`docker/development-easy`, F-48) — and, as a separate
Compose project on that same host, the agent (below). The host was a
DigitalOcean droplet for the audit and most of the build; it is now a
Vultr instance at `64.177.42.210` (see "Hosting provider and BAA status"
below).

**Why self-hosted MySQL, not a managed database:** this is a compliance
constraint, not a convenience one, decided on DigitalOcean. DigitalOcean's
published HIPAA-eligible product list covers Droplets, Kubernetes, Spaces,
and Volumes, and explicitly does not cover Managed Databases (F-41). Any
ePHI-bearing data — the OpenEMR database included — has to stay inside a
covered product, which rules out DO's managed MySQL offering regardless of
its operational convenience. The agent introduces no new datastore that
would need this same decision made twice: it reads through the FHIR API and
writes nothing to the EHR's own database (see §2).

**Hosting provider and BAA status — a documented tradeoff.** Late in the
build the deployment moved from the DigitalOcean droplet to Vultr (The
Constant Company, LLC), because of a payment constraint. The compliance
reasoning above, and F-41 in the audit, was written for DigitalOcean and
does not carry over. (Even there, F-41 records that an executed BAA was
never confirmed; the placement was on eligible products.) **No BAA covers
the Vultr host:** none has been
executed with Vultr, and this project has not established that Vultr would
sign one for this account (`findings.md` F-61). So neither OpenEMR's
database nor the agent currently runs on BAA-covered infrastructure. This
is acceptable for this exercise only because every record is synthetic
(OpenEMR demo data and Synthea-generated patients, per the case study's
demo-data constraint), so no real PHI is exposed. A production deployment
needs a substrate under an executed BAA first. Examples are DigitalOcean's
HIPAA-eligible products under a signed BAA (F-41), or another provider's
covered products under a signed agreement. The placement rules in this
section (database, agent, observability backend) then have to be
re-applied to that provider's covered-product list.

**Where the agent process lives — decided and deployed.** On the same
host as OpenEMR (`64.177.42.210`). On DigitalOcean this kept agent compute
on the covered product (F-41). On Vultr no BAA covers the host (above), but
co-location still avoids adding a second host that holds chart data. Placing it
on another host would either require its own BAA-covered placement
decision or risk PHI leaving the application host through request/response
logging. Its shape (`agent/deploy/`, operated per
`agent/docs/deployment.md`):

- **A separate Compose project** (`clinical-copilot-agent`), not a service
  inside OpenEMR's `development-easy` stack, whose compose file is
  unchanged.
- **Its own Docker network, deliberately isolated from OpenEMR's.** A
  container on OpenEMR's `development-easy_default` network gets a direct
  TCP connection to `mysql:3306`, and MySQL still accepts the default
  `root`/`root` credentials (F-34). The agent instead reaches OpenEMR's FHIR
  API the way any other client does: through its published HTTPS port, via
  the Docker host gateway (`https://host.docker.internal:9300`). From the
  agent's network, MySQL is unreachable both by name and through the gateway
  (tested live, 2026-09-16). §2's "no direct database connection anywhere in
  the agent's data path" is therefore a property of the network, not only of
  the code.
- **One public port: `0.0.0.0:8443`** (IPv4), with TLS terminated by a Caddy
  proxy in the same project. The agent's own uvicorn port 8000 is not
  published. Every request that reads chart data needs a clinician token
  OpenEMR accepts.
- **One credential: `ANTHROPIC_API_KEY`.** This corrects an earlier
  assumption that the agent would hold FHIR credentials. It holds none: the
  clinician's own token carries FHIR access on each request (§2). The key is
  stored at `/etc/clinical-copilot/agent.env` (mode 600, root only) and is
  never in the image or the repository. It is also kept out of the OpenEMR
  checkout, which is bind-mounted read-write into Apache's document root
  (F-48), so a file there could be web-served.

The bindings were verified by the rule in the case study below, after
deployment on 2026-09-16, on the DigitalOcean droplet before the move to
Vultr. On the droplet, `ss -tlnp` showed exactly one new
listener, `0.0.0.0:8443`, with MySQL and phpMyAdmin still on
`127.0.0.1:18320` and `127.0.0.1:18310`. From outside the droplet, `nc`
found 22, 8300, 9300 and 8443 open, and 8000, 18310, 18320, 3306 and 2019
closed. No equivalent `ss` and external `nc` record for the Vultr host is
in this repository. The module's Vultr deployment notes list the same
public ports (22, 8300, 9300, 8443) and loopback-only ports (18310, 18320)
but do not record how they were checked
(`oe-module-clinical-copilot/docs/deployment.md` §1).

**Stated limitations of this deployment:**

- **The certificate is not publicly trusted.** It comes from Caddy's local
  CA, because the host has no domain name (cf. F-36, R-6). Clients must
  trust the exported root or skip verification, and a client that skips it
  is not protected against interception.
- **No rate limiting.** Anyone holding a valid clinician token can call the
  endpoint repeatedly, and each call is an LLM request.
- **External reachability was checked from a single vantage point:** the
  operator's egress, `192.63.199.50`, which F-54 itself notes is not an
  independent one. IPv6 was checked only on the host (`ss` shows no IPv6
  listener on 8443); an external IPv6 probe was inconclusive.
- **The agent → OpenEMR hop skips certificate verification.** OpenEMR's
  certificate is self-signed (F-36), so the FHIR client's documented,
  temporary exception applies. The hop stays on the host.

**Where observability lives — decided for this submission.** Production
metrics come from one place: `agent/scripts/compute_metrics.py`, a local
script that reads the agent's own structured stdout log directly and
computes the `docs/KEY_METRICS.md` metrics, with no dashboard, no
observability backend, no second host, and no deployment dependency. This
is a deliberate choice, not a fallback: a general observability platform
needs dashboards built before it yields six specific numbers; a small
script goes directly at those six numbers with nothing in between. One
caveat: Metric 3 has no events to compute from, because nothing in the
pipeline emits its authorization-check event (§2), so the script reports
it as insufficient data.

**Langfuse was verified locally only.** Self-hosted Langfuse
(`agent/observability/docker-compose.langfuse.yml`) and the agent's
exporter are built, and were verified working on the developer's laptop
with synthetic test events. That includes the PHI-safe configuration that
keeps auto-instrumentation from exporting prompt or completion content.
Langfuse has never run against the deployed agent. The deployed compose
file (`agent/deploy/docker-compose.agent.yml`) passes no `LANGFUSE_*`
variables to the agent container, so export is off in production and
stdout is the only record. `agent/docs/observability.md` §9 holds a
command-level checklist for standing Langfuse up on a second DigitalOcean
droplet reached only over the private VPC network: a BAA-covered product
(F-41), never Langfuse Cloud. That checklist predates the move to Vultr and
assumes the application host is a DigitalOcean droplet in the same VPC. It
has to be redone for whichever BAA-covered provider a production deployment
uses. It is **not deployed**: the second host is an explicit v2,
post-submission step.

**Where the recorded numbers were measured.** The eval results are from the
Vultr host: `agent/evals/RESULTS.md` (A–H: 31 PASS, 0 FAIL) and
`agent/evals/RESULTS_CONVERSATION.md` (I: 12 PASS, 0 FAIL) were recorded
there on 2026-09-21, after the fixes for F-58, F-59 and F-60. They replace
the 2026-09-20 run on the same host (29 PASS, 2 FAIL — the two being F-58
and F-59 before they were fixed) and the 2026-09-16 run on the DigitalOcean
droplet (23 PASS, 4 FAIL); `agent/evals/README.md` keeps what each found.
**The fixes are committed and not deployed**, so the in-process cases
exercise them while the live-endpoint cases still run against the deployed
build — `agent/evals/README.md` says which cases are which. The measured conversation costs in `docs/COST_ANALYSIS.md`
§1.4 are from the same host and day. So is the load test:
`agent/load-test/RESULTS.md` (the 10 and 50 virtual-user runs, the real
end-to-end batch, and the CPU and memory baseline) was re-run on the Vultr
host on 2026-09-20, replacing the 2026-09-16 droplet run, which that file
keeps alongside it for comparison. Its finding is unchanged by the move:
the agent stays under 20% of one core and 110 MiB at both concurrency
levels, while OpenEMR's Apache/PHP tier saturates — on this host at a lower
concurrency than on the droplet, and against a tighter memory ceiling
(7.24 GiB against 11.7 GiB; F-44 described the droplet's hardware). Other
statements in this document that quote the 2026-09-16 runs say so where
they do. The functional checks on Vultr from 2026-09-17 are the chart embed
(module `docs/deployment.md` §2.1) and the Postman collection
(`agent/api-collection/README.md`).

### Case study: the Docker/UFW port-exposure incident (F-54)

This incident is included as a first-class architectural input, not a
security footnote, because it is a lesson about *this specific deployment
topology* — Docker Compose behind a host firewall — that will recur for
every service subsequently added to it, including the agent's own.

**What happened.** Docker publishes container ports through its own `nat`
table: `PREROUTING` DNATs traffic to the container before the packet ever
reaches an INPUT-chain rule UFW controls, and the `FORWARD` chain routes
through Docker's own chains *ahead of* any `ufw-*` chain. The `DOCKER-USER`
hook where an operator could intercept this held no rules. UFW's
default-deny, 22/80/443-only policy therefore never evaluated Docker-published
traffic at all. As a direct result, MariaDB (default `root`/`root`
credentials) and phpMyAdmin sat bound to `0.0.0.0`/`[::]` and reachable from
the public internet for a bounded window of at least 3 h 48 min and at most
18 h 09 min — a restrictive-looking firewall gave false confidence while
both services were genuinely public.

**What did not fix it.** A `docker-compose.override.yml` adding
loopback-only alternate bindings did not close the exposure: Compose merges
`ports` lists additively rather than replacing them, so both the original
public binding and the new loopback one were bound simultaneously — this
produced dual bindings and repeated container-start failures, not a fix.

**What did fix it.** Editing the base compose file directly — binding
MariaDB and phpMyAdmin to `127.0.0.1` explicitly at the base-file level
(commit `b0a0bcc`) — replaced the all-interfaces default rather than
layering an addition on top of it.

**How the fix was verified.** Not by re-reading `ufw status` — by checking
actual bound listen addresses directly: `ss -tlnp` on the host, cross-checked
against `docker compose ps` and a resolved `docker compose config`, then
confirmed from an external network vantage point.

**The generalizable rule this architecture adopts:** *any* new service this
project's Compose stack ever gains — the agent process, an observability
backend, a future cache — must have its actual bound listen address checked
the same way, from a genuinely external vantage point, before it is declared
internal-only. Firewall rule state is never sufficient evidence on its own
on a Docker host.

**Known limitation, stated plainly:** the droplet, as audited, ran the
development Compose stack rather than the hardened `docker/release`
build (F-48) — Xdebug loaded into the live web process, a writable
bind-mount of the git checkout into the web container, and a full
composer/npm/webpack build executed on every container boot against
upstream registries. The Vultr host shows the same layout: a bind-mounted
checkout and the development stack's published and loopback ports (module
`docs/deployment.md` §1). F-48's individual checks (Xdebug, the boot-time
build) were not repeated there. This is not a limitation this architecture
plan resolves by describing it; it is a stated **precondition** that must be
closed — move to the hardened, immutable build with debugging tooling
removed — before this deployment handles anything beyond synthetic data.

## 2. Authorization Model

The agent has no identity of its own with respect to patient data. Every
FHIR read it performs runs under the OAuth2 token of the clinician who is
using it, scoped to whatever that clinician's own role already permits in
OpenEMR. There is no service account, no elevated credential, and no
direct database connection anywhere in the agent's data path.

**Why delegate rather than reimplement.** An agent-side authorization layer
would have to be kept in sync with OpenEMR's own permission model by hand,
indefinitely, across every OpenEMR upgrade — a drift risk with no
enforcement mechanism behind it. Delegating to the system of record's own
scope enforcement means there is exactly one place authorization logic
lives, and it is the place already responsible for it. This was validated,
not assumed: a token scoped only to `openid api:fhir user/Patient.read` was
used to attempt `MedicationRequest` and `Observation` reads, and both were
correctly refused (F-07). Scope enforcement is a real boundary this
architecture can build on.

**Known limitation — 401/403 ambiguity (F-08).** OpenEMR returns HTTP 401
identically for "this token doesn't hold the required scope" and "this
token is invalid or expired," never the RFC 6750-correct 403 for
insufficient scope. This is OpenEMR's limitation, not a design choice this
architecture endorses — but the agent has to work around it regardless: it
cannot infer "prompt the clinician to re-authenticate" versus "this resource
is permanently out of reach for this token" from the HTTP status alone.

*What the agent does today.* It forwards the clinician's token to FHIR
unchanged and lets OpenEMR decide. A 401 from OpenEMR (or, defensively, a
403) becomes a `FhirAuthError`, and the endpoint returns
`401 fhir_token_rejected`. That message names all three possible causes
(expired, revoked, out of scope) and does not claim to know which one
applies (`agent/app/fhir/client.py`, `agent/app/api/pre_visit_brief.py`).
The agent never retries a rejected request, and no model call is made
after a rejection (eval cases D2 and D3). The only thing the agent reads
from the token is its `sub` claim, without checking the signature, as the
user part of the attribution key (§4). The token's scopes are never read.

*Planned, not built: an agent-side scope record and pre-check.* The design
is for the agent to keep its own record of which scopes the current token
holds. The record would be decoded from the JWT's own `scopes` claim, never
from the token response's `scope` field, which has been observed to
disagree with what the JWT actually contains (F-09). The agent would check
that record *before* issuing a request, rather than reacting to a bare 401
after the fact, and so tell a scope gap from an expired token. Only the
event schema for it exists (`AuthorizationCheckEvent` in
`agent/app/models/metrics_events.py`). Nothing decodes the scopes, nothing
pre-checks, and nothing emits the event. Until that is built, the agent
cannot tell the two causes apart, and `docs/KEY_METRICS.md` Metric 3 has no
agent-side data. Eval case D1 measures OpenEMR's side of that boundary
directly instead.

**Known limitation — no per-patient scoping in the platform (F-50).**
OpenEMR's ACL and OAuth-scope layers both gate by section/action
(`patients`/`demo`, `user/Patient.read`), never by which specific patient.
Delegating authorization to OpenEMR therefore means the agent inherits
whatever patient population the clinician's role can see in general — not a
care-team-specific boundary — because no such boundary exists in the
platform to delegate to. If a stricter per-patient restriction is ever
required, the only mechanism the platform actually offers is requesting a
patient-context token scoped to one patient per use, and even that is
bounded by what OpenEMR itself supports; this architecture does not
introduce an agent-side per-patient ACL to compensate, for the same
drift-risk reason it does not reimplement authorization generally.

**In the chart: how the clinician's token is obtained — decided and
deployed.** The Pre-Visit Brief card on the patient dashboard
(`interface/modules/custom_modules/oe-module-clinical-copilot`) never asks
the physician to log in again and never uses a service account. It
exchanges the physician's live OpenEMR session for a token through
OpenEMR's own SMART EHR launch. A dedicated confidential client, flagged
*Skip EHR Launch Authorization Flow*, receives an authorization code for
the session's user. The module redeems that code with its client secret
and PKCE.

The module performs the launch server side, over loopback. The browser
variant posts to `site_addr_oath`, which the audit found set to
`localhost` on the DigitalOcean droplet (F-49). Server side, the token
also never reaches the browser, and the browser never calls the agent
(which has no CORS and a privately-issued certificate). The token's `sub`
is the physician; its scopes are the client's registration, `openid` plus
the four reads; one is minted per brief. Verified live on the
DigitalOcean droplet on 2026-09-16, in headless Chrome: JWT `sub` = the
logged-in user's UUID, a FHIR read succeeded with it, and the agent
returned a brief. Re-verified on the Vultr host on 2026-09-17, from the
command line only. There, each brief
wrote one `api_token` row for the physician with exactly those scopes, the
agent's log keys started with the physician's UUID, and the brief was
returned. The card's in-browser rendering was not re-checked on Vultr.

Details, limits and operator checks:
`oe-module-clinical-copilot/README.md` and its `docs/deployment.md`.

## 3. Tool Scope

Tool scope is a direct, cited mapping from `docs/USERS.md`'s three use
cases — not a fresh judgment about what resources "seem needed." Five FHIR
resource types cover all three use cases combined, and no resource outside
this set is in scope for v1.

**What is built versus planned.** The table is the design scope. The brief
and the constrained single-turn follow-up read only `Patient`, `Condition`,
`MedicationRequest` and `AllergyIntolerance`. The multi-turn conversation
(`agent/app/graph/conversation.py`; **deployed**) also reads `Observation` and
`Encounter` through the readers in `agent/app/fhir/client.py`, when a rule
on the question's wording or on the conversation's verified context calls
for them (§4). Change Detection is not built.

| Use case (USERS.md §3) | FHIR resources required | Notes |
|---|---|---|
| **1 — Pre-Visit Brief** (built) | `Condition`, `MedicationRequest`, `AllergyIntolerance` | Current-state snapshot only. The planned "what changed since last visit" line would be *consumed* from Use Case 2's output, not independently computed, so the brief does not need `Encounter` directly. **That line is not built**, because Use Case 2 is not. The brief today has no change line. |
| **2 — Change Detection** (**not built**; planned) | `Encounter` (two — current visit and most recent prior), `Condition`, `MedicationRequest`, `Observation` | `Observation` here is scoped to new lab results in the interval only; vitals are excluded because they are recorded once per patient, never per encounter (F-20), so there is no second timepoint to diff. |
| **3 — Follow-Up Drilling** (deployed in both forms: constrained single-turn, and multi-turn) | `Condition`, `MedicationRequest`, `Encounter`, or `Observation` — whichever grounds the specific fact being drilled into | As designed: no fixed resource set; this use case re-queries whatever resource type the immediately preceding turn already surfaced, determined at conversation time, not by a fixed tool definition. As built, in the single-turn form (`agent/app/graph/follow_up.py`): one question per request, answered only from the brief's own three resource types, re-read under the caller's token, with no memory of earlier turns. As built, in the multi-turn form (`agent/app/graph/conversation.py`): a conversation. Which types a turn reads is decided by rule, never by the model: the brief's three on every turn; `Observation` when the question has lab wording; `Encounter` when it has visit wording ("last visit", "since", "last time"); and a type an earlier turn verified claims on is carried forward, which is the rule-driven form of "re-queries whatever the preceding turn surfaced". Questions about anything outside the five types are refused (§4, §5). |

**One implicit read not named by any use case:** `Patient`, to resolve
which chart is open. This is not a fourth tool-scope decision — every use
case in USERS.md begins from an already-open chart (USERS.md §1–2) — so
resolving the current patient is a precondition of the workflow itself, not
a separate agent capability requiring its own justification the way the
five clinical-data resources above do.

**What is explicitly not in tool scope, and why:** no resource supports
multi-patient panel or scheduling views (no use case requires seeing more
than one chart at a time, and the appointments table this would depend on
holds 11 static-looking rows identical across two non-overlapping patient
populations — USERS.md §1, F-43); no resource or logic characterizes a lab
result as normal or abnormal (zero reference ranges or abnormal flags exist
in the data at all — F-19); and any capability not traceable to one of the
three use cases above is out of scope regardless of how reasonable it seems
in isolation (USERS.md §4). A prescribing-time drug-interaction check was
explicitly considered and excluded at the use-case level (USERS.md §4) — it
is a different class of behavior (active decision support, not surfacing or
diffing documented facts) and is not reintroduced here under a different
name.

## 4. Multi-Turn Conversation State

Conversation memory exists for exactly one of the three use cases —
Follow-Up Drilling — because it is the only one whose defining behavior
(resolving "that," "last time" against something the immediately preceding
turn surfaced) is impossible without it. Pre-Visit Brief and Change
Detection are each fully answerable from a single exchange grounded in
current chart state (USERS.md §3); giving them conversation state they do
not need would be scope the use cases themselves do not justify.

**The endpoints that keep no conversation state.** The brief is
single-turn, and so is the constrained follow-up
(`POST /pre-visit-brief/follow-up`, `agent/app/graph/follow_up.py`): each
question stands alone, the request cannot carry earlier answers, records or
claims, and every request re-reads the chart under the caller's token. For
those two endpoints `patient_session_key`
(`<token sub>:<patient_id>:<session_id>`) is a per-request attribution label
on every logged event (`docs/KEY_METRICS.md`), not a key into any store.
Eval cases G1 and G2 check that those single-turn requests stay isolated.

**The endpoint that does keep state: multi-turn conversation**
(`POST /pre-visit-brief/conversation`, `agent/app/graph/conversation.py`,
`agent/app/conversation/state.py`; deployed).

- *What is stored.* Per conversation, the claims that **passed**
  verification in earlier turns (resource type, source id, field, value),
  grouped by turn. Not the question, not the answer text, not a failed
  claim, not a retrieved record. A later turn's model call is given those
  claims as structured context for resolving "that medication"; it is never
  given prose from an earlier turn. The context is not a source: every
  claim a turn makes cites a record retrieved *that turn* and is verified
  against it (§5), so a stale context value can at worst surface as an
  UNVERIFIED line.
- *How it is keyed.* Looked up by `(user, session)` and bound to exactly one
  patient for its whole life; it is reachable only when all three match.
  `user` is the token's `sub`, read without checking the signature, so no
  state is read, created or discarded until OpenEMR has accepted the same
  token for a read of the named patient that turn.
- *When it is invalidated.* The moment a turn arrives on the session for a
  different patient: the stored conversation is deleted, all of it, before
  routing, retrieval or drafting run, and an empty one starts. It is never
  rebound or merged, and returning to the first patient starts over too. A
  turn that was in flight when its conversation was invalidated cannot write
  into the one that replaced it. It also ends at an idle TTL (30 minutes),
  at a maximum age (one hour, the life of an OpenEMR access token), on
  `POST /pre-visit-brief/conversation/end`, and when the request names a
  patient OpenEMR does not have.
- *Where it lives.* In process memory. That is correct for the one uvicorn
  worker the agent's Dockerfile runs and wrong for more than one; the
  production answer is Redis (or equivalent) with the same key, the same
  patient binding checked on every read, and server-side TTLs.

This is a deliberate mitigation, not an afterthought: a multi-turn system
that keeps state across a patient switch is exactly the shape of system that
could answer a follow-up question about patient B using context actually
surfaced while patient A's chart was open. Invalidation has its own tests,
which G1 and G2 never were: `agent/tests/test_conversation.py` (fakes) and
eval case I1 (live FHIR, real model; `agent/evals/test_i_conversation.py`),
which switches patient mid-conversation on one token and session and checks
that none of patient A's record ids or names reaches patient B's model input
or answer, that the state was discarded, and that going back does not bring
it back.

**Known limitation of that design:** the scoping prevents cross-patient
leakage *within* the agent's own state; it does not, and cannot, fix
OpenEMR's own lack of per-patient authorization scoping (F-50, §2): the same
token still reads any chart. The mitigation addresses the agent's own added
surface, not the platform's existing one. It also learns of a patient switch
only when a conversation turn names the new patient — opening another chart
in OpenEMR, or running a brief for another patient, does not by itself end
a conversation; its TTL does.

## 5. Verification System

The reasoning model never renders prose claims directly. It drafts
structured claims, each bound to `source_id` + field + value. A
deterministic, code-based verification step — not a second model call —
then confirms two things for every claim before it renders: that the
`source_id` exists among what was actually retrieved this turn, and that
the claimed value matches what that source actually holds. Only claims
that pass render to the physician; a claim that fails is stated as
explicitly unverified — never silently dropped, never softened into vaguer
language that hides the failure.

**Domain-constraint rules are planned, not built.** The design adds
domain constraints (dosage thresholds, interaction flags, where applicable)
as a separate, additive rule set on top of source verification, not folded
into it. No such rules exist in the code. The verifier
(`agent/app/verification/checker.py`) checks three things only, and a claim
fails for one of three reasons: the source was not retrieved, the field is
not one the record has, or the value does not match. What keeps dose and
interaction content out of briefs today is the drafting prompt, which tells
the model to add no doses, interactions or contraindications
(`agent/app/reasoning/draft_claims.py`). A prompt instruction is not a
check: nothing verifies that the model followed it beyond the value
matching its source record. Interaction flags would also have to be squared
with USERS.md §4, which excludes prescribing-time interaction checking (§3).

**Why a deterministic check instead of a second model verifying the
first.** It is cheaper and faster — no second inference call per claim —
and its failure mode is legible: it names exactly which claim failed and
why (source not retrieved, or value mismatch), rather than producing a
second opinion that carries its own, separate uncertainty about whether
*it* is right.

**Verification strength is not uniform across fields, and this plan states
that explicitly rather than treating all clinical data the same way:**

- **Medications** bind to `source_id` *and* a standard RxNorm code. Coverage
  is 99.7% — 311 of 312 prescriptions carry a non-empty `rxnorm_drugcode`
  (`findings.md` F-55, added after `AUDIT.md` was finalized — this figure is
  not in `AUDIT.md`). This is a strong, machine-checkable attribution
  surface: a medication claim can be validated against a standard code, not
  just matched against free text.
- **Lab results** bind to `source_id` and a LOINC code (the `result_code`
  column exists and is populated) but the dataset carries zero reference
  ranges and zero abnormal flags across every result row in either
  environment (F-19). The agent can verifiably cite a lab value, unit, and
  date; it cannot verifiably characterize that value as normal, high, or
  low, because that judgment is not present in the source data at all. This
  is a **data gap, not a schema gap** — the schema has columns for both
  `range` and `abnormal` — and the two are not the same failure, and are not
  treated as the same failure here.

**Why this design matters architecturally, not just as a safety feature.**
This codebase's own demo-import tooling demonstrates the exact failure mode
verification exists to catch: the identical command
(`import-random-patients N false`) produced 0 of 2 successful patient
imports in one run and 4 of 5 in another, printing success-shaped output
regardless of the actual outcome (`findings.md` F-15, as amended — the
amendment is not in `AUDIT.md`, which predates it). This is not the agent's
own failure mode, but it is direct, concrete evidence that "printed
success" and "actual success" diverge in this codebase's own tools. The
verification layer's core design principle — check the actual retrieved
data, never trust an upstream success signal, including OpenEMR's own — is
justified by an observed pattern in this specific system, not a
hypothetical one.

**What verification catches:** fabricated claims (a value the model stated
that no retrieved record actually contains), and cross-patient
contamination in a claim's source (a claim whose cited `source_id` belongs
to a different patient than the one in context), provided this turn's
retrieval was itself correctly scoped to the current patient — see
"Source-to-patient attribution" below for the case where it wasn't.

**What verification does not catch — stated as known limitations, not
silently assumed covered:**

- **Retrieval completeness.** Verification confirms that sourced claims are
  real; it does not confirm that the *right* records were fetched in the
  first place. A relevant `AllergyIntolerance` row that was never retrieved
  this turn makes every claim in the response pass verification cleanly —
  the gap is invisible to this layer entirely.
- **Source data accuracy.** Verification confirms fidelity to the chart; it
  does not and cannot confirm that the chart itself is clinically correct.
- **Multi-record synthesis claims.** A claim like "trending down" across
  several `Observation` rows is not reducible to a single-claim,
  single-source verification check. This is deferred, not silently assumed
  to be covered by the same mechanism that checks single-value claims. The
  multi-turn conversation scopes around it rather than attempting it:
  questions asking for a trend, a comparison, a change between visits or a
  clinical judgment are refused by rule before any retrieval or model call
  (`agent/app/conversation/routing.py`); a question the wording rules miss
  reaches a model whose output schema has no free-text field, so the most
  it can do is cite single recorded values and report the `interpretation`
  gap, which renders as fixed wording (eval cases I5, I6). The one relation
  across records the conversation does state, "most recent of N", is
  computed in code from everything retrieved, not taken from the model.
- **Lab interpretation.** As stated above, zero reference ranges exist in
  the current dataset (F-19). v1 reports value, unit, and date only, and
  never characterizes a result as normal or abnormal. A separately-cited,
  LOINC-keyed reference-range source is a scoped v2 addition — explicitly
  not attempted here — not an oversight in v1's design.
- **Source-to-patient attribution.** Verification checks claim-to-source
  attribution, not source-to-patient attribution. It confirms a claim's
  `source_id` is among what was retrieved this turn; it does not
  independently confirm that the retrieved records belong to the patient
  in the current conversation — the resource models it compares against
  carry no patient field at all. What keeps sources to one patient is
  retrieval: every request, and every conversation turn, runs
  patient-scoped FHIR searches (`?patient=<patient_id>`) under the caller's
  token, and no retrieved record is carried between requests. The
  conversation carries verified claims between turns, but only as context
  bound to one patient (§4), and never as something a claim can verify
  against. Verification does not re-check that. If
  retrieval scoping has a bug, a claim sourced from the wrong patient's
  record passes verification cleanly. This is an open gap, not a resolved
  one.
- **Empty or all-inactive query results.** Whether a queried resource type
  returned zero results (e.g. "allergies: not recorded,"
  USERS.md §3 Use Case 1; F-21) or returned records but none currently
  active (e.g. "medications: none active") is not a claim-verification
  concern: the rendering step computes both directly from the retrieval
  results, with no LLM involvement, because each is a deterministically
  knowable fact, not something that benefits from the draft-then-verify
  pattern used for everything else.

  **Assumption this depends on:** the zero-results rendering logic assumes
  every relevant resource type is queried on every Pre-Visit Brief
  invocation. That holds today — the Pre-Visit Brief graph
  (`agent/app/graph/pre_visit_brief.py`) queries all three of its required
  types on every run, with no partial or selective retrieval path, and
  `draft_claims.py` refuses input missing any of the three. The constrained
  follow-up (`agent/app/graph/follow_up.py`) does the same full retrieval.
  The multi-turn conversation does retrieve selectively — `Observation` and
  `Encounter` only when a rule calls for them — and keeps the distinction
  explicit: every answer's closing line says "lab results not retrieved" or
  "visit dates not retrieved" for a type that was not queried, and "no lab
  results … on file" for one that was queried and came back empty; the three
  brief types are still queried on every turn. If selective retrieval is
  introduced anywhere else (e.g. Change Detection), the same rule applies:
  a resource type that was never queried must not be silently
  rendered identically to one that was queried and returned zero results.
  It also assumes the patient exists. OpenEMR's FHIR search answers an
  unknown or malformed patient id with HTTP 200 and an empty Bundle, exactly
  as for a real patient with nothing recorded (confirmed live 2026-09-16), so
  the retrieve step reads the Patient itself first and stops with "patient
  not found" on a 404 or 400 instead of rendering "not recorded".

## 6. Risks and Mitigations

| Risk | Mitigated? | How |
|---|---|---|
| Fabricated or hallucinated clinical claims reach the physician | Yes | Source-verification layer (§5) — every claim bound to a real, this-turn `source_id`; unverified claims render as explicitly unverified, never dropped or softened. |
| Cross-patient context leakage in a multi-turn exchange | Mitigated within the agent's own state, in the deployed multi-turn conversation. Not applicable to the brief or the single-turn follow-up, which keep no state | The stateless endpoints: every request, brief or single-turn follow-up, re-reads the chart for its own `patient_id` and stores nothing; eval cases G1 and G2 check that those single-turn requests stay isolated (both passed in the 2026-09-20 run against the Vultr host). Multi-turn conversation (§4): state holds verified claims only, is reachable only on a `(user, patient, session)` match, and is deleted in full before anything else runs when a turn names a different patient; every turn retrieves patient-scoped records afresh and verifies against those alone, so another patient's claim cannot verify even if asserted. Tested by `agent/tests/test_conversation.py` and eval case I1 (live FHIR, real model; passed 2026-09-20 in-process against the Vultr host's FHIR API). It does not fix OpenEMR's own per-patient authorization gap (F-50; §4), and a conversation ends on a switch only when a turn names the new patient, or at its TTL. |
| A future service (agent process, observability backend) repeats the Docker/UFW exposure pattern | Yes, by process | Binding verification via `ss`/`docker compose ps`/resolved `docker compose config` from an external vantage point is now a standing requirement for any new service in this Compose stack (§1). |
| Trusting a misleading "success" signal from upstream tooling (OpenEMR's own or otherwise) | Yes, by design principle | Verification checks actual retrieved data, never an upstream success claim — directly motivated by F-15's observed intermittent silent-failure pattern (§5). |
| The chart integration mints clinician tokens outside OpenEMR's authorization decisions | Yes | Tokens come only from OpenEMR's own authorize and token endpoints, via its EHR-launch skip flow, for a live session's user. Client enablement, the redirect URI, launch-token decryption and scopes (capped by the client registration) stay OpenEMR's decisions. The session cookie and client secret are only ever sent to a loopback address, enforced in configuration. The token never reaches the browser. Each brief writes a `patient-record` audit event (§2; module README). Not closed: one token plus one LLM call per chart open, with no caching or rate limiting; tokens are not revoked after use (1 h expiry). |
| Agent misreads a 401 as a transient/retryable auth failure | Partly: the agent does not retry, but it cannot tell the causes apart | The agent never retries a rejected FHIR request. It returns `401 fhir_token_rejected` naming expired, revoked or out of scope as possible causes, without claiming which (F-08; §2). The planned mitigation is not built: an agent-side decoded-JWT scope record, pre-checked before each request, that would tell a scope gap from an expiry. Only its event schema exists (§2). |
| Real PHI is deployed onto the currently-audited stack as-is | **No — open** | The audited droplet ran the development Compose stack, not the hardened `docker/release` build (F-48): Xdebug loaded live, a writable git-checkout bind-mount, dependency builds against upstream registries on every boot. The Vultr host shows the same layout (bind-mounted checkout, the development stack's ports), though F-48's individual checks were not repeated there (§1). Stated as an explicit precondition to close, not something this plan resolves by naming it. |
| Retrieval incompleteness (the right record never fetched) silently passes as "verified" | **No — open** | Outside what source-verification can catch by construction (§5). Requires separate retrieval-completeness evaluation this plan does not yet define. |
| Observability backend creates new ePHI-handling surface outside covered infrastructure | Yes, for this submission | No observability backend is deployed. Production metrics are computed by `agent/scripts/compute_metrics.py` from the agent's own stdout log, with no second service or host (§1). Metric 3 has no events to compute from until the §2 scope record is built. Self-hosted Langfuse is built and was verified locally only. It has never run against the deployed agent, whose compose file passes it no `LANGFUSE_*` settings. It is deferred to v2: a second DigitalOcean droplet (a BAA-covered product, F-41) on the private VPC network, per the checklist in `agent/docs/observability.md` §9, written before the move to Vultr (§1). |
| Agent compute placement creates new ePHI-handling surface outside covered infrastructure | Partly — co-located and deployed, but the host itself is not BAA-covered (next row) | The agent runs on the same host as OpenEMR (on DigitalOcean, a covered droplet, F-41; since the move to Vultr, a host no BAA covers, F-61) as its own Compose project on its own Docker network. From there it has no route to MySQL, which still accepts default `root`/`root` (F-34), and reaches OpenEMR only through the published FHIR port. One public port, `0.0.0.0:8443`, behind TLS; uvicorn's port 8000 is not published. Bindings were verified with `ss` on the host and external `nc` after deployment on the DigitalOcean droplet (§1). Its only credential is `ANTHROPIC_API_KEY`, in `/etc/clinical-copilot/agent.env` (mode 600) and never in the image or repository. Limitations (§1): the certificate is not publicly trusted (no domain), there is no rate limiting, and external checks came from the operator's egress only. |
| The deployment host holds chart data without a BAA | **No — open; accepted for this exercise** | Since the move from DigitalOcean to Vultr (§1), neither OpenEMR's database nor the agent runs on a product under an executed BAA. None has been executed with Vultr (`findings.md` F-61; contrast F-41). This is acceptable only because every record is synthetic, per the case study's demo-data constraint. **Before real PHI:** move to a substrate under an executed BAA and re-apply §1's placement rules to that provider's covered products. |
| Retrieved chart records leave the deployment host for the LLM provider on every Pre-Visit Brief | Yes, for this exercise — covered by its stated BAA assumption, not an executed BAA | **Flow:** each brief sends the patient's active conditions, active medications and all allergy records to Anthropic's Messages API for claim drafting (`agent/app/reasoning/draft_claims.py`). The fields sent are FHIR record ids, names, statuses, RxNorm codes and dates; no demographics or patient id. That is PHI leaving the application host, by design. **Coverage:** the case study brief directs treating every LLM provider as bound by a signed Business Associate Agreement under which no data is used for training ("act as if you have a signed Business Associate Agreement with all LLM providers" — as provided by the operator; the brief is not in this repository). That stated assumption is this submission's mitigation. No BAA with Anthropic has been executed or is recorded in this project. **Before real PHI:** an executed BAA with Anthropic (or a HIPAA-eligible endpoint / zero-data-retention arrangement). The current dataset is synthetic (F-41). |

Three risks are stated here as genuinely open, not mitigated; two more are
only partly mitigated, and one is not yet applicable because the feature it
concerns is not built. They are stated that way because overstating this
plan's coverage would itself be the kind of unverifiable claim §5 is
designed to prevent everywhere else in this system.
