#!/usr/bin/env bash
# Start the built BFF against the LOCAL OpenEMR dev stack.
# The client id and secret live outside the repo, in ~/.config/clinical-copilot/.
set -euo pipefail
cd "$(dirname "$0")/.."
CFG="${CFG:-$HOME/.config/clinical-copilot}"
# The login shell exports FHIR_* for the deployed host; nothing here reads them,
# and the URLs below are the local stack's, explicitly.
export PUBLIC_ORIGIN="${PUBLIC_ORIGIN:-http://localhost:8787}"
export OPENEMR_OAUTH_BASE=https://localhost:9300/oauth2/default
export OPENEMR_FHIR_BASE=https://localhost:9300/apis/default/fhir
export OAUTH_CLIENT_ID="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['client_id'])" "$CFG/dashboard-bff-local.reg.json")"
export OAUTH_CLIENT_SECRET_FILE="$CFG/dashboard-bff-local.secret"
export NODE_EXTRA_CA_CERTS="$CFG/openemr-local-ca.pem"
export STATIC_DIR="${STATIC_DIR:-dist/web}"
exec node dist/bff/server.js
