#!/usr/bin/env bash
#
# new-worktree.sh <branch> <work-order-file> [worktree-path]
#
# Starts a Week 2 work order in its own git worktree:
#   - creates the branch (from main, or $NEW_WORKTREE_BASE) and the worktree
#   - copies the local .env files across, which are never committed
#   - copies the work order in as WORK-ORDER.md, excluded from git
#   - installs the secret-scan pre-commit hook
#   - creates agent/.venv on python3.12 and installs the agent requirements
#   - prints the command to start the session
#
# Refuses to run if any .env is tracked by git — if that is ever true, the
# secret is already in history and copying more around is the wrong next move.
#
#   scripts/new-worktree.sh spike/writeback docs/w2/work-orders/00-writeback-spike.md
#   scripts/new-worktree.sh --dry-run spike/writeback docs/w2/work-orders/00-writeback-spike.md
#
# Note: this repo is an OpenEMR fork, and docs/openemr-dev-guide.md says to
# drive worktrees through `openemr-cmd worktree` because it owns compose
# overrides and port offsets. That applies to worktrees that need an OpenEMR
# container. Week 2 agent work does not, openemr-cmd is not installed here, and
# every existing worktree in this checkout was made with plain git — so this
# script uses plain git and says so out loud. If your work order needs an
# OpenEMR stack, use `openemr-cmd worktree add` instead of this script.

set -euo pipefail

DRY_RUN=0
if [ "${1:-}" = "--dry-run" ] || [ "${1:-}" = "-n" ]; then
  DRY_RUN=1
  shift
fi

BRANCH="${1:-}"
ORDER="${2:-}"
TARGET="${3:-}"

if [ -z "$BRANCH" ] || [ -z "$ORDER" ]; then
  cat >&2 <<'EOF'
usage: scripts/new-worktree.sh [--dry-run] <branch> <work-order-file> [worktree-path]

  <branch>            e.g. spike/writeback, feat/intake-extractor
  <work-order-file>   path to the work order markdown, e.g.
                      docs/w2/work-orders/00-writeback-spike.md
  [worktree-path]     defaults to a sibling of the repo, ../cc-<slug>

env:
  NEW_WORKTREE_BASE     base ref for the new branch (default: main)
  NEW_WORKTREE_NO_VENV  set to 1 to skip agent/.venv and the pip install
EOF
  exit 2
fi

REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT"

BASE="${NEW_WORKTREE_BASE:-main}"

run() {
  if [ "$DRY_RUN" -eq 1 ]; then
    printf '  [dry-run] %s\n' "$*"
  else
    "$@"
  fi
}

say() { printf '%s\n' "$*"; }

# --- refuse if any .env is tracked -------------------------------------------
TRACKED_ENV=$(git ls-files | grep -E '(^|/)\.env(\..*)?$' | grep -vE '\.(example|sample|template|dist)$' || true)
if [ -n "$TRACKED_ENV" ]; then
  cat >&2 <<EOF
new-worktree: REFUSING — these .env files are tracked by git:

$TRACKED_ENV

A tracked .env means real credentials are in the repository's history, and
copying them into another working tree makes that worse. Fix it first:

  git rm --cached <file>
  echo '.env' >> .gitignore
  # then rotate every credential that file ever held

EOF
  exit 1
fi

# --- resolve paths ------------------------------------------------------------
if [ ! -f "$ORDER" ]; then
  say "new-worktree: work order not found: $ORDER" >&2
  exit 1
fi
ORDER_ABS=$(cd "$(dirname "$ORDER")" && pwd)/$(basename "$ORDER")

SLUG=$(printf '%s' "$BRANCH" | tr '/' '-' | tr -cd '[:alnum:]._-')
if [ -z "$TARGET" ]; then
  TARGET="$(dirname "$REPO_ROOT")/cc-$SLUG"
fi

if [ -e "$TARGET" ]; then
  say "new-worktree: target already exists: $TARGET" >&2
  exit 1
fi

if git show-ref --verify --quiet "refs/heads/$BRANCH"; then
  say "new-worktree: branch '$BRANCH' already exists; checking it out into the new worktree."
  ADD_ARGS=("$TARGET" "$BRANCH")
else
  git rev-parse --verify --quiet "$BASE" >/dev/null || {
    say "new-worktree: base ref '$BASE' does not exist." >&2
    exit 1
  }
  ADD_ARGS=(-b "$BRANCH" "$TARGET" "$BASE")
fi

say "new-worktree: branch   $BRANCH  (base: $BASE)"
say "new-worktree: worktree $TARGET"
say "new-worktree: order    $ORDER"
say ""

# --- create -------------------------------------------------------------------
run git worktree add "${ADD_ARGS[@]}"

# --- copy .env files (never committed) ----------------------------------------
# .env is gitignored, so a worktree never receives one from git. Look in this
# checkout first, then fall back to the main worktree — which, when this script
# is run from a worktree, is usually the only place a real .env exists.
MAIN_WT=$(git worktree list --porcelain | awk '/^worktree /{print substr($0,10); exit}')

