# shellcheck shell=bash
#
# Shared gate definitions — Week 2. SOURCED, never executed.
#
# The one place the protected/unprotected glob lists and the path-resolution
# rules live. gate-guard.sh (the PreToolUse hook), gate-cli.sh (the query
# interface) and, through gate-cli.sh, gate-effect-check.sh and
# scripts/gate-diff.sh all read them from here, so the edit-time block, the
# after-the-fact restore and the pre-merge check cannot drift apart.
#
# This file is itself a gate file (.claude/hooks/* is protected).
#
# A caller must define, before sourcing:
#   gate_fail <message>   — abort, failing closed
#   CWD                   — the directory relative paths resolve against
#   REPO_ROOT             — canonical repository root
#
# Not executable on purpose: it has no main.

if [ -n "${BASH_SOURCE[0]:-}" ] && [ "${BASH_SOURCE[0]}" = "${0}" ]; then
  echo "gate-lib.sh is a library; source it, do not run it." >&2
  exit 2
fi

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

# --- protected paths OUTSIDE the repository ----------------------------------
# The gate's authority rests on W2_GATE_SESSION being set by a human outside
# the session. Anything that can set an environment variable for the next
# Claude Code process can grant that, so these files are gate config too, even
# though they are nowhere near the repo. Absolute, $HOME expanded at load.
HOME_PROTECTED=(
  "${HOME:-/nonexistent}/.claude/settings.json"
  "${HOME:-/nonexistent}/.claude/settings.local.json"
  "${HOME:-/nonexistent}/.zshenv"
  "${HOME:-/nonexistent}/.zshrc"
  "${HOME:-/nonexistent}/.zprofile"
  "${HOME:-/nonexistent}/.bashrc"
  "${HOME:-/nonexistent}/.bash_profile"
  "${HOME:-/nonexistent}/.profile"
)

WHY=$(cat <<'EOF'
This path is an eval-gate file. Implementation sessions do not edit eval
rubrics, eval cases, the eval baseline, judge config, CI config, the gate hook,
or the settings that wire it up (CLAUDE.md invariant 7).

The point of the gate is that the code being measured cannot move the
measurement. Editing it from an implementation session makes every downstream
"the evals pass" claim meaningless.

Shell startup files and ~/.claude settings count too: they can set
W2_GATE_SESSION for the next session, which is the same as turning the gate
off.

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
  [ "${#UNPROTECTED_GLOBS[@]}" -eq 0 ] && return 1
  for glob in "${UNPROTECTED_GLOBS[@]}"; do
    # shellcheck disable=SC2053  # intentional glob match, not string equality
    [[ $rel == $glob ]] && return 0
  done
  return 1
}

