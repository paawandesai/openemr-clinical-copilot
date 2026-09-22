"""
What the write-back endpoint can refuse, as types.

Split the way the endpoint splits its statuses, because the two halves
call for different behaviour from a caller. A `WriteBackRefused` means the
request was wrong and sending it again will be wrong the same way. A
`WriteBackUnavailable` means the request may have been fine and retrying
is safe — which it is, by construction: the ledger's UNIQUE key makes a
repeat either a no-op or the same write (CLAUDE.md invariant 6).
"""

from __future__ import annotations


class WriteBackError(Exception):
    """Base for every write-back failure."""


class WriteBackAuthError(WriteBackError):
    """
    401 or 403. The clinician's token is missing, expired, or was issued to
    a client that does not carry `api:oemr` and `user/lab-writeback.c`.

    Scopes are immutable per OAuth client in OpenEMR, so this is usually a
    registration problem rather than a session problem — see
    `docs/w2/deploy-writeback.md`.
    """


class WriteBackRefused(WriteBackError):
    """
    400 or 404: the endpoint will not write this request as sent.

    `field` is the request field the endpoint named, when it named one.
    Retrying the same payload will fail the same way.
    """

    def __init__(self, message: str, *, field: str | None = None, status_code: int = 400) -> None:
        super().__init__(message)
        self.field = field
        self.status_code = status_code


class WriteBackUnavailable(WriteBackError):
    """
    A transport failure or a 5xx. Nothing is known about whether the write
    landed, which is exactly the case idempotency exists for: send the same
    request again.
    """
