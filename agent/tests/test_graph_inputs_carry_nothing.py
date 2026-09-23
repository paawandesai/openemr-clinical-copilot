"""
No compiled graph in `app/` is ever run with document bytes, chart content or
a patient identifier in its input — the root-run trace gap (docs/w2/
root-run-trace-gap.md).

Why input specifically: every node in a Week 2 graph runs inside the Week 1
`_untraced` guard, which refuses to execute with a LangSmith tracer attached
(findings.md F-56). But a tracer attached to the compiled graph posts the
*root run* — carrying the caller's input dict verbatim — before any node
gets the chance to refuse. `UntracedGraph` stops that on its own entry
point; it cannot stop a caller that reaches the compiled graph another way,
and the node guard cannot either. So the input must hold nothing worth
leaking, whatever path runs the graph. Per-run data (tokens, patient ids,
document bytes, questions) belongs in LangGraph runtime *context*, which a
tracer does not receive.

This is a general check, not a check on one graph:

1. **Discovery.** Every module under `app` is imported and every module-level
   `UntracedGraph` or raw compiled `Pregel` is collected, under every name
   it is bound to. A new graph anywhere in `app/` is found without this
   file changing.
2. **Call sites.** Every `.ainvoke / .invoke / .astream / .stream /
   .astream_events / .abatch / .batch` call in `app/` whose receiver is one
   of those names must pass, as its input, a dict *literal* whose keys are
   in `SAFE_INPUT_KEYS` and whose values are empty literals. A variable, a
   comprehension or a non-empty value fails — the check cannot see what a
   variable holds, so it refuses to guess.
3. **Every graph is checked.** A discovered graph with no checked call site
   fails, so a graph run only through an alias or a helper cannot slip past
   step 2 by never being named at a call.
4. **Aliasing is refused.** Rebinding a graph to another name inside a
   function (`graph = PRE_VISIT_BRIEF_GRAPH`) fails, since calls through the
   alias would not be recognised.
5. **Every node is guarded.** Every node of every discovered compiled graph
   is Week 1's `_untraced` wrapper (work order 13), read off the compiled
   graph rather than the source, with a control that a bare node is caught.

`test_the_scanner_catches_*` are the controls: each feeds the scanner a
source string with one violation and asserts it is reported, so a scanner
that silently stopped matching would fail rather than pass everything.

Known limit, stated rather than hidden: this reads source, so a graph run
via `getattr(graph, "ainvoke")(...)` or from outside `app/` is not seen.
Tests may run compiled graphs directly (the tracing tests do, with
contained endpoints); they are out of scope by design.
"""

from __future__ import annotations

import ast
import importlib
import pkgutil
from dataclasses import dataclass
from pathlib import Path

from langgraph.pregel import Pregel

import app
from app.graph.pre_visit_brief import UntracedGraph

APP_ROOT = Path(app.__file__).resolve().parent

#: Input keys a graph run may carry. Bookkeeping only: `node_seconds` is the
#: per-node timing reducer both graphs seed with `{}`.
SAFE_INPUT_KEYS = frozenset({"node_seconds"})

RUN_METHODS = frozenset(
    {"ainvoke", "invoke", "astream", "stream", "astream_events", "abatch", "batch"}
)


def discover_graph_names() -> dict[str, set[str]]:
    """Graph object id -> every module-level name it is bound to."""
    names: dict[int, set[str]] = {}
    for info in pkgutil.walk_packages(app.__path__, "app."):
        module = importlib.import_module(info.name)
        for name, value in vars(module).items():
            if isinstance(value, UntracedGraph | Pregel):
                names.setdefault(id(value), set()).add(name)
    return {str(key): value for key, value in names.items()}


@dataclass(frozen=True)
class Violation:
    where: str
    why: str


def _is_empty_literal(node: ast.expr) -> bool:
    if isinstance(node, ast.Dict | ast.List | ast.Tuple | ast.Set):
        return not getattr(node, "keys", None) and not getattr(node, "elts", None)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return node.func.id in {"dict", "list", "tuple", "set"} and not node.args
    return isinstance(node, ast.Constant) and node.value in (None, 0, 0.0, "")


def _receiver_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def scan_source(
    source: str, filename: str, graph_names: set[str]
) -> tuple[list[Violation], set[str]]:
    """Violations in one file, and the graph names it calls correctly."""
    violations: list[Violation] = []
    checked: set[str] = set()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign | ast.AnnAssign):
            value = node.value
            if value is not None and _receiver_name(value) in graph_names:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name) and target.id not in graph_names:
                        violations.append(
                            Violation(
                                f"{filename}:{node.lineno}",
                                f"graph aliased as {target.id!r}; calls through it go unchecked",
                            )
                        )
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr not in RUN_METHODS:
            continue
        receiver = _receiver_name(node.func.value)
        if receiver not in graph_names:
            continue
        where = f"{filename}:{node.lineno}"
        arg = (
            node.args[0]
            if node.args
            else next((kw.value for kw in node.keywords if kw.arg in ("input", "inputs")), None)
        )
        if not isinstance(arg, ast.Dict):
            violations.append(Violation(where, "graph input is not a dict literal"))
            continue
        bad = False
        for key, value in zip(arg.keys, arg.values, strict=True):
            if not (isinstance(key, ast.Constant) and key.value in SAFE_INPUT_KEYS):
                label = key.value if isinstance(key, ast.Constant) else "**expansion"
                violations.append(Violation(where, f"graph input key {label!r} is not bookkeeping"))
                bad = True
            elif not _is_empty_literal(value):
                violations.append(Violation(where, f"graph input {key.value!r} is not empty"))
                bad = True
        if not bad:
            checked.add(receiver)
    return violations, checked


