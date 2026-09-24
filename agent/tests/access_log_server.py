"""
The agent under uvicorn's own CLI, with OpenEMR, the vision call and the
write-back faked — the server `tests/test_access_log.py` reads the stdout of.

    python -m tests.access_log_server <uvicorn argv...>

The fakes are installed before uvicorn runs and before anything imports
`app.main`, so the order of events is the deployed one: uvicorn configures
its loggers first, then imports the app. Nothing here touches logging.
"""

from __future__ import annotations

import sys

import uvicorn.main

from app.api import documents
from app.fhir import client as fhir_client
from app.workers import document_store
from app.workers.document_store import DocumentStore
from app.writeback import after_extraction
from app.writeback.exceptions import WriteBackUnavailable
from tests.intake_extractor_support import chart_for, ground_truth_vision, patient_uuid

SAMPLE = "lab-clean-001"
PATIENT = patient_uuid(SAMPLE)


async def _ensure_patient_exists(token: str, patient_id: str) -> None:
    return None


def _chart_reader(token: str, **_: object):  # noqa: ANN202 - a ChartReader
    async def read(uuid: str):  # noqa: ANN202
        return chart_for(SAMPLE)

    return read


async def _offline_writer(token: str, request: object) -> object:
    raise WriteBackUnavailable("no write-back endpoint in this test server")


def main(argv: list[str]) -> None:
    fhir_client.ensure_patient_exists = _ensure_patient_exists
    documents.fhir_chart_reader = _chart_reader
    document_store.DOCUMENTS = DocumentStore(vision=ground_truth_vision(SAMPLE))
    after_extraction.write_lab_results = _offline_writer
    assert "app.main" not in sys.modules, "the app must be imported by uvicorn, as deployed"
    uvicorn.main.main(argv)


if __name__ == "__main__":
    main(sys.argv[1:])
