"""All time handling lives here so the rest of the code never calls datetime.now() directly.

Monitoring is forward-only: a check is always recorded against *today* (UTC).
Day 1 is the first day a check ran; every later run appends a new day and
earlier days are never rewritten.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone


def now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def today() -> date:
    return now().date()


def iso(moment: datetime) -> str:
    return moment.replace(microsecond=0).isoformat()


def start_of_day(day: date) -> datetime:
    return datetime.combine(day, time(0, 0), tzinfo=timezone.utc)


def days_between(a: str | datetime, b: str | datetime) -> float:
    a_dt = datetime.fromisoformat(a) if isinstance(a, str) else a
    b_dt = datetime.fromisoformat(b) if isinstance(b, str) else b
    return (b_dt - a_dt).total_seconds() / 86400


def day_number(monitoring_start: date, day: date) -> int:
    """Calendar-based: a skipped day still counts, so Day N always means start + N - 1."""
    return (day - monitoring_start).days + 1
