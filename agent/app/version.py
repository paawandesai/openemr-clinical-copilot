"""
GET /version: which build is answering.

The Week 3 regression harness anchors every result on the commit it ran
against (docs/w3-target-hooks.md). The commit and build time are stamped into
the image at build time (agent/Dockerfile: AGENT_GIT_COMMIT, AGENT_BUILT_AT,
passed as build args by agent/deploy/docker-compose.agent.yml) and read from
the environment here; nothing is computed at request time, and the image
carries no .git directory to compute it from.

Unauthenticated, like /health: it says which code is running, nothing about
any patient or clinician. A value that is not the shape it should be is
reported as "unknown" / null rather than echoed, so a mis-set build arg cannot
make this endpoint publish arbitrary text, and so the harness sees a build it
cannot anchor on instead of a wrong anchor.
"""

from __future__ import annotations

import os
import re
from datetime import datetime

from fastapi import APIRouter

from app.models.api import VersionResponse

router = APIRouter()

UNKNOWN_COMMIT = "unknown"

# A full or abbreviated git object name: 7 to 40 lowercase hex, or 64 for a
# SHA-256 repository.
_COMMIT = re.compile(r"[0-9a-f]{7,40}|[0-9a-f]{64}")


def _commit() -> str:
    value = os.environ.get("AGENT_GIT_COMMIT", "").strip().lower()
    return value if _COMMIT.fullmatch(value) else UNKNOWN_COMMIT


def _built_at() -> str | None:
    value = os.environ.get("AGENT_BUILT_AT", "").strip()
    if not value:
        return None
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return None
    return value


@router.get("/version", response_model=VersionResponse)
async def version() -> VersionResponse:
    """The commit this image was built from, and when. Always 200."""
    return VersionResponse(commit=_commit(), built_at=_built_at())
