"""
Extraction state survives a restart (D60; app/workers/extraction_store.py and
its use in app/workers/document_store.py).

A "restart" here is what a process restart leaves behind: a new
`DocumentStore` — empty memory, a fresh job table — opened on the same file
with the same key. The vision call after the restart counts its calls, so a
test can show nothing was extracted twice.

The properties, each pinned below:

- a clinician asking about an uploaded document after a restart gets the same
  answer as before it, without a second vision call;
- the chart binding survives: another patient's lookup is still `None`, and
  the same bytes uploaded to another chart are still refused (D40 item 8);
- a wrong-patient document is still refused after a restart;
- an extraction the restart cut short comes back `failed` / `interrupted`,
  and uploading the file again extracts it;
- the file holds no patient uuid, name or value in the clear, and does not
  open under another key or on another document's row;
- production refuses to start without a usable path and key.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import stat
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from app.conversation.state import ConversationStore
from app.graph import supervisor
from app.models.w2.ask import AskResponse
from app.models.w2.documents import DocumentKind, ExtractionStatus
from app.models.w2.workers import ExtractionFailureReason, RoutingReasonCode
from app.workers import extraction_store
from app.workers.document_store import DocumentBoundElsewhere, DocumentStore
from app.workers.extraction_store import (
    DEV_DEFAULT_KEY,
    ExtractionStore,
    ExtractionStoreConfigError,
    open_from_env,
)
from app.writeback.after_extraction import WriteBackDecision
from tests.intake_extractor_support import chart_for, ground_truth_vision, patient_uuid, pdf_bytes
from tests.w2_graph_support import (
    LAB_SAMPLE,
    OTHER_PATIENT,
    PATIENT,
    TOKEN,
    USER,
    FakeChart,
    ScriptedDrafter,
    offline_retriever,
    stored_document,
)

KEY = bytes(range(32))
OTHER_KEY = bytes(range(1, 33))
#: A chart whose patient is not the one printed on lab-clean-001.
WRONG_CHART = "intake-clean-004"


class CountingVision:
    """The ground-truth transcriber, counting calls."""

    def __init__(self, sample: str = LAB_SAMPLE) -> None:
        self._inner = ground_truth_vision(sample)
        self.calls = 0

    async def __call__(self, kind, pdf, page_count):  # noqa: ANN001, ANN204
        self.calls += 1
        return await self._inner(kind, pdf, page_count)


def chart_reader(sample: str = LAB_SAMPLE):  # noqa: ANN201 - a ChartReader
    async def read(uuid: str):  # noqa: ANN202
        return chart_for(sample)

    return read


def open_store(path: Path, key: bytes = KEY, vision: CountingVision | None = None) -> DocumentStore:
    return DocumentStore(vision=vision or CountingVision(), persistence=ExtractionStore(path, key))


def ask(documents: DocumentStore, document_id: str, question: str) -> AskResponse:
    return asyncio.run(
        supervisor.run_ask_turn(
            TOKEN,
            USER,
            PATIENT,
            "session-restart-0001",
            question,
            [document_id],
            store=ConversationStore(),
            documents=documents,
            retriever=offline_retriever,
            drafter=ScriptedDrafter(),
        )
    )


def comparable(response: AskResponse) -> dict:
    """The response minus what differs between any two turns: ids and timings."""
    body = response.model_dump(mode="json")
    for name in ("correlation_id", "node_ms", "total_ms", "routing", "handoffs"):
        body.pop(name)
    body["answer_view"]["answer"].pop("correlation_id")
    return body


# --------------------------------------------------------------------------
# the store itself
# --------------------------------------------------------------------------


def test_a_report_comes_back_equal_to_the_one_saved(tmp_path: Path) -> None:
    before = open_store(tmp_path / "x.sqlite3")
    document_id = asyncio.run(stored_document(before))
    saved = before.report(document_id)
    assert saved is not None and saved.is_complete

    loaded = ExtractionStore(tmp_path / "x.sqlite3", KEY).load(document_id)
    assert loaded is not None
    assert loaded.report == saved
    assert loaded.output == saved.output
    assert loaded.patient_uuid == PATIENT
    assert loaded.pages == before.lookup(document_id, PATIENT).pages  # type: ignore[union-attr]


def test_the_file_is_owner_only_and_holds_nothing_from_the_document_in_the_clear(
    tmp_path: Path,
) -> None:
    path = tmp_path / "x.sqlite3"
    documents = open_store(path)
    document_id = asyncio.run(stored_document(documents))
    report = documents.report(document_id)
    assert report is not None and report.output.payload is not None

    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    on_disk = b"".join(p.read_bytes() for p in tmp_path.iterdir() if p.name.startswith("x.sqlite3"))
    name = report.output.payload.patient_header.name.as_presented_fact()
    assert name is not None
    # Strings long enough not to occur by chance in ciphertext: a two-digit
    # value would, so values are covered by the name and the test names.
    in_the_clear = [PATIENT, name.value, *name.value.split(), "Glucose", "Potassium"]
    for secret in in_the_clear:
        assert len(secret) >= 7 and secret.encode() not in on_disk, secret
    # The id is in the clear by design: it is the file's digest, which OpenEMR also stores.
    assert document_id.encode() in on_disk


def test_another_key_opens_nothing_and_says_so_by_id_only(tmp_path: Path) -> None:
    path = tmp_path / "x.sqlite3"
    document_id = asyncio.run(stored_document(open_store(path)))
    with capture_logs() as logs:
        assert ExtractionStore(path, OTHER_KEY).load(document_id) is None
    (event,) = [e for e in logs if e.get("event_type") == "extraction_store_row_unreadable"]
    assert event["event_data"] == {"document_id": document_id, "error_type": "InvalidTag"}
    # And the document is then unknown, as before D60: no 404-vs-409 leak, no guess.
    assert open_store(path, OTHER_KEY).lookup(document_id, PATIENT) is None


def test_a_sealed_record_does_not_open_on_another_documents_row(tmp_path: Path) -> None:
    path = tmp_path / "x.sqlite3"
    document_id = asyncio.run(stored_document(open_store(path)))
    moved = "f" * 128
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO extraction SELECT ?, kind, status, format, nonce, sealed, saved_at"
            " FROM extraction WHERE document_id = ?",
            (moved, document_id),
        )
    assert ExtractionStore(path, KEY).load(moved) is None
    assert ExtractionStore(path, KEY).load(document_id) is not None


# --------------------------------------------------------------------------
# a restart, through the document store
# --------------------------------------------------------------------------


def test_after_a_restart_the_same_question_gets_the_same_answer_without_extracting_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeChart().install(monkeypatch)
    path = tmp_path / "x.sqlite3"
    before = open_store(path)
    document_id = asyncio.run(stored_document(before))
    question = "What is the glucose on the uploaded report?"
    first = ask(before, document_id, question)
    assert first.status.value in ("answered", "partially_answered")
    assert first.answer_view is not None and first.answer_view.answer.patient_facts

    vision = CountingVision()
    after = open_store(path, vision=vision)
    with capture_logs() as logs:
        second = ask(after, document_id, question)

    assert comparable(second) == comparable(first)
    assert vision.calls == 0
    restored = [e for e in logs if e.get("event_type") == "w2_document_restored"]
    assert restored and restored[0]["event_data"] == {
        "document_id": document_id,
        "status": "complete",
        "has_report": True,
    }


def test_the_chart_binding_and_the_409_survive_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "x.sqlite3"
    document_id = asyncio.run(stored_document(open_store(path)))

    vision = CountingVision()
    after = open_store(path, vision=vision)
    assert after.lookup(document_id, OTHER_PATIENT) is None
    assert after.lookup(document_id, PATIENT) is not None

    async def upload_elsewhere() -> None:
        after.accept(OTHER_PATIENT, pdf_bytes(LAB_SAMPLE), DocumentKind.LAB_PDF)

    with pytest.raises(DocumentBoundElsewhere):
        asyncio.run(upload_elsewhere())
    assert vision.calls == 0


def test_the_409_holds_on_a_fresh_process_before_anything_else_asked(tmp_path: Path) -> None:
    """The first thing the new process sees is the other chart's upload: still refused."""
    path = tmp_path / "x.sqlite3"
    asyncio.run(stored_document(open_store(path)))

    async def upload_elsewhere() -> None:
        open_store(path).accept(OTHER_PATIENT, pdf_bytes(LAB_SAMPLE), DocumentKind.LAB_PDF)

    with pytest.raises(DocumentBoundElsewhere):
        asyncio.run(upload_elsewhere())


