"""
Uploaded documents' extraction state, on disk, so that a restart does not
forget them (D60).

    store = ExtractionStore(path, key)
    store.save(patient_uuid=..., document=..., pages=..., output=..., report=None)
    saved = store.load(document_id)      # None if absent or unreadable

`DocumentStore` (document_store.py) writes a row when it accepts an upload
and again when the extraction ends, and reads a row back the first time a
document it does not hold in memory is asked about. What a clinician gets
after a restart is therefore the report that was produced before it: the
same located values, the same identity flag, the same unsupported list. No
vision call is repeated, so nothing can read differently the second time.

WHAT IS ON DISK
---------------
One SQLite file on a Docker volume (`AGENT_EXTRACTION_STORE_PATH`). Per
document, in the clear: its id (the file's SHA3-512, D1, which OpenEMR also
stores), its kind, its extraction status and when the row was written.
Everything else — the patient uuid it is bound to (D40 item 8), the page
geometry, the extraction and its report — is one JSON record sealed with
AES-256-GCM under `AGENT_EXTRACTION_STORE_KEY`, with the document id and
the record format as associated data, so a sealed record cannot be moved
onto another document's row. The key is in the deployment's env file, not
on the volume: a copy of the volume alone is ciphertext.

The PDF's bytes are never written here. The agent keeps them only while it
extracts (D51 item 1); the chart copy in OpenEMR is the document.

A row that does not open — the key changed, the file was damaged, the
record no longer validates against the contracts — is logged by document
id and error type and treated as absent. The document is then unknown to
the agent, exactly as it was after a restart before D60, and re-uploading
it extracts it again.

The record also holds the outcome of the document's lab write-back (D54),
so a re-upload after a restart knows whether one is still owed.

RETENTION — 30 DAYS FROM THE LAST WRITE
---------------------------------------
A row is deleted 30 days after it was last written
(`AGENT_EXTRACTION_STORE_RETENTION_DAYS`): at startup, on every save, and
when a read meets one, which then counts as absent. `secure_delete` is on,
so a deleted row's pages are overwritten rather than left free. Why 30:

- **It is a copy, and expiry loses nothing.** The document itself is in
  OpenEMR, encrypted and audited, for as long as the chart keeps it. An
  expired document is unknown to the agent, as every one was after a
  restart before D60: re-uploading it extracts it again (one vision call,
  $0.10–$0.23), the write-back ledger makes that write idempotent (D1), and
  the module's own chart records still refuse the bytes on another chart.
- **Patient data outside the EHR should last as long as its use.** A
  document is uploaded for a visit; 30 days covers the visit and the
  questions that follow it. Longer adds at-rest exposure for documents no
  one is asking about.
- **It matches the other at-rest store the agent writes to.** D58 keeps
  production telemetry 30 days, so both age out on one schedule.
- **From the last write, not the last read.** Asking about a document does
  not extend it. Writes are the upload, the end of the extraction and the
  write-back's outcome.

CONFIGURATION (`open_from_env`)
-------------------------------
- With `AGENT_ENV=production` (the image's default) the service refuses to
  start unless the path is set and the key is 64 hex characters that are
  not the published dev default — the same rule as D56's telemetry secret.
- Outside production an unset path keeps the store in memory, as before
  D60, and an unset key falls back to `DEV_DEFAULT_KEY`, said at startup.
- `AGENT_EXTRACTION_STORE_RETENTION_DAYS` defaults to 30; anything but a
  whole number of days from 1 to 3650 is refused at startup, in any mode.

NO PHI IN LOGS. Every line here carries a document id, a count or an error
type. Never a value from a record, never the patient uuid (invariant 1).
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn

import structlog
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import ValidationError

from app.middleware.logging import log_event
from app.middleware.telemetry_key import MODE_ENV, PRODUCTION, production_mode
from app.models.w2.answer_view import PageGeometry
from app.models.w2.documents import SourceDocument
from app.models.w2.workers import IntakeExtractorOutput
from app.models.w2.writeback import WriteBackSummary
from app.workers.intake_extractor.report import ExtractionReport

PATH_ENV = "AGENT_EXTRACTION_STORE_PATH"
KEY_ENV = "AGENT_EXTRACTION_STORE_KEY"
RETENTION_ENV = "AGENT_EXTRACTION_STORE_RETENTION_DAYS"

#: See "RETENTION" above.
DEFAULT_RETENTION_DAYS = 30
MAX_RETENTION_DAYS = 3650

#: Public, in the repository, and therefore no key at all. Refused in
#: production; outside it, said at startup.
DEV_DEFAULT_KEY = "d60" + "0" * 61

#: Bumped when the sealed record's shape changes. It is part of the
#: associated data, so an old row fails to open rather than half-parsing.
RECORD_FORMAT = 1

_NONCE_BYTES = 12

_SCHEMA = """
CREATE TABLE IF NOT EXISTS extraction (
    document_id TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    status      TEXT NOT NULL,
    format      INTEGER NOT NULL,
    nonce       BLOB NOT NULL,
    sealed      BLOB NOT NULL,
    saved_at    TEXT NOT NULL
)
"""


class ExtractionStoreConfigError(RuntimeError):
    """The store cannot be opened as configured. Names variables, never values."""


@dataclass(frozen=True)
class SavedExtraction:
    """One document as it was last saved."""

    patient_uuid: str
    document: SourceDocument
    pages: tuple[PageGeometry, ...]
    #: The status as saved: terminal when `report` is set, otherwise the
    #: pending/in-progress status of a job this process never finished.
    output: IntakeExtractorOutput
    report: ExtractionReport | None
    #: The lab write-back's outcome (`WriteBackDecision` value), None when
    #: none has finished: not run yet, or interrupted.
    writeback: str | None = None
    #: The same outcome with its counts and conflicts, as the card shows it.
    #: None for a record saved before summaries were, or before any ended.
    writeback_summary: WriteBackSummary | None = None


def parse_key(value: str) -> bytes:
    """A 256-bit key from 64 hex characters. Raises ValueError otherwise."""
    key = bytes.fromhex(value.strip())
    if len(key) != 32:
        raise ValueError("the key must be 32 bytes, as 64 hex characters")
    return key


class ExtractionStore:
    def __init__(
        self, path: str | Path, key: bytes, *, retention_days: int = DEFAULT_RETENTION_DAYS
    ) -> None:
        self.path = Path(path)
        self.retention = timedelta(days=retention_days)
        self._aead = AESGCM(key)
        if not self.path.exists():
            # Created owner-only before SQLite opens it; SQLite keeps the mode.
            descriptor = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
            os.close(descriptor)
        # One connection, used from the event loop's thread; statements are
        # single-row and short, so they are not moved to a worker thread.
        self._db = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        # Deleted rows (retention) are overwritten, not left in free pages.
        self._db.execute("PRAGMA secure_delete=ON")
        self._db.execute(_SCHEMA)
        self.purge_expired()

    def close(self) -> None:
        self._db.close()

    # -- writing ---------------------------------------------------------------

    def save(
        self,
        *,
        patient_uuid: str,
        document: SourceDocument,
        pages: tuple[PageGeometry, ...],
        output: IntakeExtractorOutput,
        report: ExtractionReport | None = None,
        writeback: str | None = None,
        writeback_summary: WriteBackSummary | None = None,
    ) -> None:
        """Insert or replace the document's row, then drop expired ones. Raises on I/O failure."""
        record = {
            "patient_uuid": patient_uuid,
            # Each model as its own JSON text: read back through
            # model_validate_json, the exact inverse of model_dump_json.
            "document": document.model_dump_json(),
            "pages": [page.model_dump_json() for page in pages],
            "output": output.model_dump_json(),
            "report": report.model_dump_json() if report is not None else None,
            "writeback": writeback,
            "writeback_summary": (
                writeback_summary.model_dump_json() if writeback_summary is not None else None
            ),
        }
        nonce = secrets.token_bytes(_NONCE_BYTES)
        plaintext = json.dumps(record, separators=(",", ":")).encode("utf-8")
        sealed = self._aead.encrypt(nonce, plaintext, _associated(document.document_id))
        self._db.execute(
            "INSERT OR REPLACE INTO extraction"
            " (document_id, kind, status, format, nonce, sealed, saved_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                document.document_id,
                document.kind.value,
                output.status.value,
                RECORD_FORMAT,
                nonce,
                sealed,
                _stamp(datetime.now(UTC)),
            ),
        )
        self.purge_expired()

    def purge_expired(self) -> int:
        """Delete every row older than the retention period. Returns how many."""
        deleted = self._db.execute(
            "DELETE FROM extraction WHERE saved_at < ?", (self._cutoff(),)
        ).rowcount
        if deleted:
            log_event("extraction_store_rows_expired", event_data={"rows": deleted})
        return deleted

    def _cutoff(self) -> str:
        return _stamp(datetime.now(UTC) - self.retention)

    # -- reading ---------------------------------------------------------------

    def load(self, document_id: str) -> SavedExtraction | None:
        """The saved document, or None when there is none or it cannot be opened."""
        row = self._db.execute(
            "SELECT format, nonce, sealed, saved_at FROM extraction WHERE document_id = ?",
            (document_id,),
        ).fetchone()
        if row is None:
            return None
        record_format, nonce, sealed, saved_at = row
        if saved_at < self._cutoff():
            self.purge_expired()
            return None
        try:
            if record_format != RECORD_FORMAT:
                raise ValueError("unknown record format")
            plaintext = self._aead.decrypt(nonce, sealed, _associated(document_id))
            return _parse(json.loads(plaintext))
        except (InvalidTag, ValueError, ValidationError, KeyError, TypeError) as error:
            log_event(
                "extraction_store_row_unreadable",
                event_data={"document_id": document_id, "error_type": type(error).__name__},
            )
            return None

    def count(self) -> int:
        return int(self._db.execute("SELECT COUNT(*) FROM extraction").fetchone()[0])


