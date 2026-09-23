"""
Per-process state, and the startup warning when more than one process shares
the agent's traffic.

Three stores live in this process's memory and nowhere else:

- the conversation store (app/conversation/state.py) — Week 1 and the Week 2
  ask path share it;
- the document store and its extraction jobs (app/workers/document_store.py);
- the retriever's rerank circuit (app/retrieval/retriever.py).

With one worker that is correct. With two or more, each request lands on
whichever process the server picks, and what breaks is (docs:
agent/docs/deployment.md, "Week 2 and one worker"):

- an upload on process A is unknown to process B: B answers the status poll
  404 and a question naming the document `document_not_found`;
- the extraction runs on A only, and a re-upload landing on B runs it again
  (a second vision call, a second bill);
- a conversation's verified context is on one process, so a follow-up that
  refers back ("that result") is reported unresolved.

Every one of these fails safe — no process ever answers from another
patient's document or context, because each process checks its own patient
binding — but none of it works. Hence this check: the worker count is read
from the server's own configuration at startup and a warning is logged if it
is above one. It does not refuse to start, because the failure is a broken
feature, not a leak.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Mapping, Sequence

import structlog

MULTI_PROCESS_EVENT = "single_process_state_warning"

_WORKERS_FLAG = re.compile(r"^(?:--workers|-w)(?:=(\d+))?$")


def _flag_value(args: Sequence[str]) -> int | None:
    for index, arg in enumerate(args):
        match = _WORKERS_FLAG.match(arg)
        if match is None:
            continue
        value = match.group(1) or (args[index + 1] if index + 1 < len(args) else "")
        if value.isdigit():
            return int(value)
    return None


def configured_workers(argv: Sequence[str], environ: Mapping[str, str]) -> int:
    """
    How many worker processes the server was told to run: uvicorn's
    `--workers` or `WEB_CONCURRENCY`, gunicorn's `-w/--workers` on its command
    line or in `GUNICORN_CMD_ARGS`. The command line wins, as it does in both
    servers. 1 when nothing says otherwise.
    """
    from_argv = _flag_value(list(argv))
    if from_argv is not None:
        return from_argv
    from_gunicorn = _flag_value(environ.get("GUNICORN_CMD_ARGS", "").split())
    if from_gunicorn is not None:
        return from_gunicorn
    concurrency = environ.get("WEB_CONCURRENCY", "").strip()
    return int(concurrency) if concurrency.isdigit() else 1


def warn_if_multi_process(
    argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None
) -> int:
    """Log the warning when more than one worker is configured. Returns the count."""
    workers = configured_workers(
        sys.argv if argv is None else argv, os.environ if environ is None else environ
    )
    if workers > 1:
        structlog.get_logger(__name__).warning(
            MULTI_PROCESS_EVENT,
            configured_workers=workers,
            per_process_stores=["conversation", "documents", "rerank_circuit"],
            effect=(
                "uploads, extraction status and conversation context are not shared "
                "between workers; run one worker until these stores move out of process"
            ),
        )
    return workers
