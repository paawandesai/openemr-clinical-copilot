# WORK ORDER: Write-back spike — documents and lab values into OpenEMR

> **SUPERSEDED.** This draft was not the executed work order. Results of the
> executed spike: [`docs/w2/spikes/writeback.md`](../spikes/writeback.md).
> The decision it produced is D1 (superseding) in
> [`docs/w2/DECISIONS.md`](../DECISIONS.md).
>
> The body below is kept as written, unedited, for the record. Do not build
> from it — it predates the findings and several of its assumptions were
> answered differently by the spike.

## Goal

Answer D1 with evidence: decide the path by which extracted lab values and
source documents are written back into OpenEMR, and record why. The spike is
done when a reader can tell, from `docs/w2/spikes/writeback.md` alone, which
path was chosen, what was actually observed on a running instance for each
candidate, how retries behave, and what the chosen path costs us that the
others would not.

## Branch

`spike/writeback`, in its own worktree:

```bash
scripts/new-worktree.sh spike/writeback docs/w2/work-orders/00-writeback-spike.md
```

Do not merge.

## Environment

- A running OpenEMR instance with the FHIR API enabled and an OAuth2 client
  registered. The deployed Vultr host is the reference instance; a local
  `docker/development-easy` stack is acceptable and cheaper to break.
- Credentials by name: `FHIR_CLIENT_ID`, `FHIR_CLIENT_SECRET`,
  `FHIR_TEST_USERNAME`, `FHIR_TEST_PASSWORD`, `FHIR_BASE_URL`. Values come from
  `agent/.env`; never paste a value into the spike doc or a commit.
- A throwaway test patient. Do not spike writes against a patient any recorded
  eval or smoke test reads.

## In scope

- Establish which write surfaces exist, by reading the route tables, then by
  calling them. Starting facts, to be confirmed rather than trusted:
  - `apis/routes/_rest_routes_fhir_r4_us_core_3_1_0.inc.php` registers FHIR
    create/update for **Organization, Patient and Practitioner only**
    (lines 546, 553, 560, 569, 677, 684). There is **no** `POST /fhir/Observation`
    (only `GET`, lines 493 and 498) and **no** `POST /fhir/DocumentReference`
    create — line 259 is the `$docref` operation, not a create.
  - `apis/routes/_rest_routes_standard.inc.php:496` registers
    `POST /api/patient/:pid/document`, on the standard (non-FHIR) API.
  - `src/Services/DocumentService.php` and
    `src/Services/FHIR/FhirDocumentReferenceService.php` are the service-layer
    entry points behind those.
- For each candidate path — standard-API document upload, a co-pilot-owned side
  table, and any FHIR route that turns out to exist — determine and record:
  - **Authorization:** which ACL section/action and which OAuth2 scope gate it,
    and whether the clinician's own token suffices or a broader credential is
    required. A path that needs a service account broader than the user is a
    finding, not a detail (`docs/audit/findings.md` F-50).
  - **Audit trail:** what row lands in the audit log, and whether the writing
    actor is recorded as the clinician or as the co-pilot.
  - **Retry behaviour:** send the same write twice. Record exactly what
    happens — duplicate row, error, silent no-op — and what field, if any, can
    carry an idempotency key across a retry.
  - **Provenance capacity:** can the path store document id + page + located
    region alongside the value (D2), or would provenance have to live elsewhere?
- Write `docs/w2/spikes/writeback.md` with the findings and a recommendation.
- Append nothing to `DECISIONS.md`. Propose the D1 replacement body in the
  spike doc; a human appends it.

## Out of scope

- Building the write-back. This spike produces a document and a recommendation,
  not a feature.
- Extraction, the locate step, the supervisor, RAG — every other Week 2 part.
- Eval rubrics, eval baseline, judge config, CI config — gate files, blocked by
  hook (`CLAUDE.md` invariant 7).
- Week 1 files: do not move, rename or restructure anything under `agent/`.
- Patching OpenEMR to add a missing FHIR route. If the conclusion is that a
  route must be added, that is the recommendation, not the deliverable.

## Contracts to honor

- "Every write to OpenEMR carries an idempotency key." The spike's central
  question is *what field can carry one on each path* — if none can, say so
  plainly, because that disqualifies the path.
- "No raw PHI in logs, traces, or error messages." The spike doc is a
  committed artifact: record shapes, route names, status codes and row counts.
  No patient names, no MRNs, no chart content, no credential values.
- "Patient-record facts and guideline evidence are distinct `source_type`s" and
  the citation contract `{source_type, source_id, page_or_section,
  field_or_chunk_id, quote_or_value}` (+ `bbox`) — a write path that cannot
  round-trip provenance fails D2.
- `docs/w2/DECISIONS.md`: **D1 (PENDING-SPIKE)** is what this resolves.
  **D2 (DECIDED)** binds the answer: lab values carry provenance to the source
  document, and intake meds/allergies are stored as document-derived facts,
  never merged into the chart. A write path that only works by merging into the
  chart's medication or allergy list is out, regardless of how convenient.

## Acceptance tests

1. `cd agent && python -m pytest tests` — passes, unchanged (this spike should
   touch no application code; if it does, say why in the report).
2. `docs/w2/spikes/writeback.md` exists and names, for every candidate path:
   authorization, audit trail, observed double-write behaviour, and
   provenance capacity.
3. Each observed behaviour in that doc is backed by a pasted request/response
   pair or log line from a real call — status codes included, PHI redacted.
   A claim with no observation behind it is labelled UNVERIFIED in the doc
   itself, not just in the report.
4. The doc ends with one recommended path, the runner-up, and the specific
   condition that would flip the choice.

## Timebox

One working session. When it runs out: stop and report with whatever was
observed. A spike that reports "two of three paths measured, here is what we
know" is useful; a spike that guesses the third to look complete is not.

## Deliverable

- `docs/w2/spikes/writeback.md` — findings, evidence, recommendation, and a
  proposed replacement body for D1.
- No code. No `DECISIONS.md` edit.

## Report format

End with `/report`. It must produce:

- **What changed** — `file:line` for every edit.
- **Commands run** — real commands, real output, failures included verbatim.
- **NOT DONE / ASSUMED / UNVERIFIED** — three lists, none omitted. Every write
  path not actually exercised belongs in UNVERIFIED.
- **Needs human browser verification** — in particular, whether a written
  document actually appears in the OpenEMR UI where a clinician would look for
  it, and under whose name.
- **Conflicts** — anything contradicting `docs/w2/DECISIONS.md` or a `CLAUDE.md`
  invariant.

## Stop point

STOP after `/report`. Do not merge. Do not deploy. Do not implement the chosen
path. Do not append to `DECISIONS.md` — propose the D1 body and leave it for a
human to append.
