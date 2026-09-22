#!/usr/bin/env bash
#
# Install the Week 2 git hooks into this checkout (or worktree).
#
#   scripts/install-git-hooks.sh
#
# Installs a pre-commit hook that runs .claude/hooks/secret-scan.sh. Refuses to
# clobber a pre-commit hook it did not write — this repo also ships OpenEMR's
# .pre-commit-config.yaml, managed by `prek install`, and silently replacing it
# would turn off phpstan, phpcs and codespell without telling anyone.

set -euo pipefail

REPO_ROOT=$(git rev-parse --show-toplevel)
HOOK_DIR=$(git rev-parse --git-path hooks)
HOOK="$HOOK_DIR/pre-commit"
MARKER="# w2-secret-scan"

mkdir -p "$HOOK_DIR"

if [ -e "$HOOK" ] && ! grep -q "$MARKER" "$HOOK" 2>/dev/null; then
  cat >&2 <<EOF
install-git-hooks: $HOOK already exists and is not ours.

That is very likely prek/pre-commit's hook for OpenEMR's
.pre-commit-config.yaml. Not replacing it. Two ways forward:

  1. Add the secret scan to .pre-commit-config.yaml as a local hook, so both
     run under one manager; or
  2. Back up the existing hook, re-run this script, and chain the old one from
     the new hook.

Nothing was changed.
EOF
  exit 1
fi

# Git shares .git/hooks across every worktree of a repository, so this installs
# once for all of them. The hook therefore must NOT hard-code the path of the
# worktree it was installed from — that directory may be removed later. It
# resolves the scanner from whichever worktree is committing.
cat > "$HOOK" <<EOF
#!/usr/bin/env bash
$MARKER
set -e
top=\$(git rev-parse --show-toplevel)
scan="\$top/.claude/hooks/secret-scan.sh"
if [ ! -x "\$scan" ]; then
  echo "pre-commit: \$scan not found on this branch; secret scan SKIPPED." >&2
  exit 0
fi
exec "\$scan" --staged
EOF

chmod +x "$HOOK"
echo "install-git-hooks: installed $HOOK"
echo "install-git-hooks: note — .git/hooks is shared by every worktree of $REPO_ROOT"
echo "install-git-hooks: test it with  scripts/install-git-hooks.sh --verify"

if [ "${1:-}" = "--verify" ]; then
  "$REPO_ROOT/.claude/hooks/secret-scan.sh" --staged
fi
