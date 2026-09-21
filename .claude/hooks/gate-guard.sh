#!/usr/bin/env bash
#
# PreToolUse gate guard — Week 2.
#
# Blocks edits to the files that make the eval gate a gate: rubrics, the
# committed baseline, judge config, CI pipeline config, this hook itself, and
# the settings file that wires it up. Also covers the Week 1 eval suite under
# agent/evals/, whose recorded results are the comparison point for Week 2.
#
# The gate is only editable in a session started with W2_GATE_SESSION=1 in its
# environment. That variable is set outside the session, by a human, before
# launching Claude Code:
#
#     W2_GATE_SESSION=1 claude
#
# An agent cannot set it for itself: the hook reads the environment Claude Code
# was started with, and an `export` inside a Bash tool call lives and dies in
# that one subshell.
#
# Reads the PreToolUse hook input JSON on stdin (session_id, cwd, tool_name,
# tool_input, ...). Blocks with exit 2 + a reason on stderr, and also emits the
# documented permissionDecision JSON on stdout.
#
# Self-test (no Claude Code needed):
#   echo '{"cwd":"'"$PWD"'","tool_name":"Write","tool_input":{"file_path":"evals/baseline.json"}}' \
#     | .claude/hooks/gate-guard.sh; echo "exit=$?"

set -uo pipefail

# --- protected paths, repo-root-relative ------------------------------------
# Matched with bash glob patterns against the repo-relative path.
PROTECTED_GLOBS=(
  'evals/rubrics/*'
  'evals/rubrics'
  'evals/baseline.json'
  'evals/judge_config.*'
  'agent/evals/*'
  '.gitlab-ci.yml'
  '.github/workflows/w2-*'
  '.github/workflows/*eval*'
  '.claude/hooks/*'
  '.claude/settings.json'
  '.claude/settings.local.json'
)

WHY=$(cat <<'EOF'
This path is an eval-gate file. Implementation sessions do not edit eval
rubrics, the eval baseline, judge config, CI config, the gate hook, or the
settings that wire it up (CLAUDE.md invariant 7).

The point of the gate is that the code being measured cannot move the
measurement. Editing it from an implementation session makes every downstream
"the evals pass" claim meaningless.

If the gate file is genuinely wrong:
  - Do NOT route around it (no Bash redirect, no sed -i, no git checkout).
  - Report it under "Conflicts" in /report, with the specific line and why.
  - A human re-runs the session with W2_GATE_SESSION=1 to make the change.
EOF
)

# --- read hook input ---------------------------------------------------------
INPUT=$(cat)

if ! command -v jq >/dev/null 2>&1; then
  echo "gate-guard: jq not found; cannot inspect tool input, failing closed." >&2
  exit 2
fi

TOOL_NAME=$(printf '%s' "$INPUT" | jq -r '.tool_name // empty')
CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // empty')
[ -n "$CWD" ] || CWD="$PWD"

REPO_ROOT=$(git -C "$CWD" rev-parse --show-toplevel 2>/dev/null || printf '%s' "$CWD")

# Normalise a path (absolute or relative to cwd) to a repo-root-relative path.
to_repo_relative() {
  local p="$1"
  case "$p" in
    /*) : ;;
    *)  p="$CWD/$p" ;;
  esac
  # Collapse ./ and ../ without requiring the file to exist.
  local out=() part
  local IFS='/'
  for part in $p; do
    case "$part" in
      ''|'.') : ;;
      '..')   [ ${#out[@]} -gt 0 ] && unset 'out[${#out[@]}-1]' ;;
      *)      out+=("$part") ;;
    esac
  done
  p="/$(IFS=/; printf '%s' "${out[*]-}")"
  printf '%s' "${p#"$REPO_ROOT"/}"
}

is_protected() {
  local rel="$1" glob
  for glob in "${PROTECTED_GLOBS[@]}"; do
    # shellcheck disable=SC2053  # intentional glob match, not string equality
    [[ $rel == $glob ]] && return 0
  done
  return 1
}

deny() {
  local rel="$1" detail="$2"
  local reason="BLOCKED: $rel

$WHY

($detail)"
  jq -n --arg r "$reason" '{
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: $r
    }
  }'
  printf '%s\n' "$reason" >&2
  exit 2
}

# --- the escape hatch, set outside the session by a human --------------------
if [ "${W2_GATE_SESSION:-}" = "1" ]; then
  exit 0
fi

case "$TOOL_NAME" in
  Edit|Write|MultiEdit|NotebookEdit)
    PATHS=$(printf '%s' "$INPUT" | jq -r '
      [ .tool_input.file_path?, .tool_input.notebook_path?, (.tool_input.edits[]?.file_path?) ]
      | map(select(. != null)) | unique | .[]')
    while IFS= read -r raw; do
      [ -n "$raw" ] || continue
      rel=$(to_repo_relative "$raw")
      if is_protected "$rel"; then
        deny "$rel" "tool: $TOOL_NAME"
      fi
    done <<<"$PATHS"
    ;;

  Bash)
    # Best-effort second line of defence: a shell command can write a file just
    # as well as Write can. Only flags a command that both names a protected
    # path and contains something that mutates a file, so reading a gate file
    # (cat, grep, git diff) stays allowed.
    CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty')
    [ -n "$CMD" ] || exit 0
    if printf '%s' "$CMD" | grep -Eq '(>|>>|\btee\b|\bsed\b[^|]*-i|\bperl\b[^|]*-i|\brm\b|\bmv\b|\bcp\b|\btruncate\b|\bdd\b|\bpatch\b|\bgit\s+checkout\s+--|\bgit\s+restore\b|\bchmod\b|\bln\b)'; then
      for glob in "${PROTECTED_GLOBS[@]}"; do
        needle="${glob%/\*}"
        needle="${needle%\*}"
        needle="${needle%.}"
        [ -n "$needle" ] || continue
        if printf '%s' "$CMD" | grep -Fq "$needle"; then
          deny "$needle" "tool: Bash — the command both names a gate path and mutates files. If this is a read-only command that merely mentions the path, rephrase it so it does not also contain a write construct."
        fi
      done
    fi
    ;;
esac

exit 0
