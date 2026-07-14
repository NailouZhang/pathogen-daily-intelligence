from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class CoverageWindow:
    issue_date: str
    start: str
    end: str
    timezone: str


def coverage_window(window_days: int, timezone_name: str, now: datetime | None = None) -> CoverageWindow:
    tz = ZoneInfo(timezone_name)
    now = now.astimezone(tz) if now else datetime.now(tz)
    issue_day = now.date()
    end_day = issue_day - timedelta(days=1)
    start_day = end_day - timedelta(days=max(1, window_days) - 1)
    return CoverageWindow(issue_date=issue_day.isoformat(), start=start_day.isoformat(), end=end_day.isoformat(), timezone=timezone_name)


def in_window(value: str | None, start: str, end: str) -> bool:
    if not value:
        return False
    token = value[:10]
    try:
        day = date.fromisoformat(token)
        return date.fromisoformat(start) <= day <= date.fromisoformat(end)
    except ValueError:
        return False
