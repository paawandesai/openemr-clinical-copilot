#!/usr/bin/env python3
"""
Builds the request bodies the API collection sends, from committed sources.

    cd agent && .venv/bin/python api-collection/build_fixtures.py

- `fixtures/lab-clean-001.pdf` — the doc-factory's clean lab sample,
  copied verbatim. Synthetic: generated from a Synthea chart, not a real
  patient (agent/evals/w2/factory/README.md).
- `fixtures/lab-writeback.json` — a `LabWriteBackRequest` for that PDF,
  built by the agent's own `plan_write_back()` from the sample's ground
  truth, so every idempotency key and extraction id is the contract's own
  derivation (D1, D15) rather than a hand-typed string the endpoint would
  refuse. Four rows carry a standard LOINC code; the factory's ground truth
  records none, and `plan_write_back` skips an uncoded row (it has no test
  code to key on), so without them nothing would be writable.

Re-running this produces byte-identical output, so the collection's
write-back is idempotent across runs: the second POST answers
`duplicate_ignored` with the same ids.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AGENT))

from app.models.w2.factory_mapping import convert_ground_truth  # noqa: E402
from app.writeback.plan import plan_write_back  # noqa: E402

SAMPLE = AGENT / "evals/w2/factory/output/samples/lab-clean-001"
OUT = Path(__file__).resolve().parent / "fixtures"

#: Standard LOINC codes for the four rows the collection writes.
LOINC = {
    "glucose": "2345-7",
    "creatinine": "2160-0",
    "potassium": "2823-3",
    "hba1c": "4548-4",
}

EXTRACTOR_VERSION = "w2-api-collection-1"
#: Replaced per run by the collection with its encounter id (a UUID4: the
#: write-back contract requires one).
CORRELATION_ID = "00000000-0000-4000-8000-000000000000"


def main() -> None:
    OUT.mkdir(exist_ok=True)
    pdf_path = SAMPLE / "lab-clean-001.pdf"
    shutil.copyfile(pdf_path, OUT / "lab-clean-001.pdf")
    pdf = pdf_path.read_bytes()
    ground_truth = json.loads((SAMPLE / "lab-clean-001.ground_truth.json").read_text())
    extraction = convert_ground_truth(ground_truth).extraction
    coded = [
        result.model_copy(update={"loinc_code": LOINC[result.test_key]})
        for result in extraction.results
        if result.test_key in LOINC
    ]
    built = plan_write_back(
        extraction.model_copy(update={"results": coded}),
        patient_uuid=ground_truth["chart_source"]["patient_uuid"],
        document_sha3_512=hashlib.sha3_512(pdf).hexdigest(),
        extractor_version=EXTRACTOR_VERSION,
        correlation_id=CORRELATION_ID,
        content=pdf,
    )
    if built.request is None or built.writable_count != len(LOINC):
        raise SystemExit(f"expected {len(LOINC)} writable rows, got {built.writable_count}")
    (OUT / "lab-writeback.json").write_text(built.request.model_dump_json(indent=2) + "\n")
    print(f"wrote {built.writable_count} rows for patient {built.request.patient_uuid}")


if __name__ == "__main__":
    main()
