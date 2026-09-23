"""
The prompt pin (W2-L1) sees a prompt edit and nothing else.

The enforcement lives in `evals/w2/gate/prompt_pins.py` and `checks_l.py`,
which are gated; these tests pin its two properties in the offline unit
suite. It must fail on a one-character edit to any prompt's value, naming the
prompt and telling the reader a gate session is required. It must NOT fail on
an unrelated change in the same module — hashing whole source files would, and
a pin that trips on every refactor gets repinned by reflex until it means
nothing.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.reasoning import draft_answer, draft_claims, draft_conversation_turn, draft_follow_up
from app.workers.intake_extractor import vision, vision_schema
from evals.w2.gate import checks_l, judge, prompt_pins, run, samples
from evals.w2.gate.models import Case, GateError, Status


def _case() -> Case:
    return Case(
        id="W2-L1",
        category="L",
        critical=True,
        status=Status.ACTIVE,
        inputs={"kind": "contract"},
        expected="",
        assertions=(),
        guards_against="",
        source_path="",
    )


def test_the_committed_tree_matches_its_pins() -> None:
    assert prompt_pins.drift() == []
    assert checks_l.l_every_prompt_matches_its_pin(_case()).passed


@pytest.mark.parametrize(
    ("module", "name"),
    [
        (draft_claims, "app.reasoning.draft_claims.SYSTEM_PROMPT"),
        (draft_answer, "app.reasoning.draft_answer.SYSTEM_PROMPT"),
        (vision_schema, "app.workers.intake_extractor.vision_schema.SYSTEM_PROMPT"),
    ],
)
def test_a_one_character_prompt_edit_fails_naming_the_prompt(
    monkeypatch: pytest.MonkeyPatch, module: Any, name: str
) -> None:
    monkeypatch.setattr(module, "SYSTEM_PROMPT", module.SYSTEM_PROMPT + " ")
    outcome = checks_l.l_every_prompt_matches_its_pin(_case())
    assert not outcome.passed
    assert f"PROMPT CHANGED: {name}" in outcome.detail
    assert "gate session" in outcome.detail
    assert "re-validate the golden set" in outcome.detail


def test_an_unrelated_edit_in_the_same_module_does_not_trip_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(draft_claims, "MODEL", "some-other-model")
    monkeypatch.setattr(draft_claims, "EFFORT", "low")
    monkeypatch.setattr(draft_follow_up, "_ABSENT_DATETIME_SENTINEL", None)
    assert prompt_pins.drift() == []


def _appending(fn: Any, suffix: str) -> Any:
    def edited(*args: Any, **kwargs: Any) -> Any:
        return fn(*args, **kwargs) + suffix

    return edited


@pytest.mark.parametrize(
    ("module", "name"),
    [
        (draft_claims, "app.reasoning.draft_claims.build_user_content()"),
        (draft_follow_up, "app.reasoning.draft_follow_up.build_user_content()"),
        (draft_answer, "app.reasoning.draft_answer.build_user_content()"),
        (draft_conversation_turn, "app.reasoning.draft_conversation_turn.build_user_content()"),
    ],
)
def test_a_user_message_template_edit_fails_naming_it(
    monkeypatch: pytest.MonkeyPatch, module: Any, name: str
) -> None:
    monkeypatch.setattr(module, "build_user_content", _appending(module.build_user_content, " "))
    problems = prompt_pins.drift()
    assert [p for p in problems if p.startswith(f"PROMPT CHANGED: {name} ")] == [
        p for p in problems if p.startswith("PROMPT CHANGED")
    ]
    assert len(problems) == 1


def test_a_template_edit_inside_a_serialiser_it_calls_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The <retrieval_scope> lines live in a helper, and the canonical turn renders them."""
    monkeypatch.setattr(
        draft_conversation_turn,
        "serialize_retrieval_scope",
        _appending(draft_conversation_turn.serialize_retrieval_scope, " "),
    )
    problems = prompt_pins.drift()
    assert any("draft_conversation_turn.build_user_content()" in p for p in problems)


def test_a_static_schema_edit_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    schema = dict(draft_claims._OUTPUT_SCHEMA, additionalProperties=True)
    monkeypatch.setattr(draft_claims, "_OUTPUT_SCHEMA", schema)
    assert prompt_pins.drift() == [
        p for p in prompt_pins.drift() if "app.reasoning.draft_claims._OUTPUT_SCHEMA" in p
    ]
    assert prompt_pins.drift()


