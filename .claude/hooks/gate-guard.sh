#!/usr/bin/env bash
#
# PreToolUse gate guard — Week 2.
#
# Blocks edits to the files that make the eval gate a gate: rubrics, eval
# cases, the committed baseline, judge config, CI pipeline config, this hook
# itself, and the settings file that wires it up. Also covers the Week 1 eval
# suite under agent/evals/, whose recorded results are the comparison point for
# Week 2.
#
# Two directories under agent/evals/w2/ are deliberately NOT protected:
#
#   agent/evals/w2/staging/   where generators and the case factory write
#   agent/evals/w2/factory/   the generator code itself
#
# A normal session generates freely into staging/. Promoting a staged file into
# agent/evals/w2/cases/ is a gate change, and needs a W2_GATE_SESSION=1 session.
#
# Matching is done on the CANONICAL path — .. collapsed and symlinks resolved —
# so `staging/../cases/x.json` and a write through a symlinked `staging/link/`
# are both seen as what they are. Creating a link is refused anywhere in the
# eval tree, exempt directories included, on both the link and the target side.
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
# Hook mode (no arguments): reads the PreToolUse hook input JSON on stdin
# (session_id, cwd, tool_name, tool_input, ...). Blocks with exit 2 + a reason
# on stderr, and also emits the documented permissionDecision JSON on stdout.
#
# CLI modes, so other tooling never has to duplicate this list:
#   --print-globs              one protected glob per line
#   --print-exceptions         one unprotected-override glob per line
#   --check-path <path>...     print each protected path; exit 1 if any
#                              (scripts/gate-diff.sh uses this)
#
# Self-test (no Claude Code needed):
#   echo '{"cwd":"'"$PWD"'","tool_name":"Write","tool_input":{"file_path":"agent/evals/w2/baseline.json"}}' \
#     | .claude/hooks/gate-guard.sh; echo "exit=$?"

set -uo pipefail

# --- protected paths, repo-root-relative ------------------------------------
# Matched with bash glob patterns against the repo-relative path. Note that in
# a [[ ]] pattern match, * spans / — so 'agent/evals/*' covers the whole tree.
PROTECTED_GLOBS=(
  'agent/evals/w2/rubrics'
  'agent/evals/w2/rubrics/*'
  'agent/evals/w2/cases'
  'agent/evals/w2/cases/*'
  'agent/evals/w2/baseline.json'
  'agent/evals/w2/judge_config.*'
  'agent/evals/*'
  '.gitlab-ci.yml'
  '.github/workflows/w2-*'
  '.github/workflows/*eval*'
  '.claude/hooks/*'
  '.claude/settings.json'
  '.claude/settings.local.json'
)

# Checked FIRST. A path matching one of these is allowed even though a
# PROTECTED_GLOBS entry (agent/evals/*) also matches it.
UNPROTECTED_GLOBS=(
  'agent/evals/w2/staging'
  'agent/evals/w2/staging/*'
  'agent/evals/w2/factory'
  'agent/evals/w2/factory/*'
)

WHY=$(cat <<'EOF'
This path is an eval-gate file. Implementation sessions do not edit eval
rubrics, eval cases, the eval baseline, judge config, CI config, the gate hook,
or the settings that wire it up (CLAUDE.md invariant 7).

The point of the gate is that the code being measured cannot move the
measurement. Editing it from an implementation session makes every downstream
"the evals pass" claim meaningless.

Generating cases is not blocked — it just does not land in cases/ by itself:
  - Write generated cases to agent/evals/w2/staging/ (not protected).
  - Factory/generator code goes in agent/evals/w2/factory/ (not protected).
  - Promoting staging/ -> cases/ is a gate change. A human re-runs the session
    with W2_GATE_SESSION=1 to do it.

If the gate file is genuinely wrong:
  - Do NOT route around it (no Bash redirect, no sed -i, no git checkout).
  - Report it under "Conflicts" in /report, with the specific line and why.
EOF
)

