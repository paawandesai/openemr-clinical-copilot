# The Week 2 eval gate — what it protects, and how

`CLAUDE.md` invariant 7 and the staging rule beside it are the summary every
session needs. This is the detail behind them: which paths are gated, how the
two hooks enforce it, what they do not cover, and what the merge check does.
Read this before touching anything under `agent/evals/`.

## What is gated

| Directory | Gated? | What goes there |
|---|---|---|
| `agent/evals/w2/factory/` | no | generator and factory code — write freely |
| `agent/evals/w2/staging/` | no | everything a generator emits — write freely |
| `agent/evals/w2/cases/` | **yes** | the committed case set the gate scores against |
| `agent/evals/w2/rubrics/` | **yes** | per-category rubrics |
| `agent/evals/w2/baseline.json` | **yes** | the committed scores a run is compared to |
| `agent/evals/w2/judge_config.*` | **yes** | pinned judge model and settings |

Gated for the same reason — a branch must not be able to move its own
measurement — and enforced by the same globs: `.gitlab-ci.yml`,
`.github/workflows/w2-*` and `.github/workflows/*eval*`, `.claude/hooks/*`,
`.claude/settings.json`, `.claude/settings.local.json`, and all of
`agent/evals/` outside `w2/factory/` and `w2/staging/` — which includes the
Week 1 suite, whose recorded results are the comparison point Week 2 is
measured against.

## The staging rule

A normal session builds the factory, runs it, and writes as many cases as it
likes into `staging/`. **Promoting a staged file into `cases/` is a gate
change**, and only a session started with `W2_GATE_SESSION=1` can do it — a
human sets that outside the session. This exists so that a branch cannot both
fail on a case and delete the case in the same commit. Report what you staged
and why it should be promoted; do not promote it yourself.

## `gate-guard.sh` — before the call

`gate-guard.sh` runs **before** a tool call and matches on the resolved path, so
`staging/../cases/x.json` and a write through a symlinked directory are blocked
the same as a direct write. **Do not create symlinks anywhere under
`agent/evals/`** — they are refused on both the link and the target side, exempt
directories included.

Text matching is a courtesy that produces a good error message, not the part
that holds: it can only block what it can recognise in the command. It also errs
toward blocking — a Bash heredoc that merely *quotes* a gated path, such as the
table above, is refused even though the file being written is this one. Write
that content with a tool that names its target file, and let the effect check
below confirm nothing gated actually moved.

## `gate-effect-check.sh` — after the call

`gate-effect-check.sh` runs **after every tool call** — not just Bash — and does
not read the command at all: it asks git what changed and restores any
protected path that moved. A `cd` into the directory, a path held in a
variable, a script that does the writing, and an MCP tool `gate-guard.sh` has
never heard of are all caught the same way. It reverts *any* uncommitted change
to a protected path, not only the last call's — so a session that is
deliberately editing gate files must be started with `W2_GATE_SESSION=1`, or it
will lose those edits on its next tool call.

## Known limit — hook timeout

Both hooks fail *closed* on every error they can observe: bad input, an
unresolvable path, a crash inside the script all exit 2. What they cannot cover
is not running. A hook cancelled at its timeout (`.claude/settings.json`: 30s
for `gate-guard.sh`, 120s for `gate-effect-check.sh`) is a hook that returned no
decision, and the tool call proceeds. On this repo the effect check takes
~130 ms on a clean tree and ~240 ms when it restores, so the margin is large —
but the gate is not a sandbox, and a machine under heavy load, a slow filesystem
or a very dirty tree could in principle push it past the limit and let a call
through unchecked. Treat the hooks as a guard against mistakes and drift, not as
a security boundary against a determined bypass.

Two more surfaces the hooks do not cover: a process started earlier that keeps
writing in the background, and another session or editor working in the same
worktree. The effect check notices those only incidentally, on the next tool
call.

## The merge check

`scripts/gate-diff.sh <branch>` lists the gate files a branch changes and exits
nonzero if there are any. It reads the globs from
`.claude/hooks/gate-cli.sh --check-path` (same `gate-lib.sh` as the hook), so the merge check and the
edit-time hook cannot drift apart. A nonzero exit is not a veto — it means
review the gate change on its own, before looking at the code.

**Relocation, done:** `feat/doc-factory` now writes to
`agent/evals/w2/factory/`, which is ungated, and
`scripts/gate-diff.sh feat/doc-factory` passes. It used to write one level up,
directly under agent/evals/, which the gate protects. Do not put a factory
there, and do not create a repo-root evals/ directory.
