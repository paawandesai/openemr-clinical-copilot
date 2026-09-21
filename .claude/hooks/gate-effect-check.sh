#!/usr/bin/env bash
#
# PostToolUse effect check — Week 2.
#
# gate-guard.sh reads the command text before it runs, so it can only block
# what it can recognise. These all defeat text matching:
#
#   cd agent/evals/w2 && echo x > baseline.json     the path is never spelled out
#   f=agent/evals/w2/cases/v.json; echo {} > "$f"   the path is in a variable
#   ./agent/evals/w2/staging/gen.sh                 the writes are in a script
#
# This hook does not read the command at all. After every tool call it asks git
# what actually changed, and if a protected path moved, puts it back. Text
# parsing is a courtesy that produces a good error message; this is the part
# that holds.
#
# It is wired to matcher "*", not "Bash": an MCP server's filesystem tool, or
# any tool added later, writes files just as well as a shell does, and
# gate-guard.sh only knows how to read file_path/notebook_path out of the tools
# it was written for. Asking git what changed needs no such knowledge.
#
# Restores are confined to protected paths. It never runs a bare `git checkout`
# or `git clean` over the tree.
#
# WARNING — this reverts ANY uncommitted change to a protected path, not only
# one the last tool call made. In an implementation session that is exactly the
# point. If you are deliberately working on gate files, start Claude Code with:
#
#     W2_GATE_SESSION=1 claude
#
# and this hook stands down entirely. Editing gate files with the hook live
# means losing the edit on the next tool call — including this file itself.
#
# Reads PostToolUse hook input JSON on stdin; .tool_name is not consulted.
# Exit 0 = nothing to do. Exit 2 = something was restored, or an internal error
# (fail closed). The docs say exit
# 2 is not honoured on PostToolUse, so the message Claude actually reads is the
# `additionalContext` in the JSON on stdout; the restore itself is the
# enforcement either way.
#
# Self-test (no Claude Code needed):
#   jq -n --arg c "$PWD" '{cwd:$c,tool_name:"Bash",tool_input:{command:"x"}}' \
#     | .claude/hooks/gate-effect-check.sh; echo "exit=$?"

set -uo pipefail

# --- fail closed --------------------------------------------------------------
_INTENTIONAL=0
gate_exit() { _INTENTIONAL=1; exit "$1"; }

gate_fail() {
  printf 'gate-effect-check: %s\ngate-effect-check: failing closed. The gate could not verify what changed,\nwhich is not the same as nothing having changed. Report this.\n' "$1" >&2
  gate_exit 2
}

_on_exit() {
  local rc=$?
  [ -n "${_TMP:-}" ] && rm -f "$_TMP" 2>/dev/null
  [ "$_INTENTIONAL" = "1" ] && exit "$rc"
  printf 'gate-effect-check: unexpected internal error (status %s); failing closed.\n' "$rc" >&2
  exit 2
}
trap _on_exit EXIT

# --- the escape hatch, set outside the session by a human --------------------
if [ "${W2_GATE_SESSION:-}" = "1" ]; then
  gate_exit 0
fi

# --- input --------------------------------------------------------------------
command -v jq  >/dev/null 2>&1 || gate_fail "jq not found"
command -v git >/dev/null 2>&1 || gate_fail "git not found"

INPUT=$(cat) || gate_fail "could not read hook input from stdin"
[ -n "${INPUT//[[:space:]]/}" ] || gate_fail "empty stdin: no hook input to inspect"
printf '%s' "$INPUT" | jq -e . >/dev/null 2>&1 \
  || gate_fail "stdin is not valid JSON: cannot determine what ran"

CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // empty') || gate_fail "could not read .cwd"
[ -n "$CWD" ] || CWD="$PWD"

REPO_ROOT=$(git -C "$CWD" rev-parse --show-toplevel 2>/dev/null) \
  || gate_fail "\"$CWD\" is not inside a git repository; cannot tell what changed"
cd "$REPO_ROOT" || gate_fail "cannot enter repository root \"$REPO_ROOT\""

# The glob list lives in exactly one place (gate-lib.sh), reached through the
# read-only query interface. gate-guard.sh itself takes no arguments.
GUARD="$REPO_ROOT/.claude/hooks/gate-cli.sh"
[ -x "$GUARD" ] || gate_fail "gate-cli.sh not found or not executable at $GUARD"

