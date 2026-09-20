# ARCHITECTURE.md

The agent architecture lives at [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) —
the Stage 5 integration plan (deployment topology, authorization model, tool
scope, conversation state, verification system, risks and mitigations),
opening with a one-page summary of the key decisions and tradeoffs.

The three decisions that constrain the most, from that summary:

1. **The agent reads patient data only through OpenEMR's FHIR R4 API, under
   the requesting clinician's own OAuth2 token** — never direct database
   access, never a broader-than-user service account. Authorization is never
   reimplemented agent-side.
2. **Tool scope is exactly the five FHIR resource types the use cases name**
   — `Condition`, `MedicationRequest`, `AllergyIntolerance`, `Encounter`,
   `Observation` — and nothing else.
3. **Verification is a deterministic code check, not a second model call.**
   The model drafts claims bound to `source_id` + field + value; a separate
   step confirms the source was retrieved this turn and the value matches
   before anything renders. Claims that fail render as UNVERIFIED, never
   silently dropped.
