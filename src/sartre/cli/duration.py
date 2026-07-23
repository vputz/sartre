"""A strict, dependency-free duration parser for ``--keep-within`` / ``--grace``.

Accepts ``<int><unit>`` terms with units ``s/m/h/d/w`` (seconds…weeks), optionally
concatenated (``7d12h``, ``90m``). Anything else — bare numbers, fractions, unknown units,
empty — is a clear error. ``parse_duration(format_duration(td)) == td`` for whole-second
non-negative durations.
"""

from __future__ import annotations

import re
from datetime import timedelta

from sartre.cli.errors import CliError

_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
_TERM = re.compile(r"(\d+)([smhdw])")


def parse_duration(text: str) -> timedelta:
    """Parse ``"7d12h"`` → ``timedelta``. Raises :class:`CliError` on anything malformed."""
    stripped = text.strip()
    if not stripped or not re.fullmatch(r"(\d+[smhdw])+", stripped):
        raise CliError(
            f"invalid duration {text!r}: use integer terms with units s/m/h/d/w"
            " (e.g. '30d', '90m', '7d12h')"
        )
    seconds = sum(int(n) * _UNIT_SECONDS[u] for n, u in _TERM.findall(stripped))
    return timedelta(seconds=seconds)


def format_duration(td: timedelta) -> str:
    """Render a whole-second, non-negative duration back to the terse form (inverse-ish)."""
    seconds = int(td.total_seconds())
    if seconds < 0:
        raise CliError("cannot format a negative duration")
    if seconds == 0:
        return "0s"
    parts: list[str] = []
    for unit in ("w", "d", "h", "m", "s"):
        size = _UNIT_SECONDS[unit]
        if seconds >= size:
            parts.append(f"{seconds // size}{unit}")
            seconds %= size
    return "".join(parts)
