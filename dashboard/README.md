# Patient dashboard (React port over FHIR)

The OpenEMR patient dashboard as a React + TypeScript SPA, served by a small
same-origin BFF (Hono on Node) that signs the clinician in with OpenEMR's
OAuth2/OIDC and holds the tokens, so the browser never does. Read-only; it
changes nothing in OpenEMR.

**Why it is built this way, the parity matrix, the tradeoffs, and how to
deploy it:** [`../PATIENT_DASHBOARD_MIGRATION.md`](../PATIENT_DASHBOARD_MIGRATION.md).
Decision: `docs/w2/DECISIONS.md` D59.

```bash
npm ci
npm run typecheck        # strict TypeScript, SPA and BFF
npm test                 # unit tests, no network, no credentials
npm run build            # dist/web + dist/bff
scripts/run-local.sh     # http://localhost:8787 against the LOCAL OpenEMR stack
npm run test:e2e         # real OAuth + FHIR + the PHP page, local stack only
```

`scripts/run-local.sh` and `scripts/e2e-local.sh` read the local OAuth client
and OpenEMR's certificate from `~/.config/clinical-copilot/` (migration doc
§7 shows how to create them). They set every OpenEMR URL explicitly to the
local stack. Nothing reads the shell's `FHIR_*` variables, which point at the
deployed host.

| | |
|---|---|
| `web/src/` | the SPA: `components/`, `mapping/` (FHIR to view models), `fhir/types.ts`, `api.ts` |
| `bff/src/` | the BFF: `app.ts`, `oauth.ts`, `fhirProxy.ts` (the allowlist), `sessions.ts`, `log.ts`, `upstream.ts`, `static.ts` |
| `test/` | `unit/` (Vitest), `e2e/` (Playwright, installed Chrome), `fixtures/` (captured from the local API; synthetic patients, SSN replaced) |
| `Dockerfile`, `deploy/` | the container and the proposed host compose file (not deployed) |
