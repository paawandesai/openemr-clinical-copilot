"""
THROWAWAY — work order 28, item 1. Proves agent-unit-tests turns a pipeline
red. Never merge this branch.
"""


def test_this_test_fails_on_purpose() -> None:
    assert 1 == 2, "deliberate failure: proves the agent-unit-tests job blocks"
