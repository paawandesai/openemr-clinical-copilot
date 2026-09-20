# Target User and Use Cases — Clinical Co-Pilot

This document is the source of truth `ARCHITECTURE.md` must trace back to.
Every agent capability described there must point to one of the three use
cases defined below. All data claims are sourced from `docs/audit/findings.md`
(finding numbers cited inline) as it stands at commit `b4c7d04` — re-verified
directly against that file while writing this document, not restated from an
earlier reading of it.

---

## 1. Target user

**A primary care physician, in the ~90 seconds between finishing one patient
encounter and opening the next patient's chart** — the specific moment the
case study frames the co-pilot around, not "physicians" in general.

This persona, and not a pre-clinic huddle-prep persona, is what the deployed
data actually supports:

- **There is effectively no schedule to prep against.** The appointments
  table (`openemr_postcalendar_events`) holds exactly 11 rows on *both* the
  local and droplet databases, despite those two databases being independently
  seeded, non-overlapping Synthea populations of different sizes (65 vs. 53
  patients — findings.md's data census, pass 2; F-43). An identical row count
  across two structurally unrelated patient populations is not what a
  synthetic per-patient schedule would produce; findings.md records no
  finding establishing that these rows are Synthea-derived at all. A
  huddle-prep use case ("here's today's list") would require treating this
  table as if it held a real clinic day, which the evidence does not support.
  A single-chart, chart-open-triggered moment does not depend on that table
  existing at all.
- **Encounter volume per patient is extreme and unevenly distributed**, which
  argues for a moment focused on *one open chart*, not a multi-patient
  dashboard. Median local encounters-per-patient is 29, but the outlier
  patient (`pid=20`) has 530 — roughly 18x the median, range across all
  patients is 1 to 530 (F-22). A view built for skimming several patients at
  once cannot also be built to survive that outlier; a view built for one
  chart at a time can.
- **What's richly populated differs from what's sparse, and the persona has
  to be built around both.** Problem lists (1,669 rows locally / 2,384 on the
  droplet) and lab results (7,259 / 29,701 rows) are dense. Allergy
  documentation is not: only 14 of 65 local patients (21.5%) have any allergy
  record at all — 51 have none (F-21). Vitals are captured, but exactly once
  per patient, never per encounter (65/65 local patients, 53/53 droplet;
  F-20) — so there is no vitals trend to show even if the persona wanted one.
  A physician about to walk into a room needs a brief that is honest about
  which of these is which, not one that treats sparse data as if it were
  complete.
- **The record cannot support clinical judgment calls on results.** Lab
  result rows carry a value, units, and a LOINC code, but zero rows — across
  both environments — carry a reference range or an abnormal flag, though the
  schema has columns for both (F-19). This bounds what "surfacing labs" can
  mean for this persona: reporting, not interpreting.

## 2. Workflow walkthrough

**The 30 seconds before the co-pilot opens.** The physician has just finished
documenting the prior encounter and is walking to the next room. They do not
have time to open the chart and read it top to bottom — for a typical
patient that might be tractable, but the record's own outlier (F-22) proves
it isn't a design a physician can rely on across a full clinic day. What they
need is not "the chart," it's a compressed answer to: *what do I need to know
before I walk in, and what, if anything, is different from last time?*

**What they need from it.** A brief that surfaces the handful of facts that
would actually change how the visit opens — active problems, current
medications, documented allergies — stated plainly enough to scan in a few
seconds, and explicit about gaps rather than silent about them (an allergy
section that reads "not recorded" instead of appearing empty, per F-21's
distinction between "none documented" and "never captured"). If nothing looks
right, the physician needs to be able to ask one follow-up question without
re-explaining who the patient is or what they're asking about.

**What they do with the output.** They skim it in the doorway or the last few
steps of the walk, decide whether anything changes their plan for this visit
(a new problem, a medication that stopped, an allergy that wasn't there
before), optionally ask one clarifying question if something doesn't add up,
and then close the co-pilot and start the visit already oriented — not still
reading.

## 3. The three use cases

**Build status.** This section specifies all three use cases; not all are
built. **Use Case 1 (Pre-Visit Brief) is built**, except its "changed since
last visit" line. **Use Case 2 (Change Detection) is not built**, so that
line is not built either. **Use Case 3 (Follow-Up Drilling) is deployed**, in
two forms: a constrained single-turn follow-up (one question on the brief's
own records, with no conversation memory and no `Encounter` or `Observation`
queries), and the multi-turn conversation, deployed on the Vultr host since
2026-09-19 and reachable both from the chart embed and from the standalone
UI. What is built is described in `agent/README.md`; the architecture's
built/planned split is in `docs/ARCHITECTURE.md` §3–§4.

### Use case 1 — Pre-Visit Brief

**Trigger.** The physician opens the patient's chart. This is a proactive
surface, not a typed query — the equivalent explicit question, if the
physician asked it directly, is *"What do I need to know before I see this
patient?"*

**Data required.** `Condition` (active problem list), `MedicationRequest`
(current medications), `AllergyIntolerance` (with the recorded/not-recorded
distinction made explicit, not inferred from an empty list). Findings.md
verifies these are populated, non-trivial tables to draw from: 1,669
`medical_problem` rows locally (2,384 droplet), 305 `prescriptions` rows
locally (373 droplet) plus 309 `medication` list rows (377 droplet), and 43
`allergy` rows locally (59 droplet) spread across only 14 (local) / 9
(droplet) distinct patients (F-21, census). This brief also surfaces a
one-line pointer to whether anything changed since the last encounter — that
signal is *computed*, not looked up, and is the dedicated job of Use Case 2
below; the brief consumes it rather than re-deriving it. (Planned, not
built: because Use Case 2 does not exist yet, the brief as implemented has
no "changed since last visit" line — `agent/app/graph/pre_visit_brief.py`.)

**Why a conversational agent, not a dashboard.** The list lengths this brief
draws from are not predictable per patient — one patient might have 2 active
problems, `pid=20` has 92 (F-22) — so a fixed dashboard panel either wastes
space for most patients or gets unreadable for the outliers, and either way
forces the physician to do the same manual scan-and-triage work every time
regardless of what actually matters this visit. An agent can prioritize and
compress that variable-length list into a handful of sentences, and — this is
the part no static widget can do — it can say "allergies: not recorded" as an
explicit, worded statement instead of rendering an empty list a physician could
misread as "no known allergies" (F-21's exact concern). A dashboard shows
what's there; this use case requires narrating what's there *and* what's
missing.

**Multi-turn required?** No. This is answerable in a single pass over the
current chart state at the moment it opens.

### Use case 2 — Change Detection

**Status: not built.** Everything below is the specification for a planned
capability. No code computes a change between encounters.

**Trigger.** An explicit question: *"What's different since their last
visit?"* — either asked directly by the physician or invoked as the deeper,
on-demand form of the brief signal Use Case 1 surfaces inline.

**Data required.** Two `Encounter` resources (the current visit and the most
recent prior one, to anchor what "since last time" means), `Condition`
(problems added or resolved between them), `MedicationRequest` (medications
started, stopped, or changed between them), and `Observation` for new lab
results recorded in that interval. Vitals are deliberately excluded from this
diff: findings.md shows vitals are captured exactly once per patient, never
per encounter (F-20), so there is no second timepoint to compare against —
a "vitals changed since last visit" claim is not something this data can
support, and this use case does not make it.

**Why a conversational agent, not a dashboard.** This is the sharpest case in
this document. A chart view — dashboard, table, or otherwise — shows
*everything*, in order, and leaves the comparison to the reader; it is a
display of the record, not a computation over it. Answering "what's
different" requires first identifying which two encounters to compare, then
cross-referencing what appeared, disappeared, or changed between them, and
stating that delta as a claim. No dashboard widget does this without first
being told which two points to diff — and for a patient with 530 encounters
(F-22), manually locating "the last visit" and eyeballing a diff against
today is not something a physician can do in a 90-second window. Compression
with fidelity, not retrieval, is the job here.

**Multi-turn required?** No. One exchange computes one delta between two
specific, already-identified encounters.

### Use case 3 — Follow-Up Drilling

**Status: deployed, in two forms — a constrained single-turn follow-up and
the multi-turn conversation.** Below is the full specification. The
single-turn form (`POST /pre-visit-brief/follow-up`,
`agent/app/graph/follow_up.py`) answers one question at a time from the
brief's own records (conditions, medications, allergies), keeps no memory of
earlier turns and never queries `Encounter` or `Observation`. The multi-turn
conversation (`POST /pre-visit-brief/conversation`,
`agent/app/graph/conversation.py`, and the standalone chat UI in
`agent/deploy/ui/`) resolves "that" and "last time" against the claims
earlier turns *verified* — never against earlier wording — re-reads the chart
every turn, adds `Observation` or `Encounter` by rule, and verifies every
claim of every turn exactly as the brief does. It deliberately does not
answer what the record cannot verify: *"Why is that flagged?"* gets "the
records do not state it", and *"is it getting worse?"* is refused
(`docs/ARCHITECTURE.md` §4–§5). Both are deployed on the Vultr host; the
conversation has been reachable inside the OpenEMR chart embed, below the
brief card, since 2026-09-19.

**Trigger.** A reactive question that only makes sense in reference to
something just surfaced: *"Why is that flagged?"* / *"What did we try last
time for this?"*

**Data required.** No fixed resource set — by definition, this use case
re-queries whichever resource type grounds the specific fact being drilled
into: `Condition` (history of a specific problem), `MedicationRequest` (prior
therapies tried for it), `Encounter` (the visit where something was noted or
tried), or `Observation` (a specific past value referenced). What's queried
is determined by what the conversation's prior turn already surfaced, not by
this use case's own type.

**Why a conversational agent, not a dashboard.** This is the most direct of
the three: a dashboard has no mechanism for "why is that" as a follow-up — it
cannot accept a question that refers back to its own prior output, because it
has no memory of having said anything. Resolving "that" and "last time" as
references to a specific fact raised a moment earlier is only possible on a
conversational surface, and doing it without forcing the physician to
re-specify the patient, timeframe, and problem from scratch is the entire
value of asking it this way instead of going back to the chart.

**Multi-turn required?** **Yes — the only one of the three.** The referents
("that," "last time") resolve against the immediately preceding turn's
output; without conversation memory this question cannot be answered at all.
Use Cases 1 and 2 are each fully answerable from a single exchange grounded
in the current chart state — this asymmetry is deliberate, not an oversight:
multi-turn state exists here because one specific need requires it, not by
default.

## 4. Out of scope for v1

- **Lab value interpretation ("is this normal?").** Zero of the lab result
  rows in either environment carry a reference range or an abnormal flag,
  though the schema supports both (F-19). The agent reports value, unit, and
  date — it does not characterize a result, because the record cannot
  support that claim being made truthfully.
- **Multi-patient panel views or scheduling.** No use case above requires
  seeing more than one patient's chart at a time, and the appointments table
  that a schedule view would depend on is not meaningfully populated in
  either environment (Section 1, above).
- **Any capability not traceable to one of the three use cases above.** If a
  capability described in `ARCHITECTURE.md` cannot be pointed back to Pre-Visit
  Brief, Change Detection, or Follow-Up Drilling, it is out of scope for v1
  regardless of how reasonable it sounds in isolation.

**Considered, not included:** while reasoning through Use Case 1's medication
list, prescribing-time safety checks (drug-interaction or allergy
cross-referencing at the point of writing a new prescription) came up as an
adjacent, plausible capability. It is deliberately not a fourth use case here.
It is a different class of behavior — active decision support that could
change a clinical action, not surfacing or diffing what is already
documented — and it would need a drug-interaction reference source this audit
never evaluated, layered on top of allergy data that is already known to be
incomplete for most patients (F-21). Noted here so it isn't silently
reintroduced in `ARCHITECTURE.md` under a different name.
