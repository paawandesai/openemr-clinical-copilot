#!/usr/bin/env bash
#
# Secret scan for staged changes — Week 2.
#
# Two jobs:
#   1. Refuse any staged .env file (.env.example / .env.sample / .env.template
#      are fine — they hold key names, not values).
#   2. Refuse staged content that looks like a credential.
#
# Uses gitleaks when it is installed (the stronger scanner); always also runs
# the built-in pattern scan, so the check does not silently weaken on a machine
# without gitleaks. Install gitleaks with `brew install gitleaks`.
#
# Usage:
#   .claude/hooks/secret-scan.sh            # scan the git index (pre-commit)
#   .claude/hooks/secret-scan.sh --all      # scan every tracked file (audit)
#
# Exit 0 = clean, exit 1 = findings. `.git/hooks/pre-commit` calls this; see
# scripts/install-git-hooks.sh.

set -uo pipefail

MODE="${1:---staged}"
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || {
  echo "secret-scan: not inside a git repository." >&2
  exit 1
}
cd "$REPO_ROOT" || exit 1

FINDINGS=0

note_finding() {
  FINDINGS=$((FINDINGS + 1))
  printf '  %s\n' "$1" >&2
}

# ---------------------------------------------------------------------------
# Which files to look at
# ---------------------------------------------------------------------------
# macOS ships bash 3.2, which has no `mapfile`; read into an array by hand.
FILES=()
if [ "$MODE" = "--all" ]; then
  DIFF_CMD=(git diff --no-color HEAD --)
  while IFS= read -r line; do FILES+=("$line"); done < <(git ls-files)
else
  DIFF_CMD=(git diff --cached --no-color --)
  while IFS= read -r line; do FILES+=("$line"); done < <(git diff --cached --name-only --diff-filter=ACM)
fi

if [ ${#FILES[@]} -eq 0 ]; then
  echo "secret-scan: nothing staged."
  exit 0
fi

# This scanner and its docs necessarily contain credential-shaped regexes and
# example strings. Scanning them would make the hook permanently unhappy with
# itself, so they are skipped by exact path.
SELF_EXEMPT=(
  '.claude/hooks/secret-scan.sh'
  '.claude/hooks/pre-commit-guard.sh'
)

is_self_exempt() {
  local f="$1" e
  for e in "${SELF_EXEMPT[@]}"; do
    [ "$f" = "$e" ] && return 0
  done
  return 1
}

# ---------------------------------------------------------------------------
# 1. .env files
# ---------------------------------------------------------------------------
echo "secret-scan: checking for staged .env files..."
for f in "${FILES[@]}"; do
  base=$(basename "$f")
  case "$base" in
    .env.example|.env.sample|.env.template|.env.dist) continue ;;
    .env|.env.*|*.env)
      note_finding "$f — .env files are never committed. Keep it local; commit a .env.example with key names only."
      ;;
  esac
done

# ---------------------------------------------------------------------------
# 2. Credential-shaped content
# ---------------------------------------------------------------------------
echo "secret-scan: scanning staged content for credential patterns..."

# Each entry: <label>|<extended regex>
PATTERNS=(
  'Anthropic API key|sk-ant-[A-Za-z0-9_-]{8,}'
  'OpenAI API key|sk-(proj-)?[A-Za-z0-9]{20,}'
  'AWS access key id|AKIA[0-9A-Z]{16}'
  'GitHub token|gh[pousr]_[A-Za-z0-9]{20,}'
  'Slack token|xox[baprs]-[A-Za-z0-9-]{10,}'
  'Google API key|AIza[0-9A-Za-z_-]{30,}'
  'Private key block|-----BEGIN [A-Z ]*PRIVATE KEY-----'
  'JSON Web Token|eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.'
  'Assigned credential|(api[_-]?key|apikey|secret|client[_-]?secret|password|passwd|access[_-]?token|auth[_-]?token|bearer)["'"'"' ]*[:=][ ]*["'"'"']?[A-Za-z0-9/+=_-]{16,}'
)

# Lines that are obviously placeholders rather than real values.
PLACEHOLDER='(<[^>]+>|\$\{?[A-Za-z_]|your[_-]|YOUR[_-]|example|EXAMPLE|changeme|CHANGEME|placeholder|PLACEHOLDER|redacted|REDACTED|xxxx|XXXX|\.\.\.|fake|FAKE|dummy|DUMMY|test[_-]?key|TODO)'

for f in "${FILES[@]}"; do
  is_self_exempt "$f" && continue
  # Only added lines matter, and only text.
  # -E on both greps: BSD grep's BRE reads \+ as a repetition operator, so the
  # header filter must be an ERE. sed's BRE treats a bare + as a literal.
  added=$("${DIFF_CMD[@]}" "$f" 2>/dev/null | grep -E '^\+' | grep -Ev '^\+\+\+' | sed 's/^+//')
  [ -n "$added" ] || continue
  for entry in "${PATTERNS[@]}"; do
    label="${entry%%|*}"
    regex="${entry#*|}"
    while IFS= read -r line; do
      [ -n "$line" ] || continue
      printf '%s' "$line" | grep -Eq -e "$PLACEHOLDER" && continue
      trimmed=$(printf '%s' "$line" | cut -c1-120)
      note_finding "$f — $label: ${trimmed}"
    done < <(printf '%s\n' "$added" | grep -E -e "$regex" | head -5)
  done
done

# ---------------------------------------------------------------------------
# 3. gitleaks, when available
# ---------------------------------------------------------------------------
if command -v gitleaks >/dev/null 2>&1; then
  echo "secret-scan: running gitleaks..."
  if [ "$MODE" = "--all" ]; then
    gitleaks detect --no-banner --redact --exit-code 1 || FINDINGS=$((FINDINGS + 1))
  else
    gitleaks protect --staged --no-banner --redact --exit-code 1 || FINDINGS=$((FINDINGS + 1))
  fi
else
  echo "secret-scan: gitleaks not installed — pattern scan only (brew install gitleaks)."
fi

# ---------------------------------------------------------------------------
if [ "$FINDINGS" -gt 0 ]; then
  cat >&2 <<'EOF'

secret-scan: COMMIT BLOCKED — the staged changes look like they contain
credentials or a .env file.

  - Do not "fix" this by rewording the value. Remove it from the commit.
  - Unstage the file:      git restore --staged <file>
  - If the value is real and was ever pushed, rotate it. A rotated key is
    cheap; a leaked one in git history is not.
  - If this is a false positive, say so in your /report under UNVERIFIED and
    let a human decide. Do not disable the hook.

EOF
  exit 1
fi

echo "secret-scan: clean."
exit 0
