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
# meta-check set). CI runs those. The hook prints which cases it did not score
# rather than letting a green local run read as a full one.
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
cat > "$HOOK" <<EOF
#!/usr/bin/env bash
$MARKER
set -euo pipefail

top=\$(git rev-parse --show-toplevel)
if [ ! -d "\$top/agent/evals/w2/gate" ]; then
  echo "pre-push: no Week 2 eval gate on this branch; SKIPPED." >&2
  exit 0
fi

python=\${W2_GATE_PYTHON:-python3}
if ! command -v "\$python" >/dev/null 2>&1; then
  echo "pre-push: \$python not found; the gate was NOT run." >&2
  echo "pre-push: set W2_GATE_PYTHON to your interpreter, or push with --no-verify." >&2
  exit 1
fi

echo "pre-push: running the Week 2 eval gate (deterministic tier)..."
cd "\$top/agent"
if "\$python" -m evals.w2.gate --det-only; then
  echo "pre-push: deterministic tier passed. The judge tier was NOT run here — CI runs it."
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
echo "install-w2-pre-push-hook: note — .git/hooks is shared by every worktree of $REPO_ROOT"
echo "install-w2-pre-push-hook: interpreter is \$W2_GATE_PYTHON, default python3"

if [ "${1:-}" = "--verify" ]; then
  echo
  echo "install-w2-pre-push-hook: running it now..."
  "$HOOK" </dev/null
fi
