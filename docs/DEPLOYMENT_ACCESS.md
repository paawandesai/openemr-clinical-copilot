# Deployment access

How to reach the deployed system. Both services run on one Vultr host,
`64.177.42.210`.

| What | URL | How to use it |
|---|---|---|
| **Agent API** (FastAPI behind Caddy) | <https://64.177.42.210:8443> | `curl -k`; liveness and readiness below |
| **OpenEMR EHR** | <https://64.177.42.210:9300> | browser login; click through the certificate warning |

## TLS is self-signed

The host is a bare IP address with no domain name, so no public CA can issue
it a certificate. Neither certificate is publicly trusted (the agent's comes
from Caddy's internal CA, OpenEMR's is self-signed):

- **curl:** pass `-k`.
- **Browser:** accept the warning once per port (Advanced → Proceed).

## Agent API — `:8443`

```bash
curl -k https://64.177.42.210:8443/health
# 200 {"status":"alive"}: the process is up, nothing more

curl -k -o /dev/null -w '%{http_code}\n' https://64.177.42.210:8443/ready
# 200: it can answer right now (OpenEMR's FHIR API and the model provider are both up)
# 503: a required dependency is down; the body names which
```

`/ready` answers 200 with `"status": "ready"` or `"status": "degraded"`.
Degraded means only the trace export is down; answers still work.

The agent is an API, not a web application: its endpoints are listed at
`/docs` (OpenAPI), and every data-bearing one needs a clinician's OpenEMR
OAuth2 token. The root path `/` serves a single static page, the standalone
Pre-Visit Brief form, which calls that same API; there is nothing else to
browse. The co-pilot is used from inside OpenEMR.

## OpenEMR EHR — `:9300`

Open <https://64.177.42.210:9300> in a browser, accept the certificate warning,
and sign in. The demo login and the other grader-facing URLs (patient
dashboard, Langfuse) are in the [README](../README.md#grader-access).

## Availability

The agent and its proxy are their own Compose project
([`agent/deploy/docker-compose.agent.yml`](../agent/deploy/docker-compose.agent.yml)),
both with `restart: unless-stopped`: Docker brings them back after a crash or a
host reboot, unless they were stopped by hand. Deploy steps and the
post-deploy checks: [`agent/docs/deployment.md`](../agent/docs/deployment.md).
