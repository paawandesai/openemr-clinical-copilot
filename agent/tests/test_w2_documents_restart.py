"""
The document routes across a restart, and the identity outcome they report (D60).

Through the real routes (app/api/documents.py) with the process's store
swapped for one on a file: upload, "restart" (a new store on the same file),
then poll, re-upload, and upload the same bytes to another chart. And
`identity_check`, the one field the module quarantines on: `mismatch` for a
document whose printed name contradicts the chart, `confirmed` for one that
matches, null until the extraction is complete.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import documents, upload_guard
from app.main import app
from app.models.w2.documents import ExtractionStatus
from app.models.w2.workers import IntakeExtractorOutput
from app.workers import document_store
from app.workers.document_store import DocumentStore
from app.workers.extraction_store import ExtractionStore
from tests.intake_extractor_support import chart_for, ground_truth_vision
from tests.test_w2_documents_api import (  # noqa: F401 - fixtures, used by name
    OTHER_PATIENT,
    PATIENT,
    SAMPLE,
    GatedVision,
    openemr,
    status,
    upload,
    wait_for_terminal,
)

KEY = bytes(range(32))
#: A chart whose patient is not the one printed on the sample.
WRONG_CHART = "intake-clean-004"


def restart(monkeypatch: pytest.MonkeyPatch, path: Path, vision=None) -> DocumentStore:  # noqa: ANN001
    fresh = DocumentStore(
        vision=vision or ground_truth_vision(SAMPLE), persistence=ExtractionStore(path, KEY)
    )
    monkeypatch.setattr(document_store, "DOCUMENTS", fresh)
    return fresh


def serve(monkeypatch: pytest.MonkeyPatch, chart_sample: str = SAMPLE) -> TestClient:
    def chart_reader(token: str, **_: object):  # noqa: ANN202
        async def read(uuid: str):  # noqa: ANN202
            return chart_for(chart_sample)

        return read

    monkeypatch.setattr(documents, "fhir_chart_reader", chart_reader)
    upload_guard.reset_for_tests()
    return TestClient(app)


@pytest.fixture
def path(tmp_path: Path) -> Iterator[Path]:
    yield tmp_path / "extractions.sqlite3"
    upload_guard.reset_for_tests()


@pytest.mark.usefixtures("openemr")
def test_a_restart_keeps_the_status_the_binding_and_the_409(
    monkeypatch: pytest.MonkeyPatch, path: Path
) -> None:
    restart(monkeypatch, path)
    with serve(monkeypatch) as client:
        document_id = upload(client).json()["document_id"]
        before = wait_for_terminal(client, document_id)
    assert before["status"] == "complete"

    restart(monkeypatch, path)
    with serve(monkeypatch) as client:
        after = status(client, document_id).json()
        assert {k: v for k, v in after.items() if k != "correlation_id"} == {
            k: v for k, v in before.items() if k != "correlation_id"
        }
        assert status(client, document_id, patient=OTHER_PATIENT).status_code == 404
        assert upload(client, patient=OTHER_PATIENT).status_code == 409
        again = upload(client).json()
        assert again["status"] == "complete" and again["identity_check"] == "confirmed"


@pytest.mark.usefixtures("openemr")
def test_an_interrupted_upload_reports_interrupted_and_can_be_uploaded_again(
    monkeypatch: pytest.MonkeyPatch, path: Path
) -> None:
    held = GatedVision()
    restart(monkeypatch, path, vision=held)
    with serve(monkeypatch) as client:
        document_id = upload(client).json()["document_id"]
        assert status(client, document_id).json()["status"] in ("pending", "in_progress")
        held.open()  # let the held job finish so the old loop shuts down cleanly

    # Put the row back to what it held at acceptance — a pending status and
    # no report — which is what a process that died mid-extraction leaves.
    store = ExtractionStore(path, KEY)
    saved = store.load(document_id)
    assert saved is not None
    store.save(
        patient_uuid=saved.patient_uuid,
        document=saved.document,
        pages=saved.pages,
        output=IntakeExtractorOutput(
            status=ExtractionStatus.PENDING,
            document_id=document_id,
            kind=saved.document.kind,
            correlation_id=saved.output.correlation_id,
        ),
        report=None,
    )

    restart(monkeypatch, path)
    with serve(monkeypatch) as client:
        interrupted = status(client, document_id).json()
        assert (interrupted["status"], interrupted["failure_reason"]) == ("failed", "interrupted")
        assert interrupted["identity_check"] is None
        assert upload(client).json()["status"] == "pending"
        assert wait_for_terminal(client, document_id)["status"] == "complete"


@pytest.mark.usefixtures("openemr")
def test_identity_check_is_null_until_complete_then_says_which(
    monkeypatch: pytest.MonkeyPatch, path: Path
) -> None:
    held = GatedVision()
    restart(monkeypatch, path, vision=held)
    with serve(monkeypatch, chart_sample=WRONG_CHART) as client:
        accepted = upload(client).json()
        assert accepted["identity_check"] is None
        assert status(client, accepted["document_id"]).json()["identity_check"] is None
        held.open()
        final = wait_for_terminal(client, accepted["document_id"])
        assert (final["status"], final["identity_check"]) == ("complete", "mismatch")
        # A re-upload of a finished mismatch says so at once: the module
        # quarantines on the upload's answer too.
        assert upload(client).json()["identity_check"] == "mismatch"