say "new-worktree: copying local .env files (not committed, not in git)..."
ENV_FOUND=0
SEEN=""
for root in "$REPO_ROOT" "$MAIN_WT"; do
  [ -n "$root" ] && [ -d "$root" ] || continue
  while IFS= read -r envfile; do
    rel="${envfile#"$root"/}"
    case " $SEEN " in *" $rel "*) continue ;; esac
    SEEN="$SEEN $rel"
    ENV_FOUND=1
    run mkdir -p "$TARGET/$(dirname "$rel")"
    run cp "$envfile" "$TARGET/$rel"
    say "  $rel  (from ${root##*/})"
  done < <(find "$root" -maxdepth 3 -name '.env' -not -path '*/node_modules/*' -not -path '*/.git/*' -not -path '*/vendor/*' 2>/dev/null)
done

if [ "$ENV_FOUND" -eq 0 ]; then
  say "  (none found — the new worktree will need its own credentials)"
fi

# --- copy the work order in ---------------------------------------------------
run cp "$ORDER_ABS" "$TARGET/WORK-ORDER.md"
say "new-worktree: work order copied to $TARGET/WORK-ORDER.md"

# Keep the copy out of git: it is a convenience, not a second source of truth.
if [ "$DRY_RUN" -eq 1 ]; then
  say "  [dry-run] add WORK-ORDER.md to the worktree's .git/info/exclude"
else
  EXCLUDE=$(git -C "$TARGET" rev-parse --git-path info/exclude)
  mkdir -p "$(dirname "$EXCLUDE")"
  grep -qxF 'WORK-ORDER.md' "$EXCLUDE" 2>/dev/null || echo 'WORK-ORDER.md' >> "$EXCLUDE"
fi

# --- hooks --------------------------------------------------------------------
if [ "$DRY_RUN" -eq 1 ]; then
  say "  [dry-run] install the secret-scan pre-commit hook in the new worktree"
else
  (cd "$TARGET" && ./scripts/install-git-hooks.sh) || \
    say "new-worktree: WARNING — could not install the pre-commit hook; see the message above."
fi

# --- python env ---------------------------------------------------------------
# agent/.venv is gitignored, so a new worktree starts without one and
# `cd agent && python -m pytest tests` fails at *collection* — pymupdf and the
# rest of requirements.txt are simply not installed. Build it here instead, so
# the first thing a session runs is not a stale-venv error.
#
# 3.12 on purpose: the service image is python:3.12-slim and ruff's
# target-version is py312. A 3.9 `python3` cannot even import the app.
#
# Skip with NEW_WORKTREE_NO_VENV=1 — a worktree that will not run python, or a
# machine where the install is not wanted.
VENV_DIR="$TARGET/agent/.venv"

if [ "${NEW_WORKTREE_NO_VENV:-}" = "1" ]; then
  say ""
  say "new-worktree: python env SKIPPED (NEW_WORKTREE_NO_VENV=1)."
else
  PYBIN=""
  for cand in python3.12 python3 python; do
    command -v "$cand" >/dev/null 2>&1 || continue
    if "$cand" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, 12) else 1)' \
         >/dev/null 2>&1; then
      PYBIN="$cand"
      break
    fi
  done

  say ""
  if [ -z "$PYBIN" ]; then
    say "new-worktree: WARNING — no python >= 3.12 on PATH; agent/.venv not created."
    say "              install python3.12, then in the new worktree run:"
    say "                cd agent && python3.12 -m venv .venv \\"
    say "                  && ./.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt"
  else
    PYVER=$("$PYBIN" -c 'import platform; print(platform.python_version())' 2>/dev/null || echo unknown)
    say "new-worktree: python env agent/.venv  ($PYBIN, $PYVER)"

    if [ "$DRY_RUN" -eq 1 ] || [ ! -d "$VENV_DIR" ]; then
      run "$PYBIN" -m venv "$VENV_DIR"
    else
      say "  agent/.venv exists already; reusing it"
    fi

    if [ "$DRY_RUN" -eq 1 ]; then
      run "$VENV_DIR/bin/python" -m pip install -q \
        -r "$TARGET/agent/requirements.txt" -r "$TARGET/agent/requirements-dev.txt"
    else
      say "  installing requirements.txt + requirements-dev.txt (quiet, ~1 min)..."
      PIP_LOG=$(mktemp "${TMPDIR:-/tmp}/new-worktree-pip.XXXXXX")
      if "$VENV_DIR/bin/python" -m pip install -q --disable-pip-version-check \
           -r "$TARGET/agent/requirements.txt" \
           -r "$TARGET/agent/requirements-dev.txt" >"$PIP_LOG" 2>&1; then
        say "  done — \`cd agent && python -m pytest tests\` will collect"
        rm -f "$PIP_LOG"
      else
        say "new-worktree: WARNING — pip install failed; agent/.venv is incomplete."
        say "              last 15 lines of $PIP_LOG:"
        tail -15 "$PIP_LOG" | sed 's/^/                /'
        say "              finish it by hand:"
        say "                cd \"$TARGET/agent\" && ./.venv/bin/python -m pip install \\"
        say "                  -r requirements.txt -r requirements-dev.txt"
      fi
    fi
  fi
fi

# --- how to start -------------------------------------------------------------
cat <<EOF

new-worktree: done.

Start the session with:

  cd "$TARGET" && claude

Read WORK-ORDER.md first, then CLAUDE.md and docs/w2/DECISIONS.md.
End with /report. Do not merge.

The eval gate is read-only in a normal session. A human who genuinely needs to
change a rubric, the baseline, judge config or CI starts the session as:

  cd "$TARGET" && W2_GATE_SESSION=1 claude

EOF
