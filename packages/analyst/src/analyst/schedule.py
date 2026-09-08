"""Scheduled-metrics hooks. Cadence config only — no live book, no this-week schedule."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal

from warehouse.metrics import snapshot
from warehouse.store import Warehouse

Cadence = Literal["daily", "weekly", "monthly"]


@dataclass
class CadenceConfig:
    cadence: Cadence
    metrics: list[str] = field(default_factory=lambda: ["cancelation_rate", "referrals", "ar_past_30_days"])
    weekday: int = 0  # Monday
    day_of_month: int = 1


DEFAULT_SCHEDULES = [
    CadenceConfig(cadence="daily", metrics=["ar_past_30_days"]),
    CadenceConfig(cadence="weekly", metrics=["cancelation_rate", "referrals"]),
    CadenceConfig(cadence="monthly", metrics=["churn", "active_book", "referrals"]),
]


def is_due(config: CadenceConfig, now: datetime) -> bool:
    if config.cadence == "daily":
        return True
    if config.cadence == "weekly":
        return now.weekday() == config.weekday
    if config.cadence == "monthly":
        return now.day == config.day_of_month
    return False


def run_scheduled(
    wh: Warehouse,
    as_of: date,
    configs: list[CadenceConfig] | None = None,
    *,
    now: datetime | None = None,
    company: str | None = None,
) -> list[dict[str, Any]]:
    now = now or datetime.combine(as_of, datetime.min.time())
    full = snapshot(wh, as_of, company=company)
    out = []
    for config in configs or DEFAULT_SCHEDULES:
        due = is_due(config, now)
        payload = {name: full.get(name) for name in config.metrics if name in full}
        out.append(
            {
                "cadence": config.cadence,
                "due": due,
                "metrics": config.metrics,
                "payload": payload if due else None,
                "note": "Closed-month grain. Not a live schedule.",
            }
        )
    return out