is_exempt_rel() {
  local rel="$1" glob
  for glob in "${UNPROTECTED_GLOBS[@]}"; do
    # shellcheck disable=SC2053  # intentional glob match, not string equality
    [[ $rel == $glob ]] && return 0
  done
  return 1
}

is_protected_rel() {
  local rel="$1" glob
  is_exempt_rel "$rel" && return 1
  for glob in "${PROTECTED_GLOBS[@]}"; do
    # shellcheck disable=SC2053  # intentional glob match, not string equality
    [[ $rel == $glob ]] && return 0
  done
  return 1
}

# --- path canonicalisation ---------------------------------------------------
# Glob matching on a path as written is trivially defeated:
#
#   agent/evals/w2/staging/../cases/x.json     .. climbs out of the exempt dir
#   agent/evals/w2/staging/link/x.json         a symlink points into a gated one
#
# So every candidate is canonicalised first — .. collapsed and symlinks
# resolved, the way the kernel would — and the glob is matched against the real
# destination. Pure bash: no realpath/readlink -f, which are not portable.
#
# The file need not exist. The longest existing prefix is resolved physically
# with `cd -P` (which resolves symlinks AND .. in the order the OS does), and
# the non-existent tail is appended.
canonical() {
  local p="$1" base_dir="${2:-$CWD}"
  case "$p" in
    /*) : ;;
    *)  p="$base_dir/$p" ;;
  esac

  local head="$p" tail="" guard=0
  while [ ! -e "$head" ] && [ "$head" != "/" ] && [ -n "$head" ] && [ "$guard" -lt 64 ]; do
    tail="$(basename -- "$head")${tail:+/$tail}"
    head="$(dirname -- "$head")"
    guard=$((guard + 1))
  done

  if [ -d "$head" ]; then
    head="$(cd -P -- "$head" 2>/dev/null && pwd -P)" || head="$p"
  elif [ -e "$head" ]; then
    # An existing non-directory, possibly a symlink chain to a file.
    local d b n=0 t
    d="$(cd -P -- "$(dirname -- "$head")" 2>/dev/null && pwd -P)" || d="$(dirname -- "$head")"
    b="$(basename -- "$head")"
    while [ -L "$d/$b" ] && [ "$n" -lt 32 ]; do
      t="$(readlink -- "$d/$b")"
      case "$t" in
        /*) d="$(cd -P -- "$(dirname -- "$t")" 2>/dev/null && pwd -P)" ;;
        *)  d="$(cd -P -- "$d/$(dirname -- "$t")" 2>/dev/null && pwd -P)" ;;
      esac
      b="$(basename -- "$t")"
      n=$((n + 1))
    done
    head="$d/$b"
  fi

  printf '%s' "${head%/}${tail:+/$tail}"
}

# Canonical absolute path -> repo-root-relative, when it is inside the repo.
# A path outside the repo is returned absolute and matches no glob.
to_repo_relative() {
  local abs
  abs=$(canonical "$1" "${2:-$CWD}")
  case "$abs" in
    "$REPO_ROOT"/*) printf '%s' "${abs#"$REPO_ROOT"/}" ;;
    "$REPO_ROOT")   printf '%s' "." ;;
    *)              printf '%s' "$abs" ;;
  esac
}

# Convenience wrappers taking a raw (possibly relative, possibly traversing)
# path rather than an already-normalised repo-relative one.
is_protected() { is_protected_rel "$(to_repo_relative "$1" "${2:-$CWD}")"; }
is_exempt()    { is_exempt_rel    "$(to_repo_relative "$1" "${2:-$CWD}")"; }

# --- CLI modes, handled before anything reads stdin --------------------------
# CWD/REPO_ROOT are needed by canonical(); in CLI mode they come from the
# process, in hook mode they are re-derived from the input JSON below.
CWD="$PWD"
REPO_ROOT=$(git -C "$CWD" rev-parse --show-toplevel 2>/dev/null || printf '%s' "$CWD")
REPO_ROOT=$(cd -P -- "$REPO_ROOT" 2>/dev/null && pwd -P || printf '%s' "$REPO_ROOT")
case "${1:-}" in
  --print-globs)
    printf '%s\n' "${PROTECTED_GLOBS[@]}"
    exit 0
    ;;
  --print-exceptions)
    printf '%s\n' "${UNPROTECTED_GLOBS[@]}"
    exit 0
    ;;
  --check-path)
    shift
    hits=0
    for rel in "$@"; do
      if is_protected "$rel"; then
        printf '%s\n' "$rel"
        hits=1
      fi
    done
    exit "$hits"
    ;;
  --canonical)
    # Debugging aid: show what a path actually resolves to.
    shift
    for rel in "$@"; do printf '%s\t%s\n' "$rel" "$(to_repo_relative "$rel")"; done
    exit 0
    ;;
esac

# --- read hook input ---------------------------------------------------------
INPUT=$(cat)

if ! command -v jq >/dev/null 2>&1; then
  echo "gate-guard: jq not found; cannot inspect tool input, failing closed." >&2
  exit 2
fi

TOOL_NAME=$(printf '%s' "$INPUT" | jq -r '.tool_name // empty')
CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // empty')
[ -n "$CWD" ] || CWD="$PWD"

# The session's real cwd replaces the process default set above; canonical()
# and to_repo_relative() resolve against it from here on.
REPO_ROOT=$(git -C "$CWD" rev-parse --show-toplevel 2>/dev/null || printf '%s' "$CWD")
REPO_ROOT=$(cd -P -- "$REPO_ROOT" 2>/dev/null && pwd -P || printf '%s' "$REPO_ROOT")

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
    # Best-effort second line of defence: a shell command can write a file just
    # as well as Write can. Only flags a command that both names a protected
    # path and contains something that mutates a file, so reading a gate file
    # (cat, grep, git diff) stays allowed.
    CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty')
    [ -n "$CMD" ] || exit 0

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
        link_dir="$(canonical "$(dirname -- "$link_raw")")"
        for tok in "${LINK_TOKENS[@]}"; do
          for rel in "$(to_repo_relative "$tok")" "$(to_repo_relative "$tok" "$link_dir")"; do
            if is_protected_rel "$rel" || is_exempt_rel "$rel"; then
              deny "$rel" "tool: Bash — creating a link into or out of the eval tree. Links are refused on both the link and the target side, in gated AND exempt directories, because a link from agent/evals/w2/staging/ into cases/ would turn every later write through it into an unreviewed gate change."
            fi
          done
        done
      fi
    fi

    # --- ordinary mutation ---------------------------------------------------
    printf '%s' "$CMD" | grep -Eq '(>|>>|\btee\b|\bsed\b[^|]*-i|\bperl\b[^|]*-i|\brm\b|\bmv\b|\bcp\b|\btruncate\b|\bdd\b|\bpatch\b|\bgit\s+checkout\s+--|\bgit\s+restore\b|\bchmod\b)' || exit 0

    # Each path-shaped token is canonicalised and checked on its own, rather
    # than asking whether the command string "mentions" a gate path.
    # `cp w2/staging/c.json w2/cases/c.json` names an exempt path AND a gated
    # one — a per-token check blocks it, a whole-string check would not.
    for tok in $TOKENS; do
      case "$tok" in
        -*|'') continue ;;
      esac
      rel=$(to_repo_relative "$tok")
      if is_protected_rel "$rel"; then
        if [ "$rel" != "$tok" ]; then
          deny "$rel" "tool: Bash — written as \"$tok\", which resolves here. The command mutates files. Reads are fine; writes are not. Generated cases go to agent/evals/w2/staging/, and promotion into cases/ needs a W2_GATE_SESSION=1 session."
        fi
        deny "$rel" "tool: Bash — the command mutates files and names this gate path. Reads are fine; writes are not. Generated cases go to agent/evals/w2/staging/, and promotion into cases/ needs a W2_GATE_SESSION=1 session."
      fi
    done
    ;;
esac

exit 0
