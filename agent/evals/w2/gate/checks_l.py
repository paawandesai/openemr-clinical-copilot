"""
Category L — prompt pins.

A deterministic catch-all for prompt edits. The behavioural cases (B, F, H,
I, M) catch a prompt change only where a case exercises what the change
breaks, and most of them replay recorded or stubbed model output, so an edit
to a drafting prompt reaches no model in a gate run. W2-L1 hashes the VALUE
of every prompt and compares it with `prompt_pins.json`, a gate file. See
`prompt_pins.py` for what is pinned and why values, not files.

It is a catch-all, not a substitute: a pinned prompt says nothing about
whether the prompt is any good. The behavioural cases stay, and a re-pin is
only legitimate after the golden set has been re-run against the new prompt.
"""

from __future__ import annotations

from evals.w2.gate import prompt_pins
from evals.w2.gate.models import Case, CheckOutcome
from evals.w2.gate.registry import check


@check("chk.l_every_prompt_matches_its_pin")
def l_every_prompt_matches_its_pin(case: Case) -> CheckOutcome:
    """W2-L1. Every prompt's value hashes to its pin; nothing sent is unpinned."""
    del case
    # Control: the digest moves on a one-character edit. A digest that ignored
    # whitespace, or hashed something other than the value, would pass every
    # edit below for nothing.
    sample = prompt_pins.PROMPTS[0].load()
    if prompt_pins.digest(sample) == prompt_pins.digest(sample + " "):
        return CheckOutcome.failed("control: the digest does not move on a one-character edit")

    problems = prompt_pins.drift()
    if problems:
        return CheckOutcome.failed(
            "; ".join(problems) + ". " + prompt_pins.REPIN_INSTRUCTION,
            drifted=len(problems),
        )
    return CheckOutcome.ok(f"all {len(prompt_pins.PROMPTS)} prompts match their pins")
