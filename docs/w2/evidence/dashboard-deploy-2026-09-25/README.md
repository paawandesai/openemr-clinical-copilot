# Patient dashboard deployed on `64.177.42.210`, 2026-09-25

The WO31 port (D59): a React SPA plus the same-origin BFF, from `main@06f0d45`.
It is served at <https://64.177.42.210:8444>. It followed
`PATIENT_DASHBOARD_MIGRATION.md` §8, with one addition: audit F-49 had to be
fixed on the host first (`docs/audit/findings.md`, F-49 "Outcome").

## What is on the host

| Piece | Where |
|---|---|
| OAuth client "Patient Dashboard (BFF)" | Confidential; redirect `https://64.177.42.210:8444/auth/callback`, post-logout `…:8444/`. The migration doc's 10 scopes, `offline_access` included. Enabled by an administrator in Admin → System → API Clients. |
| Client secret | `/etc/patient-dashboard/client_secret`, uid 1000, mode 600, mounted as a Docker secret. Never an environment variable, never printed. |
| OpenEMR certificate | `/etc/patient-dashboard/openemr-ca.pem`, self-signed `CN=localhost`. The BFF verifies it with servername `localhost`, so TLS verification stays on. |
| Settings | `/etc/patient-dashboard/dashboard.env`, mode 600, no secrets |
| Container | `patient-dashboard-bff-1` from `/opt/patient-dashboard/dashboard` (`06f0d45`): no published port, `bff:8787` on `clinical-copilot-agent_default`, 256 MiB cap (20.8 MiB used), read-only, `cap_drop: ALL` |
| Proxy | A `https://{$AGENT_PUBLIC_HOST}:8444` site in `agent/deploy/Caddyfile` (`tls internal`, 16 KB body cap, `reverse_proxy bff:8787`), and `8444` published on the proxy service. Branch `chore/w2-dashboard-deploy`. |
| Firewall | `ufw allow 8444/tcp`. Docker's published ports bypass ufw (F-54), so the rule records the exposure; it does not create it. |
| F-49 | `site_addr_oath` = `https://64.177.42.210:9300`, set in `globals` and in a host-only compose override |

- **Proxy recreate:** the Caddy CA fingerprint is unchanged (`C5:54:80:…`).
- **Listening sockets:** the only change from before is `0.0.0.0:8444`,
  served by Caddy. Nothing listens on 8787 outside Docker.
- **Not measured:** how long 8443 was interrupted during the proxy recreate.
  The poll loop meant to measure it exited on its first successful poll.

## Verified in Chrome as `physician`, after the F-49 change

**Sign-in:** the real OAuth flow completes. Login and consent are served from
`https://64.177.42.210:9300`, and the callback lands on `:8444`.

Every card for both patients, all without errors:

| Patient | Allergies | Problems | Medications | Prescriptions | Care Team | Lab Results |
|---|---|---|---|---|---|---|
| Merlyn225 Mueller846 (pid 38) | 3 | 13 | 4 | 11 | none recorded (0 `care_teams` rows) | 30 shown, including the D54 rows "From document" |
| Jae222 Dach178 (pid 31) | 9 | 6 | 1 | 4 | none recorded (0 rows) | 30 shown of 225 |

- **CSP violations:** 0.
- **The BFF refuses what the dashboard never needs:** a request for
  `/fhir/Encounter` is 403, and `POST /fhir/Patient` is 405.
- Screenshots: [`dashboard-pid38.png`](dashboard-pid38.png),
  [`dashboard-pid31.png`](dashboard-pid31.png).

**The chart card** (brief, ask, upload with write-back) works after the
change too. The details are in the F-49 outcome.

## Rollback

Everything is in `/root/dashboard-deploy-20260925/`:

1. **Proxy.** Copy `Caddyfile.pre` and `docker-compose.agent.yml.pre` back
   into `/opt/clinical-copilot/agent/deploy/`, then
   `up -d --no-deps --no-build --force-recreate proxy`.
2. **Firewall.** `ufw delete allow 8444/tcp`.
3. **Container.** From `/opt/patient-dashboard/dashboard/deploy`, run
   `docker compose … -f docker-compose.dashboard.yml down`.
4. **Client.** Delete the "Patient Dashboard (BFF)" client and its tokens,
   then `rm -r /etc/patient-dashboard`.
5. **F-49.** Restore `globals-site_addr_oath.pre.tsv`'s value
   (`https://localhost:9300`), and delete
   `/home/deploy/openemr-clinical-copilot/docker/development-easy/docker-compose.override.yml`.
   This breaks the dashboard's sign-in again, so do it together with 1–4.
