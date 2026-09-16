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
- `droplet.postman_environment.json` — the deployed droplet's URLs and the
  seeded patient `a2c0e096-23d5-4cf9-ad5b-4895597363a7`. Credentials are
  empty placeholders; never commit filled-in values.

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
curl -k https://201.79.25.22:9300/oauth2/default/token \
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
curl -k https://201.79.25.22:8443/pre-visit-brief \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"patient_id": "a2c0e096-23d5-4cf9-ad5b-4895597363a7"}'
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
