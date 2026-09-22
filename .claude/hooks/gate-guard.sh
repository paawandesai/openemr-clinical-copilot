#!/usr/bin/env bash
#
# PreToolUse gate guard — Week 2.
#
# Blocks edits to the files that make the eval gate a gate: rubrics, eval
# cases, the committed baseline, judge config, CI pipeline config, the hooks
# themselves, and the settings that wire them up. Also covers the Week 1 eval
# suite under agent/evals/, whose recorded results are the comparison point for
# Week 2, and the out-of-repo files that could set W2_GATE_SESSION for the next
# session (~/.claude/settings*.json and the shell startup files).
#
# Two directories under agent/evals/w2/ are deliberately NOT protected:
#
#   agent/evals/w2/staging/   where generators and the case factory write
#   agent/evals/w2/factory/   the generator code itself
#
# A normal session generates freely into staging/. Promoting a staged file into
# agent/evals/w2/cases/ is a gate change, and needs a W2_GATE_SESSION=1 session.
#
# Matching is done on the CANONICAL path — .. collapsed, symlinks resolved, ~
# and $HOME expanded — so `staging/../cases/x.json`, a write through a
# symlinked `staging/link/`, and `~/.zshrc` are all seen as what they are.
# Creating a link is refused anywhere in the eval tree, exempt directories
# included, on both the link and the target side.
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
# THIS SCRIPT TAKES NO ARGUMENTS. Its only input is the PreToolUse hook JSON on
# stdin. Any argv is a configuration error and fails closed. The read-only
# query interface — --print-globs, --check-path, --canonical and friends — is
# .claude/hooks/gate-cli.sh; the glob lists both read are in gate-lib.sh.
#
# Self-test (no Claude Code needed):
#   jq -n --arg c "$PWD" '{cwd:$c,tool_name:"Write",
#                          tool_input:{file_path:"agent/evals/w2/baseline.json"}}' \
#     | .claude/hooks/gate-guard.sh; echo "exit=$?"

set -uo pipefail

# --- fail closed --------------------------------------------------------------
# A gate that errors out and returns 0 is not a gate. Every exit this script
# means to make goes through gate_exit(); anything else reaching the EXIT trap
# — an unbound variable under `set -u`, a jq crash, a failed cd, a killed
# subprocess — is an internal error and leaves with 2, which blocks the tool
# call. Exit 1 is never a decision this script makes.
_INTENTIONAL=0

gate_exit() { _INTENTIONAL=1; exit "$1"; }

gate_fail() {
  printf 'gate-guard: %s\ngate-guard: failing closed — the tool call is blocked. This is a\nbug in the gate, not a decision about your change. Report it.\n' "$1" >&2
  if command -v jq >/dev/null 2>&1; then
    jq -n --arg r "gate-guard internal error: $1 (failing closed)" '{
      hookSpecificOutput: {
        hookEventName: "PreToolUse",
        permissionDecision: "deny",
        permissionDecisionReason: $r
      }
    }' 2>/dev/null || true
  fi
  gate_exit 2
}

_on_exit() {
  local rc=$?
  [ "$_INTENTIONAL" = "1" ] && exit "$rc"
  printf 'gate-guard: unexpected internal error (status %s); failing closed.\n' "$rc" >&2
  exit 2
}
trap _on_exit EXIT

# --- no arguments -------------------------------------------------------------
# A hook whose behaviour can be steered from its command line has a second way
# in. There is one way in: stdin.
if [ "$#" -gt 0 ]; then
  gate_fail "takes no arguments (got $# — \"$1\"); the query interface is .claude/hooks/gate-cli.sh"
fi

# --- input --------------------------------------------------------------------
command -v jq >/dev/null 2>&1 || gate_fail "jq not found; cannot inspect tool input"

INPUT=$(cat) || gate_fail "could not read hook input from stdin"

