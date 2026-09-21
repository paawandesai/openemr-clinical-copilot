# Key Success Metrics — Clinical Co-Pilot

Every metric below traces to a specific promise made in `docs/USERS.md` or
`docs/ARCHITECTURE.md`, cited by section. When this document was written,
`ARCHITECTURE.md` established no correlation-ID scheme and left the
observability backend's placement open. Both are settled now: every logged
event carries a `correlation_id` (`agent/app/middleware/correlation.py`),
and `agent/scripts/compute_metrics.py` computes these metrics from the
agent's stdout log (`ARCHITECTURE.md` §1). Computation below is grounded in
what the code actually records. That is the `(user, patient, session)` key
attached to every logged event, and the per-claim `source_id` + pass/fail
determination §5's verification step computes. For the brief and the
single-turn follow-up the key is a per-request attribution label that groups
events, with no conversation state behind it. The multi-turn conversation
(deployed) tags its events with the same key and also keeps verified claims
under it (`ARCHITECTURE.md` §4). No new logging framework or infra decision
is proposed here.

**Status, stated per metric below:** Metrics 1, 2, 4, 5 and 6 are computed
from events the running agent emits. Metric 2 was computed from a field set
before rendering, so it could not see the render defect it exists to catch;
since 2026-09-21 it is computed from the rendered text itself (F-59).
Metric 3 cannot be computed from the agent's logs yet: its
event is never emitted, because the agent-side scope record it depends on
is not built (`ARCHITECTURE.md` §2). Use-case segmentation covers the
Pre-Visit Brief and the follow-up, single-turn or multi-turn: both emit the
same events under `use_case=follow_up_drilling`, one interaction per turn.
Change Detection is not built.

If these six numbers are true, the system does what `USERS.md` and
`ARCHITECTURE.md` claim it does. If any one is false, it names a specific
broken promise — not a vague sense that "the AI needs work."

---

## 1. Verification Pass Rate

**Definition.** Of all claims the reasoning model drafts in an interaction,
the percentage that pass the deterministic source-attribution check — the
claimed `source_id` exists among what was retrieved this turn, and the
claimed value matches — before rendering to the physician.

**Why this metric, for this system.** §5 states the entire trust
architecture rests on this check running before every render. This is the
most direct available proxy for whether that mechanism is doing anything in
production, versus existing as an unused feature nobody's claims ever
actually fail. A hospital CTO's core fear — a confidently-stated
hallucination reaching a physician — is exactly what this number bounds.

**How it's computed.** §5's verification step already produces a pass/fail
determination per claim as part of deciding what to render. Counting
pass vs. total drafted, rolled up per interaction (grouped by the
`(user, patient, session)` attribution key every event carries, §4),
requires no new instrumentation — only persisting a result the system
already computes internally.

**What a bad number means.** A low rate means the model is drafting claims
it cannot source — a retrieval or prompting problem, not a verification
failure. A rate suspiciously close to 100% across every use case isn't
automatically reassuring either: it should prompt checking that
verification is actually being exercised, not skipped on easy cases.

## 2. Unverified-Claim Disclosure Rate

**Definition.** Of claims that *failed* verification, the percentage
surfaced to the physician as explicitly unverified, versus silently dropped
or softened into vaguer language.

**Why this metric, for this system.** This is distinct from Metric 1:
pass rate measures how often the model is right; this measures whether the
system is honest when it isn't. §5 states this as an explicit promise —
"a claim that fails is stated as explicitly unverified — never silently
dropped, never softened into vaguer language that hides the failure." The
sharpest concrete instance of what this protects is the allergy
distinction USERS.md builds Use Case 1 around: saying "allergies: not
recorded" as an explicit, worded claim rather than rendering an empty list
a physician could misread as "no known allergies" (USERS.md §3, Use Case 1;
grounded in F-21). A silent failure here doesn't just degrade an answer —
it reproduces exactly the ambiguity USERS.md was written to eliminate.

**How it's computed.** Same substrate as Metric 1: every failed-verification
claim already has a disposition (rendered-as-unverified vs. not) decided by
the render step. Log that disposition per failed claim, per interaction.

**It is logged from the render step, since 2026-09-21.** It was not
originally: the logged field (`disclosed_as_unverified`) is set to `True`
by the verifier for every failed claim before rendering runs, so the metric
read 100% whatever the renderer did — including when the renderer hid
failures, the one defect it exists to catch (F-59; eval case C5).

