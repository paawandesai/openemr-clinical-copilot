"""
What telemetry carries in place of a patient_session_key (D56).

`patient_session_key` is `<sub>:<patient uuid>:<session id>`
(ARCHITECTURE.md §4). Inside the process it is used exactly as before: the
turn's tags, the response body, and the in-process event dicts the Week 1
evals read through `structlog.testing.capture_logs`. The conversation store
never keys on it; it keys on `(user, session_id)` and binds the patient
(app/conversation/state.py).

What **leaves** the process carries `HMAC-SHA256(secret, key)` instead, so
no emitted event holds the patient's uuid (CLAUDE.md invariant 1) and a
self-hosted Langfuse never stores one. There are two exits, and each calls
this module:

  - stdout: `pseudonymize_session_keys`, a structlog processor in
    `configure_logging`'s chain, ahead of the JSON renderer;
  - Langfuse: `langfuse_export.export_event`, through `emitted_key` and
    `emitted_event_data`.

Both replace the envelope's `patient_session_key` and the same-named
fields inside `event_data` (`ClaimVerifiedEvent`, `ClaimDisclosureEvent`,
and `conversation_invalidated`'s `previous_patient_session_key`).

Under one secret a key always maps to the same value, so a (user, patient,
session) still groups: `compute_metrics.py`'s per-interaction rollup and
Langfuse sessions work as before. Changing the secret splits a session that
spans the change into two.

**The secret** is `AGENT_TELEMETRY_HMAC_SECRET`. With `AGENT_ENV=production`
(the image's default, set in agent/Dockerfile) a missing secret, one shorter
than 32 characters, or the dev default stops the service at import
(`require_secret`, called from app/main.py). Outside production an unset
secret falls back to `DEV_DEFAULT_SECRET`, and every value computed under
it starts `psk_dev_`, so a log written with it cannot pass for one written
with a real secret.
"""

from __future__ import annotations

import functools
import hashlib
import hmac
import os
import re
from collections.abc import MutableMapping
from dataclasses import dataclass
from typing import Any

import structlog

SECRET_ENV = "AGENT_TELEMETRY_HMAC_SECRET"
MODE_ENV = "AGENT_ENV"
PRODUCTION = "production"
MIN_SECRET_LENGTH = 32

#: Public, in the repository, and therefore no secret at all: with it anyone
#: holding the patient list can recompute every emitted key. Refused in
#: production; outside it, labelled on every value it produces.
DEV_DEFAULT_SECRET = "dev-only-insecure-telemetry-hmac-secret-never-use-in-production"

PREFIX = "psk_"
DEV_PREFIX = "psk_dev_"

#: Emitted when production has no usable secret. `require_secret` stops the
#: service before that can happen; this covers a script that imports the
#: graphs with AGENT_ENV=production and never calls it. Telemetry does not
#: break a request, and it never falls back to the raw key.
UNAVAILABLE = "psk_unavailable"

#: The names that hold a patient_session_key, on the envelope or in event_data.
KEY_FIELDS = ("patient_session_key", "previous_patient_session_key")

_EMITTED = re.compile(r"^psk_(dev_)?[0-9a-f]{64}$")


class TelemetrySecretError(RuntimeError):
    """Production has no usable secret. The message names the variable, never a value."""


@dataclass(frozen=True)
class _Secret:
    value: bytes
    prefix: str


def production_mode() -> bool:
    return os.environ.get(MODE_ENV, "").strip().lower() == PRODUCTION


@functools.cache
def _secret() -> _Secret:
    # Cached: one secret for the life of the process, so one key always
    # emits one value. A failure raises and is not cached.
    configured = os.environ.get(SECRET_ENV, "")
    if production_mode():
        if not configured:
            problem = "is not set"
        elif configured == DEV_DEFAULT_SECRET:
            problem = "is the published dev default"
        elif len(configured) < MIN_SECRET_LENGTH:
            problem = f"is shorter than {MIN_SECRET_LENGTH} characters"
        else:
            return _Secret(configured.encode(), PREFIX)
        raise TelemetrySecretError(
            f"{SECRET_ENV} {problem}, and {MODE_ENV}={PRODUCTION}. Telemetry keys are "
            "HMAC'd under it so no emitted event carries a patient uuid (D56). Set it in the "
            "deployment's env file to at least 32 random characters, e.g. the output of "
            "`python -c 'import secrets; print(secrets.token_hex(32))'`."
        )
    if not configured or configured == DEV_DEFAULT_SECRET:
        return _Secret(DEV_DEFAULT_SECRET.encode(), DEV_PREFIX)
    return _Secret(configured.encode(), PREFIX)


def reset_for_tests() -> None:
    _secret.cache_clear()


def require_secret() -> None:
    """
    Called once at startup (app/main.py). Raises `TelemetrySecretError` in
    production without a usable secret; outside production, says so when
    the dev default is in use.
    """
    log = structlog.get_logger()
    try:
        secret = _secret()
    except TelemetrySecretError:
        log.error("startup_refused", reason="telemetry_hmac_secret_unusable", env_var=SECRET_ENV)
        raise
    if secret.prefix == DEV_PREFIX:
        log.warning(
            "telemetry_hmac_secret_dev_default",
            env_var=SECRET_ENV,
            detail="dev default in use; emitted keys start psk_dev_. Never in production.",
        )


def emitted_key(key: str | None) -> str | None:
    """What telemetry carries for `key`. Never the key itself; never raises."""
    if not key or _EMITTED.fullmatch(key):
        return key
    try:
        secret = _secret()
    except TelemetrySecretError:
        return UNAVAILABLE
    digest = hmac.new(secret.value, key.encode("utf-8"), hashlib.sha256).hexdigest()
    return secret.prefix + digest


def emitted_event_data(event_data: dict[str, Any]) -> dict[str, Any]:
    """`event_data` with its key fields replaced. A copy: the caller's dict is not touched."""
    if not any(isinstance(event_data.get(name), str) for name in KEY_FIELDS):
        return event_data
    return {
        name: emitted_key(value) if name in KEY_FIELDS and isinstance(value, str) else value
        for name, value in event_data.items()
    }


def pseudonymize_session_keys(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """structlog processor: the stdout exit. Runs on every line, not only `agent_event`."""
    for name in KEY_FIELDS:
        value = event_dict.get(name)
        if isinstance(value, str):
            event_dict[name] = emitted_key(value)
    data = event_dict.get("event_data")
    if isinstance(data, dict):
        event_dict["event_data"] = emitted_event_data(data)
    return event_dict
