from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from .utils import parse_date_loose


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
    return CoverageWindow(
        issue_date=issue_day.isoformat(),
        start=start_day.isoformat(),
        end=end_day.isoformat(),
        timezone=timezone_name,
    )


def _as_day(value: Any) -> date | None:
    parsed, _ = parse_date_loose(value)
    if not parsed:
        return None
    token = parsed[:10]
    try:
        if len(parsed) == 4:
            return date(int(parsed), 1, 1)
        if len(parsed) == 7:
            return date.fromisoformat(parsed + "-01")
        return date.fromisoformat(token)
    except ValueError:
        return None


def in_window(value: str | None, start: str, end: str) -> bool:
    day = _as_day(value)
    if not day:
        return False
    return date.fromisoformat(start) <= day <= date.fromisoformat(end)


def not_future(value: str | None, issue_date: str) -> bool:
    day = _as_day(value)
    if not day:
        return False
    return day <= date.fromisoformat(issue_date)


def choose_current_availability_date(
    candidates: Iterable[tuple[str, Any, str]],
    window: CoverageWindow,
) -> tuple[str | None, str, str | None]:
    """Choose the date that best represents when a work became reportable now.

    Each candidate is ``(field_name, value, precision_hint)``.  The selection
    deliberately avoids a future print/issue date when an online, created,
    indexed or entry date shows that the record is already publicly available.
    All original dates remain stored separately for audit.
    """
    normalized: list[tuple[str, str, str]] = []
    for field, value, precision_hint in candidates:
        parsed, precision = parse_date_loose(value)
        if parsed:
            normalized.append((field, parsed, precision_hint or precision))

    # Best evidence that the item became available during the current overlap window.
    priority = [
        "online_date",
        "electronic_date",
        "first_publication_date",
        "source_created_date",
        "source_indexed_date",
        "source_entry_date",
        "source_deposited_date",
        "publication_date",
        "print_date",
        "issue_date",
    ]
    for field in priority:
        for candidate_field, parsed, precision in normalized:
            if candidate_field == field and in_window(parsed, window.start, window.end):
                return parsed, precision, field

    # Older but valid public dates may be used for delayed indexing, never a future issue date.
    for field in priority:
        for candidate_field, parsed, precision in normalized:
            if candidate_field == field and not_future(parsed, window.issue_date):
                return parsed, precision, field

    # Future-only metadata is retained in its own fields but not used as the report date.
    return None, "unknown", None