Now each of the three render paths — the brief, the single-turn follow-up
and the conversation turn — calls `app/verification/disclosure.py` **after**
its render function returns, and that audit emits one
`claim_disclosure_event` per failed claim carrying `disclosed`: whether
that claim's UNVERIFIED line was actually found in the rendered text.
Metric 2 is computed from that field. Being outside the render function is
what lets it survive a broken or replaced renderer; reading the text rather
than the renderer's intent is what makes it measure the physician's view.
If failed claims exist and the audit did not report on them, the metric
prints `NOT COMPUTED` or `INCOMPLETE` and fails — a missing signal is not a
clean bill of health.

`disclosed_as_unverified` is still logged and still means something
different: what the verifier *required*, against what the physician was
*shown*.

**What it still does not cover.** The audit reads the string this service
produced. It does not see what a browser finally paints, so a display
surface that hid an UNVERIFIED line — the chart embed's own JavaScript,
say — would still be reported as disclosed. For that the evidence remains
the eval suite (B2, C1–C4).

**What a bad number means.** Anything under 100% here is a defect in the
render step, not a tuning target — §5 states this as an absolute, not a
target to approach. It requires a code fix to the render path, not a
retrieval or prompting adjustment.

## 3. Authorization Boundary Integrity

**Definition.** The rate of FHIR requests outside the current token's held
scope that are correctly rejected. Reported as a hard invariant — target
100%, not a percentage expected to vary within a tolerance band.

**Why this metric, for this system.** §2 states the entire authorization
model is delegation to OpenEMR's own scope enforcement, not an agent-side
ACL — and this was empirically tested, not assumed: a token scoped only to
`user/Patient.read` was used to attempt `MedicationRequest` and
`Observation` reads, both correctly refused (§2, F-07). This metric is
meant to be monitored continuously because a single silent regression
here — a scope check that stops firing, an OpenEMR upgrade that changes
enforcement behavior — is a PHI exposure event, not a degraded user
experience. Same posture ARCHITECTURE.md applies to the Docker/UFW incident
(§1, F-54): a boundary that looked solid failing silently, not a slow
drift, is the failure mode to watch for.

**How it's computed — as designed.** §2 plans for the agent to hold its own
decoded-JWT record of which scopes the current token holds, to distinguish
a 401 caused by insufficient scope from one caused by an expired token.
Cross-referencing that record against the actual FHIR response for every
request — flagging any accepted (2xx) response for a request the agent's
own record predicted should be out-of-scope — would compute this metric as
a byproduct of that logic, not new infrastructure.

**As built, it is not computed from the agent's logs.** The scope record is
not built (§2). The event schema exists (`AuthorizationCheckEvent`), and
`agent/scripts/compute_metrics.py` implements the cross-reference, but
nothing in the pipeline emits the event. The script therefore reports this
metric as insufficient data. It is not monitored in production today. What
is measured is OpenEMR's side of the boundary, directly and on demand: eval
case D1 sends a `user/Patient.read`-only token's reads to FHIR and got 5 of
5 out-of-scope reads refused in the 2026-09-16 run on the DigitalOcean
droplet (`agent/evals/README.md`).

**What a bad number means.** Not "needs tuning" — page someone the same day.
A drop below 100% is a live authorization regression, evaluated with the
urgency of a security incident (F-54), not a KPI in a weekly review.

## 4. Response Latency (p50 / p95) Against the 90-Second Budget

**Definition.** Time from trigger to rendered, verified response — chart-open
for Pre-Visit Brief, the explicit question for Change Detection and
Follow-Up Drilling (USERS.md §3) — reported as p50 and p95, **separately
per use case**, not blended into one number.

**Why this metric, for this system.** The persona this entire system is
built around is defined by a time constraint: a primary care physician in
the ~90 seconds between finishing one encounter and opening the next chart
(USERS.md §1–2) — the target user's own definition, not an SLA picked for
this document. The three use cases have different shapes and expected data
volumes (USERS.md §3: a single-pass snapshot, a two-encounter diff, a
variable-length reactive follow-up), so a single blended number would hide
which specific use case is failing the moment it exists to serve. The
budget also has to include verification's own cost: §5 places the
deterministic verification step *before* render, gating every response —
it is inside the budget, not layered on after the clock has stopped.