def test_a_re_upload_after_a_restart_is_a_duplicate_and_starts_no_job(tmp_path: Path) -> None:
    path = tmp_path / "x.sqlite3"

    async def first() -> None:
        async def wrote(report):  # noqa: ANN001, ANN202
            return WriteBackDecision.WRITTEN

        documents = open_store(path)
        stored, _, _ = documents.accept(
            PATIENT,
            pdf_bytes(LAB_SAMPLE),
            DocumentKind.LAB_PDF,
            chart_reader=chart_reader(),
            after_extraction=wrote,
        )
        await documents.wait(stored.document.document_id)
        await asyncio.sleep(0.05)  # the write-back step runs after the report

    asyncio.run(first())
    vision = CountingVision()
    after = open_store(path, vision=vision)
    written: list[object] = []

    async def reupload():  # noqa: ANN202
        async def write(report):  # noqa: ANN001, ANN202
            written.append(report)

        return after.accept(
            PATIENT, pdf_bytes(LAB_SAMPLE), DocumentKind.LAB_PDF, after_extraction=write
        )

    _, output, duplicate = asyncio.run(reupload())
    assert duplicate and output.status is ExtractionStatus.COMPLETE
    assert vision.calls == 0 and written == []


def test_a_wrong_patient_document_is_still_refused_after_a_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeChart().install(monkeypatch)
    path = tmp_path / "x.sqlite3"
    before = open_store(path)
    document_id = asyncio.run(stored_document(before, chart_sample=WRONG_CHART))
    report = before.report(document_id)
    assert report is not None and report.identity is not None and report.identity.hard_flag

    response = ask(open_store(path), document_id, "What is on this report?")
    assert response.reason_code is RoutingReasonCode.DOCUMENT_IDENTITY_MISMATCH
    assert response.answer_view is None


