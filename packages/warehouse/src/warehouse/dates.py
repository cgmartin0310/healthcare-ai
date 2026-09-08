"""Closed-month helpers. Closed-month is the truth grain."""

from __future__ import annotations

import calendar
from datetime import date, timedelta


def month_bounds(d: date) -> tuple[date, date]:
    last = calendar.monthrange(d.year, d.month)[1]
    return date(d.year, d.month, 1), date(d.year, d.month, last)


def add_months(d: date, months: int) -> date:
    year = d.year + (d.month - 1 + months) // 12
    month = (d.month - 1 + months) % 12 + 1
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(d.day, last))


def is_month_end(d: date) -> bool:
    return d.day == calendar.monthrange(d.year, d.month)[1]


def last_closed_month(as_of: date) -> tuple[date, date]:
    """Last fully closed calendar month relative to as_of.

    If as_of is the last day of its month, that month is closed.
    Otherwise the previous calendar month is the last closed month.
    """
    if is_month_end(as_of):
        return month_bounds(as_of)
    prev = as_of.replace(day=1) - timedelta(days=1)
    return month_bounds(prev)


def closed_months_back(as_of: date, n: int) -> list[tuple[date, date]]:
    """n most recent closed months, oldest first."""
    start, end = last_closed_month(as_of)
    months = [(start, end)]
    cursor = start
    for _ in range(n - 1):
        cursor = cursor - timedelta(days=1)
        start, end = month_bounds(cursor)
        months.append((start, end))
        cursor = start
    months.reverse()
    return months


def prior_closed_month(as_of: date) -> tuple[date, date]:
    """Month before the last closed month (churn 'prior')."""
    current_start, _ = last_closed_month(as_of)
    return month_bounds(current_start - timedelta(days=1))
