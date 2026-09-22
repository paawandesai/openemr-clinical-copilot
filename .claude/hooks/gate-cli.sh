#!/usr/bin/env bash
#
# Gate query interface — Week 2.
#
# The read-only way to ask what the gate protects. Split out of gate-guard.sh
# so that the hook itself takes no arguments at all: a hook that accepts argv
# is a hook whose behaviour can be steered by whatever reaches its command
# line, and a gate should have exactly one way in — hook input JSON on stdin.
#
#   --print-globs            one protected glob per line
#   --print-exceptions       one unprotected-override glob per line
#   --print-home-protected   one absolute out-of-repo gate file per line
#   --check-path <path>...   print each protected path; exit 1 if any
#   --canonical <path>...    show what each path resolves to
#
# Callers: .claude/hooks/gate-effect-check.sh, scripts/gate-diff.sh.
# The glob lists live in gate-lib.sh; this file defines none of its own.
#
# This file is itself a gate file (.claude/hooks/* is protected).

set -uo pipefail

_INTENTIONAL=0
gate_exit() { _INTENTIONAL=1; exit "$1"; }

gate_fail() {
  printf 'gate-cli: %s\ngate-cli: failing closed.\n' "$1" >&2
  gate_exit 2
}

_on_exit() {
  local rc=$?
  [ "$_INTENTIONAL" = "1" ] && exit "$rc"
  printf 'gate-cli: unexpected internal error (status %s); failing closed.\n' "$rc" >&2
  exit 2
}
trap _on_exit EXIT

CWD="$PWD"
REPO_ROOT=$(git -C "$CWD" rev-parse --show-toplevel 2>/dev/null || printf '%s' "$CWD")
REPO_ROOT=$(cd -P -- "$REPO_ROOT" 2>/dev/null && pwd -P || printf '%s' "$REPO_ROOT")

_LIB="$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/gate-lib.sh"
[ -r "$_LIB" ] || gate_fail "gate-lib.sh not found or unreadable at $_LIB"
# shellcheck source=/dev/null
. "$_LIB" || gate_fail "could not source gate-lib.sh"

case "${1:-}" in
  --print-globs)
    printf '%s\n' "${PROTECTED_GLOBS[@]}"
    gate_exit 0
    ;;
  --print-exceptions)
    printf '%s\n' "${UNPROTECTED_GLOBS[@]}"
    gate_exit 0
    ;;
  --print-home-protected)
    printf '%s\n' "${HOME_PROTECTED[@]}"
    gate_exit 0
    ;;
  --check-path)
    shift
    [ "$#" -gt 0 ] || gate_fail "--check-path needs at least one path"
    hits=0
    for raw in "$@"; do
      if is_protected "$raw"; then
        printf '%s\n' "$raw"
        hits=1
      fi
    done
    gate_exit "$hits"
    ;;
  --canonical)
    shift
    [ "$#" -gt 0 ] || gate_fail "--canonical needs at least one path"
    for raw in "$@"; do
      resolve_or_fail rel "$raw"
      printf '%s\t%s\n' "$raw" "$rel"
    done
    gate_exit 0
    ;;
  *)
    gate_fail "unknown mode \"${1:-}\" — see the header of this file"
    ;;
esac
