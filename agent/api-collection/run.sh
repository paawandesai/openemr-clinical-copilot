#!/usr/bin/env bash
# Runs the Week 2 core-flow collection with newman against the local stack.
#
#   FHIR_CLIENT_ID=... FHIR_CLIENT_SECRET=... agent/api-collection/run.sh
#
# The client must be an OpenEMR API client with the password grant and the
# scopes in local.postman_environment.json's token_scope (README.md, "One-time
# setup"). Nothing secret is read from, or written to, a committed file: the
# id and secret come from the environment and go to newman as --env-var.
# Extra arguments pass through to newman (e.g. --folder "1. Health").
set -euo pipefail
: "${FHIR_CLIENT_ID:?set FHIR_CLIENT_ID (see README.md)}"
: "${FHIR_CLIENT_SECRET:?set FHIR_CLIENT_SECRET (see README.md)}"
HERE="$(cd "$(dirname "$0")" && pwd)"
exec npx --yes newman@6 run "$HERE/w2-core-flow.postman_collection.json" \
  --environment "$HERE/local.postman_environment.json" \
  --env-var "client_id=$FHIR_CLIENT_ID" \
  --env-var "client_secret=$FHIR_CLIENT_SECRET" \
  --working-dir "$HERE" \
  --insecure \
  "$@"
