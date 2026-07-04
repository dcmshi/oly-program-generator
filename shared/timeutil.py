# shared/timeutil.py
"""Timezone-aware 'today' for per-athlete date math.

Week bucketing and log-date defaults must reflect the athlete's local calendar
day, not the server's — otherwise an athlete west of the server sees the week
roll over early or logs a session dated "tomorrow" (W-L5).
"""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def today_in_tz(tz_name: str | None) -> date:
    """Return the current calendar date in the given IANA timezone.

    Falls back to UTC for a missing, blank, or unrecognized zone so a bad value
    can never raise into a request handler.
    """
    if tz_name:
        try:
            return datetime.now(ZoneInfo(tz_name)).date()
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return datetime.now(UTC).date()
