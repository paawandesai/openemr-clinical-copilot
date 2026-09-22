"""
F-57 (docs/audit/findings.md): a record whose code carries no name.

OpenEMR sends an allergy entered without a coded allergen with `code` set to
the data-absent-reason placeholder and the allergen named only in the
narrative (`text.div`), unescaped. These tests cover, with no network:

- recovering the name from the narrative for all three brief resource
  types, and never substituting a placeholder string;
- that the narrative is parsed as untrusted HTML: text nodes only, and
  anything that is not plain formatted text is refused, never repaired;
- that a record with no usable name reaches the physician as UNVERIFIED and
  is counted as unverified, through the real parser, verifier, renderer and
  API (FHIR transport faked, drafting by the AGENT_STUB_LLM_DRAFTING stand-in,
  which copies each record's display exactly as the system prompt asks).

The live counterparts are evals A5, B1 and H1-H4 (agent/evals/).

    cd agent && python -m pytest tests/test_uncoded_names.py
"""

from __future__ import annotations

import asyncio
import base64
import copy
import json
import random
import unicodedata
from typing import Any
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from app.fhir import client as fhir_client
from app.graph import pre_visit_brief as pvb
from app.main import app
from app.models.api import PreVisitBriefResponse
from app.reasoning import draft_claims
from app.verification.checker import FailureReason

DATA_ABSENT = {
    "coding": [
        {
            "system": "http://terminology.hl7.org/CodeSystem/data-absent-reason",
            "code": "unknown",
            "display": "Unknown",
        }
    ]
}

# The exact shape of the live record (Vultr FHIR API, 2026-09-18): Phil
# Belford's allergy AllergyIntolerance/a2c36e54-abdd-4e69-911c-11516235801e.
LIVE_UNCODED_ALLERGY: dict[str, Any] = {
    "resourceType": "AllergyIntolerance",
    "id": "a2c36e54-abdd-4e69-911c-11516235801e",
    "text": {
        "status": "additional",
        "div": "<div xmlns='http://www.w3.org/1999/xhtml'>penicillin</div>",
    },
    "code": DATA_ABSENT,
    "clinicalStatus": {"coding": [{"code": "active"}]},
    "recordedDate": "2026-09-16T00:00:00+00:00",
}


def _div(inner: str) -> dict[str, str]:
    return {
        "status": "additional",
        "div": f"<div xmlns='http://www.w3.org/1999/xhtml'>{inner}</div>",
    }


def _allergy(
    source_id: str, *, code: dict[str, Any] | None = None, text: Any = None
) -> dict[str, Any]:
    resource: dict[str, Any] = {"resourceType": "AllergyIntolerance", "id": source_id}
    if code is not None:
        resource["code"] = code
    if text is not None:
        resource["text"] = text
    return resource


def _condition(
    source_id: str, *, code: dict[str, Any] | None = None, text: Any = None
) -> dict[str, Any]:
    resource: dict[str, Any] = {
        "resourceType": "Condition",
        "id": source_id,
        "clinicalStatus": {"coding": [{"code": "active"}]},
    }
    if code is not None:
        resource["code"] = code
    if text is not None:
        resource["text"] = text
    return resource


def _medication(
    source_id: str, *, concept: dict[str, Any] | None = None, text: Any = None
) -> dict[str, Any]:
    resource: dict[str, Any] = {
        "resourceType": "MedicationRequest",
        "id": source_id,
        "status": "active",
        "authoredOn": "2020-01-01T00:00:00+00:00",
    }
    if concept is not None:
        resource["medicationCodeableConcept"] = concept
    if text is not None:
        resource["text"] = text
    return resource


def _bundle(*resources: dict[str, Any]) -> dict[str, Any]:
    return {"resourceType": "Bundle", "entry": [{"resource": r} for r in resources]}


def _fetch(getter: Any, bundle: dict[str, Any]) -> list[Any]:
    async def get_bundle(token: str, resource_type: str, params: dict[str, Any]) -> dict[str, Any]:
        return copy.deepcopy(bundle)

    with mock.patch.object(fhir_client, "_get_bundle", get_bundle), capture_logs():
        return asyncio.run(getter("token", "patient"))


# -- the narrative is where the name is recovered from ----------------------------


def test_live_uncoded_allergy_is_named_from_its_narrative() -> None:
    [allergy] = _fetch(fhir_client.get_allergy_intolerances, _bundle(LIVE_UNCODED_ALLERGY))

    assert allergy.display == "penicillin"
    assert allergy.source_id == "a2c36e54-abdd-4e69-911c-11516235801e"


