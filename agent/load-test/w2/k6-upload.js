// Extraction-path load test — POST /documents, then poll
// GET /documents/{id}/extraction until the status is final (D7).
//
// The ask path and this path are different paths by design (D7: extraction
// runs at upload, never at question time), so they are loaded separately.
//
// On main, POST /documents is still the STUB (app/api/documents.py): it
// hashes the bytes and runs no extractor, so what this measures is the
// upload surface — body read, SHA3-512, the status state machine — not
// extraction. The extraction work itself is measured in-process by
// extraction_bench.py. This script is what to re-run, unchanged, once the
// real extractor is behind the route.
//
//   AGENT_BASE_URL=http://127.0.0.1:8100 AGENT_TOKEN=... VUS=10 DURATION=60s \
//   PDF=agent/api-collection/fixtures/lab-clean-001.pdf \
//     k6 run --summary-export=upload-10vu.json agent/load-test/w2/k6-upload.js
import http from "k6/http";
import { check, sleep } from "k6";
import { Trend, Counter } from "k6/metrics";

const BASE_URL = __ENV.AGENT_BASE_URL || "http://127.0.0.1:8100";
const TOKEN = __ENV.AGENT_TOKEN;
const PDF = open(__ENV.PDF || "../../api-collection/fixtures/lab-clean-001.pdf", "b");
const POLL_SECONDS = Number(__ENV.POLL_SECONDS || 1);

if (!TOKEN) {
  throw new Error("AGENT_TOKEN is required");
}

const uploadToComplete = new Trend("upload_to_complete_seconds");
const polls = new Counter("status_polls");

export const options = {
  vus: Number(__ENV.VUS || 10),
  duration: __ENV.DURATION || "60s",
  insecureSkipTLSVerify: true,
  summaryTrendStats: ["avg", "min", "med", "p(50)", "p(90)", "p(95)", "p(99)", "max"],
  thresholds: { http_req_failed: [{ threshold: "rate<=1", abortOnFail: false }] },
};

export default function () {
  // Make every upload a distinct document: a trailing comment changes the
  // digest, and the stub (like the real route) dedups identical bytes.
  const marker = `\n% k6 ${__VU}-${__ITER}-${Date.now()}\n`;
  const bytes = new Uint8Array(PDF.byteLength + marker.length);
  bytes.set(new Uint8Array(PDF), 0);
  for (let i = 0; i < marker.length; i++) bytes[PDF.byteLength + i] = marker.charCodeAt(i);

  const correlation = `k6-up-${__VU}-${__ITER}-${Date.now()}`;
  const headers = { Authorization: `Bearer ${TOKEN}`, "X-Correlation-ID": correlation };
  const started = Date.now();
  const res = http.post(`${BASE_URL}/documents?kind=lab_pdf`, bytes.buffer, {
    headers: { ...headers, "Content-Type": "application/pdf" },
    tags: { name: "POST /documents" },
  });
  if (!check(res, { "upload 200": (r) => r.status === 200 })) return;
  const id = res.json("document_id");

  for (let i = 0; i < 30; i++) {
    sleep(POLL_SECONDS);
    const status = http.get(`${BASE_URL}/documents/${id}/extraction`, {
      headers,
      tags: { name: "GET /documents/{id}/extraction" },
    });
    polls.add(1);
    if (!check(status, { "status 200": (r) => r.status === 200 })) return;
    const state = status.json("status");
    if (state === "complete" || state === "failed") {
      uploadToComplete.add((Date.now() - started) / 1000);
      check(status, { "extraction complete": () => state === "complete" });
      return;
    }
  }
}