def scan_app(graph_names: set[str]) -> tuple[list[Violation], set[str]]:
    violations: list[Violation] = []
    checked: set[str] = set()
    for path in sorted(APP_ROOT.rglob("*.py")):
        relative = path.relative_to(APP_ROOT).as_posix()
        found, ok = scan_source(path.read_text(), relative, graph_names)
        violations += found
        checked |= ok
    return violations, checked


# --------------------------------------------------------------------------


def test_graphs_are_discovered() -> None:
    groups = discover_graph_names().values()
    flat = {name for group in groups for name in group}
    # A control on discovery: if it stopped finding graphs, every check
    # below would pass over nothing.
    assert {"PRE_VISIT_BRIEF_GRAPH", "INTAKE_EXTRACTOR_GRAPH", "SUPERVISOR_GRAPH"} <= flat


def unguarded_nodes(graph: UntracedGraph) -> list[str]:
    """Nodes of one compiled graph whose function is not Week 1's `_untraced` wrapper."""
    found = []
    for node_name, node in graph._compiled.nodes.items():
        if node_name == "__start__":
            continue
        wrapped = getattr(node.bound, "afunc", None) or getattr(node.bound, "func", None)
        if getattr(wrapped, "__qualname__", "") != "_untraced.<locals>.untraced_node":
            found.append(node_name)
    return found


def test_every_node_of_every_graph_runs_inside_the_tracing_guard() -> None:
    """
    The other half of the rule. The input check above keeps the root run
    empty; this keeps every node from running with a tracer attached
    (CLAUDE.md invariant 1: "a node that can execute with a LangSmith tracer
    attached is a defect"). Read off the compiled graphs themselves, so a
    node added to any graph without `_untraced` fails here by name.
    """
    unguarded: list[str] = []
    for info in pkgutil.walk_packages(app.__path__, "app."):
        module = importlib.import_module(info.name)
        for name, value in vars(module).items():
            if isinstance(value, UntracedGraph):
                unguarded += [f"{info.name}.{name}:{node}" for node in unguarded_nodes(value)]
    assert unguarded == []


def test_the_node_guard_check_catches_an_unguarded_node() -> None:
    """Control: a graph with one bare node is reported, by that node's name."""
    from typing import TypedDict

    from langgraph.graph import END, START, StateGraph

    from app.graph.pre_visit_brief import _untraced

    class State(TypedDict, total=False):
        node_seconds: dict[str, float]

    async def step(state: State) -> dict:
        return {}

    async def guarded(state: State, runtime: object) -> dict:
        return {}

    graph = StateGraph(State)
    graph.add_node("bare", step)
    graph.add_node("guarded", _untraced(guarded), input_schema=State)
    graph.add_edge(START, "bare")
    graph.add_edge("bare", "guarded")
    graph.add_edge("guarded", END)
    assert unguarded_nodes(UntracedGraph(graph.compile())) == ["bare"]


def test_no_raw_compiled_graph_anywhere_in_app() -> None:
    # Week 1 asserted this for app.graph only; a graph built elsewhere (a
    # worker, the retriever) must be wrapped too.
    raw = []
    for info in pkgutil.walk_packages(app.__path__, "app."):
        module = importlib.import_module(info.name)
        raw += [f"{info.name}.{n}" for n, v in vars(module).items() if isinstance(v, Pregel)]
    assert raw == []


def test_every_graph_run_in_app_carries_only_bookkeeping_input() -> None:
    names = {name for group in discover_graph_names().values() for name in group}
    violations, _ = scan_app(names)
    assert violations == []


def test_every_discovered_graph_has_a_checked_call_site() -> None:
    groups = list(discover_graph_names().values())
    names = {name for group in groups for name in group}
    _, checked = scan_app(names)
    unchecked = [sorted(group) for group in groups if not group & checked]
    assert unchecked == []


# -- controls: the scanner sees what it claims to see ------------------------


GRAPHS = {"SOME_GRAPH"}


def _violations(source: str) -> list[str]:
    return [v.why for v in scan_source(source, "x.py", GRAPHS)[0]]


def test_the_scanner_accepts_the_safe_shape() -> None:
    found, checked = scan_source(
        'await SOME_GRAPH.ainvoke({"node_seconds": {}}, context=c)', "x.py", GRAPHS
    )
    assert found == [] and checked == {"SOME_GRAPH"}


def test_the_scanner_catches_document_bytes_in_the_input() -> None:
    assert _violations('SOME_GRAPH.ainvoke({"pdf": pdf, "node_seconds": {}}, context=c)') == [
        "graph input key 'pdf' is not bookkeeping"
    ]


def test_the_scanner_catches_a_patient_id_under_a_safe_key() -> None:
    assert _violations('SOME_GRAPH.invoke({"node_seconds": {"p": patient_id}})') == [
        "graph input 'node_seconds' is not empty"
    ]


def test_the_scanner_catches_a_variable_input() -> None:
    assert _violations("m.SOME_GRAPH.astream(state)") == ["graph input is not a dict literal"]


def test_the_scanner_catches_a_spread_input() -> None:
    assert _violations("SOME_GRAPH.ainvoke({**chart})") == [
        "graph input key '**expansion' is not bookkeeping"
    ]


def test_the_scanner_catches_an_alias() -> None:
    found = _violations("g = SOME_GRAPH\ng.ainvoke(state)")
    assert found == ["graph aliased as 'g'; calls through it go unchecked"]
