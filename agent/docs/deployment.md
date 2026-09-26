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
- `AGENT_TELEMETRY_HMAC_SECRET` (D56) is the key telemetry's
  `patient_session_key` is HMAC'd under, so no log line or Langfuse event
  carries a patient uuid. The image runs with `AGENT_ENV=production`, and
  in production the agent **refuses to start** without it (at least 32
  characters, not the dev default): the container exits and
  `up --wait` fails. Keep it stable: a new value starts new trace sessions.
  Before deploying D56 or later to a host whose env file predates it, add
  it (§4).
- `AGENT_EXTRACTION_STORE_KEY` (D60) is the AES-256-GCM key uploaded
  documents' extractions are sealed under in
  `/var/lib/clinical-copilot/extractions.sqlite3`, on the `extraction_data`
  volume. It is in the env file, not on the volume, so a copy of the volume
  alone is ciphertext. In production the agent **refuses to start** without
  it (64 hex characters, not the dev default). Keep it stable: a new key
  makes every saved extraction unreadable (logged as
  `extraction_store_row_unreadable`), and those documents must be uploaded
  again. Before deploying D60 or later, add it (§4).

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

On any host whose env file has no telemetry secret yet, before the first
deploy of D56 or later, on the host (appends a fresh 64-character secret;
run it once, since a new value splits trace sessions):

```bash
grep -q '^AGENT_TELEMETRY_HMAC_SECRET=.' /etc/clinical-copilot/agent.env \
  || ( umask 077; printf 'AGENT_TELEMETRY_HMAC_SECRET=%s\n' "$(python3 -c 'import secrets; print(secrets.token_hex(32))')" >> /etc/clinical-copilot/agent.env )
```

Likewise the extraction store's key, before the first deploy of D60 or
later (run it once: a new key makes every saved extraction unreadable):

```bash
grep -q '^AGENT_EXTRACTION_STORE_KEY=.' /etc/clinical-copilot/agent.env \
  || ( umask 077; printf 'AGENT_EXTRACTION_STORE_KEY=%s\n' "$(python3 -c 'import secrets; print(secrets.token_hex(32))')" >> /etc/clinical-copilot/agent.env )
```

The compose file creates the `extraction_data` volume on the first `up`. Do
not `docker compose down -v`: that deletes it, and every upload must then be
extracted again.

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
which Docker keeps (10 MB × 5 files). The log lines carry FHIR source ids,
and the `patient_session_key` only as its HMAC (D56). Uvicorn's access
lines still print `?patient_id=` for the document routes. So the logs stay
on the host or the operator's machine:

```bash
ssh root@64.177.42.210 'docker logs clinical-copilot-agent-agent-1 2>&1' \
  | python agent/scripts/compute_metrics.py --log-file -
```

Uvicorn's access-log lines are not JSON and are skipped by the script.

## 6. The multi-turn conversation: built, not deployed

`POST /pre-visit-brief/conversation` and the chat UI in `deploy/ui/` are on
the `feat/multiturn-chat` branch and are **not deployed**; the running host
serves the single-turn follow-up only. Nothing in this file's deploy steps
changes when it is deployed, and nothing should be assumed about it until it
is. What deploying it involves:

- **No proxy or port change.** Both routes are under `/pre-visit-brief*`,
  which the Caddyfile already sends to the agent, and the UI is the same
  static directory, so it stays same-origin with no new public port (F-54).
- **One worker, or Redis first.** Conversation state is held in process
  memory (`app/conversation/state.py`). That is correct for the single
  uvicorn worker the Dockerfile runs. With more than one worker or replica,
  turns of one conversation would land on processes that do not share it;
  that fails safe (a reference is reported unresolved, never resolved
  against another patient) but it does not work. Move the store to Redis,
  keeping the key, the patient binding checked on every read and the TTLs,
  before scaling out. A restart or redeploy ends every conversation.
- **Settings.** `AGENT_CONVERSATION_TTL_SECONDS` (idle, default 1800) and
  `AGENT_CONVERSATION_MAX_AGE_SECONDS` (default 3600, the life of an OpenEMR
  access token). The compose file does not pass them; the defaults apply.
- **Token scopes.** A turn that asks about lab results or visits reads
  `Observation` or `Encounter`, so the clinician's token needs
  `user/Observation.read` and `user/Encounter.read` in addition to the
  brief's scopes, and the OAuth client must be registered for them. Without
  them those turns answer 401 `fhir_token_rejected`; turns that stay within
  the brief's three record types are unaffected.
- **Memory holds chart text.** Stored claims carry record names and values
  (PHI) in process memory until the TTL, a patient switch or
  `POST /pre-visit-brief/conversation/end`. They are never logged or written
  to disk.
- **Re-run before relying on it:** `pytest tests/` and
  `pytest evals/test_i_conversation.py` against the host's FHIR API
  (`evals/README.md`, section I).

## 7. Week 2 (`POST /w2/ask`, `/documents`): run exactly one worker

**What a second process breaks.** Three stores live in the agent process's
memory: the conversation store (shared by
`/pre-visit-brief/conversation` and `/w2/ask`), the uploaded-document store
with its extraction jobs (`app/workers/document_store.py`), and the
retriever's rerank circuit. Since D60 the document store also saves every
upload and finished extraction to disk, so a *restart* forgets none of them
(below); a job that is *running* is still one process's memory. With two or
more uvicorn workers, or two replicas behind Caddy:

- a document uploaded to process A is unknown to process B — B answers the
  browser's status poll `404`, and a question naming it `document_not_found`;
- the extraction runs on A only, and a re-upload that lands on B runs it
  again (a second vision call, a second bill);
- a follow-up that refers to an earlier turn ("that result") is reported
  unresolved when it lands on the other process.

Each of these fails safe — every process checks its own patient binding, so
none answers from another patient's document or context — but the features
do not work.

**A restart or redeploy (D60).** Uploads and their extractions survive: the
same document answers the same way, stays bound to its chart, and the same
bytes for another chart are still `409`, with no second vision call. An
extraction that was running when the process stopped reports `failed` /
`interrupted`; uploading the file again starts it afresh. A lab report's
write-back that did not complete is retried by uploading the same file to
the same chart. Saved rows are deleted 30 days after their last write
(`AGENT_EXTRACTION_STORE_RETENTION_DAYS`); an expired document is uploaded
again. Conversations are still forgotten: a follow-up after a restart starts
a new conversation.

**For Wednesday this is acceptable because the deploy runs one worker**: the
`Dockerfile` `CMD` starts a single `uvicorn` process and the compose file
runs one replica. Keep it that way until the stores move out of process
(Redis for conversations; a persisted document/extraction store). The agent
checks at startup: if `--workers`/`-w` on the command line, `GUNICORN_CMD_ARGS`
or `WEB_CONCURRENCY` configures more than one worker, every worker logs a
`single_process_state_warning` at warning level (`app/process_state.py`).
It does not refuse to start — the failure is a broken feature, not a leak —
so check for that line after any change to how the agent is launched:

```bash
# on the host, in /opt/clinical-copilot/agent/deploy
docker compose --env-file /etc/clinical-copilot/agent.env -f docker-compose.agent.yml logs agent \
  | grep single_process_state_warning   # expect no output
```

A second *replica* (a second container) is not visible to this check; only
the compose file says how many run.
