# Stage 5 Architecture — Clinical Co-Pilot Agent Integration

**Source of record:** every capability below traces to one of the three use
cases in `docs/USERS.md`, and every specific number or finding cited traces
to `docs/AUDIT.md` or `docs/audit/findings.md` — both re-read in full this
session, not recalled from earlier in this conversation. One note on
staleness: `AUDIT.md` was finalized at F-01–F-54; `findings.md` has since
grown to F-55 (RxNorm coding coverage) and an amendment to F-15 (the
silent-import finding). Where those two entries are cited below, the
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
   cases.** Only Follow-Up Drilling needs it (USERS.md); Pre-Visit Brief and
   Change Detection are each single-turn by the use case's own definition.
   State is keyed to user + patient + session and invalidated on patient
   switch — the primary mitigation against cross-patient leakage within the
   agent's own state, not OpenEMR's own per-patient authorization gap
   (F-50; §4).
6. **MySQL stays self-hosted inside the droplet, not on a managed database
   product.** DigitalOcean's BAA covers Droplets, Kubernetes, Spaces, and
   Volumes but explicitly excludes Managed Databases (F-41); ePHI-bearing
   data must stay inside the covered Droplet. This is a compliance
   constraint on infrastructure, not a convenience choice.
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

A single DigitalOcean droplet running Docker Compose, hosting OpenEMR and
its MySQL/MariaDB database as containers on the same host — mirroring the
audited deployment (`docker/development-easy`, F-48).

**Why self-hosted MySQL, not a managed database:** this is a compliance
constraint, not a convenience one. DigitalOcean's published HIPAA-eligible
product list covers Droplets, Kubernetes, Spaces, and Volumes, and
explicitly does not cover Managed Databases (F-41). Any ePHI-bearing data —
the OpenEMR database included — has to stay inside a covered product, which
rules out DO's managed MySQL offering regardless of its operational
convenience. The agent introduces no new datastore that would need this
same decision made twice: it reads through the FHIR API and writes nothing
to the EHR's own database (see §2).

**Where the agent process lives:** as an additional service in the same
Compose stack, on the same droplet — not a separate host, and not (yet) a
specific service shape. Placing agent compute and any future observability
backend (e.g., a Langfuse-style tracing service) outside the droplet would
either require its own BAA-covered placement decision or risk PHI leaving
covered infrastructure through request/response logging; that decision is
explicitly **not yet made** and is called out under Risks (§6) rather than
asserted here as settled.

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

**Known limitation, stated plainly:** the droplet, as audited, still runs
the development Compose stack rather than the hardened `docker/release`
build (F-48) — Xdebug loaded into the live web process, a writable
bind-mount of the git checkout into the web container, and a full
composer/npm/webpack build executed on every container boot against
upstream registries. This is not a limitation this architecture plan
resolves by describing it; it is a stated **precondition** that must be
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
is permanently out of reach for this token" from the HTTP status alone. The
agent maintains its own record of which scopes the current token actually
holds — decoded from the JWT's own `scopes` claim, never from the token
response's `scope` field, which has been observed to disagree with what the
JWT actually contains (F-09) — and checks that record *before* issuing a
request, rather than reacting to a bare 401 after the fact. This also
forecloses the specific failure mode of retrying a permanently out-of-scope
request as if it were a transient auth expiry.

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

## 3. Tool Scope

Tool scope is a direct, cited mapping from `docs/USERS.md`'s three use
cases — not a fresh judgment about what resources "seem needed." Five FHIR
resource types cover all three use cases combined, and no resource outside
this set is in scope for v1.

| Use case (USERS.md §3) | FHIR resources required | Notes |
|---|---|---|
| **1 — Pre-Visit Brief** | `Condition`, `MedicationRequest`, `AllergyIntolerance` | Current-state snapshot only; the "what changed" element it surfaces is *consumed* from Use Case 2's output, not independently computed, so it does not need `Encounter` directly. |
| **2 — Change Detection** | `Encounter` (two — current visit and most recent prior), `Condition`, `MedicationRequest`, `Observation` | `Observation` here is scoped to new lab results in the interval only; vitals are excluded because they are recorded once per patient, never per encounter (F-20), so there is no second timepoint to diff. |
| **3 — Follow-Up Drilling** | `Condition`, `MedicationRequest`, `Encounter`, or `Observation` — whichever grounds the specific fact being drilled into | No fixed resource set by design: this use case re-queries whatever resource type the immediately preceding turn already surfaced, determined at conversation time, not by a fixed tool definition. |

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

