# Finding: a graph's input reaches LangSmith before any node can refuse

**Status:** fixed in the intake-extractor graph; guarded for every graph by
`agent/tests/test_graph_inputs_carry_nothing.py`. Belongs in
`W2_ARCHITECTURE.md` (tracing section) when that document exists.
**Found:** 2026-09-22, work order 12 (`feat/w2-extractor`). Recorded in D35,
item 11.

## What happened

The Week 1 defence against LangSmith tracing chart data (findings.md F-56)
has two layers: `UntracedGraph` forces tracing off on the one exported entry
point, and every node is wrapped in `_untraced`, which raises
`TracingEnabledError` when a LangSmith tracer is attached to its run.

The second layer is narrower than it looks. A tracer attached to the
**compiled graph** — passed as a callback, or picked up from
`LANGSMITH_TRACING` by any path that bypasses `UntracedGraph` — opens the
*root run* first, and the root run's payload is the caller's input dict,
verbatim. The node guard fires afterwards, on the first node. By then the
input is already queued for upload.

The Pre-Visit Brief was never exposed, by luck of shape rather than by
design: its input is `{"node_seconds": {}}`, and its per-run data (bearer
token, patient id) is LangGraph runtime *context*, which a tracer does not
receive. The first version of the intake-extractor graph put the document's
PDF bytes in the input. A test that attached a real `LangChainTracer` to the
compiled graph saw the node guard refuse — and LangSmith attempt to POST the
root run, PDF included, to `api.smith.langchain.com`. It was rejected with
401 (no API key is configured in that environment), and the document was a
synthetic factory sample, but the request left the machine.

## The rule

**A graph's input carries bookkeeping only. Everything per-run — tokens,
patient identifiers, document bytes, questions, chart records — is runtime
context.** State produced *inside* nodes is safe to hold in graph state,
because a node that refuses to run produces none.

## How it is enforced

`test_graph_inputs_carry_nothing.py` imports every module under `app`,
finds every `UntracedGraph` and raw compiled `Pregel`, and scans every run
call on them in `app/`: the input must be a dict literal of allowlisted
bookkeeping keys (`node_seconds`) with empty values. Variables, spreads,
non-empty values, aliases and graphs with no checked call site all fail,
and each failure mode has a control test. Its known limit is in its
docstring: it reads source, so a run through `getattr` or from outside
`app/` is not seen.

The containment harness for anything that *does* attach a tracer in a test
is Week 1's `_TracingEnabled` (`test_langsmith_tracing_guard.py`): a
loopback recorder as the LangSmith endpoint and every non-loopback DNS
lookup refused. A tracing test written without it can send data.
