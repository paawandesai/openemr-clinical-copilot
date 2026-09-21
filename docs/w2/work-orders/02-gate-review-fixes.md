WORK ORDER 02: gate fixes from independent review. Branch
chore/w2-scaffolding. Save this verbatim as
docs/w2/work-orders/02-gate-review-fixes.md and commit it first.
Timebox 30 min.

1. Edit/Write fail-open (gate-guard.sh ~:341):
   - Add || gate_fail to the jq extraction.
   - Type-assert in the filter: path fields must be strings; arrays of
     strings are expanded and each checked; any other type -> exit 2.
   - Missing/empty path for an Edit/Write/MultiEdit/NotebookEdit call
     -> exit 2 (a write tool with no path we can check is not an allow).
   - Test: tool_input as a string, file_path as an array containing a
     protected path, file_path null, Write with no file_path, MultiEdit
     with a protected path in edits -> all exit 2. Normal writes allowed.

2. Unknown tool_name: add an explicit *) arm that exits 2 with
   "gate-guard: unrecognised tool <name>; update the case arms and the
   matcher together." Test with tool_name "Task" + a protected path,
   and an unknown tool with no path.

3. Out-of-repo gate config:
   - Block Edit/Write and Bash-token writes to ~/.claude/settings.json,
     ~/.claude/settings.local.json, ~/.zshenv, ~/.zshrc, ~/.zprofile,
     ~/.bashrc, ~/.bash_profile, ~/.profile (resolve ~ and $HOME).
   - scripts/gate-diff.sh: additionally fail if the string
     W2_GATE_SESSION appears in any of those files or in the environment
     block of ~/.claude/settings.json. Print which file.
   - Test both.

4. Move --print-globs, --print-exceptions, --check-path, --canonical
   out of gate-guard.sh into .claude/hooks/gate-cli.sh (protected too),
   sharing the glob definitions via a sourced read-only file (also
   protected). gate-guard.sh must take no argv; any argv -> exit 2.
   Update gate-effect-check.sh and gate-diff.sh to call gate-cli.sh.

5. CLAUDE.md: note the hook-timeout behaviour as a known limit.

ACCEPTANCE: all tests above with real output; prior regression suite
(traversal, symlinks, staging allowed, cases blocked, W2_GATE_SESSION=1
allows); 368 unit tests; check-claude-paths.sh and gate-diff.sh re-run.
Then give me a fresh review map with CURRENT line numbers, generated
after the final commit.

/report. STOP. Do not merge.
