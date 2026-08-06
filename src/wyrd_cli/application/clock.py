"""Injectable UTC clock boundary."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from wyrd_cli.domain.models import validate_utc_second


class Clock(Protocol):
    """Return a timezone-aware UTC instant with second precision."""

    def now(self) -> datetime:
        """Return one canonical timestamp for an application mutation."""
        ...


class SystemClock:
    """Production clock; domain and services do not call ``datetime.now``."""

    def now(self) -> datetime:
        return datetime.now(UTC).replace(microsecond=0)


def read_clock(clock: Clock) -> datetime:
    """Validate a clock implementation at its application boundary."""

    return validate_utc_second(clock.now())
