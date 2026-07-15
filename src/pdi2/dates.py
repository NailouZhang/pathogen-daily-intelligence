from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .utils import parse_iso_date


def date_window(days: int, end: date | None = None, timezone_name: str = "Asia/Shanghai") -> tuple[date, date]:
    end = end or datetime.now(ZoneInfo(timezone_name)).date()
    return end - timedelta(days=max(1, days)), end


def choose_availability_date(record: dict[str, Any], start: date, end: date) -> tuple[str | None, str | None]:
    ordered = [
        ("online_date", record.get("online_date")),
        ("first_publication_date", record.get("first_publication_date")),
        ("created_date", record.get("created_date")),
        ("indexed_date", record.get("indexed_date")),
        ("published_date", record.get("published_date")),
        ("print_date", record.get("print_date")),
    ]
    for basis, value in ordered:
        parsed = parse_iso_date(value)
        if parsed and start <= parsed <= end:
            return parsed.isoformat(), basis
    for basis, value in ordered:
        parsed = parse_iso_date(value)
        if parsed and parsed <= end:
            return parsed.isoformat(), basis
    return None, None
