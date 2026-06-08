from __future__ import annotations

import random
from collections.abc import Sequence
from typing import TypeVar

T = TypeVar("T")


def weighted_choice(rng: random.Random, items: Sequence[T], weights: Sequence[float]) -> T:
    if not items:
        raise ValueError("weighted_choice requires at least one item")
    total = sum(max(0.0, weight) for weight in weights)
    if total <= 0:
        return rng.choice(list(items))
    threshold = rng.uniform(0, total)
    running = 0.0
    for item, weight in zip(items, weights, strict=True):
        running += max(0.0, weight)
        if running >= threshold:
            return item
    return items[-1]


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))

