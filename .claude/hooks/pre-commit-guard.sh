#!/usr/bin/env bash
#
# PreToolUse guard for `git commit` — Week 2.
#
# The git pre-commit hook (scripts/install-git-hooks.sh) is the real gate; this
# runs the same scanner one step earlier so the block lands in the transcript
# as a reason Claude can act on, rather than as a failed shell command.
#
# Reads PreToolUse hook input JSON on stdin. Only acts on a Bash tool call that
# is actually a `git commit`. Blocks with exit 2 + reason on stderr.
#
# Self-test:
#   echo '{"cwd":"'"$PWD"'","tool_name":"Bash","tool_input":{"command":"git commit -m x"}}' \
#     | .claude/hooks/pre-commit-guard.sh; echo "exit=$?"

set -uo pipefail

INPUT=$(cat)

command -v jq >/dev/null 2>&1 || exit 0

TOOL_NAME=$(printf '%s' "$INPUT" | jq -r '.tool_name // empty')
[ "$TOOL_NAME" = "Bash" ] || exit 0

CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty')
printf '%s' "$CMD" | grep -Eq '\bgit\b[^|;&]*\bcommit\b' || exit 0

# --no-verify skips the git hook, so it must not skip this one.
CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // empty')
[ -n "$CWD" ] || CWD="$PWD"

SCAN="$(cd "$CWD" && git rev-parse --show-toplevel 2>/dev/null)/.claude/hooks/secret-scan.sh"
[ -x "$SCAN" ] || exit 0

OUTPUT=$(cd "$CWD" && "$SCAN" --staged 2>&1)
STATUS=$?

if [ "$STATUS" -ne 0 ]; then
  REASON="BLOCKED: the staged changes did not pass the secret scan.

$OUTPUT

Remove the credential or the .env file from the commit. Do not pass
--no-verify, do not reword the value, do not disable the hook. If you believe
this is a false positive, report it under UNVERIFIED in /report and leave the
decision to a human."
  jq -n --arg r "$REASON" '{
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: $r
    }
  }'
  printf '%s\n' "$REASON" >&2
  exit 2
fi

exit 0
