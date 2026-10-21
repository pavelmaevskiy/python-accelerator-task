"""Pure availability rules (half-open ``[start, end)`` intervals)."""

from collections.abc import Sequence
from datetime import time
from typing import NamedTuple


class Slot(NamedTuple):
    weekday: int
    start: time
    end: time


def half_open_overlap(a: tuple[time, time], b: tuple[time, time]) -> bool:
    """``[a0, a1)`` and ``[b0, b1)`` overlap iff each starts before the other ends."""
    return a[0] < b[1] and b[0] < a[1]


def has_overlap(slots: Sequence[Slot]) -> bool:
    """True if any two slots on the same weekday overlap (adjacent slots are fine)."""
    ordered = sorted(slots)
    for prev, cur in zip(ordered, ordered[1:], strict=False):
        if prev.weekday == cur.weekday and cur.start < prev.end:
            return True
    return False
