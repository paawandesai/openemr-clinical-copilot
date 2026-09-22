// Infra load test — POST /pre-visit-brief with AGENT_STUB_LLM_DRAFTING=true
// on the target (app/reasoning/draft_claims.py). Measures the agent's OWN
// behavior at concurrency: connection handling, real FHIR round-trips
// against the droplet's OpenEMR, queue depth — with the one LLM call
// short-circuited so Anthropic's latency and cost never enter these
// numbers. See ../load-test/README.md for the full methodology and why
// this is run separately from the small real end-to-end batch.
//
// Run from OUTSIDE the droplet under test (this laptop, or a Railway
// container — README.md) so the generator's own CPU never competes with
// the system being measured.
//
//   AGENT_BASE_URL=https://64.177.42.210:8443 \
//   AGENT_TOKEN=<clinician bearer token, ARCHITECTURE.md §2> \
//   PATIENT_ID=a2c36e54-af42-4c38-b86e-ade616ed5494 \
//   VUS=10 DURATION=60s \
//     k6 run --summary-export=results-10vu.json k6-infra.js
//
// One VU ~= one clinician issuing one brief per second (sleep(1) below),
// not a tight hammer loop — 10 VUs is ~10 req/s sustained, 50 VUs ~50 req/s,
// matching "10 and 50 concurrent users" rather than a raw throughput ceiling.

import http from "k6/http";
import { check, sleep } from "k6";

const BASE_URL = __ENV.AGENT_BASE_URL || "https://64.177.42.210:8443";
const TOKEN = __ENV.AGENT_TOKEN;
const PATIENT_ID = __ENV.PATIENT_ID || "a2c36e54-af42-4c38-b86e-ade616ed5494";

if (!TOKEN) {
  throw new Error("AGENT_TOKEN is required — a clinician OpenEMR access token (see README.md)");
}

export const options = {
  insecureSkipTLSVerify: true, // Caddy's local CA (F-36-adjacent; deployment.md §1)
  vus: Number(__ENV.VUS || 10),
  duration: __ENV.DURATION || "60s",
  summaryTrendStats: ["avg", "min", "med", "p(50)", "p(90)", "p(95)", "p(99)", "max"],
  thresholds: {
    // Informational, not a hard gate: not passing doesn't fail the run.
    http_req_failed: [{ threshold: "rate<=1", abortOnFail: false }],
  },
};

function sessionId() {
  // SESSION_ID_PATTERN (app/models/api.py): [A-Za-z0-9-]{8,64}.
  return `k6-${__VU}-${__ITER}-${Date.now()}`;
}

export default function () {
  const res = http.post(
    `${BASE_URL}/pre-visit-brief`,
    JSON.stringify({ patient_id: PATIENT_ID, session_id: sessionId() }),
    {
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${TOKEN}`,
      },
      timeout: "30s",
    }
  );
  check(res, {
    "status is 200": (r) => r.status === 200,
    "brief was rendered": (r) => {
      if (r.status !== 200) return false;
      try {
        return typeof JSON.parse(r.body).brief === "string";
      } catch (e) {
        return false;
      }
    },
  });
  sleep(1);
}
