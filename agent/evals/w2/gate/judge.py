"""
The judge tier.

Reserved for the two judgements that need reading comprehension:
`factually_consistent` and `safe_refusal` (D6). Everything about it is aimed
at one property — the same output must get the same verdict tomorrow — since
a judge that flips on a rerun produces false BLOCKs, and a gate that fails
for no reason is a gate people learn to re-run until it passes.

The flake-control stack:

* **A pinned model, checked.** The request names `claude-haiku-4-5`; the
  response's `model` is compared with the pinned `model_version` and a
  mismatch fails the tier. A version pin nobody verifies is a comment.
* **Thinking off, output schema-constrained.** The reply is
  `{verdict, reason}` and nothing else, so there is no free-form phrasing to
  vary between runs.
* **Best of three, majority of two.** A single anomalous call cannot move a
  verdict.
* **A cache keyed on (case id, sha256(output), prompt version, model
  version).** Those are the four things that can legitimately change a
  verdict; nothing else re-runs the model.

What is NOT here: `temperature=0`. The design doc and the work order both ask
for it and it is not expressible — `temperature` was removed from
`Messages.create` in the anthropic 1.x SDK and the current model generation
rejects sampling parameters. Recorded in judge_config.json and reported as a
conflict rather than quietly dropped.

And the honest caveat, printed on every run: this judge is UNCALIBRATED. It
has not been scored against 100-200 human-labelled examples per rubric, so
its agreement with a human is unknown. Category M detects a judge that has
*drifted*; it does not establish that the judge was right to begin with.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evals.w2.gate.loader import load_judge_config
from evals.w2.gate.models import GateError
from evals.w2.gate.paths import JUDGE_CACHE, JUDGE_PROMPTS_DIR, W2_ROOT


class JudgeUnavailable(RuntimeError):
    """No credential. The judge tier is skipped with a visible notice."""


def price_per_mtok(config: dict[str, Any]) -> tuple[float, float]:
    """
    The (input, output) price per million tokens for the pinned judge model.

    Raises rather than defaulting. A price table keyed by model id is only
    useful if a model with no entry is an error: the alternative is a cost
    line that reads $0.0000 after somebody changes the model, which is
    indistinguishable from a run that made no calls and is exactly the kind
    of number people quote in a report a year later.
    """
    model = config.get("model_version")
    table = config.get("pricing_usd_per_mtok")
    if not isinstance(table, dict) or not table:
        raise GateError(
            "judge_config.json has no `pricing_usd_per_mtok` table; the gate will not "
            "report a cost it cannot compute"
        )
    entry = table.get(model)
    if entry is None:
        raise GateError(
            f"judge_config.json pins model_version {model!r} and its "
            f"`pricing_usd_per_mtok` table has no entry for it (it has "
            f"{sorted(table)}). Add the price beside the model rather than letting "
            "the run report $0.0000."
        )
    try:
        return float(entry["input"]), float(entry["output"])
    except (KeyError, TypeError, ValueError) as exc:
        raise GateError(
            f"the price entry for {model!r} is not a pair of numbers "
            f"({{'input': ..., 'output': ...}}): {exc}"
        ) from exc


def check_pricing_config() -> None:
    """
    Startup check, run on every gate run — `--det-only` included, where the
    judge itself is never constructed. A config error should surface on the
    cheapest run, not only on the one that spends money.
    """
    price_per_mtok(load_judge_config())


@dataclass(frozen=True)
class JudgeVerdict:
    verdict: str
    votes: tuple[str, ...]
    majority: int
    model_version: str
    prompt_version: str
    cached: bool
    reasons: tuple[str, ...]

    @property
    def unanimous(self) -> bool:
        return len(set(self.votes)) == 1


def _payload_digest(payload: dict[str, Any]) -> str:
    material = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _prompt(config: dict[str, Any], rubric: str) -> str:
    relative = config["prompts"].get(rubric)
    if relative is None:
        raise GateError(f"judge_config.json has no prompt for rubric {rubric}")
    path = (
        (W2_ROOT / relative)
        if not relative.startswith("judge_prompts/")
        else (JUDGE_PROMPTS_DIR / Path(relative).name)
    )
    if not path.is_file():
        raise GateError(f"judge prompt {path} is missing")
    return path.read_text(encoding="utf-8")


def _render(rubric: str, payload: dict[str, Any]) -> str:
    if rubric == "factually_consistent":
        return (
            "STATEMENT:\n"
            f"{payload['statement']}\n\n"
            "SOURCE TEXT IT CITES:\n"
            f"{payload['source_text']}\n"
        )
    if rubric == "safe_refusal":
        return f"REQUEST:\n{payload['request']}\n\nRESPONSE:\n{payload['response']}\n"
    raise GateError(f"the judge has no rendering for rubric {rubric}")


class Judge:
    """One judge for a whole gate run. Holds the client, the config and the cache."""

    def __init__(self, *, cache_dir: Path | None = None) -> None:
        self.config = load_judge_config()
        self.prompt_version: str = self.config["prompt_version"]
        self.model: str = self.config["model"]
        self.model_version: str = self.config["model_version"]
        # Fails at construction, before a single call is billed.
        self.price_in, self.price_out = price_per_mtok(self.config)
        self.cache_dir = cache_dir or JUDGE_CACHE
        self._client: Any | None = None
        self.calls_made = 0
        self.cache_hits = 0
        #: Token usage across this run's live calls, so the gate can print
        #: what a cold cache actually costs instead of an estimate.
        self.input_tokens = 0
        self.output_tokens = 0

    @staticmethod
    def credential_present() -> bool:
        return bool(os.environ.get("ANTHROPIC_API_KEY"))

    def _client_or_raise(self) -> Any:
        if self._client is not None:
            return self._client
        if not self.credential_present():
            raise JudgeUnavailable("ANTHROPIC_API_KEY is not set; the judge tier cannot run")
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - environment, not logic
            raise GateError(f"the anthropic SDK is not installed: {exc}") from exc
        self._client = anthropic.Anthropic()
        return self._client

    def _prompt_digest(self, rubric: str) -> str:
        """
        sha256 of the prompt TEXT, not of its version string.

        The design doc keys the cache on `judge_prompt_version`. That is one
        edit away from being wrong in the direction that matters: a grader who
        loosens a rubric and forgets to bump the version would get the old
        verdicts served straight out of the cache, and the judge meta-check
        set — the thing that exists to catch exactly that — would never run.
        Hashing the text means a changed prompt always misses.
        """
        return hashlib.sha256(_prompt(self.config, rubric).encode("utf-8")).hexdigest()[:16]

    def _cache_path(self, case_id: str, rubric: str, digest: str) -> Path:
        key = hashlib.sha256(
            "|".join(
                (
                    case_id,
                    rubric,
                    digest,
                    self.prompt_version,
                    self._prompt_digest(rubric),
                    self.model_version,
                )
            ).encode()
        ).hexdigest()[:32]
        return self.cache_dir / f"{case_id}.{rubric}.{key}.json"

    def _one_vote(self, rubric: str, payload: dict[str, Any]) -> tuple[str, str]:
        client = self._client_or_raise()
        response = client.messages.create(
            model=self.model,
            max_tokens=int(self.config["max_tokens"]),
            system=_prompt(self.config, rubric),
            output_config={
                "format": {"type": "json_schema", "schema": self.config["output_schema"]}
            },
            messages=[{"role": "user", "content": _render(rubric, payload)}],
        )
        self.calls_made += 1
        usage = getattr(response, "usage", None)
        if usage is not None:
            self.input_tokens += int(getattr(usage, "input_tokens", 0) or 0)
            self.output_tokens += int(getattr(usage, "output_tokens", 0) or 0)
        served = getattr(response, "model", "")
        if served != self.model_version:
            raise GateError(
                f"the judge was served {served!r} but judge_config.json pins "
                f"{self.model_version!r}. Every baseline verdict was produced by the "
                "pinned version; scoring against a different one is not a comparison."
            )
        text = "".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        )
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise GateError(f"the judge did not return the pinned output schema: {exc}") from exc
        verdict = parsed.get("verdict")
        if verdict not in ("pass", "fail"):
            raise GateError(f"the judge returned verdict {verdict!r}")
        return verdict, str(parsed.get("reason", ""))[:400]

    def estimated_cost_usd(self) -> float:
        """What this run's live judge calls cost, from the reported usage."""
        return (self.input_tokens * self.price_in + self.output_tokens * self.price_out) / 1_000_000

    def grade(self, case_id: str, rubric: str, payload: dict[str, Any]) -> JudgeVerdict:
        """Best of three, cached. Raises JudgeUnavailable with no credential."""
        digest = _payload_digest(payload)
        cache_path = self._cache_path(case_id, rubric, digest)
        if cache_path.is_file():
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            self.cache_hits += 1
            return JudgeVerdict(
                verdict=cached["verdict"],
                votes=tuple(cached["votes"]),
                majority=cached["majority"],
                model_version=cached["model_version"],
                prompt_version=cached["prompt_version"],
                cached=True,
                reasons=tuple(cached.get("reasons", ())),
            )

        votes: list[str] = []
        reasons: list[str] = []
        for _ in range(int(self.config["best_of"])):
            verdict, reason = self._one_vote(rubric, payload)
            votes.append(verdict)
            reasons.append(reason)

        tally = {value: votes.count(value) for value in set(votes)}
        verdict = max(tally, key=lambda value: tally[value])
        majority = tally[verdict]
        if majority < int(self.config["majority"]):
            raise GateError(
                f"{case_id}: no {self.config['majority']}-of-{self.config['best_of']} "
                f"majority in {votes}"
            )

        result = JudgeVerdict(
            verdict=verdict,
            votes=tuple(votes),
            majority=majority,
            model_version=self.model_version,
            prompt_version=self.prompt_version,
            cached=False,
            reasons=tuple(reasons),
        )
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(
                {
                    "case_id": case_id,
                    "rubric": rubric,
                    "payload_sha256": digest,
                    "verdict": result.verdict,
                    "votes": list(result.votes),
                    "majority": result.majority,
                    "model_version": result.model_version,
                    "prompt_version": result.prompt_version,
                    "reasons": list(result.reasons),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return result


#: One judge per process. The cache and the call counter are per-run state
#: the report prints, so handing every check its own Judge would lose them.
_JUDGE: Judge | None = None


def judge() -> Judge:
    global _JUDGE
    if _JUDGE is None:
        _JUDGE = Judge()
    return _JUDGE


def reset_judge() -> None:
    """For the planted-regression harness, which reloads config between runs."""
    global _JUDGE
    _JUDGE = None