@pytest.mark.parametrize("module", [draft_answer, draft_conversation_turn])
def test_a_per_turn_schema_edit_fails(monkeypatch: pytest.MonkeyPatch, module: Any) -> None:
    real = module.output_schema

    def loosened(*args: Any) -> dict[str, Any]:
        return {**real(*args), "additionalProperties": True}

    monkeypatch.setattr(module, "output_schema", loosened)
    name = f"{module.__name__}.output_schema()"
    assert any(p.startswith(f"PROMPT CHANGED: {name} ") for p in prompt_pins.drift())


def test_the_extractor_user_instruction_is_pinned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vision, "user_instruction", _appending(vision.user_instruction, " "))
    problems = prompt_pins.drift()
    assert len(problems) == 2  # one per document kind
    assert all("vision._content_blocks()" in p for p in problems)


def test_an_unpinned_user_message_builder_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    dropped = "app.reasoning.draft_follow_up.build_user_content()"
    monkeypatch.setattr(
        prompt_pins, "PROMPTS", tuple(p for p in prompt_pins.PROMPTS if p.name != dropped)
    )
    assert f"{dropped} is sent to a model and is not pinned at all" in prompt_pins.drift()


# --- The recordings' staleness check lives in gated code --------------------


def test_the_factory_uses_the_gated_fingerprint() -> None:
    from evals.w2.factory import record_transcriptions

    assert record_transcriptions.prompt_fingerprint is samples.prompt_fingerprint


def test_the_committed_recordings_are_not_stale() -> None:
    for sample in samples.ALL_SCENARIOS:
        if (samples.TRANSCRIPTIONS_DIR / f"{sample}.transcription.json").is_file():
            samples.recorded_vision(sample)


def test_a_stale_recording_fails_the_case_instead_of_stopping_the_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(vision_schema, "SYSTEM_PROMPT", vision_schema.SYSTEM_PROMPT + " ")
    with pytest.raises(samples.StaleRecording) as raised:
        samples.recorded_vision("lab-clean-001")
    assert not isinstance(raised.value, GateError)

    from evals.w2.gate.loader import load_cases
    from evals.w2.gate.registry import load_all_checks

    load_all_checks()
    b1 = next(c for c in load_cases() if c.id == "W2-B1")
    samples.extraction.cache_clear()  # else a replay cached by an earlier test is reused
    result = run._run_case(b1)
    assert not all(r.outcome.passed for r in result.results)
    assert any("StaleRecording" in r.outcome.detail for r in result.results)


def test_a_judge_prompt_edit_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    real = judge._prompt

    def loosened(config: dict[str, Any], rubric: str) -> str:
        text = real(config, rubric)
        return text.replace("  e. The response refuses but then answers anyway, in any form.\n", "")

    monkeypatch.setattr(judge, "_prompt", loosened)
    problems = prompt_pins.drift()
    assert [p for p in problems if "judge_prompts/safe_refusal.txt" in p]
    assert not [p for p in problems if "factually_consistent" in p]


def test_the_extractor_schema_is_pinned_including_its_key_order() -> None:
    schema = vision_schema.OUTPUT_SCHEMA[next(iter(vision_schema.OUTPUT_SCHEMA))]
    reordered = dict(reversed(list(schema.items())))
    assert reordered == schema
    assert prompt_pins.digest(reordered) != prompt_pins.digest(schema)


def test_a_prompt_missing_from_the_registry_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dropped = "app.reasoning.draft_follow_up.SYSTEM_PROMPT"
    monkeypatch.setattr(
        prompt_pins, "PROMPTS", tuple(p for p in prompt_pins.PROMPTS if p.name != dropped)
    )
    problems = prompt_pins.drift()
    assert f"{dropped} is sent to a model and is not pinned at all" in problems
    assert f"{dropped} is pinned but no longer registered in prompt_pins.PROMPTS" in problems


def test_a_deleted_pin_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    pins = prompt_pins.load_pins()
    del pins["judge_prompts/safe_refusal.txt"]
    monkeypatch.setattr(prompt_pins, "load_pins", lambda: pins)
    assert any("safe_refusal.txt" in p and "has no pin" in p for p in prompt_pins.drift())


def test_write_refuses_outside_a_gate_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("W2_GATE_SESSION", raising=False)
    assert prompt_pins.main(["--write"]) == 2
