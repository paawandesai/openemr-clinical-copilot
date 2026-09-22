WORK ORDER 09: write-back endpoint, part 1: minimal path + D1's
required verification. Branch feat/w2-writeback. Do not merge.

STEP 0: attempt a Write to agent/evals/w2/cases/probe.json. It must be
BLOCKED. If not, STOP.

READ FIRST: CLAUDE.md, docs/openemr-dev-guide.md, docs/w2/DECISIONS.md
(the superseding D1 entry), docs/w2/spikes/writeback.md,
docs/w2/LOCAL_ENV.md, and the write-back contract on the contracts
branch:
  git show feat/w2-contracts:agent/app/models/w2/writeback.py
If that file is not committed there yet, build against D1's text and
note it.

ENVIRONMENT: LOCAL OpenEMR only. Never write to the Vultr deployment.
Credentials from .env; never print secrets or tokens. If you add any
D-entry, use D22 or later (D21 is reserved for the retrieval branch).

GOAL
Prove D1's design before anything is built on it. This work order ends
at a checkpoint.

1. Design choice, reported with reasoning: implement as an OpenEMR
   custom module registering its own REST route and scope (preferred,
   if the fork's module/event system supports it cleanly; the Week 1
   chart-embed is already a module), or as a core route. Pick one and
   say why.

2. Custom OAuth scope for the endpoint; register a fresh LOCAL client
   with it (per LOCAL_ENV.md, including
   skip_ehr_launch_authorization_flow=1 if needed).

3. Ledger table via the module's install/upgrade SQL, with the UNIQUE
   idempotency key from D1.

4. Minimal endpoint: accept ONE document + ONE lab result, in one
   transaction: store the document through OpenEMR's own document
   code path (so on-disk encryption applies), write the
   procedure_order -> procedure_order_code -> procedure_report ->
   procedure_result chain via OpenEMR's DB layer, write the ledger row,
   put the extraction id (only) in procedure_result.comments, return
   real ids. Same request twice: second returns the same ids, creates
   zero rows (show row counts before/after).

5. CHECKPOINT: D1's required verification, with evidence:
   a. FHIR round-trip: GET /fhir/Observation for the patient shows the
      result with value, unit, reference range, interpretation, and
      note[0].text = the extraction id.
   b. Audit log: show the log table rows OpenEMR recorded for the write
      (event, user, table), or state plainly that none were recorded.
   c. UI: tell me exactly what to click in OpenEMR to see the result in
      the Procedure Results screen, and what I should see. I will check
      it in the browser myself.
   If (b) shows no audit entries, STOP and report; that changes the
   design.

OUT OF SCOPE: batching multiple results, the agent-side client,
supervisor wiring, Vultr, anything under agent/evals/.
ACCEPTANCE: phpstan/phpcs clean on changed PHP files; the checkpoint
evidence above; all test records created during development listed.
/report. STOP at the checkpoint. Do not merge.