is_protected_rel() {
  local rel="$1" glob
  is_exempt_rel "$rel" && return 1
  # Absolute paths reach here when they resolved outside the repository; those
  # are only ever protected by the HOME_PROTECTED list.
  for glob in "${HOME_PROTECTED[@]}"; do
    [ -n "$glob" ] && [ "$rel" = "$glob" ] && return 0
  done
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
# canonical() and to_repo_relative() are called inside $( ), so they signal
# failure by RETURNING nonzero, never by exiting — an exit there ends only the
# subshell. Callers go through resolve_or_fail(), which runs in the current
# shell and turns that nonzero into a real fail-closed block.
canon_err() { printf 'gate: %s\n' "$1" >&2; return 1; }

canonical() {
  local p="$1" base_dir="${2:-$CWD}"

  # A tool input or a shell token may carry ~ or $HOME unexpanded; neither the
  # kernel nor [[ ]] will expand them, so ~/.zshrc would never match the
  # HOME_PROTECTED entry it plainly is.
  case "$p" in
    '~')            p="${HOME:-/nonexistent}" ;;
    '~/'*)          p="${HOME:-/nonexistent}/${p#\~/}" ;;
    '$HOME')        p="${HOME:-/nonexistent}" ;;
    '$HOME/'*)      p="${HOME:-/nonexistent}/${p#\$HOME/}" ;;
    '${HOME}')      p="${HOME:-/nonexistent}" ;;
    '${HOME}/'*)    p="${HOME:-/nonexistent}/${p#\$\{HOME\}/}" ;;
  esac

  case "$p" in
    /*) : ;;
    *)  p="$base_dir/$p" ;;
  esac

  # Strip trailing components until something exists. `-L` is tested as well as
  # `-e`, because `-e` is false for a dangling or looping symlink: without it
  # the loop would strip the link away and canonicalise the path as if the link
  # were an ordinary missing directory. A dangling
  # `staging/dangle -> ../cases/nope` would then read as "under staging/".
  local head="$p" tail="" guard=0
  while [ ! -e "$head" ] && [ ! -L "$head" ] && [ "$head" != "/" ] && [ -n "$head" ] && [ "$guard" -lt 64 ]; do
    tail="$(basename -- "$head")${tail:+/$tail}"
    head="$(dirname -- "$head")"
    guard=$((guard + 1))
  done
  if [ "$guard" -ge 64 ]; then
    canon_err "path \"$p\" has more than 64 unresolvable components"
    return 1
  fi

  if [ -d "$head" ]; then
    # A cd -P failure here is a symlink loop (ELOOP) or an unreadable
    # directory. Either way the real destination is unknown, so fail closed
    # rather than fall back to the unresolved path.
    head="$(cd -P -- "$head" 2>/dev/null && pwd -P)" \
      || { canon_err "cannot resolve directory \"$head\" (symlink loop or unreadable)"; return 1; }
  elif [ -e "$head" ] || [ -L "$head" ]; then
    # An existing non-directory, possibly a symlink chain to a file. `-e` is
    # false for a dangling or looping link, so `-L` is tested too.
    local d b n=0 t
    d="$(cd -P -- "$(dirname -- "$head")" 2>/dev/null && pwd -P)" \
      || { canon_err "cannot resolve parent of \"$head\""; return 1; }
    b="$(basename -- "$head")"
    while [ -L "$d/$b" ] && [ "$n" -lt 32 ]; do
      t="$(readlink -- "$d/$b")" || { canon_err "cannot read link \"$d/$b\""; return 1; }
      case "$t" in
        /*) d="$(cd -P -- "$(dirname -- "$t")" 2>/dev/null && pwd -P)" \
              || { canon_err "cannot resolve link target directory for \"$d/$b\""; return 1; } ;;
        *)  d="$(cd -P -- "$d/$(dirname -- "$t")" 2>/dev/null && pwd -P)" \
              || { canon_err "cannot resolve link target directory for \"$d/$b\""; return 1; } ;;
      esac
      b="$(basename -- "$t")"
      n=$((n + 1))
    done
    # Still a link after 32 hops: a loop, or a chain too deep to trust.
    if [ -L "$d/$b" ]; then
      canon_err "symlink chain from \"$p\" exceeds 32 hops (loop?) — cannot determine the real path"
      return 1
    fi
    head="$d/$b"
  fi

  printf '%s' "${head%/}${tail:+/$tail}"
}

# Canonical absolute path -> repo-root-relative, when it is inside the repo.
# A path outside the repo is returned absolute; only HOME_PROTECTED matches it.
to_repo_relative() {
  local abs
  abs=$(canonical "$1" "${2:-$CWD}") || return 1
  case "$abs" in
    "$REPO_ROOT"/*) printf '%s' "${abs#"$REPO_ROOT"/}" ;;
    "$REPO_ROOT")   printf '%s' "." ;;
    *)              printf '%s' "$abs" ;;
  esac
}

# resolve_or_fail <varname> <raw path> [base dir]
# Resolves in the CURRENT shell so a resolution failure can block the tool
# call, and assigns the result to the named variable.
resolve_or_fail() {
  local __var="$1" __raw="$2" __base="${3:-$CWD}" __out
  __out=$(to_repo_relative "$__raw" "$__base") \
    || gate_fail "cannot resolve path \"$__raw\" — refusing to guess where it points"
  printf -v "$__var" '%s' "$__out"
}

# Convenience wrappers taking a raw (possibly relative, possibly traversing)
# path rather than an already-normalised repo-relative one.
is_protected() { local __r; resolve_or_fail __r "$1" "${2:-$CWD}"; is_protected_rel "$__r"; }
is_exempt()    { local __r; resolve_or_fail __r "$1" "${2:-$CWD}"; is_exempt_rel    "$__r"; }
