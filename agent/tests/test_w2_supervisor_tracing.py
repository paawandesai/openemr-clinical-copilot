"""
The supervisor graph under LANGSMITH_TRACING=true (findings.md F-56,
docs/w2/root-run-trace-gap.md), in Week 1's containment harness: the
LangSmith endpoint is a loopback recorder and every non-loopback lookup is
refused, so a broken guard fails a test instead of sending data.

1. Through `run_ask_turn` (the only way app/ runs it), nothing is sent.
2. The compiled graph reached directly, with a tracer attached as a
   callback: the first node refuses, and the root run that *is* posted
   carries none of the question, token, patient id or document id — because
   they are runtime context, not graph input.
3. Control: the same direct run with the question put into the graph input
   (the defect the rule exists to prevent) does post it. Without this, a
   recorder that silently stopped receiving would pass (2).
"""

from __future__ import annotations

import asyncio
import os

from langchain_core.tracers.langchain import LangChainTracer, wait_for_all_tracers
from structlog.testing import capture_logs

from app.conversation.state import ConversationStore
from app.graph import pre_visit_brief as pvb
from app.graph import supervisor
from app.workers.document_store import DocumentStore
from tests.test_langsmith_tracing_guard import _reset_langsmith, _TracingEnabled
from tests.w2_graph_support import PATIENT, TOKEN, USER, ScriptedDrafter, offline_retriever

QUESTION = "SENTINEL-QUESTION-what-does-uspstf-say-about-statins-4e1a"
DOCUMENT_ID = "SENTINEL-DOCUMENT-ID-" + "d" * 40
SESSION = "session-trace-0001"


class SupervisorTracingTest(_TracingEnabled):
    def context(self) -> supervisor.SupervisorContext:  # type: ignore[override]
        store = ConversationStore()
        return supervisor.SupervisorContext(
            token=TOKEN,
            patient_id=PATIENT,
            patient_session_key=f"{USER}:{PATIENT}:{SESSION}",
            question=QUESTION,
            document_ids=(DOCUMENT_ID,),
            snapshot=store.open(USER, SESSION, PATIENT),
            correlation_id="0b1a7c1e-5d0b-4c1e-9d1e-1a2b3c4d5e6f",
            documents=DocumentStore(),
            retriever=offline_retriever,
            drafter=ScriptedDrafter(),
        )

    def secrets(self) -> tuple[str, ...]:
        return (QUESTION, TOKEN, PATIENT, DOCUMENT_ID)

    def test_run_ask_turn_sends_nothing(self) -> None:
        with capture_logs():
            response = asyncio.run(
                supervisor.run_ask_turn(
                    TOKEN,
                    USER,
                    PATIENT,
                    SESSION,
                    "What problems are on her list?",
                    store=ConversationStore(),
                    documents=DocumentStore(),
                    retriever=offline_retriever,
                    drafter=ScriptedDrafter(),
                )
            )
        self.assertEqual(response.routing[-1].decision.value, "answer")  # it really ran
        self.assert_nothing_sent()

    def test_a_tracer_on_the_compiled_graph_gets_an_empty_root_run_and_a_refusal(self) -> None:
        os.environ.pop("LANGSMITH_TRACING")
        _reset_langsmith()
        compiled = supervisor.SUPERVISOR_GRAPH._compiled

        with capture_logs(), self.assertRaises(pvb.TracingEnabledError):
            asyncio.run(
                compiled.ainvoke(
                    {"node_seconds": {}},
                    {"callbacks": [LangChainTracer()]},
                    context=self.context(),
                )
            )

        self.assertEqual(self.fhir_calls, 0)
        wait_for_all_tracers()
        self.assertNotEqual(self.recorder.requests, [], "expected the refused run to be traced")
        payload = self.recorder.payload()
        for secret in self.secrets():
            with self.subTest(absent=secret):
                self.assertNotIn(secret.encode(), payload)
        self.assertEqual(self.smith_lookups(), [])

    def test_control_a_question_put_in_the_input_is_posted(self) -> None:
        os.environ.pop("LANGSMITH_TRACING")
        _reset_langsmith()
        compiled = supervisor.SUPERVISOR_GRAPH._compiled

        with capture_logs(), self.assertRaises(pvb.TracingEnabledError):
            asyncio.run(
                compiled.ainvoke(
                    {"node_seconds": {QUESTION: 0.0}},
                    {"callbacks": [LangChainTracer()]},
                    context=self.context(),
                )
            )

        wait_for_all_tracers()
        self.assertIn(QUESTION.encode(), self.recorder.payload())
        self.assertEqual(self.smith_lookups(), [])
