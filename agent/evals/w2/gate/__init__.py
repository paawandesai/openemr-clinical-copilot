"""
The Week 2 eval gate.

One command, one exit code:

    cd agent && python -m evals.w2.gate            # 0 pass, 1 blocked, 2 error
    cd agent && python -m evals.w2.gate --det-only # deterministic tier only

The gate is five rules applied in a fixed order (`verdict.py`), over cases
loaded from `cases/` (`loader.py`) and executed by checkers registered in
`checks_*.py` (`registry.py`). Deterministic checkers are pure: no network,
no credentials, no model. The judge tier (`judge.py`) is the only part that
calls anything, and it announces itself when it is skipped.
"""