# Empty or unparseable stdin is not "no tool call to check" — it means this
# script cannot tell what is about to happen, which is the case it must never
# wave through.
[ -n "${INPUT//[[:space:]]/}" ] || gate_fail "empty stdin: no hook input to inspect"
printf '%s' "$INPUT" | jq -e . >/dev/null 2>&1 \
  || gate_fail "stdin is not valid JSON: cannot determine the tool call"

TOOL_NAME=$(printf '%s' "$INPUT" | jq -r '.tool_name // empty') \
  || gate_fail "could not read .tool_name from hook input"
[ -n "$TOOL_NAME" ] || gate_fail "hook input has no .tool_name"

CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // empty') || gate_fail "could not read .cwd"
[ -n "$CWD" ] || CWD="$PWD"

REPO_ROOT=$(git -C "$CWD" rev-parse --show-toplevel 2>/dev/null || printf '%s' "$CWD")
REPO_ROOT=$(cd -P -- "$REPO_ROOT" 2>/dev/null && pwd -P || printf '%s' "$REPO_ROOT")

# --- shared definitions -------------------------------------------------------
_LIB="$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/gate-lib.sh"
[ -r "$_LIB" ] || gate_fail "gate-lib.sh not found or unreadable at $_LIB"
# shellcheck source=/dev/null
. "$_LIB" || gate_fail "could not source gate-lib.sh"

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
  gate_exit 2
}

# --- the escape hatch, set outside the session by a human --------------------
if [ "${W2_GATE_SESSION:-}" = "1" ]; then
  gate_exit 0
fi

