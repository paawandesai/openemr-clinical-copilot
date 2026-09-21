WORK ORDER 01: gate hardening, part 2 + review map.
Branch chore/w2-scaffolding. Save this work order verbatim as
docs/w2/work-orders/01-gate-hardening.md and commit it first.

NOTE: this edits gate files (.claude/hooks/*, .claude/settings.json).
If your own hook blocks you, stop and tell me; I will restart this
session with W2_GATE_SESSION=1. Do not work around the block.

1. Effect-based check for Bash (does not depend on parsing command text):
   - Add a PostToolUse hook on Bash that runs
     git status --porcelain --untracked-files=all against the
     protected globs (read them from gate-guard.sh; don't duplicate).
   - When W2_GATE_SESSION is unset and any protected path is modified,
     staged, deleted, or newly created: restore it (git checkout /
     git clean, protected paths only), print which paths were restored,
     and exit 2.
   - Show it catching and restoring:
       cd agent/evals/w2 && echo x > baseline.json
       f=agent/evals/w2/cases/v.json; echo {} > "$f"
       a script file under staging/ that writes into cases/, then run it
   - Show a normal staging write and a pytest run unaffected.

2. Fail closed:
   - Every unexpected internal error in gate-guard.sh and the new hook
     exits 2 with a message, never 0 or 1 (trap ERR, guard unbound
     variables and empty/malformed stdin).
   - Test each: malformed JSON on stdin, empty stdin, a symlink loop
     (exercises the 32-hop guard), a path containing spaces. Each must
     give exit 2 or a correct decision, never a silent allow.

3. Review map for my manual review. Do NOT summarise the logic or
   assert it is correct; give me file:line pointers only, so I read the
   code myself:
   a. Where W2_GATE_SESSION is read, and confirmation nothing lets an
      argument, file, or stdin field override it (list every reference).
   b. The call order in each arm: canonicalisation, then exempt check,
      then protected check (line numbers for each step, per arm).
   c. Every exit statement in both hook scripts, each tagged
      ALLOW / BLOCK / ERROR.
   d. The PROTECTED and UNPROTECTED glob definitions.
   e. The settings.json entries showing which tools each hook is
      attached to (Edit, Write, MultiEdit, Bash, PostToolUse Bash).
      Flag any file-writing tool that is NOT covered.

Leave the D1 line-number reference as is.

ACCEPTANCE: real output for every test in 1 and 2; the full unit suite
still at 368 passed; gate-diff.sh and check-claude-paths.sh re-run.

/report, with the review map as its own section at the end. STOP.
Do not merge.
