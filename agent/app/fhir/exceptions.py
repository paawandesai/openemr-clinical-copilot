"""
Typed FHIR client exceptions — ARCHITECTURE.md §2.

§2's known limitation (F-08, findings.md): OpenEMR returns HTTP 401
identically for "this token is expired/invalid" and "this token lacks the
required scope," never the RFC 6750-correct 403 for insufficient scope.
This client's job is only to surface that a 401 happened; it does not
guess which cause it was. The caller — built in a later task — is
responsible for the scope-vs-expiry disambiguation, using its own
decoded-JWT record of which scopes the current token actually holds (§2).
"""

from __future__ import annotations


class FhirClientError(Exception):
    """Base class for every error this client raises."""


class FhirAuthError(FhirClientError):
    """
    Raised on HTTP 401 (and, defensively, 403 — though F-08 establishes
    that OpenEMR does not currently emit 403 for insufficient scope; live
    testing against the deployed instance during this task confirmed 401
    is what actually comes back for both an out-of-scope request and a
    forged/expired token). Covers BOTH causes; this client does not
    attempt to tell them apart — see the module docstring and §2.
    """


class FhirNotFoundError(FhirClientError):
    """
    Raised on a genuine HTTP 404.

    Confirmed live against the deployed droplet during this task: a
    patient-scoped search (?patient=<id>) for a patient with zero matching
    resources returns HTTP 200 with an empty Bundle (`total: 0`), not a
    404 — OpenEMR's FHIR search endpoints do not 404 for "no data found."
    This exception exists for an actual 404 response (e.g. an unrecognized
    resource type in the URL path). Callers should check for an empty
    result list, not catch this, to detect "no matching resources for this
    patient."
    """


class FhirServerError(FhirClientError):
    """Raised on any other non-2xx response (5xx, or an unexpected 4xx)."""
