"""Utility for parsing simple human-readable durations."""

from __future__ import annotations

import re
from typing import Final

_UNIT_TO_SECONDS: Final[dict[str, int]] = {
    "w": 7 * 24 * 60 * 60,
    "d": 24 * 60 * 60,
    "h": 60 * 60,
    "m": 60,
    "s": 1,
}
_DURATION_PATTERN: Final[re.Pattern[str]] = re.compile(r"(?P<value>\d+)(?P<unit>[wdhms])")


def parse_duration(duration: str) -> int:
    """Return the total seconds represented by a compact duration string.

    Examples:
        >>> parse_duration("2h30m")
        9000
        >>> parse_duration("1d")
        86400
        >>> parse_duration("1w2d")
        777600
    """
    cleaned = duration.strip()
    if not cleaned:
        return 0

    total_seconds = 0
    cursor = 0

    for match in _DURATION_PATTERN.finditer(cleaned):
        if match.start() != cursor:
            raise ValueError(f"Invalid duration format: {duration!r}")

        value = int(match.group("value"))
        unit = match.group("unit")
        total_seconds += value * _UNIT_TO_SECONDS[unit]
        cursor = match.end()

    if cursor != len(cleaned):
        raise ValueError(f"Invalid duration format: {duration!r}")

    return total_seconds