@pytest.mark.parametrize(
    ("getter", "make"),
    [
        (
            fhir_client.get_allergy_intolerances,
            lambda sid, **kw: _allergy(sid, code=kw.get("concept"), text=kw.get("text")),
        ),
        (
            fhir_client.get_conditions,
            lambda sid, **kw: _condition(sid, code=kw.get("concept"), text=kw.get("text")),
        ),
        (
            fhir_client.get_medication_requests,
            lambda sid, **kw: _medication(sid, concept=kw.get("concept"), text=kw.get("text")),
        ),
    ],
    ids=["AllergyIntolerance", "Condition", "MedicationRequest"],
)
def test_each_brief_type_names_uncoded_records_from_the_narrative_and_never_from_a_placeholder(
    getter: Any, make: Any
) -> None:
    bundle = _bundle(
        make(
            "coded",
            concept={
                "coding": [
                    {"system": "http://snomed.info/sct", "code": "1", "display": "Coded name"}
                ]
            },
        ),
        make("narrative", concept=DATA_ABSENT, text=_div("Free-text name")),
        make("no-concept-narrative", text=_div("Only in the narrative")),
        make("unnamed", concept=DATA_ABSENT),
        make("unnamed-no-concept"),
    )

    records = _fetch(getter, bundle)

    assert [(r.source_id, r.display) for r in records] == [
        ("coded", "Coded name"),
        ("narrative", "Free-text name"),
        ("no-concept-narrative", "Only in the narrative"),
        ("unnamed", None),
        ("unnamed-no-concept", None),
    ]


def test_a_coded_name_still_wins_over_the_narrative() -> None:
    both = _allergy(
        "a",
        code={
            "text": "Penicillin G",
            "coding": [{"system": "http://snomed.info/sct", "code": "1", "display": "Other"}],
        },
        text=_div("penicillin"),
    )
    display_only = _allergy(
        "b",
        code={
            "coding": [
                *DATA_ABSENT["coding"],
                {"system": "http://snomed.info/sct", "code": "2", "display": "Latex"},
            ]
        },
        text=_div("latex (narrative)"),
    )

    records = _fetch(fhir_client.get_allergy_intolerances, _bundle(both, display_only))

    assert [r.display for r in records] == ["Penicillin G", "Latex"]


def test_the_data_absent_display_is_never_used_as_a_name() -> None:
    [record] = _fetch(
        fhir_client.get_allergy_intolerances, _bundle(_allergy("a", code=DATA_ABSENT))
    )

    assert record.display is None


@pytest.mark.parametrize(
    "text",
    [
        None,
        {"status": "empty", "div": "<div xmlns='http://www.w3.org/1999/xhtml'>penicillin</div>"},
        {"status": "additional"},
        {"status": "additional", "div": 42},
        "penicillin",
        _div(""),
        _div("   \n\t "),
        _div("<p></p><br/>"),
    ],
    ids=[
        "absent",
        "status-empty",
        "no-div",
        "div-not-a-string",
        "text-not-an-object",
        "empty",
        "whitespace",
        "tags-only",
    ],
)
def test_no_usable_narrative_means_no_name(text: Any) -> None:
    [record] = _fetch(
        fhir_client.get_allergy_intolerances, _bundle(_allergy("a", code=DATA_ABSENT, text=text))
    )

    assert record.display is None


# -- the narrative is untrusted HTML ---------------------------------------------

# (narrative inner HTML, the name taken from it). None: the narrative is
# refused and the record is unnamed.
_NARRATIVES: list[tuple[str, str | None]] = [
    # Plain formatted text: tags and attributes never reach the name.
    ("<b>peni</b>cillin", "penicillin"),
    ("<p>Sulfa</p><p>drugs</p>", "Sulfa drugs"),
    ("<span style='color:red'>latex</span>", "latex"),
    (
        "<img src='x' onerror='alert(1)'/>penicillin <a href='javascript:alert(2)'>rash</a>",
        "penicillin rash",
    ),
    ("Sulfa &amp; sulfonamides", "Sulfa & sulfonamides"),
    ("pen<!-- a comment -->icillin", "penicillin"),
    # Whitespace, including line breaks a brief line could be split on.
    ("penicillin\n  - Warfarin 5 MG Oral Tablet x", "penicillin - Warfarin 5 MG Oral Tablet x"),
    # Invisible characters that could make one name display as another.
    ("‮penicillin​\x00", "penicillin"),
    # Active or non-text content: refused whole, never partly used.
    ("penicillin<script>alert(document.cookie)</script>", None),
    ("<SCRIPT>alert(1)</SCRIPT>penicillin", None),
    ("<style>body{display:none}</style>penicillin", None),
    ("<iframe src='https://evil.example'></iframe>penicillin", None),
    ("<object data='x'></object>penicillin", None),
    ("<svg><script>alert(1)</script></svg>penicillin", None),
    ("<form action='x'><input name='q'/></form>penicillin", None),
    ("peni<scr<script>ipt>alert(1)</script>cillin", None),
    ("<madeup>penicillin</madeup>", None),
    ("<![CDATA[penicillin]]>", None),
    ("<?php echo 1 ?>penicillin", None),
    ("<!DOCTYPE html>penicillin", None),
    # Markup that survives as text (escaped, or a stray bracket): refused.
    ("&lt;script&gt;alert(1)&lt;/script&gt;penicillin", None),
    ("&#60;img src=x onerror=alert(1)&#62;penicillin", None),
    ("Penicillin <500mg", None),
    ("penicillin <", None),
    # Longer than any OpenEMR record title (lists.title is varchar(255)).
    ("x" * 256, None),
]