def test_an_extraction_a_restart_cut_short_is_interrupted_and_a_re_upload_restarts_it(
    tmp_path: Path,
) -> None:
    path = tmp_path / "x.sqlite3"
    pdf = pdf_bytes(LAB_SAMPLE)

    async def accept_and_die() -> str:
        stored, output, _ = open_store(path).accept(PATIENT, pdf, DocumentKind.LAB_PDF)
        assert output.status is ExtractionStatus.PENDING
        return stored.document.document_id  # the loop ends; the job never ran

    document_id = asyncio.run(accept_and_die())

    vision = CountingVision()
    after = open_store(path, vision=vision)
    status = after.status(document_id)
    assert status is not None and status.status is ExtractionStatus.FAILED
    assert status.failure_reason is ExtractionFailureReason.INTERRUPTED

    async def reupload() -> ExtractionStatus:
        async def read(uuid: str):  # noqa: ANN202
            return chart_for(LAB_SAMPLE)

        _, output, duplicate = after.accept(PATIENT, pdf, DocumentKind.LAB_PDF, chart_reader=read)
        assert duplicate and output.status is ExtractionStatus.PENDING
        report = await after.wait(document_id)
        assert report is not None
        return report.output.status

    assert asyncio.run(reupload()) is ExtractionStatus.COMPLETE
    assert vision.calls == 1
    # And that result is what the next restart sees.
    assert open_store(path).status(document_id).status is ExtractionStatus.COMPLETE  # type: ignore[union-attr]


def test_a_failed_save_does_not_fail_the_upload(tmp_path: Path) -> None:
    class Broken(ExtractionStore):
        def save(self, **kwargs) -> None:  # noqa: ANN003
            raise sqlite3.OperationalError("disk I/O error")

    documents = DocumentStore(
        vision=CountingVision(), persistence=Broken(tmp_path / "x.sqlite3", KEY)
    )
    with capture_logs() as logs:
        document_id = asyncio.run(stored_document(documents))
    assert documents.status(document_id).status is ExtractionStatus.COMPLETE  # type: ignore[union-attr]
    failures = [e for e in logs if e.get("event_type") == "extraction_store_save_failed"]
    assert {e["event_data"]["status"] for e in failures} == {"pending", "complete"}
    assert all(e["event_data"]["error_type"] == "OperationalError" for e in failures)


# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    for name in (
        extraction_store.PATH_ENV,
        extraction_store.KEY_ENV,
        extraction_store.RETENTION_ENV,
        "AGENT_ENV",
    ):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_outside_production_no_path_means_memory(env: pytest.MonkeyPatch) -> None:
    assert open_from_env() is None


