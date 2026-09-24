#!/usr/bin/env bash
#
# Trace retention for the production Langfuse: delete everything older than
# LANGFUSE_RETENTION_DAYS (in .env; 30, reasoning in D58). Run nightly from
# cron on the Langfuse host (install-host.sh installs
# /etc/cron.d/langfuse-retention), from /opt/langfuse.
#
# Why a script: Langfuse's own retention job is an Enterprise feature on
# self-hosted instances; OSS Langfuse keeps data forever. So this deletes
# from the stores it writes, by the same time columns its job would use:
#
#   ClickHouse  events_core, events_full (v4's spans: start_time), traces,
#               observations, observations_batch_staging (timestamp /
#               start_time), scores (timestamp), blob_storage_file_log
#               (created_at)
#   MinIO       the raw ingested event blobs under events/: a lifecycle rule
#               that expires them after the same number of days, set here
#               on every run so it cannot drift from .env
#
# Postgres holds no trace data (users, projects, keys, dashboards).

set -euo pipefail

compose=(docker compose --project-directory "$(pwd)"
    -f docker-compose.langfuse.yml -f docker-compose.langfuse.prod.yml)

days="$(sed -n 's/^LANGFUSE_RETENTION_DAYS=//p' .env)"
[[ "$days" =~ ^[0-9]+$ && "$days" -ge 3 ]] || { echo "LANGFUSE_RETENTION_DAYS must be >= 3" >&2; exit 2; }

tables=(
    "events_core start_time"
    "events_full start_time"
    "traces timestamp"
    "observations start_time"
    "observations_batch_staging start_time"
    "scores timestamp"
    "blob_storage_file_log created_at"
)

for entry in "${tables[@]}"; do
    read -r table column <<<"$entry"
    "${compose[@]}" exec -T clickhouse sh -c \
        "clickhouse-client --user clickhouse --password \"\$CLICKHOUSE_PASSWORD\" \
         --mutations_sync 1 \
         -q \"ALTER TABLE ${table} DELETE WHERE ${column} < now() - INTERVAL ${days} DAY\""
done

"${compose[@]}" exec -T minio sh -ec "
    mc alias set retention http://localhost:9000 minio \"\$MINIO_ROOT_PASSWORD\" >/dev/null
    mc ilm rule rm --all --force retention/langfuse >/dev/null 2>&1 || true
    mc ilm rule add --expire-days ${days} retention/langfuse >/dev/null
    mc alias rm retention >/dev/null"

echo "$(date -u +%FT%TZ) retention: ${days} days applied to ${#tables[@]} ClickHouse tables and the MinIO bucket"
