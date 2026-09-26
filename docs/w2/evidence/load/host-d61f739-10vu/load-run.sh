#!/usr/bin/env bash
# 10-VU load on the live agent (main@d61f739) through its proxy, with a memory
# watchdog: k6 is killed if the agent passes 80% of its 4608 MiB cap.
#   load-run.sh <out dir> <token file>
set -uo pipefail
OUT="$1"; TOKEN_FILE="$2"
HOST=root@64.177.42.210
REPO="/Users/paawandesai/Documents/Work/Gauntlet AI/Week 1/agentForge/cc-feat-w2-prod-langfuse"
LIMIT_MIB=3686   # 0.8 * 4608
mkdir -p "$OUT"

to_mib() {  # "1.23GiB" / "812.4MiB" -> MiB (integer)
    python3 -c "import re,sys; v,u=re.match(r'([0-9.]+)([KMG]i?B)',sys.argv[1]).groups(); print(int(float(v)*{'KiB':1/1024,'MiB':1,'GiB':1024,'KB':1/1024,'MB':1,'GB':1024}[u]))" "$1"
}

echo "ts,agent_mib,agent_cpu,openemr_mib,openemr_cpu,mysql_mib,mysql_cpu" > "$OUT/resources.csv"
(
    while true; do
        line=$(ssh -o ConnectTimeout=5 "$HOST" 'docker stats --no-stream --format "{{.Name}} {{.MemUsage}} {{.CPUPerc}}" clinical-copilot-agent-agent-1 development-easy-openemr-1 development-easy-mysql-1' 2>/dev/null) || { sleep 5; continue; }
        a=$(awk '/agent-agent/{print $2}' <<<"$line"); ac=$(awk '/agent-agent/{print $NF}' <<<"$line")
        o=$(awk '/openemr-1/{print $2}' <<<"$line"); oc=$(awk '/openemr-1/{print $NF}' <<<"$line")
        m=$(awk '/mysql-1/{print $2}' <<<"$line"); mc=$(awk '/mysql-1/{print $NF}' <<<"$line")
        am=$(to_mib "$a")
        echo "$(date -u +%H:%M:%S),$am,$ac,$(to_mib "$o"),$oc,$(to_mib "$m"),$mc" >> "$OUT/resources.csv"
        if [ "$am" -gt "$LIMIT_MIB" ]; then
            echo "WATCHDOG: agent at ${am} MiB > ${LIMIT_MIB} MiB, stopping k6" | tee "$OUT/watchdog.txt"
            pkill -INT -f "k6 run .*k6-ask.js"
        fi
        sleep 5
    done
) &
SAMPLER=$!
trap 'kill $SAMPLER 2>/dev/null' EXIT

sleep 6   # an at-rest sample first
date -u +%FT%TZ > "$OUT/start.txt"
AGENT_BASE_URL=https://64.177.42.210:8443 AGENT_TOKEN="$(cat "$TOKEN_FILE")" \
PATIENT_ID=a2c373ec-57bf-41cb-8767-94410dcf663d ROUTE=conversation \
QUESTION="What medications is the patient on?" VUS=10 DURATION=3m \
    k6 run --quiet --summary-export="$OUT/k6-summary.json" "$REPO/agent/load-test/w2/k6-ask.js" > "$OUT/k6.txt" 2>&1
echo "k6 exit $?" >> "$OUT/k6.txt"
date -u +%FT%TZ > "$OUT/end.txt"
sleep 10  # drain samples
