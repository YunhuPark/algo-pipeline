from __future__ import annotations

import math


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """Return a bounded Wilson score interval for a binomial rate."""

    if total < 0 or successes < 0 or successes > total:
        raise ValueError("invalid binomial counts")
    if total == 0:
        return (0.0, 1.0)

    rate = successes / total
    denominator = 1.0 + (z * z / total)
    center = (rate + (z * z / (2.0 * total))) / denominator
    margin = (
        z
        * math.sqrt(
            (rate * (1.0 - rate) / total)
            + (z * z / (4.0 * total * total))
        )
        / denominator
    )
    return (max(0.0, center - margin), min(1.0, center + margin))

