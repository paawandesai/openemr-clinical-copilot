WORK ORDER 04: Week 1 debt triage. Branch chore/w1-debt. Do not merge.
Timebox 90 min.

STEP 0: prove the gate is live. Attempt a Write to
agent/evals/w2/cases/probe.json. It must be BLOCKED. If not, STOP.

READ FIRST: CLAUDE.md, docs/w2/DECISIONS.md. Do not trust my list of
debt below as current: several items may already be fixed (a later
round closed F-58/59/60 and reported 43/43 evals on Vultr). VERIFY each
item's status from code, git log, and the latest eval results before
acting on it.

1. FIX, the TOON trim cap (highest priority):
   - Find the prompt-serialization trim. Report the exact cap and
     what gets cut, in what order.
   - Determine whether any patient in the current data exceeds it, and
     what clinically relevant rows would be dropped (counts by
     resource type, not names).
   - Required outcome: no SILENT truncation. If trimming happens, the
     model input must say so explicitly ("N further <type> records
     omitted"), abnormal and most-recent results must be kept first,
     and the answer path must not claim completeness when trimmed.
   - Add unit tests for: under cap, at cap, over cap with abnormal
     values beyond the cap.
   - Run the Week 1 evals that touch serialization (list which);
     report pass/fail and cost. No regressions allowed.

2. FIX, stale references:
   - Every occurrence of the old DigitalOcean IP or host across the
     repo (Postman/Bruno collection, scripts, docs): replace with the
     Vultr host or an env var.
   - Every hard-coded patient UUID in README, docs, Postman/Bruno
     collections, and scripts: check each against the Vultr FHIR server
     (GET only). Replace any that 404 with the current UUID of the same
     patient if identifiable by demographics, else flag it. Known stale:
     a2c0e096-... (the F-57 patient; its current Vultr UUID starts
     a2c36e54-).
   - Files under agent/evals/ are gate files: do NOT edit them; list
     each stale occurrence there and I will handle them in a gate
     session.

3. DOCUMENT, create docs/w2/WEEK1_DEBT.md. For each item: status
   (open / fixed / partially fixed, with evidence), impact on Week 2,
   and decision (fix now / document / defer):
   - F-57 pattern beyond allergies (conditions, medications): is the
     FHIR-client fix general or allergy-specific? Check the code.
   - No FHIR paging (silent omission risk).
   - Refusal fallback model not on the HIPAA-eligible list.
   - Any Week 1 eval failures still open (check latest results).
   - OpenEMR PHP tier saturating around 10 concurrent users.
   - From the W2 spike: CapabilityStatement advertises unsupported
     DocumentReference create; GET /api/procedure returns 200 with an
     HTML SQL error; document download 500s under bearer auth.
   - The Week 1 OAuth client still enabled from grading (human action
     only; just record it).

OUT OF SCOPE: any refactor beyond items 1-2; OAuth client changes;
anything under agent/evals/.

/report, with the eval results for item 1 shown in full. STOP.
Do not merge.