def _stamp(moment: datetime) -> str:
    """UTC, to the second, in one fixed format, so text order is time order."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _associated(document_id: str) -> bytes:
    return f"extraction:{RECORD_FORMAT}:{document_id}".encode("ascii")


def _parse(record: dict[str, Any]) -> SavedExtraction:
    report = record["report"]
    return SavedExtraction(
        patient_uuid=str(record["patient_uuid"]),
        document=SourceDocument.model_validate_json(record["document"]),
        pages=tuple(PageGeometry.model_validate_json(page) for page in record["pages"]),
        output=IntakeExtractorOutput.model_validate_json(record["output"]),
        report=ExtractionReport.model_validate_json(report) if report is not None else None,
        writeback=record.get("writeback"),
        writeback_summary=(
            WriteBackSummary.model_validate_json(summary)
            if (summary := record.get("writeback_summary")) is not None
            else None
        ),
    )


# -- configuration -------------------------------------------------------------


def open_from_env() -> ExtractionStore | None:
    """
    The store the environment configures, or None for in-memory state.
    Raises `ExtractionStoreConfigError` when production is misconfigured or
    the file cannot be opened.
    """
    log = structlog.get_logger()
    path = os.environ.get(PATH_ENV, "").strip()
    configured_key = os.environ.get(KEY_ENV, "").strip()
    production = production_mode()

    if not path:
        if production:
            _refuse(f"{PATH_ENV} is not set, and {MODE_ENV}={PRODUCTION}", "path_unset")
        log.warning(
            "extraction_store_in_memory",
            env_var=PATH_ENV,
            detail="uploads and their extractions are forgotten on restart. Never in production.",
        )
        return None

    if production:
        if not configured_key:
            _refuse(f"{KEY_ENV} is not set", "key_unset")
        if configured_key == DEV_DEFAULT_KEY:
            _refuse(f"{KEY_ENV} is the published dev default", "key_dev_default")
    elif not configured_key or configured_key == DEV_DEFAULT_KEY:
        log.warning(
            "extraction_store_key_dev_default",
            env_var=KEY_ENV,
            detail="dev default in use: the store is readable by anyone with the repo.",
        )
        configured_key = DEV_DEFAULT_KEY

    try:
        key = parse_key(configured_key)
    except ValueError:
        _refuse(f"{KEY_ENV} is not 64 hex characters", "key_malformed")

    retention = os.environ.get(RETENTION_ENV, "").strip() or str(DEFAULT_RETENTION_DAYS)
    if not retention.isdigit() or not 1 <= int(retention) <= MAX_RETENTION_DAYS:
        _refuse(
            f"{RETENTION_ENV} is not a whole number of days from 1 to {MAX_RETENTION_DAYS}",
            "retention_invalid",
        )

    try:
        store = ExtractionStore(path, key, retention_days=int(retention))
    except (OSError, sqlite3.Error) as error:
        log.error("startup_refused", reason="extraction_store_unopenable", env_var=PATH_ENV)
        raise ExtractionStoreConfigError(
            f"{PATH_ENV} names a file that cannot be opened ({type(error).__name__}). In the "
            "container it must be on the writable volume the compose file mounts."
        ) from error
    log.info("extraction_store_opened", documents=store.count(), retention_days=int(retention))
    return store


def _refuse(problem: str, reason: str) -> NoReturn:
    structlog.get_logger().error("startup_refused", reason=f"extraction_store_{reason}")
    raise ExtractionStoreConfigError(
        f"{problem}. Uploaded documents' extractions are kept on disk, sealed under this key, "
        "so a restart does not forget them (D60). Set both in the deployment's env file; make "
        "the key with `python -c 'import secrets; print(secrets.token_hex(32))'` and keep it: "
        "a new key makes every saved extraction unreadable."
    )
