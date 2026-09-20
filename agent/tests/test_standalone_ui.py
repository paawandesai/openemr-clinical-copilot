"""
The standalone page (deploy/ui/index.html) is static and has no JS test
runner, so this checks its source: findings.md F-62 requires every rendered
turn to open with the patient the answer is for, taken from the response.
"""

from __future__ import annotations

from pathlib import Path

PAGE = (Path(__file__).resolve().parent.parent / "deploy" / "ui" / "index.html").read_text(encoding="utf-8")


def _render_turn() -> str:
    start = PAGE.index("function renderTurn(data)")
    return PAGE[start : PAGE.index("function renderConvoMeta(", start)]


def test_every_turn_opens_with_the_patient_the_response_names() -> None:
    body = _render_turn()
    header = body.index('class="answer-patient"')

    assert "escapeHtml(data.patient_id)" in body[header : body.index("turn-label", header)]
    # Before the turn label, the context notes, the status banner and the claims.
    for later in ("turn-label", "notes +", "verify-banner", "renderTurnItems("):
        assert header < body.index(later, body.index("return ("))


def test_the_header_is_not_built_from_the_form_or_the_question() -> None:
    body = _render_turn()
    header = body[body.index('class="answer-patient"') :].split("\n", 1)[0]

    assert "conversation.patientId" not in header
    assert "data.question" not in header
    assert "els.patientId" not in header