**How it's computed.** Trigger and render timestamps per interaction,
segmented by use case and grouped by the same `(user, patient, session)`
attribution key every event carries (§4: a label, not conversation state).

**What a bad number means.** A p95 that exceeds a physician's actual
available window means the tool works against the exact moment it was
designed for, independent of whether its answers are correct — a
Metric-1-perfect system that answers after the physician has already
entered the room has failed USERS.md's own framing just as completely as
a fast, wrong one.

## 5. Retrieval Coverage per Query (proxy, stated honestly)

**Definition.** For a given interaction, the fraction of that use case's
`USERS.md`-defined required FHIR resource types (per the §3 Tool Scope
table) that were actually queried before the response was generated.

**Why this metric, for this system.** §5 states this outright as a blind
spot by construction: "Verification confirms that sourced claims are real;
it does not confirm that the *right* records were fetched in the first
place. A relevant `AllergyIntolerance` row that was never retrieved this
turn makes every claim in the response pass verification cleanly — the gap
is invisible to this layer entirely." This metric is the closest available
substitute for observing that exact gap in production. It is explicitly a
**proxy and a mitigation, not a guarantee** — matching how ARCHITECTURE.md
itself frames the limitation. Even full resource-type coverage does not
prove every relevant *row* within that type was fetched (e.g., touching the
`AllergyIntolerance` endpoint is not the same as having retrieved every
allergy row for that patient); this metric catches only the coarser,
type-level version of the gap.

**How it's computed.** Per interaction, log which resource types from the
§3 table were actually queried and compare against that use case's defined
requirement set.

**What a bad number means.** It does not mean the rendered response was
factually wrong — verification would still pass on whatever *was*
retrieved. It means the exact blind spot §5 names was structurally live for
that interaction, and it should trigger a qualitative review of that
interaction's retrieval logic, not just a re-run of the number.

## 6. Cost per Interaction

**Definition.** Token cost (all model calls within one completed use-case
interaction) per interaction, broken out by use case.

**Why this metric, for this system.** The three use cases carry
meaningfully different profiles: Pre-Visit Brief and Change Detection are
each single-turn (USERS.md §3; ARCHITECTURE.md §4), while Follow-Up
Drilling is the only one designed to carry conversation state across turns
(ARCHITECTURE.md §4) — a materially different token footprint that a
single blended average would obscure. Change Detection does not exist, so
today's per-use-case figures cover the brief, the single-turn follow-up and
the multi-turn conversation. The conversation emits the same
`llm_usage_event` per turn, and its token footprint has been measured:
3,970 to 22,690 input tokens a turn across charts of 5 to 184 records,
against the brief's 1,243 to 4,378 (`docs/COST_ANALYSIS.md` §1.4,
measured 2026-09-20 on the Vultr host). Worth noting: because §5's
verification step is a deterministic code check, not a second model call,
it contributes effectively zero token cost — this metric tracks the
reasoning step almost exclusively, not a diluted average across two
inference calls.

**How it's computed.** Standard token accounting (input + output across
every model call in the interaction) rolled up per the same `(user,
patient, session)` attribution key, grouped by use case.

**What this establishes.** The seed number for the separately required AI
Cost Analysis deliverable (100 / 1K / 10K / 100K user projections) — not
attempted here. The point is narrower: measure the real, per-use-case unit
cost from actual usage before projecting from it, rather than estimating
top-down from an average that may not reflect Follow-Up Drilling's
different shape.

---

## What is deliberately not on this list

**Raw usage volume, session counts, or any adoption/engagement metric** —
how often the tool is opened, how many turns a conversation runs, daily or
weekly active users. USERS.md and ARCHITECTURE.md are both built around one
question: can this system's claims be trusted. None of ARCHITECTURE.md's
eight summary decisions, and none of USERS.md's three use cases, are framed
around how much the tool gets used — a physician using it constantly while
it silently drops failed verifications would be this document's worst
outcome, not a success. Adoption matters eventually, but it is not evidence
the system does what it claims, and folding it in here would blur the one
thing this document proves.

**Physician satisfaction or a generic "helpfulness" score** — for the same
reason: neither traces to a specific promise in either source document, and
a satisfaction score can rise even as Metric 2 quietly falls. This document
follows the same discipline USERS.md applies to its own "Out of scope"
section (§4) and ARCHITECTURE.md applies to its Risks table (§6): state
what's excluded and why, rather than letting a metric in just because it's
easy to collect and looks good on a dashboard.
