"""
Measures what one chart-side session costs in model tokens: a Pre-Visit
Brief, then four follow-up turns, on the deployed agent, for several
patients of different chart sizes (docs/COST_ANALYSIS.md §1.4).

    python scripts/measure_session_cost.py <patient_id> [<patient_id> ...] > session_calls.json
    # then, on the agent host, the token counts for those calls:
    docker logs --since <start> clinical-copilot-agent-agent-1 2>&1 | grep llm_usage_event > usage.jsonl
    python scripts/measure_session_cost.py --join session_calls.json usage.jsonl

The agent's responses carry no token counts; its log does, as
llm_usage_event keyed by correlation_id (counts and latency only, no
content). Every call here is a real, billed model call. Credentials and
defaults are the eval suite's (evals/README.md).
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from evals.harness import load_dotenv  # noqa: E402

QUESTIONS = (
    ("base set", "Which of her medications are active?"),
    ("reference back", "When was that prescribed?"),
    ("lab expansion", "What was her most recent hemoglobin?"),
    ("visit expansion, labs carried", "When was she last seen?"),
)
SCOPE = (
    "openid user/Patient.read user/Condition.read user/MedicationRequest.read "
    "user/AllergyIntolerance.read user/Observation.read user/Encounter.read"
)
INPUT_PER_MTOK, OUTPUT_PER_MTOK = 5.00, 25.00  # claude-opus-5, COST_ANALYSIS.md §1.2


def measure(patient_ids: list[str]) -> None:
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    fhir = os.environ.get("FHIR_BASE_URL", "https://64.177.42.210:9300/apis/default/fhir")
    agent = os.environ.get("EVAL_AGENT_URL", "https://64.177.42.210:8443")
    with httpx.Client(verify=False, timeout=120.0) as http:  # noqa: S501 - self-signed demo host (F-36)
        token = http.post(
            fhir.split("/apis/")[0] + "/oauth2/default/token",
            data={
                "grant_type": "password",
                "client_id": os.environ["FHIR_CLIENT_ID"],
                "client_secret": os.environ["FHIR_CLIENT_SECRET"],
                "username": os.environ.get("FHIR_TEST_USERNAME", "physician"),
                "password": os.environ.get("FHIR_TEST_PASSWORD", "physician"),
                "user_role": "users",
                "scope": SCOPE,
            },
        ).json()["access_token"]
        auth = {"Authorization": f"Bearer {token}"}
        calls = []
        for patient_id in patient_ids:
            brief = http.post(f"{agent}/pre-visit-brief", json={"patient_id": patient_id}, headers=auth).json()
            calls.append({
                "patient_id": patient_id, "kind": "brief", "label": "brief",
                "correlation_id": brief.get("correlation_id"), "claims": len(brief.get("claims") or []),
                "records": {s["resource_type"]: s["retrieved"] for s in brief.get("sections") or []},
                "total_seconds": (brief.get("timing") or {}).get("total_seconds"),
            })
            session_id = f"cost-{uuid.uuid4().hex[:16]}"
            for label, question in QUESTIONS:
                turn = http.post(
                    f"{agent}/pre-visit-brief/conversation",
                    json={"patient_id": patient_id, "session_id": session_id, "question": question},
                    headers=auth,
                ).json()
                calls.append({
                    "patient_id": patient_id, "kind": "turn", "label": label,
                    "correlation_id": turn.get("correlation_id"), "turn_status": turn.get("turn_status"),
                    "claims": len(turn.get("claims") or []),
                    "context_claims": (turn.get("conversation") or {}).get("context_claims"),
                    "retrieved": {r["resource_type"]: [r["considered"], r["retrieved"]] for r in turn.get("retrieval") or []},
                    "total_seconds": (turn.get("timing") or {}).get("total_seconds"),
                })
            http.post(
                f"{agent}/pre-visit-brief/conversation/end",
                json={"patient_id": patient_id, "session_id": session_id}, headers=auth,
            )
    json.dump(calls, sys.stdout, indent=1)


def join(calls_path: str, usage_path: str) -> None:
    usage = {}
    for line in Path(usage_path).read_text().splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("event_type") == "llm_usage_event":
            usage[event["correlation_id"]] = event["event_data"]
    for call in json.loads(Path(calls_path).read_text()):
        data = usage.get(call["correlation_id"])
        if data is None:
            print(f"{call['patient_id'][:13]}  {call['label']:<30} no model call logged ({call.get('turn_status')})")
            continue
        cost = data["input_tokens"] * INPUT_PER_MTOK / 1e6 + data["output_tokens"] * OUTPUT_PER_MTOK / 1e6
        detail = call.get("records") or call.get("retrieved")
        print(
            f"{call['patient_id'][:13]}  {call['label']:<30} in {data['input_tokens']:>6}  out {data['output_tokens']:>5}  "
            f"cache r/w {data['cache_read_input_tokens']}/{data['cache_creation_input_tokens']}  "
            f"model {data['latency_ms']:>6} ms  total {call['total_seconds']:.1f} s  ${cost:.4f}  "
            f"{call.get('turn_status', '')} {call['claims']} claims  {json.dumps(detail)}"
        )


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--join":
        join(sys.argv[2], sys.argv[3])
    elif len(sys.argv) > 1:
        measure(sys.argv[1:])
    else:
        sys.exit(__doc__)
