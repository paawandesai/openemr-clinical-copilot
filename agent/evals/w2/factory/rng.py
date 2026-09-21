"""
Seed derivation.

Every random choice in the factory comes from a stream derived from
``(scenario seed, purpose)`` — never from the global ``random`` module and
never from clock or process state. Two runs of the same spec therefore make
the same choices, which is what makes the ground truth reproducible.
"""

from __future__ import annotations

import hashlib
import random


def derive_seed(seed: int, *purpose: str) -> int:
    """A stable 64-bit seed for one named stream of one scenario."""
    payload = str(seed).encode("utf-8")
    for part in purpose:
        payload += b"\x00" + part.encode("utf-8")
    return int.from_bytes(hashlib.blake2b(payload, digest_size=8).digest(), "big")


def stream(seed: int, *purpose: str) -> random.Random:
    """An independent ``random.Random`` for one named stream of one scenario."""
    return random.Random(derive_seed(seed, *purpose))
