#!/usr/bin/env bash
# Seed the Week 3 test users, panels and canary patients into a running
# OpenEMR container (scripts/w3/seed-target.php; docs/w3-target-hooks.md).
# DEV AND TEST INSTANCES ONLY.
#
#   scripts/w3/seed-target.sh [--json]
#
# OPENEMR_CONTAINER picks the container (default development-easy-openemr-1).
# W3_{PHYSICIAN,NURSE,RESIDENT}_PASSWORD override the demo passwords; they are
# passed through the environment, never on a command line.
set -euo pipefail

container="${OPENEMR_CONTAINER:-development-easy-openemr-1}"
webroot="${OPENEMR_WEBROOT:-/var/www/localhost/htdocs/openemr}"

exec docker exec -u apache \
  -e W3_PHYSICIAN_PASSWORD -e W3_NURSE_PASSWORD -e W3_RESIDENT_PASSWORD \
  "$container" php "$webroot/scripts/w3/seed-target.php" "$@"
