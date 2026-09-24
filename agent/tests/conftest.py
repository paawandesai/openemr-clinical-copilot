"""
Suite-wide guard: no unit test writes to an OpenEMR.

Since D54 a lab upload through `POST /documents` ends by posting its rows
to the write-back endpoint, whose URL defaults to the deployed host. Every
test that drives the route with a lab sample would reach it. This swaps
the writer for one that records the request and answers as an unreachable
endpoint would — the path `after_extraction` already handles — so the suite
stays offline (CLAUDE.md: unit tests use no network and no credentials).
A test that wants a write to succeed passes its own `writer`.
"""

from __future__ import annotations

import pytest

from app.models.w2.writeback import LabWriteBackRequest, LabWriteBackResponse
from app.writeback import after_extraction
from app.writeback.exceptions import WriteBackUnavailable


class OfflineWriter:
    def __init__(self) -> None:
        self.requests: list[tuple[str, LabWriteBackRequest]] = []

    async def __call__(self, token: str, request: LabWriteBackRequest) -> LabWriteBackResponse:
        self.requests.append((token, request))
        raise WriteBackUnavailable("the unit suite has no write-back endpoint")


@pytest.fixture(autouse=True)
def offline_writeback(monkeypatch: pytest.MonkeyPatch) -> OfflineWriter:
    writer = OfflineWriter()
    monkeypatch.setattr(after_extraction, "write_lab_results", writer)
    return writer
