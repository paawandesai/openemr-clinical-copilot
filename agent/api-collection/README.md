# API collection — Clinical Co-Pilot agent

A Postman v2.1 collection that runs end to end without reading the source:

| Folder | Requests | Checks |
|---|---|---|
| 1. Liveness and readiness | `GET /health`, `GET /ready` | 200 `alive`; 200 `ready` with FHIR and LLM provider healthy |
| 2. Get a clinician token | `POST {openemr_url}/oauth2/default/token` | OpenEMR issues a token; stored for folders 3–4 |
| 3. Pre-Visit Brief | `POST /pre-visit-brief` for the seeded patient | 200; brief rendered; every claim verified or marked unverified; `user:patient:session` key; correlation id matches header; token never echoed |
| 4. Failure paths | no token; malformed `patient_id`; unknown patient; token OpenEMR rejects | 401 `missing_bearer_token`; 422 `invalid_request`; 404 `patient_not_found`; 401 `fhir_token_rejected` |

Files:

- `clinical-copilot-agent.postman_collection.json` — the requests and tests.
- `droplet.postman_environment.json` — the deployment host's URLs and a
  seeded `patient_id`. Credentials are empty placeholders; never commit
  filled-in values.

**Set `patient_id` before running against the current host.** The file
name and its default `patient_id`, `a2c0e096-23d5-4cf9-ad5b-4895597363a7`
(Phil Belford), predate the move from DigitalOcean to Vultr. The Vultr host
is a fresh install with new FHIR ids, so that id does not exist there.
Phil Belford on Vultr is `a2c36e54-af42-4c38-b86e-ade616ed5494`.

**Phil Belford is not a clean demo.** His penicillin allergy is stored
without an allergen code. The brief shows it as `(unspecified allergen)`
and counts it as verified. That is defect F-57 (`docs/audit/findings.md`),
not correct output. The collection's checks still pass on him, because they
test the contract, not the allergen name. To demo a brief whose allergies
are named correctly, use a patient with coded allergies. On Vultr,
Tonja658 Grant908 (`a2c37430-0a29-4c57-8195-2c924c2b654a`) has ten coded
allergies, all returned verified. Her brief currently lists each current
medication twice, because her chart holds each one both as a medication
list entry and as a prescription.

## Run it

With [Newman](https://www.npmjs.com/package/newman) (Node 18+):

```bash
cp agent/api-collection/droplet.postman_environment.json /tmp/copilot-env.json
# fill in client_id, client_secret, openemr_username, openemr_password in /tmp/copilot-env.json

npx newman run agent/api-collection/clinical-copilot-agent.postman_collection.json \
  -e /tmp/copilot-env.json \
  --insecure
```

Or import both files into Postman, select the environment, turn off
*Settings → General → SSL certificate verification*, and run the collection.

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
(`agent/scripts/smoke_test_fhir.py`):

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
  -d '{"patient_id": "a2c37430-0a29-4c57-8195-2c924c2b654a"}'
```

What you need from the operator:

- **client_id / client_secret** of an OAuth client OpenEMR has enabled for the
  password grant. A newly registered client cannot mint tokens until an
  administrator enables it (F-02; the one-time steps are in the
  `agent/scripts/smoke_test_fhir.py` docstring).
- **An OpenEMR clinician login** (`user_role=users`). The smoke tests default
  to the seeded demo account; findings F-32 records that such demo accounts
  are themselves a live risk.

Tokens expire after one hour. An expired token, a revoked one and one missing
a scope all come back as `401 fhir_token_rejected` — OpenEMR does not
distinguish them (F-08), so request a fresh token with the four scopes above.