# --- what actually changed ----------------------------------------------------
# -z because a path may contain a space, a quote or a newline; the default
# porcelain format escapes those and would need un-escaping.
#
# The output goes to a file, not a variable: $(...) silently drops NUL bytes, so
# capturing -z output in a variable throws away every record separator and the
# parse loop below reads nothing at all — a silent "nothing changed".
_TMP=$(mktemp "${TMPDIR:-/tmp}/gate-effect-check.XXXXXX") \
  || gate_fail "could not create a temporary file"

git status --porcelain -z --untracked-files=all > "$_TMP" \
  || gate_fail "git status failed; cannot tell what changed"

PATHS=()
while IFS= read -r -d '' entry; do
  [ -n "$entry" ] || continue
  xy="${entry:0:2}"
  path="${entry:3}"
  # A rename or copy is followed by a second NUL-terminated field holding the
  # original path. Both sides matter: renaming a case file out of cases/ is a
  # deletion from cases/.
  case "$xy" in
    R?|C?|?R|?C)
      if IFS= read -r -d '' orig; then
        PATHS+=("$orig")
      fi
      ;;
  esac
  PATHS+=("$path")
done < "$_TMP"

if [ "${#PATHS[@]}" -eq 0 ]; then
  gate_exit 0
fi

# --- which of them are gate files --------------------------------------------
HITS=$("$GUARD" --check-path "${PATHS[@]}")
GUARD_RC=$?
case "$GUARD_RC" in
  0) gate_exit 0 ;;                      # nothing protected changed
  1) : ;;                                # protected paths found
  *) gate_fail "gate-cli.sh --check-path exited $GUARD_RC" ;;
esac

# --- restore ------------------------------------------------------------------
RESTORED=()
FAILED=()

restore_one() {
  local path="$1"
  if git cat-file -e "HEAD:$path" 2>/dev/null; then
    # Tracked at HEAD: put the index and the working tree back.
    git reset -q HEAD -- "$path" >/dev/null 2>&1
    if git checkout -q HEAD -- "$path" >/dev/null 2>&1; then
      RESTORED+=("$path (restored from HEAD)")
    else
      FAILED+=("$path")
    fi
  else
    # Not at HEAD: newly created, staged or not. Remove it.
    git rm -q -f --cached --ignore-unmatch -- "$path" >/dev/null 2>&1
    if rm -f -- "$path" 2>/dev/null; then
      RESTORED+=("$path (removed: not present at HEAD)")
    else
      FAILED+=("$path")
    fi
  fi
}

while IFS= read -r hit; do
  [ -n "$hit" ] || continue
  restore_one "$hit"
done <<<"$HITS"

# --- report -------------------------------------------------------------------
{
  printf '\ngate-effect-check: a protected path changed on disk. Restored:\n\n'
  for r in "${RESTORED[@]:-}"; do [ -n "$r" ] && printf '  %s\n' "$r"; done
  if [ "${#FAILED[@]}" -gt 0 ]; then
    printf '\ngate-effect-check: COULD NOT restore these — fix them by hand:\n\n'
    for f in "${FAILED[@]}"; do printf '  %s\n' "$f"; done
  fi
  cat <<'EOF'

These are eval-gate files: rubrics, eval cases, the baseline, judge config, CI
config, the gate hook, or the settings that wire it up (CLAUDE.md invariant 7).
The tool call that just ran changed one, whether or not it named it.

The check is on the effect, not the command text, so `cd` into the directory, a
path held in a variable, and a script that does the writing are all caught the
same way.

Generated cases go in agent/evals/w2/staging/, which is not gated. Promoting a
staged file into cases/ is a gate change: report it, and a human re-runs the
session with W2_GATE_SESSION=1.
EOF
} >&2

N=$(printf '%s\n' "${RESTORED[@]:-}" | grep -c .)
MSG="gate-effect-check restored $N protected path(s) that the last tool call changed. See stderr for the list. Do not retry the write; report it instead."
jq -n --arg m "$MSG" '{
  hookSpecificOutput: {
    hookEventName: "PostToolUse",
    additionalContext: $m
  },
  systemMessage: $m
}' 2>/dev/null || true

gate_exit 2
