---
description: End-of-session report for a Week 2 work order — what changed, real command output, and what was not done
argument-hint: [optional note to include at the top]
disable-model-invocation: true
---

Produce the end-of-session report for this work order.

## Context collected for you

Branch and working tree:

```!
git rev-parse --abbrev-ref HEAD
git status --short
```

Changes against the branch point:

```!
git diff --stat $(git merge-base HEAD main 2>/dev/null || echo HEAD) 2>/dev/null | tail -40
```

## Instructions

$ARGUMENTS

Write the report to the terminal as Markdown, with exactly these five sections,
in this order, none omitted. If a section has nothing in it, write `None.` —
never delete the heading.

### 1. What changed

Every edit as `file:line`, one per line, with a short clause saying what the
change does. Group by file. Use the diff above to make sure nothing is missed —
including files you changed and then reverted, which belong here with a note.

### 2. Commands run

For each command you actually ran this session: the command, then its real
output. Rules:

- Paste the real output. Truncate long output in the middle with `[...]`, never
  at the end — the tail is where failures live.
- Include failures verbatim. A failing test is a result, not an embarrassment.
- Tests and evals are mandatory entries if you ran them, and a mandatory line
  under §3 if you did not.
- **Never write output for a command you did not run.** If you believe a
  command would pass, that belongs under ASSUMED in §3, not here.

### 3. NOT DONE / ASSUMED / UNVERIFIED

Three separate labelled lists.

- **NOT DONE** — anything in the work order's scope you did not do, and why.
  Includes tests not run.
- **ASSUMED** — every assumption load-bearing for the work: a contract you
  inferred rather than read, a default you picked, an ambiguity you resolved
  one way. Say what you assumed and what it would break if wrong.
- **UNVERIFIED** — anything you wrote that you did not observe working. Code
  that compiles is not code that runs; a hook that is configured is not a hook
  that fired.

### 4. Needs human browser verification

What only a person clicking through the UI can confirm — rendered output,
OpenEMR screens, anything behind a login. Name the URL and what to look at. If
nothing, `None.`

### 5. Conflicts

Anything you hit that contradicts `docs/w2/DECISIONS.md` or a `CLAUDE.md`
invariant, including:

- A `DECIDED` entry that the work order or the code pushed against — name the
  ID, say what pushed against it, and what you did instead of re-deciding it.
- An invariant you could not satisfy — say which, and where.
- A gate file you needed to change and did not (`CLAUDE.md` invariant 7).
- A path in `CLAUDE.md` that turned out not to exist.

Do not resolve a conflict silently. Reporting it is the whole point.

---

Then stop. Do not merge, do not deploy, do not start the next piece of work.
