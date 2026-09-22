"""``python -m evals.w2.factory`` entry point."""

from __future__ import annotations

import sys

from evals.w2.factory.cli import main

if __name__ == "__main__":
    sys.exit(main())
