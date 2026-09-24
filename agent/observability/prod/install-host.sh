#!/usr/bin/env bash
#
# Stands up the production Langfuse on a fresh Ubuntu 24.04 host (D58).
# Run as root ON that host, from /opt/langfuse, after copying into it:
#   ../docker-compose.langfuse.yml and everything in this directory.
#
#   ./install-host.sh <public hostname, e.g. 64-177-51-119.sslip.io>
#
# Idempotent: every step checks before it changes anything, so a re-run
# after a failure picks up where it stopped. Prints no secret.
# The runbook, with how to verify each step: docs/w2/OBSERVABILITY.md §7.

set -euo pipefail

hostname="${1:?usage: install-host.sh <public hostname>}"
cd "$(dirname "$0")"
export DEBIAN_FRONTEND=noninteractive

step() { printf '\n== %s\n' "$*"; }

step "packages"
apt-get update -q
apt-get install -y -q ufw unattended-upgrades ca-certificates curl openssl

step "firewall: deny inbound except 22, 80, 443"
# Docker's published ports bypass ufw (docs/AUDIT.md F-54), so the compose
# files publish only Caddy's 80/443; everything else is loopback or unpublished.
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 443/udp
ufw --force enable

step "unattended upgrades"
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
systemctl enable --now unattended-upgrades

step "swap (the host is below Langfuse's 16 GiB recommendation)"
if ! swapon --show | grep -q .; then
    fallocate -l 4G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile >/dev/null
    swapon /swapfile
    grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

step "docker engine + compose plugin (Docker's apt repository)"
if ! command -v docker >/dev/null; then
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    . /etc/os-release
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update -q
    apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-compose-plugin
fi
systemctl enable --now docker

step "secrets (generated here, never copied in)"
[[ -e .env ]] || ./generate-env-prod.sh "$hostname"

step "stack up"
compose=(docker compose --project-directory "$(pwd)"
    -f docker-compose.langfuse.yml -f docker-compose.langfuse.prod.yml)
"${compose[@]}" up -d --wait

step "grader viewer account"
./create-viewer.sh

step "retention: nightly at 03:17 UTC, and once now"
cat > /etc/cron.d/langfuse-retention <<EOF
17 3 * * * root cd $(pwd) && ./retention.sh >> /var/log/langfuse-retention.log 2>&1
EOF
./retention.sh

step "done"
"${compose[@]}" ps --format 'table {{.Service}}\t{{.Status}}'
