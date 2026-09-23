# Clinical Co-Pilot

A Clinical Co-Pilot AI agent built into a fork of [OpenEMR](https://open-emr.org)
8.2.0-dev. Upstream OpenEMR's own README is preserved as
[README-OPENEMR.md](README-OPENEMR.md).

**Repository (submission):** https://labs.gauntletai.com/paawandesai/openemr-clinical-copilot

| | Week 1: baseline | Week 2: multimodal |
|---|---|---|
| What it does | Pre-Visit Brief and multi-turn follow-up, read from the chart over FHIR; every claim deterministically verified | lab PDFs and intake forms extracted to a strict schema with page-located values; a supervisor over an intake-extractor and an evidence-retriever; hybrid RAG over guideline evidence; an eval gate |
| Deployed at `64.177.42.210`? | **Yes**, the whole Week 1 surface | **Partly**, as of the rehearsal host, before the Week 2 deploy **[PENDING `chore/w2-deploy-rehearsal`]**. Retrieval, OCR and the write-back endpoint are deployed. The Week 2 ask path (`POST /w2/ask`) is on `main` but **not** on the rehearsal host, before the Week 2 deploy **[PENDING `chore/w2-deploy-rehearsal`]** |
| Start reading | [Week 1 baseline](#week-1-baseline) below | [Week 2: start here](#week-2-start-here), next |

---

## Week 2: start here

**Snapshot: `main@f8b16c7`**, which is on `origin`. Everything in this
section is on that commit unless it is marked PENDING, which names the
unmerged branch the claim rests on: **[PENDING `feat/w2-php-wiring`]**, **[PENDING `chore/w2-deploy-rehearsal`]**, **[PENDING `feat/w2-eval-gate`]** or **[PENDING `feat/w2-answer-scope`]**.

- **Merge order:** the first three merge before submission in this order:
  `feat/w2-php-wiring`, then `chore/w2-deploy-rehearsal` (which deploys),
  then `feat/w2-eval-gate`.
- **Repin:** every citation is then repinned to the one resulting `main`
  commit, and the markers come out.
- **Answer-scope:** `feat/w2-answer-scope` fixes the slow document question.
- **Live host:** figures are labelled "rehearsal host, before the Week 2 deploy" until the deploy session
  re-measures.

### What Week 2 adds, and where it runs

| Piece | Code | On `main` | On the live host (rehearsal host, before the Week 2 deploy) **[PENDING `chore/w2-deploy-rehearsal`]** |
|---|---|---|---|
| Document extraction: text layer / OCR → one vision call → deterministic locate → flag cross-check → discrepancies | `agent/app/graph/intake_extractor.py`, `agent/app/workers/intake_extractor/`, `agent/app/extraction/` | yes | library only: the deployed `/documents` is a stub that hashes and discards |
| Supervisor + workers, `POST /w2/ask` | `agent/app/graph/supervisor.py`, `agent/app/supervisor/`, `agent/app/api/ask.py` | yes | **no** |
| Guideline retrieval: 28 documents, BM25 + local embeddings + local rerank | `agent/app/retrieval/` | yes | yes, preloaded, nothing reads it |
| Week 2 verifier, citation contract | `agent/app/verification/w2_checker.py`, `agent/app/models/w2/` | yes | no (it serves `/w2/ask`) |
| Source-grounded answer view: PDF.js with bbox overlay | `interface/modules/custom_modules/oe-module-clinical-copilot/public/assets/w2/` | yes, **fixture mode only**: the chart card is not registered on `main`. It is wired into the chart on `feat/w2-php-wiring` **[PENDING `feat/w2-php-wiring`]** | no |
| Lab write-back endpoint + idempotency ledger | the same module, `src/WriteBack/` | yes | yes, rehearsed |
| Eval gate: 96 cases, CI job **[PENDING `feat/w2-eval-gate`]** | `agent/evals/w2/gate/`, `.gitlab-ci.yml` | **no**, `feat/w2-eval-gate` | n/a: runs on recorded fixtures |

Where the facts in this table come from, and what is not wired yet:
[`W2_ARCHITECTURE.md`](W2_ARCHITECTURE.md) §8 and §11.

### Run the core Week 2 flow

Every command runs from the repository root unless it starts with `cd`.
Python 3.12 (the agent image is `python:3.12-slim`) and Tesseract are
required.

```bash
git clone https://labs.gauntletai.com/paawandesai/openemr-clinical-copilot.git
cd openemr-clinical-copilot
brew install tesseract                      # or: apt-get install tesseract-ocr
cd agent && python3.12 -m venv .venv && . .venv/bin/activate
python -m pip install -r requirements.txt -r requirements-dev.txt
```

**1. Offline, no credentials, no network.** These cover extraction, locate,
the flag cross-check, discrepancies, routing and verification.

```bash
cd agent
python -m pytest tests                       # unit tests; a bare `pytest` also works (testpaths)
python -m tests.intake_extractor_measurement # the extractor over the six committed samples, vision stubbed
```

Do **not** run `pytest evals`: it runs the Week 1 suite live, against real
FHIR and Anthropic. The second command's output, as committed, is
[`docs/w2/evidence/extractor-measurement.txt`](docs/w2/evidence/extractor-measurement.txt).

**2. The answer view with its PDF overlay** (fixture mode; a Node runtime is
the only extra requirement):

```bash
cd agent && python scripts/build_ui_fixtures.py && cd ..
node interface/modules/custom_modules/oe-module-clinical-copilot/dev/serve.mjs
# open http://127.0.0.1:8760/dev/fixture.html?scenario=lab-multipage-003 and click a document chip
```

**3. Live: upload → extraction → an answer citing a document and a
guideline.** This costs one Anthropic drafting call; the vision call is
replayed from a recording. It needs `agent/.env` with:

| Variable | Value |
|---|---|
| `ANTHROPIC_API_KEY` | an Anthropic API key |
| `FHIR_CLIENT_ID`, `FHIR_CLIENT_SECRET` | an OpenEMR OAuth client **enabled for the password grant** |
| `FHIR_BASE_URL` | defaults to `https://64.177.42.210:9300/apis/default/fhir` |
| `FHIR_TEST_USERNAME`, `FHIR_TEST_PASSWORD` | default `physician` / `physician` |

No password-grant client is published. How to register one:
[agent/api-collection/README.md](agent/api-collection/README.md) (the
`UPDATE oauth_clients … grant_types` step) or
[docs/w2/LOCAL_ENV.md](docs/w2/LOCAL_ENV.md) §4.

```bash
cd agent
python scripts/w2_e2e_ask.py                                        # lab-clean-001; exits 0 only with ≥1 document AND ≥1 guideline citation
python scripts/w2_e2e_ask.py --sample lab-critical-potassium-007 \
  --live-vision --expect-critical                                    # + one live vision call: a critical potassium leads the answer
```

A question about the uploaded report takes about 1.5–2 minutes: 91.9 s and
130.5 s in the only two real-route runs, about $0.46 each
(`docs/w2/LOAD_TEST.md` §6). That is a known
problem, recorded in `W2_ARCHITECTURE.md` §11.

What each printed section means, and what to expect:
[`docs/w2/DEMO_SCRIPT.md`](docs/w2/DEMO_SCRIPT.md). **No output from this
script has been committed yet**, so treat the expectations there as a
specification to check against, not as a record.

**4. The whole encounter over HTTP: the Week 2 core-flow collection.** It
runs against a **local** stack (OpenEMR as in
[docs/w2/LOCAL_ENV.md](docs/w2/LOCAL_ENV.md), plus the agent from this
checkout), with real model calls. In order, it runs:

- the upload, and extraction polling;
- the brief and a conversation turn;
- one `/w2/ask` about the uploaded document, and one guideline question;
- the lab write-back, then the same write-back again, which must be
  idempotent;
- the document preview;
- the refusal cases.

```bash
LOG=$PWD/agent/logs/run.jsonl AGENT_ENV_FILE=<file with ANTHROPIC_API_KEY> LANGFUSE=off \
  agent/load-test/w2/start_local_agent.sh &                          # agent on 127.0.0.1:8100, one worker; needs agent/.venv
FHIR_CLIENT_ID=<client_id> FHIR_CLIENT_SECRET=<client_secret> agent/api-collection/run.sh
```

The client must allow the password grant and hold the scopes in
`agent/api-collection/local.postman_environment.json`. The one-time
registration is in [agent/api-collection/README.md](agent/api-collection/README.md),
"Week 2 core flow".

The last committed run is `docs/w2/evidence/api-collection-run.txt`: 44
requests, 102 assertions, 0 failed. It took 2 min 52.7 s, including the
1 min 31.9 s document question, and one encounter costs about $0.82
(`docs/w2/OBSERVABILITY.md` §2). **It was not re-run in the session that
wrote this README.** Route contracts are in
[docs/w2/ui-integration.md](docs/w2/ui-integration.md) §2.2–§2.4 and
`agent/app/models/w2/ask.py`.

### Run the eval gate

**[PENDING `feat/w2-eval-gate`]**: this whole subsection rests on `feat/w2-eval-gate`, which is on
`origin` but not merged into `main`:

```bash
git fetch origin && git worktree add ../w2-eval-gate origin/feat/w2-eval-gate
cd ../w2-eval-gate/agent
python -m pip install -r requirements.txt -r requirements-dev.txt    # plus Tesseract, as above
python -m evals.w2.gate --det-only                                   # deterministic tier; no key, no model call
ANTHROPIC_API_KEY=… python -m evals.w2.gate                          # both tiers; a cold judge run is 24 calls, $0.0288
python -m evals.w2.gate.planted_regressions --out ../docs/w2/EVAL_GATE_EVIDENCE.md   # needs the key
```

| Exit code | Meaning |
|---|---|
| 0 | PASS |
| 1 | BLOCKED |
| 2 | ERROR |
| 3 | INCOMPLETE: a critical case went unscored |

`--det-only` alone therefore exits 3, because the judge's critical
meta-check cases go unscored. The gate README on that branch
(`agent/evals/w2/README.md`) covers the five rules, the cases and the judge.
The committed evidence is `docs/w2/EVAL_GATE_EVIDENCE.md` on that branch:
the control run passes, and 13 of 13 planted regressions block.

In CI the gate is the `w2-eval-gate` job in `.gitlab-ci.yml` on that branch.
It needs a masked, **unprotected** `ANTHROPIC_API_KEY` variable, a runner
tagged `w2-eval`, and "Pipelines must succeed" enabled; the file's header
says why. No committed evidence shows it has run on a GitLab runner.

### Week 2 documents

| Document | What it covers |
|---|---|
| [W2_ARCHITECTURE.md](W2_ARCHITECTURE.md) | key decisions and tradeoffs, ingestion, worker graph and routing table, RAG, citation contract, eval gate, write-back, observability, risks, known limitations |
| [KEY_METRICS.md](KEY_METRICS.md) | the Week 2 metrics, with honest denominators; the Week 1 six are in [docs/KEY_METRICS.md](docs/KEY_METRICS.md) |
| [docs/w2/COST_AND_LATENCY.md](docs/w2/COST_AND_LATENCY.md) | actual dev spend, per-encounter cost, projections at 100 to 100K encounters a day, p50/p95 and bottlenecks |
| [docs/w2/DEMO_SCRIPT.md](docs/w2/DEMO_SCRIPT.md) | the recorded walkthrough |
| [docs/w2/DECISIONS.md](docs/w2/DECISIONS.md) | every Week 2 decision, D1–D40 and D49 on `main` (D41 **[PENDING `chore/w2-deploy-rehearsal`]** and D42–D48 **[PENDING `feat/w2-eval-gate`]** are on their branches) |
| [docs/w2/EVAL_DESIGN.md](docs/w2/EVAL_DESIGN.md), [docs/w2/GATE.md](docs/w2/GATE.md) | why the gate is shaped as it is, and how gate files are protected |
| [docs/w2/retrieval-bakeoff.md](docs/w2/retrieval-bakeoff.md) | the corpus, its licensing, and the retrieval measurements |
| [docs/w2/spikes/writeback.md](docs/w2/spikes/writeback.md), [docs/w2/deploy-writeback.md](docs/w2/deploy-writeback.md) | how OpenEMR can be written to, and how the endpoint deploys |
| [docs/w2/ui-integration.md](docs/w2/ui-integration.md) | the answer view, and the PHP still to wire |
| [docs/w2/WEEK1_DEBT.md](docs/w2/WEEK1_DEBT.md), [docs/w2/LOCAL_ENV.md](docs/w2/LOCAL_ENV.md), [docs/w2/root-run-trace-gap.md](docs/w2/root-run-trace-gap.md) | Week 1 debt triage, a local stack that matches the host, and the trace-containment finding |

`docs/w2/OBSERVABILITY.md`, `ALERTS.md` and `LOAD_TEST.md` (tracing,
dashboard, alerts, load tests and the real-route `/w2/ask` measurements)
are on `main`. `docs/w2/DEPLOY.md` **[PENDING `chore/w2-deploy-rehearsal`]**, the host rehearsal and its
measurements, is on `chore/w2-deploy-rehearsal`, which **is not on
`origin`**. Until it is pushed or merged, the numbers the documents above
cite from it cannot be checked from a clone.

---

## Week 1 baseline

Everything from here down describes the Week 1 build and the OpenEMR
platform setup. It is unchanged by Week 2, apart from this heading.

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
