"""
The small-sample statistics the gate uses. No dependencies.

At 6-15 cases per category the normal approximation is not usable: one case
is 6.7 to 16.7 percentage points, far coarser than the 5% band the assignment
asks for, and the Wald interval's coverage collapses near p=1 — which is
exactly where a healthy category sits.
"""

from __future__ import annotations

import math

#: 95% two-sided.
Z = 1.959963984540054


def wilson_lower_bound(successes: int, n: int, z: float = Z) -> float:
    """
    The Wilson score interval's lower bound for `successes` out of `n`.

    Returns 0.0 for n == 0: no evidence is not the same as evidence of
    failure, and the caller decides what an empty category means.
    """
    if n <= 0:
        return 0.0
    if successes < 0 or successes > n:
        raise ValueError(f"{successes} successes out of {n}")
    p = successes / n
    z2 = z * z
    denominator = 1.0 + z2 / n
    centre = p + z2 / (2 * n)
    half = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    return max(0.0, (centre - half) / denominator)


def all_pass_lower_bound(n: int, z: float = Z) -> float:
    """
    The Wilson lower bound a category scoring 100% on `n` cases would have:
    n / (n + z^2). This is the gate's floor, less the category's slack.

    Making the floor a function of n rather than a committed constant is what
    keeps it meaningful as pending cases activate — at n=4 an all-pass bound
    is 0.510 and at n=10 it is 0.723, and no single number is right for both.
    """
    if n <= 0:
        return 0.0
    return n / (n + z * z)
