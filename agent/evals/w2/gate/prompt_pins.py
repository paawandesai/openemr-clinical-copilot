"""
The pinned hash of every prompt the system and the judge send (W2-L1).

Why this exists. The behavioural cases catch a prompt edit only where some
case happens to exercise what the edit breaks, and most of them run against
recorded or stubbed model output — so an edit to a drafting prompt reaches no
model in a gate run at all. The investigation on chore/w2-prompt-change-check
found four of seven prompts could be edited harmfully and still pass, and the
safe_refusal judge loosening passed two of three runs. The pin is the
catch-all: any change to a prompt's VALUE fails the deterministic tier, and
the only way back to green is a gate session that re-validates the golden set
against the new prompt and rewrites `prompt_pins.json`. It does not replace
the behavioural cases; it is what catches the edits they cannot see.

What is hashed is the value the code sends, imported from the module that
sends it — never the source file. An unrelated edit elsewhere in
draft_claims.py does not trip the pin; a one-character edit to its
SYSTEM_PROMPT does.

A prompt is everything the model is given, not just the system prompt:

* each drafter's SYSTEM_PROMPT and the extractor's;
* each structured-output schema — static ones as they stand, and the two
  built per turn (the answer and conversation drafters) rendered from a
  fixed synthetic turn;
* each user-message template — the four drafters' `build_user_content` and
  the extractor's page-labelled content blocks with its user instruction —
  rendered from the same fixed synthetic turn;
* both judge prompts.

A template or per-turn schema is pinned by RENDERING it from fixed inputs
(`_Canon` below: sentinel values, one of each kind of source, a lab scope
that omits rows, a full page of encounters) so that every literal line of
it appears in the output. The inputs are synthetic and hold no chart data.
Change the template and the rendering changes; change the data model a
template serialises and it changes too, which is correct — that is part of
what the model reads.

    cd agent && python -m evals.w2.gate.prompt_pins          # compare, exit 1 on drift
    cd agent && python -m evals.w2.gate.prompt_pins --write  # W2_GATE_SESSION=1 only
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import importlib
import json
import os
import pkgutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from evals.w2.gate.models import GateError
from evals.w2.gate.paths import PROMPT_PINS

SCHEMA_VERSION = "w2-gate/prompt_pins/1.0.0"

REPIN_INSTRUCTION = (
    "A prompt change requires a gate session (W2_GATE_SESSION=1) to re-validate the "
    "golden set against the new prompt and update the pin: "
    "`cd agent && python -m evals.w2.gate.prompt_pins --write`."
)


@dataclass(frozen=True)
class Prompt:
    name: str
    #: The file a reader opens to find it, for the failure message.
    source: str
    load: Callable[[], Any]


def _attr(module: str, name: str) -> Callable[[], Any]:
    return lambda: getattr(importlib.import_module(module), name)


def _vision_schema(kind: str) -> Callable[[], Any]:
    def load() -> Any:
        from app.models.w2.documents import DocumentKind
        from app.workers.intake_extractor.vision_schema import OUTPUT_SCHEMA

        return OUTPUT_SCHEMA[DocumentKind(kind)]

    return load


class _Canon:
    """
    Fixed, synthetic inputs a template or a per-turn schema is rendered from.
    Built on first use, so importing this module needs none of app/.
    """

    QUESTION = "<physician question>"

    @staticmethod
    @functools.cache
    def turn_chart() -> dict[Any, list[Any]]:
        from datetime import UTC, datetime

        from app.conversation.routing import ENCOUNTER_COUNT
        from app.models.fhir_resources import Encounter, FhirResourceType
        from app.reasoning.draft_conversation_turn import TURN_RESOURCE_MODELS

        chart: dict[Any, list[Any]] = {rt: [] for rt in TURN_RESOURCE_MODELS}
        # A full page of encounters, so the "older visits were not retrieved"
        # line of <retrieval_scope> is rendered.
        chart[FhirResourceType.ENCOUNTER] = [
            Encounter(source_id=f"encounter-{i}", date=datetime(2026, 1, i + 1, tzinfo=UTC))
            for i in range(ENCOUNTER_COUNT)
        ]
        return chart

    @staticmethod
    def lab_scope() -> Any:
        from app.conversation.routing import LabScope

        # Every optional line of <retrieval_scope>: some omitted, one code cut.
        return LabScope(
            retrieved=3,
            considered=2,
            codes=("00000-0",),
            omitted=1,
            truncated_codes=(("00000-0", 1, 2),),
        )

    @staticmethod
    def context() -> tuple[Any, ...]:
        from app.conversation.state import VerifiedTurn
        from app.models.claims import Claim
        from app.models.fhir_resources import FhirResourceType

        claim = Claim(
            source_id="<source id>",
            field="display",
            value="<value>",
            resource_type=FhirResourceType.CONDITION,
        )
        return (VerifiedTurn(turn=1, claims=(claim,)),)

    @staticmethod
    def documents() -> dict[str, list[Any]]:
        from app.models.w2.citations import BBox, Citation
        from app.models.w2.documents import DocumentKind
        from app.supervisor.facts import DocumentFact

        document_id = "0" * 128
        path = "results.panel.analyte.value"
        citation = Citation.located_document(
            source_id=document_id,
            field_path=path,
            quote="<quote>",
            bbox=BBox(page=1, x0=0, y0=0, x1=1, y1=1),
            extraction_id="w2x-" + "0" * 32,
        )
        fact = DocumentFact(
            document_id=document_id,
            kind=DocumentKind.LAB_PDF,
            field_path=path,
            group="<test name>",
            part="result",
            quote="<quote>",
            value="<quote>",
            citation=citation,
        )
        return {document_id: [fact]}

    @staticmethod
    def chunks() -> list[Any]:
        from app.models.w2.workers import EvidenceChunk, GuidelineSource, RetrievalMethod

        return [
            EvidenceChunk(
                chunk_id="<chunk id>",
                text="<guideline text>",
                source=GuidelineSource(
                    guideline_id="<guideline id>",
                    title="<title>",
                    section="<section>",
                    publisher="<publisher>",
                ),
                retrieval_score=0.0,
                method=RetrievalMethod.HYBRID,
            )
        ]


def _rendered(module: str, attr: str) -> Callable[[], Any]:
    """A template or per-turn schema, rendered from `_Canon`."""

    def load() -> Any:
        from app.reasoning import draft_conversation_turn as turn

        mod = importlib.import_module(f"{_REASONING}.{module}")
        fn = getattr(mod, attr)
        chart = _Canon.turn_chart()
        if module == "draft_claims":
            return fn({rt: [] for rt in mod._BRIEF_RESOURCE_MODELS})
        if module == "draft_follow_up":
            return fn({rt: [] for rt in mod.BOUNDED_RESOURCE_MODELS}, _Canon.QUESTION)
        if module == "draft_conversation_turn" and attr == "build_user_content":
            return fn(chart, _Canon.QUESTION, _Canon.context(), _Canon.lab_scope())
        if module == "draft_conversation_turn" and attr == "output_schema":
            return fn(turn.turn_resource_types(chart))
        if module == "draft_answer" and attr == "build_user_content":
            return fn(
                chart,
                _Canon.documents(),
                _Canon.chunks(),
                _Canon.QUESTION,
                _Canon.context(),
                withheld_rows=1,
            )
        if module == "draft_answer" and attr == "output_schema":
            return fn(turn.turn_resource_types(chart), _Canon.documents(), _Canon.chunks())
        raise GateError(f"no canonical rendering for {module}.{attr}")

    return load


def _vision_user_content(kind: str) -> Callable[[], Any]:
    def load() -> Any:
        from app.models.w2.documents import DocumentKind
        from app.workers.intake_extractor.vision import _content_blocks

        # One page with an empty image: the page label, the image block's
        # shape and the user instruction, which is all of it that is text.
        return _content_blocks(DocumentKind(kind), [b""], 1, 1)

    return load


def _judge_prompt(rubric: str) -> Callable[[], Any]:
    def load() -> Any:
        # The judge's own loader, so the pinned text is exactly what it sends.
        from evals.w2.gate import judge

        return judge._prompt(judge.load_judge_config(), rubric)

    return load


_REASONING = "app.reasoning"
_VISION = "app.workers.intake_extractor.vision_schema"

_DRAFTERS = ("draft_answer", "draft_claims", "draft_conversation_turn", "draft_follow_up")

#: What each drafter sends besides its SYSTEM_PROMPT: `_OUTPUT_SCHEMA` is a
#: static schema, pinned as it stands; a name ending in "()" is a function,
#: pinned by rendering it from `_Canon`. `unregistered()` reads this table, so
#: a drafter that gains one of these attributes must be registered here.
_STATIC_SCHEMA = "_OUTPUT_SCHEMA"
_RENDERED = ("output_schema", "build_user_content")
_SENT_BY: dict[str, tuple[str, ...]] = {
    "draft_answer": ("output_schema()", "build_user_content()"),
    "draft_claims": ("_OUTPUT_SCHEMA", "build_user_content()"),
    "draft_conversation_turn": ("output_schema()", "build_user_content()"),
    "draft_follow_up": ("_OUTPUT_SCHEMA", "build_user_content()"),
}

PROMPTS: tuple[Prompt, ...] = (
    *(
        Prompt(
            name=f"{_REASONING}.{module}.SYSTEM_PROMPT",
            source=f"agent/app/reasoning/{module}.py",
            load=_attr(f"{_REASONING}.{module}", "SYSTEM_PROMPT"),
        )
        for module in _DRAFTERS
    ),
    *(
        Prompt(
            name=f"{_REASONING}.{module}.{sent}",
            source=f"agent/app/reasoning/{module}.py",
            load=(
                _rendered(module, sent.removesuffix("()"))
                if sent.endswith("()")
                else _attr(f"{_REASONING}.{module}", sent)
            ),
        )
        for module in _DRAFTERS
        for sent in _SENT_BY[module]
    ),
    Prompt(
        name=f"{_VISION}.SYSTEM_PROMPT",
        source="agent/app/workers/intake_extractor/vision_schema.py",
        load=_attr(_VISION, "SYSTEM_PROMPT"),
    ),
    *(
        Prompt(
            name=f"{_VISION}.OUTPUT_SCHEMA[{kind}]",
            source="agent/app/workers/intake_extractor/vision_schema.py",
            load=_vision_schema(kind),
        )
        for kind in ("lab_pdf", "intake_form")
    ),
    *(
        Prompt(
            name=f"app.workers.intake_extractor.vision._content_blocks()[{kind}]",
            source=(
                "agent/app/workers/intake_extractor/vision.py (page labels) and "
                "vision_schema.py (user_instruction)"
            ),
            load=_vision_user_content(kind),
        )
        for kind in ("lab_pdf", "intake_form")
    ),
    *(
        Prompt(
            name=f"judge_prompts/{rubric}.txt",
            source=f"agent/evals/w2/judge_prompts/{rubric}.txt",
            load=_judge_prompt(rubric),
        )
        for rubric in ("factually_consistent", "safe_refusal")
    ),
)


def digest(value: Any) -> str:
    """
    sha256 of a prompt value.

    A schema is serialised WITHOUT sorting its keys: under constrained
    decoding the property order is the order the model generates in, so a
    reordered schema is a changed prompt and must hash differently.
    """
    if isinstance(value, str):
        material = value
    elif isinstance(value, dict | list):
        material = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    else:
        raise GateError(f"cannot pin a prompt of type {type(value).__name__}")
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def current() -> dict[str, str]:
    return {prompt.name: digest(prompt.load()) for prompt in PROMPTS}


def unregistered() -> list[str]:
    """
    Prompts that exist and are not in PROMPTS, so the pin cannot silently
    stop being a catch-all: a module under app/reasoning/ with a
    SYSTEM_PROMPT, a static `_OUTPUT_SCHEMA`, or an `output_schema` or
    `build_user_content` function that is not pinned, or a new judge rubric
    in judge_config.json.
    """
    from evals.w2.gate import judge

    known = {prompt.name for prompt in PROMPTS}
    found: list[str] = []
    package = importlib.import_module(_REASONING)
    for info in pkgutil.iter_modules(package.__path__):
        module = importlib.import_module(f"{_REASONING}.{info.name}")
        sent: list[str] = []
        if isinstance(getattr(module, "SYSTEM_PROMPT", None), str):
            sent.append("SYSTEM_PROMPT")
        if isinstance(getattr(module, _STATIC_SCHEMA, None), dict):
            sent.append(_STATIC_SCHEMA)
        sent += [f"{attr}()" for attr in _RENDERED if callable(getattr(module, attr, None))]
        for attr in sent:
            name = f"{_REASONING}.{info.name}.{attr}"
            if name not in known:
                found.append(name)
    for rubric in judge.load_judge_config()["prompts"]:
        name = f"judge_prompts/{rubric}.txt"
        if name not in known:
            found.append(name)
    return found


def load_pins() -> dict[str, Any]:
    try:
        raw = json.loads(PROMPT_PINS.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise GateError(f"{PROMPT_PINS} is missing") from exc
    except json.JSONDecodeError as exc:
        raise GateError(f"{PROMPT_PINS} is not valid JSON: {exc}") from exc
    return dict(raw.get("pins", {}))


def drift() -> list[str]:
    """Every way the prompts differ from their pins, one line each. Empty = pinned."""
    pins = load_pins()
    sources = {prompt.name: prompt.source for prompt in PROMPTS}
    problems: list[str] = []
    for name, value in current().items():
        pinned = pins.get(name, {}).get("sha256")
        if pinned is None:
            problems.append(f"{name} ({sources[name]}) has no pin")
        elif pinned != value:
            problems.append(
                f"PROMPT CHANGED: {name} ({sources[name]}) hashes {value[:12]}, "
                f"pinned {pinned[:12]}"
            )
    for name in sorted(set(pins) - set(sources)):
        problems.append(f"{name} is pinned but no longer registered in prompt_pins.PROMPTS")
    for name in unregistered():
        problems.append(f"{name} is sent to a model and is not pinned at all")
    return problems


def _write() -> None:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "note": (
            "sha256 of every prompt VALUE the system or the judge sends, computed by "
            "agent/evals/w2/gate/prompt_pins.py and checked by W2-L1. A gate file: "
            "rewritten only in a W2_GATE_SESSION=1 session, after the golden set has "
            "been re-run against the changed prompt."
        ),
        "pins": {
            prompt.name: {"source": prompt.source, "sha256": digest(prompt.load())}
            for prompt in PROMPTS
        },
    }
    PROMPT_PINS.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.w2.gate.prompt_pins")
    parser.add_argument("--write", action="store_true", help="rewrite prompt_pins.json")
    args = parser.parse_args(argv)
    if args.write:
        if os.environ.get("W2_GATE_SESSION") != "1":
            print(
                "refusing to write prompt_pins.json: it is a gate file and this is not a "
                "W2_GATE_SESSION=1 session",
                file=sys.stderr,
            )
            return 2
        if unregistered():
            print(f"register these first: {unregistered()}", file=sys.stderr)
            return 2
        _write()
        print(f"pins written to {PROMPT_PINS}")
        return 0
    problems = drift()
    for line in problems:
        print(line)
    if not problems:
        print(f"all {len(PROMPTS)} prompts match their pins")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
