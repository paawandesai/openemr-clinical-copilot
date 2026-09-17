# Deployment — agent service on the OpenEMR host

The agent runs on the same host as OpenEMR, a Vultr instance at
`64.177.42.210`, as its own Compose project
(`agent/deploy/docker-compose.agent.yml`). OpenEMR's
`docker/development-easy` stack is not modified.

The deployment moved from a DigitalOcean droplet (`201.79.25.22`) to Vultr
late in the build. No BAA covers the Vultr host. That is acceptable only
because all data is synthetic (`docs/ARCHITECTURE.md` §1,
`docs/audit/findings.md` F-61). The network checks in §2 were run on the
DigitalOcean droplet before the move.

```
internet ──TLS──▶ 0.0.0.0:8443  proxy (Caddy)        ┐ compose project
                                   │ agent:8000      │ clinical-copilot-agent,
                                   ▼                 │ its own network
                                 agent (FastAPI)     ┘
                                   │ https://host.docker.internal:9300  (Docker host gateway)
                                   ▼
                  0.0.0.0:9300 ─▶ openemr   ┐ compose project development-easy,
                127.0.0.1:18320 ─▶ mysql     │ network development-easy_default
                127.0.0.1:18310 ─▶ phpmyadmin┘ (unchanged)
```

## 1. Port binding (the F-54 decision)

**Exactly one new public port: `0.0.0.0:8443/tcp`, TLS, served by the proxy.**
The agent's own port 8000 is not published at all; only the proxy can reach
it, over the agent project's network. IPv4 only. Docker-published ports
bypass UFW (F-33, F-54), so 8443 is reachable from the internet whatever UFW
says, and that is intended here.

Why public: the API collection, the eval suite and load tests call the agent
from outside the host.

Why that is acceptable:

- Every request that touches chart data needs a clinician token OpenEMR
  accepts. No token, or one that is not a JWT, is refused with 401 before any
  FHIR call. A token OpenEMR rejects fails the first FHIR read, before
  anything is retrieved and before the LLM is called.
- TLS, because the bearer token and the returned brief would otherwise cross
  the internet in cleartext. Plain HTTP to 8443 is refused (400).
- `/health` and `/ready` are unauthenticated and return fixed strings only
  (F-37). `/ready`'s FHIR and LLM probes are cached for 15 s, so polling it
  cannot turn into load on OpenEMR or calls against the API key.
- The proxy caps request bodies at 16 KB, has no admin endpoint
  (`admin off`), and writes no access log.
- Both containers drop all Linux capabilities (the proxy keeps only
  `NET_BIND_SERVICE`, which its binary needs to start), set
  `no-new-privileges`, and run with read-only root filesystems. The agent
  runs as an unprivileged user.

Gaps, stated rather than assumed closed:

- **Certificate not publicly trusted.** It comes from Caddy's local CA, since
  the host has an IP address and no domain name. Clients either trust the
  exported root (§5) or skip verification (`curl -k`, `newman --insecure`).
  Encrypted, but a client that skips verification is not protected against
  interception. Closing it needs a domain name and a public certificate.
- **No rate limiting.** Anyone holding a valid clinician token can call
  `/pre-visit-brief` repeatedly, and each call is an LLM request.
- **`/docs` and `/openapi.json` are public.** They describe the contract;
  they expose no data.
- **The agent → OpenEMR hop skips certificate verification.** OpenEMR serves
  a self-signed `CN=localhost` certificate (F-36), so the client's documented
  temporary exception (`FHIR_VERIFY_TLS=false`) applies. The hop never leaves
  the host. The fix is pinning OpenEMR's certificate.

**Check the actual bindings after every deploy (§4), never from this file.**

## 2. How the agent reaches OpenEMR

`FHIR_BASE_URL=https://host.docker.internal:9300/apis/default/fhir`: OpenEMR's
published HTTPS port, reached through the Docker host gateway
(`extra_hosts: host.docker.internal:host-gateway`).

The agent does **not** join OpenEMR's `development-easy_default` network,
though addressing `openemr` by service name there would be simpler. MySQL is
on that network with default `root`/`root` credentials (F-34), and a
container on it reaches `mysql:3306` directly. The agent never needs the
database (ARCHITECTURE.md §2), so on its own network it has no route to it.

Checked on the DigitalOcean droplet before the move to Vultr (2026-09-16,
throwaway containers, no credentials sent):

| From | Target | Result |
|---|---|---|
| `development-easy_default` | `http://openemr/apis/default/fhir/metadata` | 200 |
| `development-easy_default` | `mysql:3306` | TCP connection established |
| separate network + host gateway | `https://host.docker.internal:9300/apis/default/fhir/metadata` | 200 |
| separate network + host gateway | `host.docker.internal:18320` (MySQL, loopback-bound) | timed out |
| separate network | `mysql:3306` | timed out |

