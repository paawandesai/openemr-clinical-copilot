#!/usr/bin/env bash
#
# Every UUID-shaped value in the production Langfuse's stores, and where it
# sits. Run on the Langfuse host from /opt/langfuse:
#
#   ./scan-uuids.sh [environment]      # e.g. production, local-proof; default: all
#
# Prints TSV: store, location, uuid. One row per distinct (location, uuid).
#
#   clickhouse  <table>.<column> or <table>.metadata.<key>, for every row of
#               the span tables (events_full, events_core), and every row of
#               every other table, rendered whole (formatRow) so no column
#               is missed
#   minio       events/  the raw ingested event blobs, as Langfuse stored them
#
# It classifies nothing. Deciding which UUIDs are patient-linked needs the
# EHR: docs/w2/OBSERVABILITY.md §7 checks every one against OpenEMR's
# uuid_registry, the table that records every UUID OpenEMR has issued.

set -euo pipefail

env_filter="${1:-}"
[[ -z "$env_filter" || "$env_filter" =~ ^[a-z0-9-]+$ ]] || { echo "bad environment" >&2; exit 2; }

compose=(docker compose --project-directory "$(pwd)"
    -f docker-compose.langfuse.yml -f docker-compose.langfuse.prod.yml)

R="'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'"
where_env=""
[[ -n "$env_filter" ]] && where_env="WHERE environment = '${env_filter}'"

ch() {
    "${compose[@]}" exec -T clickhouse sh -c \
        'clickhouse-client --user clickhouse --password "$CLICKHOUSE_PASSWORD" --multiquery'
}

wanted="'name','trace_name','session_id','user_id','status_message','input','output',
'blob_storage_file_path','version','release','tags','tool_calls','tool_call_names'"

for table in events_full events_core; do
    # Only the columns this table has; a query error below then means a real failure.
    columns="$(ch <<<"SELECT name FROM system.columns WHERE database = currentDatabase()
        AND table = '${table}' AND name IN (${wanted}) FORMAT TSV")"
    for column in $columns; do
        ch <<<"SELECT DISTINCT 'clickhouse', '${table}.${column}', lower(u) FROM ${table}
            ARRAY JOIN extractAll(toString(${column}), ${R}) AS u ${where_env} FORMAT TSV"
    done
    ch <<<"SELECT DISTINCT 'clickhouse', '${table}.metadata.' || k, lower(u) FROM ${table}
        ARRAY JOIN metadata_names AS k, metadata_values AS v
        ARRAY JOIN extractAll(v, ${R}) AS u ${where_env} FORMAT TSV"
done
for table in scores traces observations observations_batch_staging blob_storage_file_log dataset_run_items_rmt; do
    filter="$where_env"
    [[ "$table" == blob_storage_file_log || "$table" == dataset_run_items_rmt ]] && filter=""
    ch <<<"SELECT DISTINCT 'clickhouse', '${table}.*', lower(u) FROM ${table}
        ARRAY JOIN extractAll(formatRow('JSONEachRow', *), ${R}) AS u ${filter} FORMAT TSV"
done

# The MinIO image has no grep: the blobs stream out, and are matched here.
"${compose[@]}" exec -T minio sh -ec '
    mc alias set scan http://localhost:9000 minio "$MINIO_ROOT_PASSWORD" >/dev/null
    for object in $(mc find scan/langfuse/events/ --name "*.json"); do
        mc cat "$object"
        echo
    done
    mc alias rm scan >/dev/null' \
    | { grep -oiE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' || true; } \
    | tr 'A-F' 'a-f' | sort -u | sed 's#^#minio\tevents/*.json\t#'
