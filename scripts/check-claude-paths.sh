#!/usr/bin/env bash
#
# check-claude-paths.sh — every path CLAUDE.md names must exist.
#
# CLAUDE.md is the first thing a session reads. A path in it that has moved or
# been deleted sends the session looking in the wrong place, which is worse
# than saying nothing. This checks them all, including `file.py:NN` line
# references.
#
#   scripts/check-claude-paths.sh
#
# Exit 0 = every path resolves. 1 = at least one does not.
#
# Paths that intentionally do not exist yet are listed in PLANNED below. The
# script also fails if a PLANNED entry HAS appeared, so the list cannot rot.

set -uo pipefail

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || {
  echo "check-claude-paths: not inside a git repository." >&2
  exit 1
}
cd "$REPO_ROOT" || exit 1

# Named in CLAUDE.md as being built. Absent on purpose, for now.
PLANNED=(
  'agent/evals/w2/baseline.json'
)

# Repo-relative prefixes a real path starts with. Anything else in backticks is
# prose ("into `cases/`"), a branch name (`feat/doc-factory`) or a URL.
TOPLEVEL='^(agent|docs|scripts|src|library|interface|apis|templates|sql|tests|public|sites|\.claude|\.github|\.gitlab-ci\.yml|composer\.json|package\.json|CLAUDE\.md|README\.md)(/|$)'

is_planned() {
  local p="$1" q
  for q in "${PLANNED[@]}"; do [ "$p" = "$q" ] && return 0; done
  return 1
}

ok=0; miss=0; planned_ok=0

check_one() {
  local f="$1" line=""
  case "$f" in
    *:[0-9]*) line="${f##*:}"; f="${f%:*}" ;;
  esac

  if is_planned "$f"; then
    if [ -e "$f" ]; then
      printf '  STALE   %s — listed as PLANNED but it exists now; remove it from PLANNED\n' "$f"
      miss=$((miss + 1))
    else
      planned_ok=$((planned_ok + 1))
    fi
    return
  fi

  if [ ! -e "$f" ]; then
    printf '  MISSING %s\n' "$f"
    miss=$((miss + 1))
    return
  fi

  if [ -n "$line" ]; then
    local total
    total=$(wc -l < "$f" | tr -d ' ')
    if [ "$line" -gt "$total" ]; then
      printf '  MISSING %s:%s — file has only %s lines\n' "$f" "$line" "$total"
      miss=$((miss + 1))
      return
    fi
  fi
  ok=$((ok + 1))
}

expand_braces() {
  local s="$1"
  if [[ $s == *"{"* ]]; then
    local pre="${s%%\{*}" rest="${s#*\{}" opts="${rest%%\}*}" post="${rest#*\}}" o
    local IFS=','
    for o in $opts; do printf '%s\n' "${pre}${o}${post}"; done
  else
    printf '%s\n' "$s"
  fi
}

while IFS= read -r token; do
  [ -n "$token" ] || continue
  while IFS= read -r cand; do
    [ -n "$cand" ] || continue
    printf '%s' "$cand" | grep -Eq "$TOPLEVEL" || continue
    check_one "$cand"
  done < <(expand_braces "$token")
done < <(grep -oE '`[A-Za-z0-9_./{},:*-]+`' CLAUDE.md | tr -d '`' | grep -Fv '*' | sort -u)

printf '\ncheck-claude-paths: %d resolved, %d planned-and-absent, %d broken\n' \
  "$ok" "$planned_ok" "$miss"

[ "$miss" -eq 0 ] || exit 1
exit 0
