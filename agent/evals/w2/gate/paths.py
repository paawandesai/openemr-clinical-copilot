"""Where the gate's files are, resolved once from this module's location."""

from __future__ import annotations

from pathlib import Path

#: agent/evals/w2/
W2_ROOT = Path(__file__).resolve().parent.parent
#: the repository root
REPO_ROOT = W2_ROOT.parent.parent.parent

CASES_DIR = W2_ROOT / "cases"
RUBRICS_DIR = W2_ROOT / "rubrics"
FIXTURES_DIR = W2_ROOT / "fixtures"
FACTORY_SAMPLES_DIR = W2_ROOT / "factory" / "output" / "samples"
JUDGE_PROMPTS_DIR = W2_ROOT / "judge_prompts"

CASE_SCHEMA = W2_ROOT / "case_schema.json"
RUBRICS_FILE = RUBRICS_DIR / "rubrics.json"
CATEGORIES_FILE = RUBRICS_DIR / "categories.json"
JUDGE_CONFIG = W2_ROOT / "judge_config.json"
BASELINE = W2_ROOT / "baseline.json"
#: The pinned hash of every prompt the system or the judge sends (W2-L1).
PROMPT_PINS = W2_ROOT / "prompt_pins.json"
JUDGE_CACHE = W2_ROOT / ".judge_cache"

#: The published wire schemas (D22), checked by W2-A6.
PUBLISHED_SCHEMAS_DIR = REPO_ROOT / "docs" / "w2" / "schemas"
