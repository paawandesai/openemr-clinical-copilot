"""Offline checks behind docs/w2/DEMO_SCRIPT.md. No model call, no network.

Run from agent/: python <this file>
"""

import asyncio
import sys

sys.path.insert(0, ".")

from app.supervisor.routing import classify  # noqa: E402
from app.workers.intake_extractor import attach_and_extract  # noqa: E402
from tests.intake_extractor_support import (  # noqa: E402
    chart_for,
    doc_kind,
    patient_uuid,
    pdf_bytes,
    recorded_vision,
)

QUESTIONS = (
    "What was her glucose on the uploaded lab report, and what does the USPSTF recommend "
    "about screening for prediabetes and type 2 diabetes?",
    "What was her potassium on the uploaded lab report?",
)


async def heavy_scan() -> None:
    sample = "lab-scanned-heavy-abnormal-002"

    async def chart_reader(_uuid):  # noqa: ANN001, ANN202
        return chart_for(sample)

    report = await attach_and_extract(
        patient_uuid(sample),
        pdf_bytes(sample),
        doc_kind(sample),
        vision=recorded_vision(sample),
        chart_reader=chart_reader,
        document_id=sample,
    )
    rows = [(f.field_path, f.outcome) for f in report.fields]
    print(f"## {sample}, recorded vision (agent/evals/w2/staging/transcriptions/)")
    for path, outcome in rows:
        if ".potassium." in path:
            print(f"{path} {outcome}")
    print(f"located {sum(o == 'located' for _, o in rows)} of {len(rows)} fields")


print("## classify() on the demo questions")
for question in QUESTIONS:
    intent = classify(question)
    print(f"{intent.intent.value} {list(intent.rule_ids)} {[c.text for c in intent.concepts]}")
print()
asyncio.run(heavy_scan())