case "$TOOL_NAME" in
  Edit|Write|MultiEdit|NotebookEdit)
    # The extraction is type-asserted in jq and its status is checked. Before,
    # a tool_input that was not an object, a path field holding a number or an
    # array, or a write tool with no path at all, all produced an empty list
    # and fell through to "allow" — a write tool whose target this script
    # cannot read is precisely the case it must not wave through.
    PATHS=$(printf '%s' "$INPUT" | jq -r '
      def as_paths:
        if   type == "string" then .
        elif type == "array"  then (.[] | if type == "string" then .
                                          else error("path array holds a non-string element") end)
        else error("path field is \(type), not a string or array of strings") end;

      (.tool_input // error("tool_input is missing")) as $ti
      | (if ($ti | type) != "object" then error("tool_input is \($ti | type), not an object") else . end)
      | [ $ti.file_path?, $ti.notebook_path?,
          ($ti.edits[]?.file_path?), ($ti.edits[]?.notebook_path?) ]
      | map(select(. != null))
      | (if length == 0 then error("no file_path/notebook_path on a write tool") else . end)
      | .[] | as_paths
    ' 2>&1) || gate_fail "cannot read the target path for $TOOL_NAME: ${PATHS:-jq failed}"

    [ -n "${PATHS//[[:space:]]/}" ] \
      || gate_fail "$TOOL_NAME resolved to no path to check"

    while IFS= read -r raw; do
      [ -n "$raw" ] || continue
      resolve_or_fail rel "$raw"
      if is_protected_rel "$rel"; then
        # Report the resolved path, not the one as typed — otherwise
        # "staging/../cases/x.json" would be denied with a message naming a
        # directory that is not actually being written to.
        if [ "$rel" != "$raw" ]; then
          deny "$rel" "tool: $TOOL_NAME — written as \"$raw\", which resolves here"
        fi
        deny "$rel" "tool: $TOOL_NAME"
      fi
    done <<<"$PATHS"
    ;;

  Bash)
    CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty') \
      || gate_fail "could not read .tool_input.command"
    [ -n "$CMD" ] || gate_exit 0

    # Path-shaped tokens, with shell punctuation flattened to whitespace.
    TOKENS=$(printf '%s' "$CMD" | tr '|;&()<>`"'"'"'\n\t' ' ' )

    # --- link creation anywhere in the gated OR exempt tree ------------------
    # A symlink is the way to make an exempt directory into a door. Once
    # agent/evals/w2/staging/c -> ../cases exists, "write to staging/c/x.json"
    # is a write into cases/ that canonicalisation is needed to see. Rather
    # than rely on catching it afterwards, link creation is refused anywhere in
    # the eval tree — protected or exempt, link side or target side.
    if printf '%s' "$CMD" | grep -Eq '(^|[[:space:]/])ln([[:space:]]|$)|\bcp[[:space:]]+(-[[:alnum:]]*[sl])|os\.symlink|symlink_to|\bsymlink\('; then
      LINK_TOKENS=()
      for tok in $TOKENS; do
        case "$tok" in
          -*|'') continue ;;
          *[./]*) LINK_TOKENS+=("$tok") ;;
        esac
      done
      n=${#LINK_TOKENS[@]}
      if [ "$n" -ge 1 ]; then
        # `ln [opts] <target> <linkname>`: the target is resolved relative to
        # the link's directory, not to cwd, so resolve it that way too.
        link_raw="${LINK_TOKENS[$((n - 1))]}"
        # Canonicalise the link's PARENT, not the link itself: `ln` creates a
        # new name, and if that name already exists as a link, resolving it
        # would resolve the target against the wrong directory.
        link_dir="$(canonical "$(dirname -- "$link_raw")")" \
          || gate_fail "cannot resolve the link's parent directory from \"$link_raw\""
        for tok in "${LINK_TOKENS[@]}"; do
          resolve_or_fail rel_cwd "$tok"
          resolve_or_fail rel_lnk "$tok" "$link_dir"
          for rel in "$rel_cwd" "$rel_lnk"; do
            if is_protected_rel "$rel" || is_exempt_rel "$rel"; then
              deny "$rel" "tool: Bash — creating a link into or out of the eval tree. Links are refused on both the link and the target side, in gated AND exempt directories, because a link from agent/evals/w2/staging/ into cases/ would turn every later write through it into an unreviewed gate change."
            fi
          done
        done
      fi
    fi

    # --- ordinary mutation ---------------------------------------------------
    printf '%s' "$CMD" | grep -Eq '(>|>>|\btee\b|\bsed\b[^|]*-i|\bperl\b[^|]*-i|\brm\b|\bmv\b|\bcp\b|\btruncate\b|\bdd\b|\bpatch\b|\bgit\s+checkout\s+--|\bgit\s+restore\b|\bchmod\b)' || gate_exit 0

    # Each path-shaped token is canonicalised and checked on its own, rather
    # than asking whether the command string "mentions" a gate path.
    # `cp w2/staging/c.json w2/cases/c.json` names an exempt path AND a gated
    # one — a per-token check blocks it, a whole-string check would not.
    for tok in $TOKENS; do
      case "$tok" in
        -*|'') continue ;;
      esac
      resolve_or_fail rel "$tok"
      if is_protected_rel "$rel"; then
        if [ "$rel" != "$tok" ]; then
          deny "$rel" "tool: Bash — written as \"$tok\", which resolves here. The command mutates files. Reads are fine; writes are not. Generated cases go to agent/evals/w2/staging/, and promotion into cases/ needs a W2_GATE_SESSION=1 session."
        fi
        deny "$rel" "tool: Bash — the command mutates files and names this gate path. Reads are fine; writes are not. Generated cases go to agent/evals/w2/staging/, and promotion into cases/ needs a W2_GATE_SESSION=1 session."
      fi
    done
    ;;

  *)
    # Reached only if the settings.json matcher is widened without adding an
    # arm here. Failing closed is the whole point: an unrecognised tool is one
    # whose arguments this script does not know how to read, which is exactly
    # the tool that could write a gate file unnoticed.
    gate_fail "unrecognised tool $TOOL_NAME; update the case arms and the matcher together."
    ;;
esac

gate_exit 0
