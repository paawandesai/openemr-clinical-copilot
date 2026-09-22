#!/usr/bin/env bash
#
# Install a pre-push hook that runs the Week 2 eval gate's DETERMINISTIC tier
# before anything leaves this machine.
#
#   scripts/install-w2-pre-push-hook.sh            # install
#   scripts/install-w2-pre-push-hook.sh --verify   # install, then run it now
#   scripts/install-w2-pre-push-hook.sh --remove   # uninstall
#
# Deterministic tier only, on purpose. It needs no credential, makes no model
# call, costs nothing and runs in about a second, so it is a hook people keep.
# A pre-push hook that spends money and takes a minute is a hook people learn
# to push past with --no-verify, and then it is worse than nothing because it
# looks like coverage.
#
# What it therefore does NOT cover: the judge tier (categories with a
# factually_consistent or safe_refusal assertion, which today means the judge
# meta-check set, all of it critical). CI runs those.
#
# The gate exits 3 for a run that scored no critical case, and 3 is nonzero, so
# the hook passes --allow-incomplete to map it to 0. That flag lives HERE, at
# the call site, and not inside the gate: this hook is the one caller entitled
# to say "I am not claiming to be evidence", and anyone reading the hook can
# see it claim that. CI never passes it, so a CI run with no credential fails.
# The gate still prints NOT EVIDENCE with the unscored case ids either way.
#
# This is a companion to scripts/install-git-hooks.sh (the pre-commit secret
# scan), kept separate because that script's refusal-to-clobber logic is about
# prek's pre-commit hook and has nothing to say about pre-push.
#
# Bypass, when you genuinely need to:  git push --no-verify

set -euo pipefail

REPO_ROOT=$(git rev-parse --show-toplevel)
HOOK_DIR=$(git rev-parse --git-path hooks)
HOOK="$HOOK_DIR/pre-push"
MARKER="# w2-eval-gate-pre-push"

if [ "${1:-}" = "--remove" ]; then
  if [ -e "$HOOK" ] && grep -q "$MARKER" "$HOOK" 2>/dev/null; then
    rm -f "$HOOK"
    echo "install-w2-pre-push-hook: removed $HOOK"
  else
    echo "install-w2-pre-push-hook: nothing of ours at $HOOK; left alone"
  fi
  exit 0
fi

mkdir -p "$HOOK_DIR"

# A zero-byte hook is not somebody else's hook, it is debris — most likely a
# heredoc that hung and left a truncated file behind (which is exactly how
# this arm came to be written). Clear it rather than refusing forever.
if [ -e "$HOOK" ] && [ ! -s "$HOOK" ]; then
  echo "install-w2-pre-push-hook: $HOOK is empty; replacing it."
  rm -f "$HOOK"
fi

if [ -e "$HOOK" ] && ! grep -q "$MARKER" "$HOOK" 2>/dev/null; then
  cat >&2 <<EOF
install-w2-pre-push-hook: $HOOK already exists and is not ours.

Not replacing it — a pre-push hook somebody else installed is doing something,
and silently dropping it would turn that off without telling anyone. Back it
up, chain it from the new hook, and re-run this script.

Nothing was changed.
EOF
  exit 1
fi

# .git/hooks is shared by every worktree of a repository, so this installs once
# for all of them. The hook must not hard-code the worktree it was installed
# from: that directory may be deleted later. It resolves everything from
# whichever worktree is pushing, and skips itself on a branch that predates the
# gate rather than failing the push.
# NOTE on the heredoc below: it is UNQUOTED, because $MARKER is interpolated
# into it at install time. That means a backtick inside it is a command
# substitution that runs while the hook is being written — `python3` with no
# arguments opens an interactive REPL and the installer hangs with a
# zero-byte hook already truncated into place. Escape every backtick in the
# hook body, and every $ that the HOOK should expand rather than the
# installer.
cat > "$HOOK" <<EOF
#!/usr/bin/env bash
$MARKER
set -euo pipefail

top=\$(git rev-parse --show-toplevel)
if [ ! -d "\$top/agent/evals/w2/gate" ]; then
  echo "pre-push: no Week 2 eval gate on this branch; SKIPPED." >&2
  exit 0
fi

# scripts/new-worktree.sh builds agent/.venv on python 3.12 and installs the
# requirements, so prefer it. A bare \`python3\` is often the system 3.9, which
# cannot import the app at all — finding it and failing on an import error
# would read as a gate failure rather than as a missing interpreter.
python=\${W2_GATE_PYTHON:-}
if [ -z "\$python" ] && [ -x "\$top/agent/.venv/bin/python" ]; then
  python="\$top/agent/.venv/bin/python"
fi
python=\${python:-python3}
if ! command -v "\$python" >/dev/null 2>&1; then
  echo "pre-push: \$python not found; the gate was NOT run." >&2
  echo "pre-push: run scripts/new-worktree.sh, set W2_GATE_PYTHON, or push --no-verify." >&2
  exit 1
fi

echo "pre-push: running the Week 2 eval gate (deterministic tier)..."
cd "\$top/agent"
if "\$python" -m evals.w2.gate --det-only --allow-incomplete; then
  echo "pre-push: deterministic tier passed. The judge tier was NOT run here — CI runs it,"
  echo "pre-push: and this run is not evidence that the category-M cases hold."
  exit 0
fi
cat >&2 <<'MSG'

pre-push: the Week 2 eval gate BLOCKED. The push was stopped.

This is the deterministic tier: every failure above is exact and reproducible,
not a flake. Read the "guards against" line on the failing case — it says what
the failure would have cost in production.

If the case is wrong rather than the code, say so in your session report. Do
not edit it: cases, rubrics, the baseline and the judge config are gate files
and only a W2_GATE_SESSION=1 session may change them (CLAUDE.md invariant 7).

To push anyway:  git push --no-verify
MSG
exit 1
EOF

chmod +x "$HOOK"
echo "install-w2-pre-push-hook: installed $HOOK"
# $REPO_ROOT is the CURRENT worktree, not the repository the hooks belong to —
# naming it here read as "shared by every worktree of this worktree". Name the
# directory itself, which is unambiguous from wherever the installer was run.
echo "install-w2-pre-push-hook: note — this hooks directory is shared by every worktree:"
echo "install-w2-pre-push-hook:   $HOOK_DIR"
echo "install-w2-pre-push-hook: interpreter is \$W2_GATE_PYTHON, default python3"

if [ "${1:-}" = "--verify" ]; then
  echo
  echo "install-w2-pre-push-hook: running it now..."
  "$HOOK" </dev/null
fi
