#!/usr/bin/env bash
# Runs the Playwright suite against the LOCAL OpenEMR dev stack.
# The test process itself needs the client and OpenEMR's local certificate
# for the live refresh check (test/e2e/refresh.spec.ts); the BFF gets the
# same values from scripts/run-local.sh.
set -euo pipefail
cd "$(dirname "$0")/.."
CFG="${CFG:-$HOME/.config/clinical-copilot}"
export NODE_EXTRA_CA_CERTS="$CFG/openemr-local-ca.pem"
export E2E_CLIENT_ID="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['client_id'])" "$CFG/dashboard-bff-local.reg.json")"
export E2E_CLIENT_SECRET_FILE="$CFG/dashboard-bff-local.secret"
exec npx playwright test "$@"
