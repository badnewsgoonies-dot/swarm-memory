#!/usr/bin/env python3
"""Temporal decay scoring for memory retrieval."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import exp
from typing import Union


def _as_datetime(value: Union[datetime, float, int]) -> datetime:
    """Normalize various timestamp inputs to an aware UTC datetime."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    raise TypeError(f"Unsupported timestamp type: {type(value)!r}")


def temporal_decay_score(
    timestamp: Union[datetime, float, int],
    now: datetime | None = None,
    tau_days: float = 7.0,
) -> float:
    """
    Compute a time-decay score using query-time decay.

    Score = exp(-age_days / tau_days), clamped so future timestamps return 1.0.
    """
    ts = _as_datetime(timestamp)
    current = _as_datetime(now or datetime.now(tz=timezone.utc))

    age_days = (current - ts).total_seconds() / 86_400.0
    if age_days < 0:
        age_days = 0.0

    return float(exp(-age_days / tau_days))


if __name__ == "__main__":
    example = datetime.now(tz=timezone.utc) - timedelta(days=3)
    print(f"Decay score (3 days old, tau=7): {temporal_decay_score(example):.4f}")
