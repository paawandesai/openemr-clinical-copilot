"""
Who may upload a document, how often, and how many extractions run at once.

The `/documents` routes are the one place an unauthenticated caller could
make this service do real work: read up to 25 MiB, and — once the
extractor is wired in — spend OCR CPU and vision-model money. Every other
route's work starts with a FHIR read that OpenEMR refuses for a bad token,
so a bad token costs one round trip. An upload's work starts with the body.
So this module decides everything it can **before the body is read**.

AUTHORIZATION — `authorize_document_request`
--------------------------------------------
The agent holds no OAuth credential of its own (ARCHITECTURE.md §2), so it
cannot call OpenEMR's introspection endpoint, which authenticates the
*client* asking. It does what `/pre-visit-brief` does, plus a scope check:

1. **Shape.** The bearer must be an OpenEMR access token: a JWT whose
   payload carries `sub`, `aud` (the OAuth client id) and `scopes`. A
   made-up string stops here — 401 `malformed_bearer_token`.
2. **Scope.** The token's `scopes` must include every entry of
   `REQUIRED_SCOPES` — the resources the extraction behind this route reads
   from the chart (`app/workers/intake_extractor/chart.py`). Otherwise 403
   `insufficient_scope`, naming the missing scopes.
3. **OpenEMR accepts it, for this patient.** `GET Patient/<patient_id>`
   with the token. OpenEMR checks the signature, expiry, revocation and the
   clinician's access; anything it refuses is 401 `fhir_token_rejected`,
   and an unknown patient is 404.

The claims in step 1-2 are read without checking the signature, and that
is safe for the same reason it is on `/pre-visit-brief`: nothing is done on
their strength until step 3, where OpenEMR verifies the very token they
came from. A forged token that claims every scope passes step 2 and fails
step 3. A forged token that claims too few gets a 403, which tells its
author nothing they did not write themselves.

LIMITS — `UploadLimits`
-----------------------
Counted only for requests that passed authorization, so a flood of bad
tokens cannot spend a clinician's budget:

- per OAuth client (`aud`) — every chart-embed upload shares one client, so
  this is the ceiling for the whole clinic;
- per user (`sub`);
- per patient.

Each is a sliding window. Over any of them: 429 with `Retry-After`.

**Concurrent extractions** are capped separately by `ExtractionSlots`, and
the cap does not queue: a full house is 503 with `Retry-After`, because a
queue would hold 25 MiB bodies in memory while they wait. OCR is ~6 s of
one core per scanned page on the 4-vCPU host, and it more than doubles
rerank latency in the same container (docs/w2/DEPLOY.md §8), so the
default leaves half the machine for everything else.

In-process state: correct for the deployed single uvicorn worker. More
workers would each count on their own, so each limit would be multiplied
by the worker count.

No PHI in logs (CLAUDE.md invariant 1): refusals log the reason and the
dimension that tripped, never the patient id, the user or the token.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

import httpx
import structlog
from fastapi.security import HTTPAuthorizationCredentials

from app.api.pre_visit_brief import ApiError
from app.fhir import client as fhir_client
from app.fhir.exceptions import FhirAuthError, FhirClientError, PatientNotFoundError
from app.models.api import ErrorCode

logger = structlog.get_logger(__name__)

#: What the extraction behind `/documents` reads from the chart, for every
#: document kind: the identity check reads Patient, and the discrepancy
#: step reads medications and allergies (D2). A token that cannot read
#: these could not complete an extraction anyway.
REQUIRED_SCOPES: tuple[str, ...] = (
    "user/Patient.read",
    "user/MedicationRequest.read",
    "user/AllergyIntolerance.read",
)

_CLAIM_ID = re.compile(r"^[A-Za-z0-9._~:/+=@-]{1,200}$")


@dataclass(frozen=True)
class DocumentCaller:
    """Who is asking, as far as OpenEMR has confirmed it."""

    token: str
    user: str
    client_id: str
    patient_id: str


def _claims(token: str) -> dict[str, object] | None:
    parts = token.split(".")
    if len(parts) != 3:
        return None
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except ValueError:
        return None
    return claims if isinstance(claims, dict) else None


def _single_audience(value: object) -> str | None:
    if isinstance(value, list) and len(value) == 1:
        value = value[0]
    return value if isinstance(value, str) and _CLAIM_ID.fullmatch(value) else None


async def authorize_document_request(
    credentials: HTTPAuthorizationCredentials | None, patient_id: str
) -> DocumentCaller:
    """Steps 1-3 of the module docstring. Raises ApiError; never reads the body."""
    if credentials is None or not credentials.credentials:
        raise ApiError(
            401,
            ErrorCode.MISSING_BEARER_TOKEN,
            "This endpoint needs the clinician's OpenEMR access token as a bearer token.",
            {"WWW-Authenticate": "Bearer"},
        )
    token = credentials.credentials
    claims = _claims(token)
    user = claims.get("sub") if claims else None
    client_id = _single_audience(claims.get("aud")) if claims else None
    scopes = claims.get("scopes") if claims else None
    if (
        not isinstance(user, str)
        or not _CLAIM_ID.fullmatch(user)
        or client_id is None
        or not isinstance(scopes, list)
        or not all(isinstance(s, str) for s in scopes)
    ):
        logger.info("w2_document_auth_refused", reason="malformed_bearer_token")
        raise ApiError(
            401,
            ErrorCode.MALFORMED_BEARER_TOKEN,
            "The bearer token is not an OpenEMR access token.",
            {"WWW-Authenticate": 'Bearer error="invalid_token"'},
        )

    missing = [scope for scope in REQUIRED_SCOPES if scope not in scopes]
    if missing:
        logger.info("w2_document_auth_refused", reason="insufficient_scope", missing=missing)
        raise ApiError(
            403,
            ErrorCode.INSUFFICIENT_SCOPE,
            "The access token lacks a scope document extraction needs: " + ", ".join(missing) + ".",
            {
                "WWW-Authenticate": f'Bearer error="insufficient_scope", scope="{" ".join(REQUIRED_SCOPES)}"'
            },
        )

    try:
        await fhir_client.ensure_patient_exists(token, patient_id)
    except FhirAuthError:
        logger.info("w2_document_auth_refused", reason="fhir_token_rejected")
        raise ApiError(
            401,
            ErrorCode.FHIR_TOKEN_REJECTED,
            "OpenEMR rejected the bearer token: it may be expired, revoked or not valid "
            "for this patient.",
            {"WWW-Authenticate": 'Bearer error="invalid_token"'},
        ) from None
    except PatientNotFoundError:
        raise ApiError(
            404, ErrorCode.PATIENT_NOT_FOUND, "OpenEMR has no patient with this patient_id."
        ) from None
    except (FhirClientError, httpx.TransportError):
        logger.error("w2_document_auth_refused", reason="fhir_unavailable")
        raise ApiError(
            502,
            ErrorCode.FHIR_UNAVAILABLE,
            "OpenEMR could not be reached to check the access token. Check GET /ready.",
        ) from None

    return DocumentCaller(token=token, user=user, client_id=client_id, patient_id=patient_id)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        return default
    return value if value > 0 else default


@dataclass
class _Window:
    limit: int
    seconds: float
    hits: dict[str, deque[float]] = field(default_factory=dict)

    def retry_after(self, key: str, now: float) -> float | None:
        """Seconds until `key` may try again, or None if it may now."""
        hits = self.hits.setdefault(key, deque())
        while hits and now - hits[0] >= self.seconds:
            hits.popleft()
        if len(hits) < self.limit:
            return None
        return self.seconds - (now - hits[0])

    def record(self, key: str, now: float) -> None:
        self.hits.setdefault(key, deque()).append(now)

    def prune(self, now: float) -> None:
        for key in [k for k, v in self.hits.items() if not v or now - v[-1] >= self.seconds]:
            del self.hits[key]


class UploadLimits:
    """Sliding-window upload limits per OAuth client, per user and per patient."""

    def __init__(
        self,
        per_client: int,
        per_user: int,
        per_patient: int,
        window_seconds: float,
    ) -> None:
        self.windows = {
            "client": _Window(per_client, window_seconds),
            "user": _Window(per_user, window_seconds),
            "patient": _Window(per_patient, window_seconds),
        }

    @classmethod
    def from_env(cls) -> UploadLimits:
        return cls(
            per_client=_env_int("AGENT_UPLOAD_LIMIT_PER_CLIENT", 120),
            per_user=_env_int("AGENT_UPLOAD_LIMIT_PER_USER", 20),
            per_patient=_env_int("AGENT_UPLOAD_LIMIT_PER_PATIENT", 10),
            window_seconds=_env_int("AGENT_UPLOAD_LIMIT_WINDOW_SECONDS", 600),
        )

    def check_and_record(self, caller: DocumentCaller, now: float | None = None) -> None:
        """Count one upload, or raise 429 without counting it."""
        now = time.monotonic() if now is None else now
        keys = {"client": caller.client_id, "user": caller.user, "patient": caller.patient_id}
        waits = {
            dimension: wait
            for dimension, window in self.windows.items()
            if (wait := window.retry_after(keys[dimension], now)) is not None
        }
        if waits:
            retry = max(1, int(max(waits.values()) + 0.999))
            logger.info(
                "w2_document_rate_limited", dimensions=sorted(waits), retry_after_seconds=retry
            )
            raise ApiError(
                429,
                ErrorCode.RATE_LIMITED,
                "Too many document uploads; try again later.",
                {"Retry-After": str(retry)},
            )
        for dimension, window in self.windows.items():
            window.record(keys[dimension], now)
            window.prune(now)


class ExtractionSlots:
    """
    A non-queuing cap on concurrent extractions.

    `slot()` is a context manager for work done inside the request.
    Background work (D7: extraction runs after the upload returns) takes
    one with `try_acquire()` before the request returns and calls
    `release()` exactly once when it finishes.
    """

    def __init__(self, limit: int) -> None:
        self.limit = limit
        # A plain counter is enough: every caller runs on the one event loop
        # and nothing awaits between the check and the increment.
        self._in_use = 0

    @classmethod
    def from_env(cls) -> ExtractionSlots:
        return cls(_env_int("AGENT_EXTRACTION_MAX_CONCURRENT", 2))

    @property
    def in_use(self) -> int:
        return self._in_use

    def try_acquire(self) -> None:
        if self._in_use >= self.limit:
            logger.info("w2_extraction_capacity_full", in_use=self._in_use, limit=self.limit)
            raise ApiError(
                503,
                ErrorCode.EXTRACTION_CAPACITY,
                "Document extraction is at capacity; try again shortly.",
                {"Retry-After": "10"},
            )
        self._in_use += 1

    def release(self) -> None:
        self._in_use = max(0, self._in_use - 1)

    @contextmanager
    def slot(self) -> Iterator[None]:
        self.try_acquire()
        try:
            yield
        finally:
            self.release()


LIMITS = UploadLimits.from_env()
SLOTS = ExtractionSlots.from_env()


def reset_for_tests() -> None:
    """Fresh limits and slots. Used by tests only."""
    global LIMITS, SLOTS
    LIMITS = UploadLimits.from_env()
    SLOTS = ExtractionSlots.from_env()
