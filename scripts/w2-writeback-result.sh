#!/usr/bin/env bash
#
# Run the delegated write-back suite locally and write the result artifact the
# eval gate scores W2-K4 and W2-K6 against.
#
# In CI the `w2-writeback-php` job writes this file itself and the gate job
# consumes it as a dependency. Locally there is no artifact, so both cases are
# reported UNSCORED and the gate exits 3 (INCOMPLETE) — which also means
# `--seed-baseline` refuses to run, because a baseline seeded from a run that
# scored nothing for those cases would be recording an absence as a result.
#
# This script closes that gap honestly: it actually runs the 143-test PHPUnit
# suite in the same image CI uses, and records what really happened for the
# commit you are on. It is not a way to hand the gate a green — a failing suite
# writes status: failed and the gate blocks.
#
# The artifact is gitignored on purpose. It is evidence about one commit, and
# committing one would let a stale green travel with the branch.
set -euo pipefail

cd "$(dirname "$0")/.."
root="$PWD"
commit="$(git rev-parse HEAD)"
suite="interface/modules/custom_modules/oe-module-clinical-copilot/phpunit.xml"

if [ ! -d vendor ]; then
  echo "vendor/ is missing — run: docker run --rm -v \"\$PWD\":/app -w /app composer:2 \\" >&2
  echo "  composer install --ignore-platform-reqs --no-scripts" >&2
  exit 2
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "WARNING: the tree is dirty. The artifact will name $commit, but that is" >&2
  echo "not what is about to be tested. Commit first if you mean to seed a baseline." >&2
fi

set +e
docker run --rm -v "$root":/app -w /app php:8.3-cli \
  vendor/bin/phpunit -c "$suite" --log-junit /app/w2-writeback-php.xml
rc=$?
set -e

if [ "$rc" -eq 0 ]; then status=success; else status=failed; fi
printf '{"schema":"w2-gate/delegated-result/1.0.0","job":"w2-writeback-php","commit":"%s","pipeline_id":"local","job_id":"local","job_url":"(local run of %s)","exit_code":%d,"status":"%s","junit":"w2-writeback-php.xml"}\n' \
  "$commit" "$(basename "$0")" "$rc" "$status" > "$root/w2-writeback-php.result.json"

cat "$root/w2-writeback-php.result.json"
exit "$rc"
