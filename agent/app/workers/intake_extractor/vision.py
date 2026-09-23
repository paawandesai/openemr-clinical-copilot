"""
The vision call — D3 stage 2. The only model call in the worker.

Reuses Week 1's call exactly (`app/reasoning/draft_claims.py`): the same
cached `AsyncAnthropic` client (and so the SDK's default retry policy — two
retries on 408/409/429/5xx and connection errors — and its default request
timeout), the same model, effort, refusal fallback, `_final_text` parsing
and `provider_health` bookkeeping, and the same `llm_usage_event` /
`llm_call_failed` log shapes, so KEY_METRICS Metric 6 reads this call the
way it reads the brief's. What differs is the content: page images instead
of chart JSON, and the transcription schema in `vision_schema.py`.

**One call per document; pagination.** A document of up to
`MAX_PAGES_PER_CALL` pages (10) is sent in a single call, every page as one
PNG image block preceded by a `Page N` text label, which is what the model
reports `page` against. A longer document is split into consecutive
windows of 10 pages, one call per window, and the transcriptions are merged
in page order (`merge_raw_documents`): header fields take the first window
that reports them present, rows are concatenated, and a repeated
`test_key` gets a `_2`, `_3` suffix rather than failing the document.
Nothing above `MAX_PAGES` (30) is sent at all — that is
`page_limit_exceeded`, before any spend. Every committed factory sample is
one call.

**Resolution.** Pages are rendered at 200 dpi: a US Letter page is 2200 px
on its long edge, inside the 2576 px limit current models read at full
resolution, and lab fine print stays legible. That is roughly 3.5k input
tokens a page.

**Nothing here is logged but counts.** No prompt, no image, no output text:
the log lines carry token counts, latency, model ids and an error type name
(CLAUDE.md invariant 1).
"""

from __future__ import annotations

import asyncio
import base64
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field

import anthropic
import pymupdf
from pydantic import ValidationError

from app.middleware.correlation import get_correlation_id
from app.middleware.logging import log_event
from app.models.w2.documents import DocumentKind
from app.models.w2.workers import ExtractionFailureReason
from app.reasoning import provider_health
from app.reasoning.draft_claims import (
    _FALLBACK_BETA,
    _MAX_TOKENS,
    EFFORT,
    MODEL,
    ClaimDraftingError,
    _client,
    _final_text,
)
from app.workers.intake_extractor.vision_schema import (
    OUTPUT_SCHEMA,
    RAW_MODEL,
    SYSTEM_PROMPT,
    RawDocument,
    RawIntakeDocument,
    RawLabDocument,
    RawLabResult,
    user_instruction,
)

MAX_PAGES_PER_CALL = 10
MAX_PAGES = 30
RENDER_DPI = 200

#: Wall-clock bound on one window's call, retries included. Extraction runs
#: at upload time (D7), so this protects the job queue, not a user waiting.
CALL_TIMEOUT_SECONDS = 600.0

#: USD per million tokens, from Anthropic's published first-party prices
#: for the model this worker requests. Used for the per-document cost the
#: work order asks the worker to log. A served model not in this table (a
#: refusal fallback) is logged with `cost_usd: null` rather than guessed —
#: see D35 for why this constant exists despite compute_metrics.py's rule
#: against built-in prices.
PRICES_PER_MTOK: dict[str, dict[str, float]] = {
    "claude-opus-5": {"input": 5.0, "output": 25.0, "cache_write": 6.25, "cache_read": 0.5},
}


