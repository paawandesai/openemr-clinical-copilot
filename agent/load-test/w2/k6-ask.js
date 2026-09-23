// Ask-path load test against the LOCAL stack — POST /pre-visit-brief/conversation
// (one conversation turn: authorize, route, FHIR retrieval, draft, verify,
// render) or, with ROUTE=brief, POST /pre-visit-brief.
//
// Run with the agent started STUB=true (AGENT_STUB_LLM_DRAFTING): the model
// call is replaced by a same-shape draft built from the retrieved records,
// so these numbers are the agent + OpenEMR under concurrency, not
// Anthropic's latency or rate limits. agent/load-test/README.md explains why
// the two are measured apart; agent/load-test/w2/README.md has the commands.
//
//   AGENT_BASE_URL=http://127.0.0.1:8100 AGENT_TOKEN=... VUS=10 DURATION=60s \
//     k6 run --summary-export=ask-10vu.json agent/load-test/w2/k6-ask.js
//
// One VU is one clinician asking a question, reading the answer for a
// second, and asking again. Each iteration is a new session (turn 1), so
// every request does the full retrieval rather than hitting stored context.
import http from "k6/http";
import { check, sleep } from "k6";
import { Trend } from "k6/metrics";

const BASE_URL = __ENV.AGENT_BASE_URL || "http://127.0.0.1:8100";
const TOKEN = __ENV.AGENT_TOKEN;
const PATIENT_ID = __ENV.PATIENT_ID || "a2c36e54-af42-4c38-b86e-ade616ed5494";
const ROUTE = __ENV.ROUTE || "conversation";
const QUESTION = __ENV.QUESTION || "What medications is the patient on?";

if (!TOKEN) {
  throw new Error("AGENT_TOKEN is required: a clinician OpenEMR access token");
}

// Server-side step timings from the response body, so the by-step split is
// in the k6 summary too, not only in the agent's log.
const retrieveSeconds = new Trend("step_retrieve_seconds");
const draftSeconds = new Trend("step_draft_seconds");
const verifySeconds = new Trend("step_verify_seconds");

export const options = {
  vus: Number(__ENV.VUS || 10),
  duration: __ENV.DURATION || "60s",
  insecureSkipTLSVerify: true,
  summaryTrendStats: ["avg", "min", "med", "p(50)", "p(90)", "p(95)", "p(99)", "max"],
  thresholds: {
    // Informational: a run that fails these still completes and reports.
    http_req_failed: [{ threshold: "rate<=1", abortOnFail: false }],
  },
};

export default function () {
  const session = `k6-${__VU}-${__ITER}-${Date.now()}`;
  const url = ROUTE === "brief" ? `${BASE_URL}/pre-visit-brief` : `${BASE_URL}/pre-visit-brief/conversation`;
  const body =
    ROUTE === "brief"
      ? { patient_id: PATIENT_ID, session_id: session }
      : { patient_id: PATIENT_ID, session_id: session, question: QUESTION };
  const res = http.post(url, JSON.stringify(body), {
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${TOKEN}`,
      "X-Correlation-ID": session,
    },
    timeout: "60s",
  });
  const ok = check(res, { "status is 200": (r) => r.status === 200 });
  if (ok) {
    const timing = res.json("timing");
    retrieveSeconds.add(timing.retrieve_seconds);
    draftSeconds.add(timing.draft_seconds);
    verifySeconds.add(timing.verify_seconds);
  }
  sleep(1);
}
