# Clinical Co-Pilot

A Clinical Co-Pilot AI agent built into a fork of [OpenEMR](https://open-emr.org)
8.2.0-dev. Upstream OpenEMR's own README is preserved as
[README-OPENEMR.md](README-OPENEMR.md).

**Repository (submission):** https://labs.gauntletai.com/paawandesai/openemr-clinical-copilot

## Deployed application

| What | URL |
|---|---|
| **OpenEMR**, with the Clinical Co-Pilot card embedded in the patient chart | https://64.177.42.210:9300 |
| **Agent API + standalone Pre-Visit Brief UI** | https://64.177.42.210:8443 |

**The certificates are self-signed.** Neither port has a certificate from a
public CA: OpenEMR's is self-signed, and the agent's comes from its proxy's
local CA. Your browser will show a warning for each port. Accept it once and
continue. With `curl`, pass `-k`.

**Demo login:** `physician` / `physician` (username as password). This is a
seeded demo-data account recorded as a security finding in
[docs/AUDIT.md](docs/AUDIT.md) (F-32); a real deployment would not ship it. An
`admin` account also exists, but its default password was rotated after the
public exposure recorded as F-31, so it is not listed here.

How to see the agent:

- **In the chart (the embed).** Open port 9300 and log in as a clinician with
  medical-record access, e.g. `physician`. Go to **Finder**, then open any
  patient. The patient's **Dashboard** opens, and its first card is
  **Clinical Co-Pilot: Pre-Visit Brief**, above Demographics. It generates
  the brief when the chart opens. Users without medical-record access, such
  as `receptionist`, do not see the card. Below the brief, **Ask about this
  patient** opens the multi-turn conversation in the same card. Walkthrough:
  [module deployment §3](interface/modules/custom_modules/oe-module-clinical-copilot/docs/deployment.md).
  Phil Belford, the walkthrough's and API collection's example patient, is
  worth a look: his penicillin allergy carries no allergen code, and the
  brief used to render it as "(unspecified allergen)" marked verified —
  defect F-57 in [docs/audit/findings.md](docs/audit/findings.md). Since the
  fix deployed, it reads "penicillin", verified against the record's
  narrative.
- **Standalone UI.** Open `https://64.177.42.210:8443/`, then paste a
  patient's FHIR id and a clinician's OpenEMR access token. **Need a token?**
  on the page shows the command to get one.
- **API.** `GET /health`, `GET /ready`, `POST /pre-visit-brief`,
  `POST /pre-visit-brief/follow-up` (one constrained, single-turn question
  on a brief), `POST /pre-visit-brief/conversation` and
  `POST /pre-visit-brief/conversation/end` (the multi-turn conversation,
  one turn per request), and `GET /docs` (the OpenAPI contract), all on
  port 8443.
  The easiest way to run every workflow is the
  [Postman collection](agent/api-collection/README.md).

Audit, eval and load-test records each name the host they were run against.
The **evals and the load test are current**, both measured on the Vultr host
on the Vultr host: the eval suites on 2026-09-21 (**43 of 43 pass** — cases
A–H 31 of 31, the conversation cases 12 of 12 — with no failures and nothing
skipped, after the fixes for F-58, F-59 and F-60 landed), and the load and
baseline performance test on 2026-09-20
([agent/load-test/RESULTS.md](agent/load-test/RESULTS.md)), which found
OpenEMR's PHP tier — not the agent — to be what caps throughput. The
**audit is pre-migration**, measured against the DigitalOcean droplet; that
host was a different install, so patient ids in the audit's records do not
exist on the current one.
No BAA covers the Vultr host. That is acceptable only because all data is
synthetic
([docs/ARCHITECTURE.md §1](docs/ARCHITECTURE.md#1-deployment-topology)).

## What this project is

A **Pre-Visit Brief** agent for a primary care physician about to open the
next patient's chart. It lists active problems, current medications and
allergies, and appears as a card in this OpenEMR fork's patient chart. The
agent (`agent/`, FastAPI + LangGraph) reads the chart only through OpenEMR's
FHIR R4 API, using the clinician's own OAuth token. Its one LLM call drafts
claims, each tied to a source record. Before anything is shown, a
deterministic checker compares each claim to that record. Claims that fail
are marked UNVERIFIED, never silently dropped. Every request carries a
correlation id through structured JSON logs. The eval suite was run against
the deployed agent on Vultr; the load tests date from the pre-migration
DigitalOcean host (above). Follow-Up Drilling is also deployed, in two
forms: a constrained, single-turn follow-up question, and a multi-turn
conversation that resolves "that" and "last time" against the claims earlier
turns verified. Change Detection is not built
([agent/README.md](agent/README.md), "Not built yet").

## Deliverables

The graded documents live under `docs/` and `agent/`.

| Deliverable | Where |
|---|---|
| Setup guide | This README: §1–4 cover OpenEMR. For the agent, see [agent/README.md](agent/README.md) and [agent/docs/deployment.md](agent/docs/deployment.md) |
| Audit | [docs/AUDIT.md](docs/AUDIT.md) (summary plus all findings). Evidence ledger: [docs/audit/findings.md](docs/audit/findings.md) |
| Users and use cases | [docs/USERS.md](docs/USERS.md) |
| Agent architecture | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Key metrics | [docs/KEY_METRICS.md](docs/KEY_METRICS.md) |
| AI cost analysis | [docs/COST_ANALYSIS.md](docs/COST_ANALYSIS.md) |
| Eval dataset | Suite and case descriptions: [agent/evals/README.md](agent/evals/README.md). Latest results: [agent/evals/RESULTS.md](agent/evals/RESULTS.md) |
| Load and baseline performance tests | Results: [agent/load-test/RESULTS.md](agent/load-test/RESULTS.md). Method: [agent/load-test/README.md](agent/load-test/README.md) |
| API collection (Postman) | [agent/api-collection/clinical-copilot-agent.postman_collection.json](agent/api-collection/clinical-copilot-agent.postman_collection.json). How to run it: [agent/api-collection/README.md](agent/api-collection/README.md) |
| Agent setup and deployment | [agent/README.md](agent/README.md), [agent/docs/deployment.md](agent/docs/deployment.md) |
| OpenEMR chart embed | [interface/modules/custom_modules/oe-module-clinical-copilot/README.md](interface/modules/custom_modules/oe-module-clinical-copilot/README.md) |
| Observability | [agent/docs/observability.md](agent/docs/observability.md) |

---

## 1. Prerequisites

### Host

| Component | Version | How confirmed |
|---|---|---|
| macOS on Apple Silicon (M-series), 15 CPU / 24 GB RAM | — | Given by the operator; not verified in this session |
| Colima (Docker runtime — **not** Docker Desktop) | — | `docker context show` → `colima` |
| Docker Engine | 29.7.1 client / 29.5.2 server | `docker version` |
| Docker Compose | 5.5.1 | `docker compose version` |
| Homebrew | — | Given by the operator; used to install `docker-compose` |

Docker Desktop is not used. Colima must be sized up from its defaults before the
stack will run comfortably — see step 2.

### Inside the containers

These are the versions the stack actually runs, read live from the running
containers rather than assumed:

| Component | Version | How confirmed |
|---|---|---|
| OpenEMR | 8.2.0-dev (`v_database` 541) | `version.php` and the `version` DB table agree |
| PHP | 8.5.6 | `docker compose exec -T openemr php -v` |
| Apache httpd | 2.4.67 (Unix) | `docker compose exec -T openemr httpd -v` |
| MariaDB server | 11.8.8-MariaDB | `docker compose exec -T mysql mariadb --version` |
| Node.js / npm | v24.14.1 / 11.11.0 | `docker compose exec -T openemr node -v; npm -v` |
| Composer | 2.10.1 | `docker compose exec -T openemr composer --version` |
| Alpine Linux | 3.23.5 | `docker compose exec -T openemr cat /etc/alpine-release` |
| phpMyAdmin | 5.2.3 | Pinned digest in `docker/development-easy/docker-compose.yml` |

The `openemr/openemr:flex` image publishes an arm64 manifest entry, so it runs
native on Apple Silicon with no emulation (given by the operator). The MariaDB
client inside the container self-reports `aarch64`, which is consistent with
that.

---

## 2. Setup

Every command in this section was run in this repository, except where marked.

### Step 1 — Clone the fork and add the upstream remote

```bash
git clone https://labs.gauntletai.com/paawandesai/openemr-clinical-copilot.git
cd openemr-clinical-copilot
git remote add upstream git@github.com:Gauntlet-HQ/openemr-base-clean.git
```

> **Clone from `labs.gauntletai.com`, not GitHub.**
> `github.com/paawandesai/clinical-copilot` is a stale mirror. It holds only
> the initial OpenEMR import (1 commit on `main`) and none of the agent, embed,
> evals or docs.
>
> The clone itself was not re-run. `git remote -v` in the working copy reports
> `origin  https://labs.gauntletai.com/paawandesai/openemr-clinical-copilot.git`
> and `upstream  git@github.com:Gauntlet-HQ/openemr-base-clean.git`.

### Step 2 — Size the Colima VM

*(Given by the operator; not re-run in this session.)* Colima's defaults are too
small for this stack. Resize before first start:

```bash
colima stop
colima start --cpu 6 --memory 12 --disk 100 --vm-type vz --mount-type virtiofs
```

The `--vm-type vz --mount-type virtiofs` flags matter: this stack bind-mounts
the entire repository into the container, and virtiofs bind mounts are
dramatically faster than the alternative.

### Step 3 — Install the Docker Compose CLI plugin

*(Given by the operator; not re-run in this session.)* A Colima install does not
necessarily provide `docker compose`. If `docker compose` reports
`unknown command`, install it and link it as a CLI plugin:

```bash
brew install docker-compose
mkdir -p ~/.docker/cli-plugins
ln -sfn "$(brew --prefix)/opt/docker-compose/bin/docker-compose" ~/.docker/cli-plugins/docker-compose
```

Confirm with `docker compose version` (expect `Docker Compose version 5.5.1` or
later).

### Step 4 — Start the three services this project needs

```bash
cd docker/development-easy
HOST_UID=$(id -u) HOST_GID=$(id -g) docker compose up -d mysql openemr phpmyadmin
```

Two things are deliberate here:

- **Only `mysql`, `openemr` and `phpmyadmin` are started.** `docker-compose.yml`
  also defines `selenium`, `couchdb`, `openldap` and `mailpit`. Those are
  upstream dev conveniences this project does not need, and leaving them down
  saves memory. Naming the three services explicitly is what keeps the rest
  stopped — a bare `docker compose up -d` would start everything.
- **`HOST_UID` / `HOST_GID` must be exported.** The container entrypoint makes
  apache adopt these ids. Without them apache defaults to uid 1000 and writes
  bind-mounted files as uid 1000, which causes permission problems on any host
  whose uid differs (macOS user accounts normally start at 501).

> This exact invocation was not typed in this session — the stack was already
> running. Its effects are verified: `docker inspect` shows `HOST_UID=501` and
> `HOST_GID=20` in the container environment, `id apache` inside the container
> reports `uid=501(apache) gid=20(dialout)` matching the host user, and
> `docker compose ps --services --filter status=running` lists exactly
> `mysql`, `openemr`, `phpmyadmin`.

### Step 5 — Install OpenEMR (required — do not skip)

**Compose will report every container healthy before OpenEMR is installed.** The
containers being up is not the same as the application being usable; the install
is a separate, explicit step. Run it:

```bash
docker compose exec -T openemr /root/devtools dev-reset-install-demodata
```

This wipes the database and reinstalls from scratch, then loads OpenEMR's
standard demo dataset. It takes several minutes — most of it is
`sql_upgrade.php` migrating the 5.0.0-era demo dataset forward to 8.2. It
finishes with a block of `Set <global> to <value>` lines and exits 0.

`openemr-cmd` (the CLI referenced throughout `CONTRIBUTING.md` and `CLAUDE.md`)
is **not installed** in this environment. Every devtools command here is run
through `docker compose exec -T openemr /root/devtools <subcommand>` instead,
which is the documented equivalent. Run `/root/devtools` with no arguments and
it prints nothing; the subcommand list lives in the comment header of the script
itself (`docker compose exec -T openemr head -125 /root/devtools`).

### Step 6 — Generate the OAuth2 key pair

OpenEMR does not create its OAuth2 keys at install time. They are generated
lazily on the first OAuth2 request, so until something triggers that, the health
endpoint reports `oauth_keys: false`. Trigger it by registering a client:

```bash
docker compose exec -T openemr /root/devtools register-oauth2-client
```

Expected output — a client id and secret, and exit 0:

```
Registering a oauth2 client to site default
client id: "zYinBZAW5lI5wznAUKl-GVw21tX4QyDeVE0UZQbwaIg"
client secret: "81VXoufIzsEZm1R9acpTRZbstOfk3MG_EzjZGLOr7iXuZzoI3paf2YJVlAdCsWjIkUh55InsyV79QHmCRmwAXw"
```

This writes `oaprivate.key` (mode 0640) and `oapublic.key` (mode 0660) into
`sites/default/documents/certificates/`. The passphrase protecting the private
key is generated per-install from PHP's CSPRNG and stored encrypted in the
`keys` table — it is not static or derived, so these values differ on every
install.

### Step 7 — Seed realistic patient data

`dev-reset-install-demodata` alone leaves you with **3 patients, 3 encounters
all dated 2014-02-01, and zero lab orders or results**. That is not enough to
develop or demo a clinical co-pilot against. Generate realistic synthetic
patients with Synthea:

```bash
docker compose exec -T openemr /root/devtools import-random-patients 5
```

Expected output ends with:

```
Completed patients import (5 patients) (4 total seconds) (0.8 average seconds per patient)
Started uuid creation
Completed uuid creation (4 total seconds; 0.0011111111111111 total hours)

Finished patients import 5
Completed run for following number of random patients: 5
```

Increase the count for a fuller dataset. Each patient takes a few seconds and
brings a full history — encounters, conditions, medications and lab results.

**First run only:** the command installs an OpenJDK 17 JRE into the container
and downloads the ~197 MB Synthea JAR to `/root/synthea`. Both are cached
afterwards. (Not re-observed in this session — the JAR and
`openjdk version "17.0.20"` were already present.)

#### The optional third argument

`import-random-patients <count> [false]` takes an optional third argument. The
literal string `false` turns off the importer's development mode:

| Invocation | Mode | Behaviour |
|---|---|---|
| `import-random-patients 5` | Development mode **ON** | Prints `Development Mode is ON (performance mode)`. Imports via a single console call. **This is the form that works.** |
| `import-random-patients 2 false` | Development mode **OFF** | Prints `Development Mode is OFF (audit log will be used)`. **Creates document rows but no patients — see Troubleshooting.** |

**Use the two-argument form.** The `false` variant is documented here only
because it looks like it works and does not. Details in Troubleshooting below.

### Step 8 — Run the agent and the chart embed

The steps above bring up OpenEMR only. The agent is a separate Python service
in `agent/`, and it needs an `ANTHROPIC_API_KEY`. To set it up:

- Run it locally or run its tests: [agent/README.md](agent/README.md), under
  *Running locally*.
- Deploy it as a container behind a TLS proxy, next to OpenEMR:
  [agent/docs/deployment.md](agent/docs/deployment.md).
- Enable the patient-chart card, set up its OAuth client and trust the
  agent's CA:
  [oe-module-clinical-copilot/README.md](interface/modules/custom_modules/oe-module-clinical-copilot/README.md).

---

## 3. Verification

Run all five. Every expected output below was captured from this stack.

**1. Readiness endpoint**

```bash
curl -sk https://localhost:9300/meta/health/readyz
```

```json
{"status":"setup_required","checks":{"installed":false,"database":true,"filesystem":true,"session":true,"oauth_keys":true,"cache":true}}
```

`oauth_keys: true` confirms step 6 worked. Note that `installed` reports `false`
and `status` reports `setup_required` **even on a correctly installed system** —
that is a known reporting defect in this fork, not a sign your install failed;
see [docs/AUDIT.md](docs/AUDIT.md). Judge the install by the table and patient counts below, not by
this field. A liveness probe is also available:

```bash
curl -sk https://localhost:9300/meta/health/livez     # => {"status":"alive"}
```

**2. Table count** — note the client binary is `mariadb`, not `mysql`:

```bash
docker compose exec -T mysql mariadb -uopenemr -popenemr openemr \
  -e "SELECT COUNT(*) AS table_count FROM information_schema.tables WHERE table_schema='openemr';"
```

```
table_count
284
```

**3. Clinical data census** — confirms seeding actually landed:

```bash
docker compose exec -T mysql mariadb -uopenemr -popenemr openemr -t -e "
SELECT (SELECT COUNT(*) FROM patient_data)     AS patients,
       (SELECT COUNT(*) FROM form_encounter)   AS encounters,
       (SELECT COUNT(*) FROM procedure_order)  AS lab_orders,
       (SELECT COUNT(*) FROM procedure_result) AS lab_results,
       (SELECT COUNT(*) FROM lists)            AS list_items;"
```

```
+----------+------------+------------+-------------+------------+
| patients | encounters | lab_orders | lab_results | list_items |
+----------+------------+------------+-------------+------------+
|       65 |       2800 |       7417 |        7259 |       2021 |
+----------+------------+------------+-------------+------------+
```

Your numbers will differ with how many Synthea patients you imported. What
matters is that `patients` is well above 3 and `lab_results` is non-zero — if
`lab_results` is 0 you are still on demo data only and step 7 did not run.

**4. Login page responds**

```bash
curl -sk -o /dev/null -w '%{http_code}\n' https://localhost:9300/interface/login/login.php
```

```
200
```

**5. Log in through a browser**

| Service | URL | Credentials |
|---|---|---|
| OpenEMR (HTTPS) | https://localhost:9300/ | `admin` / `pass` |
| OpenEMR (HTTP) | http://localhost:8300/ | `admin` / `pass` |
| phpMyAdmin | http://localhost:8310/ | `openemr` / `openemr` |
| MariaDB (direct) | `localhost:8320` | `openemr` / `openemr` |

The HTTPS certificate is self-signed, so expect a browser warning. Both
`http://localhost:8300/interface/login/login.php` and phpMyAdmin on 8310 were
confirmed returning `200`.

---

## 4. Troubleshooting

**`docker compose` returns "unknown command"**
*Cause:* Colima does not necessarily ship the Compose CLI plugin, unlike Docker
Desktop.
*Fix:* `brew install docker-compose`, then
`mkdir -p ~/.docker/cli-plugins && ln -sfn "$(brew --prefix)/opt/docker-compose/bin/docker-compose" ~/.docker/cli-plugins/docker-compose`.
(Given by the operator.)

**`docker compose exec mysql mysql ...` fails with "executable file not found"**
*Cause:* The image is MariaDB 11.8.8. The client binary is `mariadb`; the
`mysql` compatibility symlink is not present.
*Fix:* Use `mariadb` — e.g.
`docker compose exec -T mysql mariadb -uopenemr -popenemr openemr -e "..."`.
The same applies to `mariadb-dump` instead of `mysqldump`.

**Files written by the container are owned by uid 1000; permission errors on host-side edits or `git commit`**
*Cause:* `HOST_UID` / `HOST_GID` were not exported when the stack was started, so
the container entrypoint left apache at its default uid 1000. This stack
bind-mounts the whole repo, so everything apache writes lands in your working
tree with the wrong owner.
*Fix:* Always start with
`HOST_UID=$(id -u) HOST_GID=$(id -g) docker compose up -d mysql openemr phpmyadmin`.
Verify with `docker compose exec -T openemr id apache` — the uid should match
your `id -u` (501 on a standard macOS account), not 1000.

**All containers report healthy, but OpenEMR is not usable**
*Cause:* Container health and application installation are independent. A freshly
started stack passes its health checks while the application is still
uninstalled.
*Fix:* Run step 5 explicitly:
`docker compose exec -T openemr /root/devtools dev-reset-install-demodata`. Do
not treat `docker compose ps` showing healthy as evidence the app is ready — use
the table and patient counts in Verification instead.

**`readyz` reports `"installed":false` and `"status":"setup_required"` on a working install**
*Cause:* A known reporting defect in this fork; the field does not reflect actual
install state once the application has bootstrapped. Documented in
[docs/AUDIT.md](docs/AUDIT.md).
*Fix:* None needed — ignore that field. Verify the install with the table count
and clinical census instead. The other checks in the same response
(`database`, `filesystem`, `session`, `oauth_keys`, `cache`) are meaningful.

**`openemr-cmd: command not found`**
*Cause:* `openemr-cmd` is not installed in this environment, though
`CONTRIBUTING.md` and `CLAUDE.md` assume it.
*Fix:* Call the in-container script directly —
`docker compose exec -T openemr /root/devtools <subcommand>`. This is equivalent
for every subcommand. Note that running `/root/devtools` with no arguments
prints nothing at all; the subcommand list is in the script's comment header
(`docker compose exec -T openemr head -125 /root/devtools`).

**`readyz` reports `"oauth_keys":false` after a successful install**
*Cause:* OpenEMR generates its OAuth2 key pair lazily, on the first OAuth2
request — not during installation. Until then the key files do not exist.
*Fix:* `docker compose exec -T openemr /root/devtools register-oauth2-client`.
Confirm with
`docker compose exec -T openemr ls -la /var/www/localhost/htdocs/openemr/sites/default/documents/certificates/` —
`oaprivate.key` and `oapublic.key` should both be present.

**Only 3 patients, and every lab query returns nothing**
*Cause:* You have `dev-reset-install-demodata` data only. That dataset is 3
patients with 3 encounters all dated 2014-02-01 and zero rows in
`procedure_order` / `procedure_result`.
*Fix:* Seed with Synthea — `docker compose exec -T openemr /root/devtools
import-random-patients 5` (or a larger count). Re-run the clinical census to
confirm `lab_results` is non-zero.

**`import-random-patients N false` reports success but no patients appear**
*Cause:* Verified in this session: `import-random-patients 2 false` exits 0 and
prints `System has successfully imported CCDA number: 1`, `... 2` and
`Finished patients import 2`, but `patient_data` does not change. With
development mode off, `contrib/util/ccda_import/import_ccda.php` takes a
different path — it creates a `documents` row, then shells out to
`openemr:ccda-import` and `openemr:ccda-newpatient` passing `--auth_name=`
(empty). The devtools wrapper never supplies an auth name, and the `exec()`
calls' exit statuses are not checked, so the failure is silent. The run leaves
orphan `documents` rows with no patient attached.
*Fix:* Use the two-argument form, `import-random-patients <count>`. If you have
already run the `false` variant, its document rows are inert — identify them
with
`SELECT id, name FROM documents WHERE foreign_id IS NULL OR foreign_id = 0;`.

**The stack is slow, or the container runs out of memory**
*Cause:* Colima's default VM is undersized, and/or the mount type is not
virtiofs. This stack bind-mounts the whole repository, so mount performance
dominates.
*Fix:* Resize as in step 2:
`colima start --cpu 6 --memory 12 --disk 100 --vm-type vz --mount-type virtiofs`.
(Given by the operator.)

---

## 5. Repository layout

Only the parts relevant to this project. This is an OpenEMR fork, so most of the
tree is upstream application code.

```
agent/                      The Clinical Co-Pilot agent (Python, FastAPI + LangGraph)
  app/                      Retrieval, drafting, verification, rendering, HTTP API
  evals/                    Eval suite and RESULTS.md
  load-test/                Load/baseline performance tests and RESULTS.md
  api-collection/           Postman collection and environment
  deploy/                   Compose file, Caddy TLS proxy, standalone UI (deploy/ui)
  docs/                     deployment.md, observability.md
interface/modules/custom_modules/oe-module-clinical-copilot/
                            OpenEMR module: the Pre-Visit Brief card in the patient chart
docs/                       AUDIT.md, USERS.md, ARCHITECTURE.md, KEY_METRICS.md,
                            COST_ANALYSIS.md; audit/findings.md is the evidence ledger
src/                        Modern PSR-4 code (OpenEMR\ namespace)
interface/                  Web UI controllers and templates (legacy entry points)
library/                    Legacy procedural PHP helpers
templates/                  Twig (modern) and Smarty (legacy) templates
meta/health/index.php       /meta/health/livez and /readyz probe endpoints
sql/                        Database schema and migrations
tests/                      PHPUnit suites (unit, api, e2e, services, isolated)
contrib/util/ccda_import/   CCDA importer used by import-random-patients
docker/development-easy/    The compose stack used for local development
  docker-compose.yml        Service, port, volume and environment definitions
CLAUDE.md                   Coding standards and workflow for this repo
README-OPENEMR.md           Upstream OpenEMR's original README
```

Inside the `openemr` container the repository is mounted twice: read-write at
`/var/www/localhost/htdocs/openemr` and read-only at `/openemr`. `sites/`,
`vendor/`, `node_modules/` and the theme and asset directories are named Docker
volumes layered over the bind mount, so changes there do **not** appear in your
working tree.