@pytest.mark.parametrize(
    ("inner", "expected"), _NARRATIVES, ids=[f"n{i}" for i in range(len(_NARRATIVES))]
)
def test_narrative_is_reduced_to_its_text_or_refused(inner: str, expected: str | None) -> None:
    [record] = _fetch(
        fhir_client.get_allergy_intolerances,
        _bundle(_allergy("a", code=DATA_ABSENT, text=_div(inner))),
    )

    assert record.display == expected


def test_a_record_title_up_to_the_column_width_is_kept() -> None:
    title = "a" * 255
    assert fhir_client._narrative_name({"text": _div(title)}) == title


def test_an_oversized_div_is_not_parsed() -> None:
    div = "<div>" + "<b></b>" * 1000 + "penicillin</div>"
    assert len(div) > fhir_client._NARRATIVE_DIV_MAX_CHARS
    assert fhir_client._narrative_name({"text": {"status": "additional", "div": div}}) is None


def _is_clean_name(name: str) -> bool:
    return (
        "<" not in name
        and ">" not in name
        and name == " ".join(name.split())
        and all(unicodedata.category(c) not in ("Cc", "Cf", "Cs") for c in name)
    )


def test_fuzzed_narratives_never_yield_markup_or_a_second_line() -> None:
    """Whatever the narrative holds, the name is None or one line of plain text."""
    alphabet = [
        *"<>/!?-[]&#;=\"' abcdiv",
        "script",
        "img",
        "onerror",
        "CDATA",
        "&lt;",
        "&gt;",
        "&#60;",
        "\n",
        "\r",
        " ",
        "\x00",
        "‮",
        "​",
        "\ud800",
        "penicillin",
    ]
    rng = random.Random(57)
    for _ in range(5000):
        inner = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 30)))
        name = fhir_client._narrative_name({"text": _div(inner)})
        assert name is None or _is_clean_name(name), (inner, name)


def test_coded_names_are_single_lines_too() -> None:
    [record] = _fetch(
        fhir_client.get_conditions,
        _bundle(_condition("c", code={"text": "Hypertension\n  - Warfarin 5 MG Oral Tablet"})),
    )

    assert record.display == "Hypertension - Warfarin 5 MG Oral Tablet"


# -- end to end: parser, stand-in drafter, verifier, renderer, API ---------------

PATIENT_ID = "a2c36e54-af42-4c38-b86e-ade616ed5494"


