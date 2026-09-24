# API collection — Clinical Co-Pilot agent

A Postman v2.1 collection that runs end to end without reading the source:

| Folder | Requests | Checks |
|---|---|---|
| 1. Liveness and readiness | `GET /health`, `GET /ready` | 200 `alive`; 200 `ready` with FHIR and LLM provider healthy (`degraded`, also 200, when only the observability backend is down, D58) |
| 2. Get a clinician token | `POST {openemr_url}/oauth2/default/token` | OpenEMR issues a token; stored for folders 3–4 |
| 3. Pre-Visit Brief | `POST /pre-visit-brief` for the demo patient (`patient_id`) | 200; brief rendered; every claim verified or marked unverified; `user:patient:session` key; correlation id matches header; token never echoed |
| 4. Failure paths | no token; malformed `patient_id`; unknown patient; token OpenEMR rejects | 401 `missing_bearer_token`; 422 `invalid_request`; 404 `patient_not_found`; 401 `fhir_token_rejected` |

Files:

- `clinical-copilot-agent.postman_collection.json` — the requests and tests.
- `vultr.postman_environment.json` — the Vultr deployment host's URLs
  (`https://64.177.42.210:8443` for the agent, `https://64.177.42.210:9300`
  for OpenEMR) and the demo `patient_id`. The file name predates the move
  from DigitalOcean. **All four credential values ship empty:**
  `client_id`, `client_secret`, `openemr_username` and `openemr_password`.
  Supply them at run time ([Credentials](#credentials)); never commit
  filled-in values.

**Demo patient.** `patient_id` defaults to Susan Underwood
(`a2c36e54-af4c-4fde-a609-4fe1419590ac`, pid 2 on the Vultr host). Her brief
was checked live on 2026-09-17 through the chart embed, as the `physician`
account. It listed one active problem (diabetes) and three current
medications (Lisinopril, Metformin, Lipitor), with no line repeated.
Allergies showed as not recorded, which is correct: her chart has none.
All 4 of 4 claims were verified.

**To show the F-57 fix,** use Phil Belford on Vultr
(`a2c36e54-af42-4c38-b86e-ade616ed5494`). OpenEMR sends his penicillin
allergy with no allergen code, carrying the name only in the record's
narrative (`FhirAllergyIntoleranceService.php:217-221`). The brief used to
render that as `(unspecified allergen)` and count it verified — defect F-57
in `docs/audit/findings.md`. Since the fix deployed, his brief names
`penicillin`, verified against the narrative. Checked live on 2026-09-20:
two active problems, two current medications, `Allergies: - penicillin`,
5 of 5 claims verified. Eval cases A5 and B1 hold this behaviour, and group
H covers the shapes this population lacks.

Do not use these as the demo patient:

- **`a2c0e096-23d5-4cf9-ad5b-4895597363a7`**, the old default: Phil Belford
  on the DigitalOcean host. The Vultr host is a fresh install with new FHIR
  ids, so this id does not exist there (404 `patient_not_found`).
- **Synthea patients whose charts hold each medication twice**, as a
  medication-list entry and as a prescription. Every claim verifies, but
  each current medication is listed twice. Examples: Abbie917 Ullrich385
  (`a2c37342-f80b-4243-a201-5290c89a58da`), and Tonja658 Grant908
  (`a2c37430-0a29-4c57-8195-2c924c2b654a`). Tonja's ten coded allergies do
  render by name, so use her only to show allergies.

## Credentials

The collection needs two things the environment file does not carry.

1. **`client_id` and `client_secret`** of an OAuth client that OpenEMR has
   enabled for the password grant.
2. **A clinician login** (`user_role=users`) for `openemr_username` and
   `openemr_password`. Use the demo account `physician` / `physician`. It
   is public in the repository README, and findings F-32 records that such
   demo accounts are themselves a risk.

**Getting a client id and secret.** Registering and enabling a client needs
root SSH access to the host. A grader without that access asks the operator
for a `client_id` and `client_secret`, passed on outside the repository. The
operator creates one on the host:

```bash
ssh root@64.177.42.210

# 1. Register a client. It prints a client id and a client secret, each in
#    double quotes; drop the quotes. Record the secret now: it is stored
#    encrypted and cannot be read back later (F-05).
docker exec development-easy-openemr-1 /root/devtools register-oauth2-client

# 2. Enable it and allow the password grant. A newly registered client is
#    disabled and allows only authorization_code (F-02). Enter the database
#    root password when prompted (F-34).
docker exec -it development-easy-mysql-1 mariadb -uroot -p openemr -e \
  "UPDATE oauth_clients SET is_enabled = 1, grant_types = 'authorization_code,password,refresh_token' WHERE client_id = '<client id from step 1>';"
```

The client is registered with OpenEMR's full API scope list, which includes
the four reads the collection requests. These are the same one-time steps
as in `agent/scripts/smoke_test_fhir.py`. They were checked against the
Vultr host on 2026-09-17, read-only:
- The `devtools` command and both container names exist.
- The password grant is on (`oauth_password_grant` = 3).
- The host's enabled password-grant clients have exactly the configuration
  step 2 sets, and one of them passed the full collection (below).

The steps were not re-run to create a new client.

## Run it

Verified passing on 2026-09-17 against the Vultr host, with the four
credentials supplied: a password-grant client configured as above, and
`physician` / `physician`. Newman ran 8 requests and 13 assertions with 0
failures. With the environment file as committed (empty credentials), the
token request in folder 2 fails, and so do the requests that need its token.

With [Newman](https://www.npmjs.com/package/newman) (Node 18+), passing the
credentials on the command line so no file holds them:

```bash
export FHIR_CLIENT_ID='<client id>' FHIR_CLIENT_SECRET='<client secret>'
export OPENEMR_USERNAME=physician OPENEMR_PASSWORD=physician

npx newman run agent/api-collection/clinical-copilot-agent.postman_collection.json \
  -e agent/api-collection/vultr.postman_environment.json \
  --env-var client_id="$FHIR_CLIENT_ID" \
  --env-var client_secret="$FHIR_CLIENT_SECRET" \
  --env-var openemr_username="$OPENEMR_USERNAME" \
  --env-var openemr_password="$OPENEMR_PASSWORD" \
  --insecure
```

Or import both files into Postman, select the environment, and fill in the
four credential values there. Do not export the filled-in environment back
into the repository. Turn off *Settings → General → SSL certificate
verification*, and run the collection.

`--insecure` is needed because both OpenEMR (port 9300) and the agent (port
8443) use certificates that are not publicly trusted — OpenEMR's is
self-signed (F-36); the agent's comes from the proxy's own local CA. To verify
the agent's certificate instead of skipping verification for it, export that
CA's root as shown in `agent/docs/deployment.md` and pass
`--ssl-extra-ca-certs root.crt` (OpenEMR's port still needs `--insecure`).

## Getting a token

The agent never mints tokens: each request carries the clinician's own
OpenEMR access token (`docs/ARCHITECTURE.md` §2). Folder 2 does this for you;
by hand it is the same password grant the smoke tests use
(`agent/scripts/smoke_test_fhir.py`), with the variables exported in
[Run it](#run-it):

```bash
curl -k https://64.177.42.210:9300/oauth2/default/token \
  --data-urlencode grant_type=password \
  --data-urlencode client_id="$FHIR_CLIENT_ID" \
  --data-urlencode client_secret="$FHIR_CLIENT_SECRET" \
  --data-urlencode username="$OPENEMR_USERNAME" \
  --data-urlencode password="$OPENEMR_PASSWORD" \
  --data-urlencode user_role=users \
  --data-urlencode scope="openid user/Patient.read user/Condition.read user/MedicationRequest.read user/AllergyIntolerance.read"
```

Then call the agent with the returned `access_token`:

```bash
curl -k https://64.177.42.210:8443/pre-visit-brief \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"patient_id": "a2c36e54-af4c-4fde-a609-4fe1419590ac"}'
```

The client and login come from [Credentials](#credentials). The client used
before the migration does not exist on the Vultr host.

Tokens expire after one hour. An expired token, a revoked one and one missing
a scope all come back as `401 fhir_token_rejected` — OpenEMR does not
distinguish them (F-08), so request a fresh token with the four scopes above.

---

## Week 2 core flow — `w2-core-flow.postman_collection.json`

One clinician encounter, end to end, against the **local** stack
(`docs/w2/LOCAL_ENV.md`). Every agent request carries the same
`X-Correlation-ID` (`{{encounter_id}}`, a UUID minted per run), so the
whole encounter is one correlation id in the agent's log and one trace in
Langfuse (`docs/w2/OBSERVABILITY.md` §2).

| Folder | Requests | What the tests check |
|---|---|---|
| 0. Setup | `POST {openemr}/oauth2/default/token` (password grant) | a token with `user/lab-writeback.c`; kept in a collection variable for this run only |
| 1. Health | `GET /health`, `GET /ready` | `/health` 200; `/ready` names `fhir`, `llm_provider`, `observability_backend`, and is 503 exactly when `not_ready` (`degraded` is 200, D58) |
| 2. Encounter | upload → extraction status (polled until final) → Pre-Visit Brief → one conversation turn → `/w2/ask` about the uploaded document → `/w2/ask` guideline question → write-back → same write-back again → OpenEMR sign-in → open chart → document preview | upload returns a status, not a wait (D7); a completed extraction has an extraction id; the correlation id comes back on every agent response; every claim went through the verifier; each `/w2/ask` records a routing decision with its reason (D4) and a final status; write-back returns real ids, and the repeat returns `duplicate_ignored` with the **same** ids (invariant 6); the preview is the PDF |
| 3. Refusals | upload without a token; unknown document; unknown patient; another chart's document; end the conversation | 401, 404, 404 `patient_not_found`, 404, 200 |

Files:

- `w2-core-flow.postman_collection.json` — the requests and tests.
- `local.postman_environment.json` — local URLs, the demo patient and the
  token scope. `client_id`, `client_secret`, `openemr_username` and
  `openemr_password` ship **empty**.
- `fixtures/lab-clean-001.pdf` — the doc-factory's clean lab sample
  (synthetic). `fixtures/lab-writeback.json` — a `LabWriteBackRequest` for
  it, built by `build_fixtures.py` through the agent's own
  `plan_write_back()`, so every idempotency key is the contract's
  derivation. Regenerate with `cd agent && .venv/bin/python
  api-collection/build_fixtures.py` (byte-identical output).
- `run.sh` — runs it with newman.

### What each variable is

| Variable | Local value | Meaning |
|---|---|---|
| `agent_base_url` | `http://127.0.0.1:8100` | The agent. Started from this checkout by `agent/load-test/w2/start_local_agent.sh`. The committed container behind `https://127.0.0.1:8443` also works if it runs this branch. |
| `openemr_base_url` | `https://localhost:9300` | Local OpenEMR (token endpoint, write-back endpoint, preview page). |
| `client_id`, `client_secret` | *(empty)* | An OpenEMR API client with the **password grant** and the scopes in `token_scope`. |
| `openemr_username`, `openemr_password` | *(empty)* | A clinician login; `run.sh` defaults to the public demo account `physician` / `physician`. |
| `patient_uuid` | `a2c373ec-57bf-41cb-8767-94410dcf663d` | The patient the lab fixture belongs to (doc-factory sample `lab-clean-001`). Same UUID on the Vultr host and locally (`docs/w2/LOCAL_ENV.md`). |
| `patient_pid` | `38` | That patient's **local** pid, for opening the chart before the preview. pids differ per instance: `SELECT pid FROM patient_data WHERE uuid = UNHEX(REPLACE('<uuid>','-',''))`. |
| `question` | `What medications is the patient on?` | The conversation turn's question. |
| `document_question`, `guideline_question` | see the file | The two `/w2/ask` questions: one the supervisor answers from the uploaded document, one it sends to the evidence retriever. |

### One-time setup (local)

Register a client through OpenEMR's own registration endpoint, then enable
it and allow the password grant (an admin does the second half in *Admin →
System → API Clients*; locally it is one SQL statement):

```bash
curl -sk -X POST https://localhost:9300/oauth2/default/registration \
  -H 'Content-Type: application/json' -d '{
    "application_type": "private", "client_name": "w2-api-collection-local",
    "redirect_uris": ["https://localhost:9300/interface/modules/custom_modules/oe-module-clinical-copilot/public/oauth-callback.php"],
    "token_endpoint_auth_method": "client_secret_post",
    "contacts": ["local-dev@example.invalid"],
    "scope": "openid api:oemr api:fhir user/Patient.read user/Condition.read user/MedicationRequest.read user/AllergyIntolerance.read user/Observation.read user/Encounter.read user/lab-writeback.c"
  }' > reg.json    # the secret is shown once; keep reg.json out of the repo

docker exec development-easy-mysql-1 mariadb -uroot -proot -e "
  UPDATE openemr.oauth_clients
     SET is_enabled = 1, grant_types = 'authorization_code,password,refresh_token'
   WHERE client_name = 'w2-api-collection-local';"
```

### Run

```bash
FHIR_CLIENT_ID=<client_id> FHIR_CLIENT_SECRET=<client_secret> agent/api-collection/run.sh
# one folder:  ... run.sh --folder "1. Health"
```

In the Postman app: import both JSON files, fill the four empty values in
the environment, and run the collection with the Runner (order matters —
later requests read ids earlier ones saved). Point the file bodies at
`fixtures/` if Postman asks.

**Last local run:** 2026-09-22, against the real routes (after
`feat/w2-graph`). 44 requests, status polls included; 102 assertions;
0 failures. Output: `docs/w2/evidence/api-collection-run.txt`.

**What one run costs and takes.** It makes real model calls, about $0.82 per
run:
- the extraction's vision call, on the first upload of the PDF per agent
  process (identical bytes are the same document and are not re-extracted);
- the brief, the conversation turn and both asks.

It takes about 3 minutes. The `/w2/ask` about the document alone takes
90–130 s (`docs/w2/LOAD_TEST.md` §6), and the extraction 15–30 s.

**What is not an agent call.** The write-back is the OpenEMR module's
endpoint, called directly with the clinician's token. That is the same call
`app/writeback/client.py` makes, but no agent route posts a write-back yet.
