#!/usr/bin/env bash
# Mints one OpenEMR clinician access token (password grant, ARCHITECTURE.md
# §2) and prints only the access_token to stdout — everything else (scope,
# expiry, id_token) goes to stderr. Same grant/scopes as
# scripts/smoke_test_fhir.py; see that file's docstring for the one-time
# oauth_clients provisioning this depends on (findings.md F-02).
#
# Required env: FHIR_CLIENT_ID, FHIR_CLIENT_SECRET
# Optional env: FHIR_TEST_USERNAME (default: physician)
#               FHIR_TEST_PASSWORD (default: physician)
#               FHIR_TOKEN_URL     (default: https://201.79.25.22:9300/oauth2/default/token)
#
# Token expires in 1 hour (OpenEMR default). Re-run this before a run that
# might outlast a previously minted token.
set -euo pipefail

: "${FHIR_CLIENT_ID:?set FHIR_CLIENT_ID}"
: "${FHIR_CLIENT_SECRET:?set FHIR_CLIENT_SECRET}"
USERNAME="${FHIR_TEST_USERNAME:-physician}"
PASSWORD="${FHIR_TEST_PASSWORD:-physician}"
TOKEN_URL="${FHIR_TOKEN_URL:-https://201.79.25.22:9300/oauth2/default/token}"

RESPONSE=$(curl -sk "$TOKEN_URL" \
  --data-urlencode grant_type=password \
  --data-urlencode client_id="$FHIR_CLIENT_ID" \
  --data-urlencode client_secret="$FHIR_CLIENT_SECRET" \
  --data-urlencode username="$USERNAME" \
  --data-urlencode password="$PASSWORD" \
  --data-urlencode user_role=users \
  --data-urlencode scope="openid user/Patient.read user/Condition.read user/MedicationRequest.read user/AllergyIntolerance.read")

python3 -c '
import json, sys
data = json.loads(sys.argv[1])
if "access_token" not in data:
    print(json.dumps(data), file=sys.stderr)
    sys.exit(1)
expires_in = data.get("expires_in")
scope = data.get("scope")
print("expires_in={}s scope={!r}".format(expires_in, scope), file=sys.stderr)
print(data["access_token"])
' "$RESPONSE"
