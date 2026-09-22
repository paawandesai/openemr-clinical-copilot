WORK ORDER 05: formatting baseline + housekeeping. Branch
chore/ruff-baseline (based on chore/w2-scaffolding). Do not merge.

STEP 0: prove the gate is live. Attempt a Write to
agent/evals/w2/cases/probe.json. It must be BLOCKED. If not, STOP.

1. Formatting baseline for Week 1 code, excluding eval-gate files:
   - In agent/pyproject.toml, add an extend-exclude for the Week 1 eval
     files under agent/evals/ (list the actual files; do NOT exclude
     agent/evals/w2/). Comment in the file explaining that they are
     eval-gate files and are formatted only in a W2_GATE_SESSION.
   - Run ruff format on agent/, then ruff check --fix (safe fixes ONLY,
     never --unsafe-fixes). Do not change UP042 (str,Enum -> StrEnum):
     it alters str()/format() output, which may change serialized values.
   - Before committing, run scripts/gate-diff.sh on this branch and
     confirm no file under agent/evals/ was touched.
   - One formatting-only commit. Add its hash to .git-blame-ignore-revs
     in a second commit.

2. Verification:
   - Full unit suite: must match the pre-format pass count (368).
   - Smoke run of the Week 1 live evals: pick 5 cases spanning the
     categories, run them, and report pass/fail against their last known
     results. Report cost.
   - List remaining ruff findings by rule (unfixed, left for later).

3. Housekeeping:
   - Delete the untracked spike doc in the MAIN worktree
     (find it via git worktree list): docs/w2/spikes/writeback.md.
     Before deleting, verify it is byte-identical to the committed copy
     on chore/w2-scaffolding (sha256). If not identical, stop and report.
   - Install gitleaks (brew install gitleaks). Then run the secret-scan
     hook against a planted fake secret to show it now runs in gitleaks
     mode; remove the fake afterwards.

/report. STOP. Do not merge.