**Scoping decision:** conversation state is keyed to
`(user, patient, session)`, and is invalidated the moment the patient
context switches — a new chart opening ends the prior conversation's state
rather than carrying it forward. This is a deliberate mitigation, not an
afterthought: a multi-turn system that keeps state across a patient switch
is exactly the shape of system that could answer a follow-up question about
patient B using context actually surfaced while patient A's chart was open.
Scoping state to the same triple that authorization is already scoped to
(§2) means a patient switch — which already changes what the clinician's
token can see in a patient-context flow — also structurally ends the
window in which stale cross-patient context could be referenced.

**Known limitation:** this scoping prevents cross-patient leakage *within*
the agent's own state; it does not, and cannot, retroactively fix
OpenEMR's own lack of per-patient authorization scoping (F-50, §2) if the
same underlying token is reused for a subsequent patient before the
conversation state itself is invalidated. The mitigation addresses the
agent's own added surface, not the platform's existing one.

## 5. Verification System

The reasoning model never renders prose claims directly. It drafts
structured claims, each bound to `source_id` + field + value. A
deterministic, code-based verification step — not a second model call —
then confirms two things for every claim before it renders: that the
`source_id` exists among what was actually retrieved this turn, and that
the claimed value matches what that source actually holds. Domain
constraints (dosage thresholds, interaction flags, where applicable) are a
separate, additive rule set layered on top of source verification, not
folded into it. Only claims that pass render to the physician; a claim that
fails is stated as explicitly unverified — never silently dropped, never
softened into vaguer language that hides the failure.

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
  to be covered by the same mechanism that checks single-value claims.
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
  carry no patient field at all. Cross-patient contamination is currently
  mitigated only at the conversation-state layer (the
  `(user, patient, session)` key and invalidation on patient switch, §4),
  not re-checked here: if that scoping has a bug, a claim sourced from the
  wrong patient's record passes verification cleanly. This is an open gap,
  not a resolved one.
- **Empty or all-inactive query results.** Whether a queried resource type
  returned zero results (e.g. "allergies: not recorded,"
  USERS.md §3 Use Case 1; F-21) or returned records but none currently
  active (e.g. "medications: none active") is not a claim-verification
  concern: the rendering step computes both directly from the retrieval
  results, with no LLM involvement, because each is a deterministically
  knowable fact, not something that benefits from the draft-then-verify
  pattern used for everything else.

## 6. Risks and Mitigations

| Risk | Mitigated? | How |
|---|---|---|
| Fabricated or hallucinated clinical claims reach the physician | Yes | Source-verification layer (§5) — every claim bound to a real, this-turn `source_id`; unverified claims render as explicitly unverified, never dropped or softened. |
| Cross-patient context leakage in a multi-turn exchange | Yes, within agent state | Conversation state exists only for Use Case 3, keyed to `(user, patient, session)`, invalidated on patient switch — not a fix for OpenEMR's own per-patient authorization gap (F-50; §4). |
| A future service (agent process, observability backend) repeats the Docker/UFW exposure pattern | Yes, by process | Binding verification via `ss`/`docker compose ps`/resolved `docker compose config` from an external vantage point is now a standing requirement for any new service in this Compose stack (§1). |
| Trusting a misleading "success" signal from upstream tooling (OpenEMR's own or otherwise) | Yes, by design principle | Verification checks actual retrieved data, never an upstream success claim — directly motivated by F-15's observed intermittent silent-failure pattern (§5). |
| Agent misreads a 401 as a transient/retryable auth failure | Yes | Agent holds its own decoded-JWT scope record and pre-checks before requesting, rather than inferring cause from HTTP status alone (§2). |
| Real PHI is deployed onto the currently-audited stack as-is | **No — open** | The audited droplet runs the development Compose stack, not the hardened `docker/release` build (F-48): Xdebug loaded live, a writable git-checkout bind-mount, dependency builds against upstream registries on every boot. Stated as an explicit precondition to close, not something this plan resolves by naming it. |
| Retrieval incompleteness (the right record never fetched) silently passes as "verified" | **No — open** | Outside what source-verification can catch by construction (§5). Requires separate retrieval-completeness evaluation this plan does not yet define. |
| Agent compute / logging backend placement creates new ePHI-handling surface outside covered infrastructure | **No — open** | Not yet decided which specific service shape the agent process and any observability backend take, only that they are planned to stay on the same covered droplet (§1). |

Three risks are stated here as genuinely open, not mitigated, because
overstating this plan's coverage would itself be the kind of unverifiable
claim §5 is designed to prevent everywhere else in this system.
