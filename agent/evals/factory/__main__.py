"""``python -m evals.factory`` entry point."""

from __future__ import annotations

import sys

from evals.factory.cli import main

if __name__ == "__main__":
    sys.exit(main())
