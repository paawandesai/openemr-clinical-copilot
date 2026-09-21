#!/usr/bin/env bash
#
# gate-diff.sh [branch] — pre-merge check.
#
# Lists every file a branch changes that is an eval-gate file, and exits
# nonzero if there are any. Run this before every merge: a branch that moves
# the measurement has to be reviewed as a gate change, not waved through as
# implementation work.
#
#   scripts/gate-diff.sh                 # the current branch vs main
#   scripts/gate-diff.sh feat/intake     # a named branch vs main
#   BASE=release scripts/gate-diff.sh …  # compare against a different base
#
# The protected list is NOT duplicated here. It is read from
# .claude/hooks/gate-guard.sh via --check-path, so the merge check and the
# edit-time hook can never drift apart.
#
# Exit codes: 0 = no gate files touched. 1 = gate files touched (details on
# stdout). 2 = usage or environment error.

set -uo pipefail

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || {
  echo "gate-diff: not inside a git repository." >&2
  exit 2
}
cd "$REPO_ROOT" || exit 2

GUARD="$REPO_ROOT/.claude/hooks/gate-guard.sh"
if [ ! -x "$GUARD" ]; then
  echo "gate-diff: $GUARD not found or not executable." >&2
  exit 2
fi

BASE="${BASE:-main}"
BRANCH="${1:-$(git rev-parse --abbrev-ref HEAD)}"

git rev-parse --verify --quiet "$BASE" >/dev/null || {
  echo "gate-diff: base ref '$BASE' does not exist." >&2
  exit 2
}
git rev-parse --verify --quiet "$BRANCH" >/dev/null || {
  echo "gate-diff: branch '$BRANCH' does not exist." >&2
  exit 2
}

echo "gate-diff: $BRANCH vs $BASE (merge-base: $(git merge-base "$BASE" "$BRANCH" | cut -c1-8))"

# bash 3.2 has no mapfile.
CHANGED=()
while IFS= read -r f; do
  [ -n "$f" ] && CHANGED+=("$f")
done < <(git diff --name-only "$BASE...$BRANCH")

if [ ${#CHANGED[@]} -eq 0 ]; then
  echo "gate-diff: no files changed on this branch."
  exit 0
fi

echo "gate-diff: ${#CHANGED[@]} file(s) changed; checking against the gate globs..."

HITS=$("$GUARD" --check-path "${CHANGED[@]}")
STATUS=$?

if [ "$STATUS" -eq 0 ]; then
  echo "gate-diff: PASS — no eval-gate files touched."
  exit 0
fi

cat <<EOF

gate-diff: FAIL — this branch changes eval-gate files:

$(printf '%s\n' "$HITS" | sed 's/^/  /')

These are rubrics, eval cases, the baseline, judge config, CI config, the gate
hook, or the settings that wire it up. A branch that changes them is changing
the measurement, not the thing being measured.

Before merging:
  - Confirm the change was made deliberately, in a W2_GATE_SESSION=1 session.
  - Review it as a gate change on its own, separately from the code.
  - If it was not deliberate, revert those paths:
      git checkout $BASE -- <path>

Generated cases belong in agent/evals/w2/staging/ (not gated). Promotion into
agent/evals/w2/cases/ is what this check is here to surface.
EOF

exit 1
