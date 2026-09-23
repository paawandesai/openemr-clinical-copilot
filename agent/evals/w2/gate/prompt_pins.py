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

    cd agent && python -m evals.w2.gate.prompt_pins          # compare, exit 1 on drift
    cd agent && python -m evals.w2.gate.prompt_pins --write  # W2_GATE_SESSION=1 only
"""

from __future__ import annotations

import argparse
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


def _judge_prompt(rubric: str) -> Callable[[], Any]:
    def load() -> Any:
        # The judge's own loader, so the pinned text is exactly what it sends.
        from evals.w2.gate import judge

        return judge._prompt(judge.load_judge_config(), rubric)

    return load


_REASONING = "app.reasoning"
_VISION = "app.workers.intake_extractor.vision_schema"

PROMPTS: tuple[Prompt, ...] = (
    *(
        Prompt(
            name=f"{_REASONING}.{module}.SYSTEM_PROMPT",
            source=f"agent/app/reasoning/{module}.py",
            load=_attr(f"{_REASONING}.{module}", "SYSTEM_PROMPT"),
        )
        for module in (
            "draft_answer",
            "draft_claims",
            "draft_conversation_turn",
            "draft_follow_up",
        )
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
    elif isinstance(value, dict):
        material = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    else:
        raise GateError(f"cannot pin a prompt of type {type(value).__name__}")
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def current() -> dict[str, str]:
    return {prompt.name: digest(prompt.load()) for prompt in PROMPTS}


def unregistered() -> list[str]:
    """
    Prompts that exist and are not in PROMPTS, so the pin cannot silently
    stop being a catch-all: a new module under app/reasoning/ with a
    SYSTEM_PROMPT, or a new judge rubric in judge_config.json.
    """
    from evals.w2.gate import judge

    known = {prompt.name for prompt in PROMPTS}
    found: list[str] = []
    package = importlib.import_module(_REASONING)
    for info in pkgutil.iter_modules(package.__path__):
        module = importlib.import_module(f"{_REASONING}.{info.name}")
        if isinstance(getattr(module, "SYSTEM_PROMPT", None), str):
            name = f"{_REASONING}.{info.name}.SYSTEM_PROMPT"
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