class VisionCallError(RuntimeError):
    """The vision step failed. `reason` is what the caller shows; the message holds no PHI."""

    def __init__(self, reason: ExtractionFailureReason, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason


@dataclass
class VisionUsage:
    """Summed over every window's call. Counts only — safe to log."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    latency_ms: int = 0
    served_models: list[str] = field(default_factory=list)
    cost_usd: float | None = 0.0

    def add(self, other: VisionUsage) -> None:
        self.calls += other.calls
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_creation_input_tokens += other.cache_creation_input_tokens
        self.cache_read_input_tokens += other.cache_read_input_tokens
        self.latency_ms += other.latency_ms
        self.served_models.extend(other.served_models)
        if self.cost_usd is None or other.cost_usd is None:
            self.cost_usd = None
        else:
            self.cost_usd += other.cost_usd


@dataclass(frozen=True)
class VisionResult:
    raw: RawDocument
    usage: VisionUsage


#: The seam the worker calls through. The default is `extract_with_claude`;
#: tests and the factory measurement pass a stub built from ground truth.
VisionExtractor = Callable[[DocumentKind, bytes, int], Awaitable[VisionResult]]


def cost_usd(model: str, usage: VisionUsage) -> float | None:
    price = PRICES_PER_MTOK.get(model)
    if price is None:
        return None
    return (
        usage.input_tokens * price["input"]
        + usage.output_tokens * price["output"]
        + usage.cache_creation_input_tokens * price["cache_write"]
        + usage.cache_read_input_tokens * price["cache_read"]
    ) / 1_000_000


def page_windows(page_count: int) -> list[tuple[int, int]]:
    """1-based inclusive page ranges, one per call."""
    if page_count > MAX_PAGES:
        raise VisionCallError(
            ExtractionFailureReason.PAGE_LIMIT_EXCEEDED,
            f"document has {page_count} pages; the limit is {MAX_PAGES}",
        )
    return [
        (start, min(start + MAX_PAGES_PER_CALL - 1, page_count))
        for start in range(1, page_count + 1, MAX_PAGES_PER_CALL)
    ]


def render_pages(pdf_bytes: bytes, first: int, last: int) -> list[bytes]:
    """PNG bytes for pages first..last (1-based, inclusive)."""
    handle = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        return [
            handle[number - 1].get_pixmap(dpi=RENDER_DPI, alpha=False).tobytes("png")
            for number in range(first, last + 1)
        ]
    finally:
        handle.close()


def _content_blocks(
    kind: DocumentKind, images: Sequence[bytes], first: int, last: int
) -> list[dict[str, object]]:
    blocks: list[dict[str, object]] = []
    for offset, image in enumerate(images):
        blocks.append({"type": "text", "text": f"Page {first + offset}"})
        blocks.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64.b64encode(image).decode("ascii"),
                },
            }
        )
    blocks.append({"type": "text", "text": user_instruction(kind, first, last)})
    return blocks


def parse_raw(kind: DocumentKind, text: str) -> RawDocument:
    """
    The model's JSON -> the strict raw model, or `model_output_invalid`.
    `model_validate_json` is the door (D22): JSON strings reach Literal
    fields the way the schema means them.
    """
    try:
        return RAW_MODEL[kind].model_validate_json(text)
    except ValidationError as error:
        raise VisionCallError(
            ExtractionFailureReason.MODEL_OUTPUT_INVALID,
            f"model output did not match the {kind.value} transcription schema "
            f"({error.error_count()} errors)",
        ) from None


async def _call_window(
    kind: DocumentKind, pdf_bytes: bytes, first: int, last: int
) -> tuple[str, VisionUsage]:
    images = render_pages(pdf_bytes, first, last)
    correlation_id = get_correlation_id() or "no-request-context"
    started = time.perf_counter()
    try:
        async with asyncio.timeout(CALL_TIMEOUT_SECONDS):
            async with _client().beta.messages.stream(
                model=MODEL,
                max_tokens=_MAX_TOKENS,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": _content_blocks(kind, images, first, last)}],
                thinking={"type": "adaptive"},
                output_config={
                    "effort": EFFORT,
                    "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA[kind]},
                },
                betas=[_FALLBACK_BETA],
                fallbacks="default",
            ) as stream:
                message = await stream.get_final_message()
                request_id = stream.request_id
    except (anthropic.APIError, TimeoutError) as exc:
        provider_health.record_failure()
        log_event(
            "llm_call_failed",
            event_data={
                "correlation_id": correlation_id,
                "requested_model": MODEL,
                "latency_ms": round((time.perf_counter() - started) * 1000),
                "error_type": type(exc).__name__,
                "consecutive_failures": provider_health.consecutive_failures(),
                "worker": "intake_extractor",
            },
        )
        reason = (
            ExtractionFailureReason.TIMED_OUT
            if isinstance(exc, TimeoutError | anthropic.APITimeoutError)
            else ExtractionFailureReason.VISION_CALL_FAILED
        )
        raise VisionCallError(reason, f"vision call failed ({type(exc).__name__})") from None
    provider_health.record_success()
    latency_ms = round((time.perf_counter() - started) * 1000)

    usage = message.usage
    fallback_ran = any(iteration.type == "fallback_message" for iteration in usage.iterations or [])
    window = VisionUsage(
        calls=1,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_creation_input_tokens=usage.cache_creation_input_tokens or 0,
        cache_read_input_tokens=usage.cache_read_input_tokens or 0,
        latency_ms=latency_ms,
        served_models=[message.model],
    )
    window.cost_usd = cost_usd(message.model, window)
    # Same llm_usage_event shape as draft_claims.py, plus the page window
    # and cost. No use_case: document extraction is not one of USERS.md's
    # three use cases (reported in the session report, not invented here).
    log_event(
        "llm_usage_event",
        event_data={
            "correlation_id": correlation_id,
            "model": message.model,
            "requested_model": MODEL,
            "served_model": message.model,
            "fallback_ran": fallback_ran,
            "effort": EFFORT,
            "request_id": request_id,
            "input_tokens": window.input_tokens,
            "output_tokens": window.output_tokens,
            "cache_creation_input_tokens": window.cache_creation_input_tokens,
            "cache_read_input_tokens": window.cache_read_input_tokens,
            "latency_ms": latency_ms,
            "stop_reason": message.stop_reason,
            "worker": "intake_extractor",
            "pages": last - first + 1,
            "cost_usd": window.cost_usd,
        },
    )

    if message.stop_reason in ("refusal", "max_tokens"):
        raise VisionCallError(
            ExtractionFailureReason.VISION_CALL_FAILED,
            f"vision call stopped with {message.stop_reason}",
        )
    try:
        return _final_text(message), window
    except ClaimDraftingError:
        raise VisionCallError(
            ExtractionFailureReason.MODEL_OUTPUT_INVALID, "vision response had no text block"
        ) from None


async def extract_with_claude(
    kind: DocumentKind, pdf_bytes: bytes, page_count: int
) -> VisionResult:
    """The production `VisionExtractor`: one call per page window, merged."""
    total = VisionUsage(cost_usd=0.0)
    parts: list[RawDocument] = []
    for first, last in page_windows(page_count):
        text, usage = await _call_window(kind, pdf_bytes, first, last)
        total.add(usage)
        parts.append(parse_raw(kind, text))
    return VisionResult(raw=merge_raw_documents(parts), usage=total)


# --------------------------------------------------------------------------
# Merging windows
# --------------------------------------------------------------------------


def _first_present(values: Sequence):  # noqa: ANN001, ANN202 - RawField | RawCheckbox
    for value in values:
        if value.state != "absent":
            return value
    return values[0]


def _merge_model(parts: Sequence, names: Sequence[str]) -> dict:  # noqa: ANN001
    return {name: _first_present([getattr(part, name) for part in parts]) for name in names}


def merge_raw_documents(parts: Sequence[RawDocument]) -> RawDocument:
    """Page-ordered merge of window transcriptions. Identity for one part."""
    if len(parts) == 1:
        return parts[0]
    first = parts[0]
    patient = type(first.patient)(
        **_merge_model([part.patient for part in parts], list(type(first.patient).model_fields))
    )
    if isinstance(first, RawLabDocument):
        labs = [part for part in parts if isinstance(part, RawLabDocument)]
        results: list[RawLabResult] = []
        seen: dict[str, int] = {}
        for part in labs:
            for row in part.results:
                count = seen.get(row.test_key, 0) + 1
                seen[row.test_key] = count
                if count > 1:
                    row = row.model_copy(update={"test_key": f"{row.test_key}_{count}"})
                results.append(row)
        return RawLabDocument(
            patient=patient,
            performing_lab=type(first.performing_lab)(
                **_merge_model(
                    [part.performing_lab for part in labs],
                    list(type(first.performing_lab).model_fields),
                )
            ),
            order=type(first.order)(
                **_merge_model([part.order for part in labs], list(type(first.order).model_fields))
            ),
            results=results,
        )
    intakes = [part for part in parts if isinstance(part, RawIntakeDocument)]
    scalars = _merge_model(
        intakes,
        [
            "visit_date",
            "chief_concern",
            "no_known_drug_allergies",
            "signature_name",
            "signature_date",
        ],
    )
    return RawIntakeDocument(
        patient=patient,
        medications=[row for part in intakes for row in part.medications],
        allergies=[row for part in intakes for row in part.allergies],
        family_history=[row for part in intakes for row in part.family_history],
        **scalars,
    )