def test_outside_production_no_key_means_the_labelled_dev_key(
    env: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env.setenv(extraction_store.PATH_ENV, str(tmp_path / "x.sqlite3"))
    with capture_logs() as logs:
        store = open_from_env()
    assert store is not None
    assert any(e["event"] == "extraction_store_key_dev_default" for e in logs)


@pytest.mark.parametrize(
    ("path", "key", "problem"),
    [
        (None, "ab" * 32, "AGENT_EXTRACTION_STORE_PATH is not set"),
        ("x.sqlite3", None, "AGENT_EXTRACTION_STORE_KEY is not set"),
        ("x.sqlite3", DEV_DEFAULT_KEY, "published dev default"),
        ("x.sqlite3", "ab" * 16, "not 64 hex characters"),
        ("x.sqlite3", "zz" * 32, "not 64 hex characters"),
        ("missing-dir/x.sqlite3", "ab" * 32, "cannot be opened"),
    ],
)
def test_production_refuses_to_start_without_a_usable_store(
    env: pytest.MonkeyPatch, tmp_path: Path, path: str | None, key: str | None, problem: str
) -> None:
    env.setenv("AGENT_ENV", "production")
    if path is not None:
        env.setenv(extraction_store.PATH_ENV, str(tmp_path / path))
    if key is not None:
        env.setenv(extraction_store.KEY_ENV, key)
    with pytest.raises(ExtractionStoreConfigError) as refused:
        open_from_env()
    assert problem in str(refused.value)
    if key is not None:
        assert key not in str(refused.value)


def test_production_opens_a_configured_store(env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env.setenv("AGENT_ENV", "production")
    env.setenv(extraction_store.PATH_ENV, str(tmp_path / "x.sqlite3"))
    env.setenv(extraction_store.KEY_ENV, "ab" * 32)
    assert open_from_env() is not None


def test_the_image_and_the_compose_file_put_the_store_on_a_volume() -> None:
    agent = Path(__file__).resolve().parents[1]
    dockerfile = (agent / "Dockerfile").read_text()
    compose = (agent / "deploy" / "docker-compose.agent.yml").read_text()
    assert "ENV AGENT_EXTRACTION_STORE_PATH=/var/lib/clinical-copilot/" in dockerfile
    assert "extraction_data:/var/lib/clinical-copilot" in compose
    assert "AGENT_EXTRACTION_STORE_KEY: ${AGENT_EXTRACTION_STORE_KEY:-}" in compose


# --------------------------------------------------------------------------
# retention
# --------------------------------------------------------------------------


def age_rows(path: Path, days: float) -> None:
    """Backdate every row's saved_at, as if written `days` ago."""
    from datetime import UTC, datetime, timedelta

    then = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with sqlite3.connect(path) as db:
        db.execute("UPDATE extraction SET saved_at = ?", (then,))


def test_the_default_retention_is_30_days() -> None:
    assert extraction_store.DEFAULT_RETENTION_DAYS == 30


def test_a_row_inside_retention_is_read_and_one_past_it_is_absent_and_deleted(
    tmp_path: Path,
) -> None:
    path = tmp_path / "x.sqlite3"
    document_id = asyncio.run(stored_document(open_store(path)))

    age_rows(path, 29.9)
    assert ExtractionStore(path, KEY).load(document_id) is not None

    age_rows(path, 30.1)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM extraction").fetchone()[0] == 1
    with capture_logs() as logs:
        assert open_store(path).lookup(document_id, PATIENT) is None
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM extraction").fetchone()[0] == 0
    (expired,) = [e for e in logs if e.get("event_type") == "extraction_store_rows_expired"]
    assert expired["event_data"] == {"rows": 1}


def test_a_row_that_expires_while_the_process_runs_is_not_read(tmp_path: Path) -> None:
    """No restart and no save in between: the read itself refuses it."""
    path = tmp_path / "x.sqlite3"
    document_id = asyncio.run(stored_document(open_store(path)))
    store = ExtractionStore(path, KEY)  # opened while the row is fresh
    age_rows(path, 30.1)
    assert store.load(document_id) is None


def test_an_expired_document_is_unknown_so_a_re_upload_extracts_it_again(
    tmp_path: Path,
) -> None:
    path = tmp_path / "x.sqlite3"
    asyncio.run(stored_document(open_store(path)))
    age_rows(path, 31)
    vision = CountingVision()
    asyncio.run(stored_document(open_store(path, vision=vision)))
    assert vision.calls == 1


def test_a_save_purges_rows_that_expired_while_the_process_ran(tmp_path: Path) -> None:
    path = tmp_path / "x.sqlite3"
    store = ExtractionStore(path, KEY)
    documents = DocumentStore(vision=CountingVision(), persistence=store)
    old_id = asyncio.run(stored_document(documents))
    age_rows(path, 31)
    # The same process, no restart: the next upload's save removes the old row.
    asyncio.run(stored_document(documents, "intake-clean-004", patient_uuid("intake-clean-004")))
    with sqlite3.connect(path) as db:
        ids = [row[0] for row in db.execute("SELECT document_id FROM extraction")]
    assert old_id not in ids and len(ids) == 1


def test_deleted_rows_are_overwritten_not_left_in_free_pages(tmp_path: Path) -> None:
    store = ExtractionStore(tmp_path / "x.sqlite3", KEY)
    assert store._db.execute("PRAGMA secure_delete").fetchone()[0] == 1


def test_the_retention_is_configurable_and_refused_when_malformed(
    env: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env.setenv(extraction_store.PATH_ENV, str(tmp_path / "x.sqlite3"))
    env.setenv(extraction_store.RETENTION_ENV, "7")
    store = open_from_env()
    assert store is not None and store.retention.days == 7
    for bad in ("0", "-1", "30d", "3651"):
        env.setenv(extraction_store.RETENTION_ENV, bad)
        with pytest.raises(ExtractionStoreConfigError, match="RETENTION_DAYS"):
            open_from_env()
