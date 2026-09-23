"""
The rerank circuit (D41): opened by a slow rerank, closed again by time or
by skipped turns, reported apart from a slow rerank, and announced when it
opens and when it closes.

Before D41 the circuit latched for the life of the process, and a turn it
skipped reported `over_budget=true` exactly like a turn that was slow —
which is how one slow rerank in 52 was once read as 15%.

    cd agent && python -m pytest tests/test_rerank_circuit.py
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from structlog.testing import capture_logs

from app.retrieval.build import load_index
from app.retrieval.corpus_meta import load_registry
from app.retrieval.retriever import (
    RERANK_CIRCUIT_CLOSED_EVENT,
    RERANK_CIRCUIT_OPENED_EVENT,
    RERANK_CIRCUIT_RESET_SECONDS,
    RERANK_CIRCUIT_RESET_TURNS,
    GuidelineRetriever,
)
from app.retrieval.worker import RETRIEVAL_EVENT, run_evidence_retriever
from tests.test_retrieval_corpus import FakeEmbedder
from tests.test_retrieval_worker import StubReranker, request

SLOW = 0.05
BUDGET = 0.01


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def build(reranker: StubReranker, clock: Clock, *, seconds: float = 60.0, turns: int = 10):
    return GuidelineRetriever(
        load_index(),
        load_registry(),
        embedder=FakeEmbedder(),
        reranker=reranker,
        cache_dir=None,
        rerank_budget_seconds=BUDGET,
        circuit_reset_seconds=seconds,
        circuit_reset_turns=turns,
        clock=clock,
    )


def events(logs: list[dict], event_type: str) -> list[dict]:
    return [log["event_data"] for log in logs if log.get("event_type") == event_type]


def ask(retriever: GuidelineRetriever):
    return run_evidence_retriever(request(rerank=True), retriever)


def test_a_slow_rerank_opens_the_circuit_once_and_says_so() -> None:
    clock = Clock()
    retriever = build(StubReranker(delay_seconds=SLOW), clock)
    with capture_logs() as logs:
        output = ask(retriever)
    assert output.reranked is False
    assert retriever.rerank_circuit_open
    opened = events(logs, RERANK_CIRCUIT_OPENED_EVENT)
    assert len(opened) == 1
    assert opened[0]["elapsed_ms"] >= SLOW * 1000 * 0.9
    assert opened[0]["budget_ms"] == BUDGET * 1000
    line = events(logs, RETRIEVAL_EVENT)[0]
    assert (line["rerank_over_budget"], line["rerank_skipped_circuit_open"]) == (True, False)


def test_a_skipped_turn_is_not_reported_as_over_budget() -> None:
    clock = Clock()
    retriever = build(StubReranker(delay_seconds=SLOW), clock)
    ask(retriever)
    with capture_logs() as logs:
        output = ask(retriever)
    assert output.reranked is False
    line = events(logs, RETRIEVAL_EVENT)[0]
    assert (line["rerank_over_budget"], line["rerank_skipped_circuit_open"]) == (False, True)
    assert line["rerank_ms"] == 0.0


def test_the_circuit_closes_after_its_time_and_the_reranker_is_tried_again() -> None:
    clock = Clock()
    stub = StubReranker(delay_seconds=SLOW)
    retriever = build(stub, clock, seconds=60.0, turns=100)
    ask(retriever)
    ask(retriever)
    assert stub.calls == 1, "skipped while open"

    stub.delay_seconds = 0.0
    clock.now += 60.0
    with capture_logs() as logs:
        output = ask(retriever)
    assert output.reranked is True
    assert stub.calls == 2
    assert not retriever.rerank_circuit_open
    closed = events(logs, RERANK_CIRCUIT_CLOSED_EVENT)
    assert closed == [{"reason": "timeout", "open_seconds": 60.0, "skipped_turns": 1}]


def test_the_circuit_closes_after_enough_skipped_turns() -> None:
    clock = Clock()
    stub = StubReranker(delay_seconds=SLOW)
    retriever = build(stub, clock, seconds=3600.0, turns=3)
    ask(retriever)
    for _ in range(3):
        assert ask(retriever).reranked is False
    assert stub.calls == 1

    stub.delay_seconds = 0.0
    with capture_logs() as logs:
        assert ask(retriever).reranked is True
    assert stub.calls == 2
    closed = events(logs, RERANK_CIRCUIT_CLOSED_EVENT)
    assert [(c["reason"], c["skipped_turns"]) for c in closed] == [("turns", 3)]


def test_a_retry_that_is_still_slow_opens_it_again() -> None:
    clock = Clock()
    retriever = build(StubReranker(delay_seconds=SLOW), clock, seconds=60.0)
    ask(retriever)
    clock.now += 60.0
    with capture_logs() as logs:
        output = ask(retriever)
    assert output.reranked is False
    assert retriever.rerank_circuit_open
    assert len(events(logs, RERANK_CIRCUIT_CLOSED_EVENT)) == 1
    assert len(events(logs, RERANK_CIRCUIT_OPENED_EVENT)) == 1


def test_a_manual_reset_is_reported_and_a_reset_of_a_shut_circuit_is_silent() -> None:
    clock = Clock()
    retriever = build(StubReranker(delay_seconds=SLOW), clock)
    with capture_logs() as logs:
        retriever.reset_rerank_circuit()
    assert events(logs, RERANK_CIRCUIT_CLOSED_EVENT) == []

    ask(retriever)
    clock.now += 5.0
    with capture_logs() as logs:
        retriever.reset_rerank_circuit()
    assert events(logs, RERANK_CIRCUIT_CLOSED_EVENT) == [
        {"reason": "manual", "open_seconds": 5.0, "skipped_turns": 0}
    ]
    assert not retriever.rerank_circuit_open


def test_two_slow_reranks_in_flight_together_open_it_once() -> None:
    class Gate(StubReranker):
        def __init__(self) -> None:
            super().__init__(delay_seconds=SLOW)
            self.barrier = threading.Barrier(2)

        def score(self, query: str, passages: list[str]) -> list[float]:
            self.barrier.wait(timeout=5)
            return super().score(query, passages)

    retriever = build(Gate(), Clock())
    with capture_logs() as logs, ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: ask(retriever), range(2)))
    assert len(events(logs, RERANK_CIRCUIT_OPENED_EVENT)) == 1
    over = [line["rerank_over_budget"] for line in events(logs, RETRIEVAL_EVENT)]
    assert over == [True, True], "both really were slow"


def test_the_reset_defaults_and_their_environment_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    retriever = build(StubReranker(), Clock())
    assert (RERANK_CIRCUIT_RESET_SECONDS, RERANK_CIRCUIT_RESET_TURNS) == (60.0, 10)

    monkeypatch.setenv("AGENT_RERANK_CIRCUIT_RESET_SECONDS", "15")
    monkeypatch.setenv("AGENT_RERANK_CIRCUIT_RESET_TURNS", "nonsense")
    from_env = GuidelineRetriever(
        retriever.index, retriever.registry, embedder=FakeEmbedder(), cache_dir=None
    )
    assert (from_env.circuit_reset_seconds, from_env.circuit_reset_turns) == (15.0, 10)


def test_circuit_events_carry_numbers_and_a_reason_only() -> None:
    clock = Clock()
    retriever = build(StubReranker(delay_seconds=SLOW), clock, turns=1)
    with capture_logs() as logs:
        ask(retriever)
        ask(retriever)
        ask(retriever)
    for event_type in (RERANK_CIRCUIT_OPENED_EVENT, RERANK_CIRCUIT_CLOSED_EVENT):
        for data in events(logs, event_type):
            for key, value in data.items():
                assert isinstance(value, int | float) or (
                    key == "reason" and value in {"timeout", "turns", "manual"}
                ), (key, value)
    assert "eGFR" not in json.dumps(events(logs, RERANK_CIRCUIT_OPENED_EVENT))