## 3. Secrets

The agent's only credential is `ANTHROPIC_API_KEY`. It holds no FHIR
credentials: each request brings the clinician's token. The OAuth
`client_id`/`client_secret` are needed only by whoever obtains a token (the
API collection, the smoke tests), never by the agent.

- Not in the image: supplied at runtime from an env file, and
  `agent/.dockerignore` keeps `.env` files out of the build context.
- On the host the env file is `/etc/clinical-copilot/agent.env` (mode 600,
  format: `agent/deploy/.env.example`), **not** inside a code checkout. The
  OpenEMR checkout `/home/deploy/openemr-clinical-copilot` is bind-mounted
  read-write into Apache's document root, so a file placed there may be
  web-served. The agent's code is deployed to `/opt/clinical-copilot`
  instead.
- `docker compose config` prints resolved environment values, including the
  key. Use the filtered command in §4.

## 4. Deploy (operator commands)

From the repository root on the operator's laptop — ship the committed
`agent/` tree (tracked files only: no `.env`, logs or virtualenv):

```bash
DEPLOY_HOST=root@64.177.42.210
COMMIT=$(git rev-parse --short HEAD)
ssh "$DEPLOY_HOST" 'rm -rf /opt/clinical-copilot/agent && mkdir -p /opt/clinical-copilot'
git archive "$COMMIT" agent | ssh "$DEPLOY_HOST" 'tar -x -C /opt/clinical-copilot'
ssh "$DEPLOY_HOST" "echo $COMMIT > /opt/clinical-copilot/DEPLOYED_COMMIT"
```

First deploy only — on the host, create the env file without the key
reaching shell history or the screen:

```bash
install -d -m 700 /etc/clinical-copilot
read -rsp 'ANTHROPIC_API_KEY: ' KEY; echo
( umask 077; printf 'ANTHROPIC_API_KEY=%s\nAGENT_PUBLIC_HOST=64.177.42.210\n' "$KEY" > /etc/clinical-copilot/agent.env )
unset KEY
```

Start (and, after later deploys, rebuild) — on the host:

```bash
cd /opt/clinical-copilot/agent/deploy
docker compose --env-file /etc/clinical-copilot/agent.env -f docker-compose.agent.yml up -d --build --wait
```

Verify the bindings — on the host:

```bash
ss -tlnp | grep -v sshd
docker compose --env-file /etc/clinical-copilot/agent.env -f docker-compose.agent.yml ps
docker compose --env-file /etc/clinical-copilot/agent.env -f docker-compose.agent.yml config --format json \
  | python3 -c 'import json,sys; [print(n, s.get("ports")) for n, s in json.load(sys.stdin)["services"].items()]'
```

Expected: a new `0.0.0.0:8443` listener and nothing else new. `8300`/`9300`
stay public. `18310`/`18320` stay `127.0.0.1`. No listener on 8000.

Then, from a machine **outside** the host:

```bash
for port in 8443 8000 18310 18320 3306 2019; do nc -z -w 3 64.177.42.210 $port && echo "$port open" || echo "$port closed"; done
curl -k https://64.177.42.210:8443/health
curl -k https://64.177.42.210:8443/ready
```

Expected: only 8443 open among these. Then run the API collection
(`agent/api-collection/README.md`) for a real brief.

Stop: `docker compose --env-file /etc/clinical-copilot/agent.env -f docker-compose.agent.yml down`
(OpenEMR is unaffected).

## 5. Operating

**Trusting the certificate instead of `-k`:**

```bash
ssh root@64.177.42.210 'cd /opt/clinical-copilot/agent/deploy && docker compose --env-file /etc/clinical-copilot/agent.env -f docker-compose.agent.yml exec -T proxy cat /data/caddy/pki/authorities/local/root.crt' > agent-root.crt
curl --cacert agent-root.crt https://64.177.42.210:8443/health
```

The root persists in the `caddy_data` volume, so it survives restarts.

**Metrics (KEY_METRICS.md) from the live service:** the agent logs to stdout,
which Docker keeps (10 MB × 5 files). The log lines carry
`patient_session_key` and FHIR source ids, so they stay on the host or the
operator's machine:

```bash
ssh root@64.177.42.210 'docker logs clinical-copilot-agent-agent-1 2>&1' \
  | python agent/scripts/compute_metrics.py --log-file -
```

Uvicorn's access-log lines are not JSON and are skipped by the script.