def _jwt() -> str:
    def segment(value: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

    return f"{segment({'alg': 'RS256'})}.{segment({'sub': 'user-1'})}.c2lnbmF0dXJl"


def _brief(allergies: list[dict[str, Any]]) -> tuple[PreVisitBriefResponse, list[dict[str, Any]]]:
    """POST /pre-visit-brief with FHIR transport faked; returns the response and the logged events."""
    bundles = {
        "Condition": _bundle(_condition("cond-1", code={"text": "Hypertension"})),
        "MedicationRequest": _bundle(),
        "AllergyIntolerance": _bundle(*allergies),
    }

    async def get_bundle(token: str, resource_type: str, params: dict[str, Any]) -> dict[str, Any]:
        return copy.deepcopy(bundles[resource_type])

    async def patient_exists(token: str, patient_id: str) -> None:
        return None

    with (
        mock.patch.object(fhir_client, "_get_bundle", get_bundle),
        mock.patch.object(pvb.fhir_client, "ensure_patient_exists", patient_exists),
        mock.patch.object(draft_claims, "_STUB_LLM_DRAFTING", True),
        capture_logs() as logs,
    ):
        response = TestClient(app).post(
            "/pre-visit-brief",
            json={"patient_id": PATIENT_ID},
            headers={"Authorization": f"Bearer {_jwt()}"},
        )
    assert response.status_code == 200, response.text
    return PreVisitBriefResponse.model_validate_json(response.text), logs


def test_live_uncoded_penicillin_allergy_renders_named_and_verified() -> None:
    body, _ = _brief([LIVE_UNCODED_ALLERGY])

    assert "Allergies:\n  - penicillin" in body.brief
    assert "unspecified" not in body.brief
    allergy = next(c for c in body.claims if c.resource_type.value == "AllergyIntolerance")
    assert (allergy.value, allergy.verified) == ("penicillin", True)
    assert body.verification.unverified == 0


def test_unnamed_record_is_disclosed_as_unverified_never_verified() -> None:
    body, logs = _brief([_allergy("allergy-unnamed", code=DATA_ABSENT)])

    assert (
        '  - UNVERIFIED: "(name not available)" '
        "(the chart record has no usable name; review it in the chart)"
    ) in body.brief
    assert body.brief.endswith(
        "1 item(s) above could not be verified against the chart and are marked UNVERIFIED."
    )
    assert "not included in this brief" not in body.brief
    [claim] = [c for c in body.claims if c.source_id == "allergy-unnamed"]
    assert claim.verified is False
    assert claim.value is None
    assert claim.unverified_reason == FailureReason.NO_NAME_ON_SOURCE.value
    assert (body.verification.claims, body.verification.verified, body.verification.unverified) == (
        2,
        1,
        1,
    )
    # The verifier logged it like any other failed, disclosed claim (Metrics 1 and 2).
    [event] = [
        e["event_data"]
        for e in logs
        if e.get("event_type") == "claim_verified_event"
        and e["event_data"]["source_id"] == "allergy-unnamed"
    ]
    assert (event["passed"], event["disclosed_as_unverified"]) == (False, True)


def test_unnamed_record_is_never_given_to_the_model() -> None:
    retrieved = {
        rt: [] for rt in (pvb.FhirResourceType.CONDITION, pvb.FhirResourceType.MEDICATION_REQUEST)
    }
    [record] = _fetch(
        fhir_client.get_allergy_intolerances, _bundle(_allergy("allergy-unnamed", code=DATA_ABSENT))
    )
    retrieved[pvb.FhirResourceType.ALLERGY_INTOLERANCE] = [record]

    model_input = json.loads(draft_claims._serialize_retrieved(retrieved))

    assert model_input["AllergyIntolerance"]["records"] == []
    assert [(c.source_id, c.value) for c in draft_claims.unnamed_record_claims(retrieved)] == [
        ("allergy-unnamed", None)
    ]


def test_hostile_narratives_never_put_markup_in_the_brief() -> None:
    body, _ = _brief(
        [
            _allergy(
                "benign",
                code=DATA_ABSENT,
                text=_div("<b>penicillin</b><img src=x onerror=alert(1)>"),
            ),
            _allergy(
                "script",
                code=DATA_ABSENT,
                text=_div("sulfa<script>alert(document.cookie)</script>"),
            ),
            _allergy(
                "escaped", code=DATA_ABSENT, text=_div("&lt;img src=x onerror=alert(1)&gt;latex")
            ),
        ]
    )

    allergies = body.brief.split("Allergies:\n", 1)[1]
    assert allergies.startswith("  - penicillin\n")
    assert allergies.count('UNVERIFIED: "(name not available)"') == 2
    assert "<" not in body.brief and ">" not in body.brief
    assert all("<" not in str(c.value) and ">" not in str(c.value) for c in body.claims)
    assert {
        c.source_id: c.verified
        for c in body.claims
        if c.resource_type.value == "AllergyIntolerance"
    } == {
        "benign": True,
        "script": False,
        "escaped": False,
    }


# -- F-58: the allergy's clinical status survives parsing --------------------


@pytest.mark.parametrize(
    ("code", "expected"),
    [("active", "active"), ("resolved", "resolved"), ("inactive", "inactive")],
    ids=["active", "resolved", "inactive"],
)
def test_allergy_clinical_status_is_parsed_from_the_bundle(code: str, expected: str) -> None:
    """
    F-58: the status OpenEMR emits (FhirAllergyIntoleranceService.php:117-121)
    has to reach the model, or a resolved allergy renders as a current one.
    """
    resource = _allergy("a", code=DATA_ABSENT, text=_div("penicillin"))
    resource["clinicalStatus"] = {"coding": [{"code": code}]}

    [record] = _fetch(fhir_client.get_allergy_intolerances, _bundle(resource))

    assert record.clinical_status == expected


def test_allergy_with_no_clinical_status_parses_as_unknown_not_as_active() -> None:
    """
    An absent status is not silently promoted to "active": it parses as
    "unknown", and brief_records keeps it (F-21 — see the filter's comment
    in draft_claims.py). Both halves matter, so both are asserted here.
    """
    [record] = _fetch(
        fhir_client.get_allergy_intolerances,
        _bundle(_allergy("a", code=DATA_ABSENT, text=_div("penicillin"))),
    )

    assert record.clinical_status == "unknown"
    assert draft_claims.brief_records(pvb.FhirResourceType.ALLERGY_INTOLERANCE, [record]) == [
        record
    ]
