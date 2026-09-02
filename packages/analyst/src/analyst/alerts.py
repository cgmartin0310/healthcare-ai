"""User-defined alerts evaluated against locked metrics."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from warehouse.metrics import cancelation_rate, early_quit_watch, referral_volume_change
from warehouse.store import Warehouse


@dataclass
class AlertDef:
    id: str
    name: str
    type: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class AlertHit:
    id: str
    name: str
    triggered: bool
    message: str
    evidence: dict[str, Any]


DEFAULT_ALERTS = [
    AlertDef(
        id="cancel_over_25",
        name="Cancelation over 25% (last 3 closed months)",
        type="cancelation_over_threshold",
        params={"threshold": 0.25, "months": 3},
    ),
    AlertDef(
        id="ref_drop_10",
        name="Referral volume −10% vs prior closed month",
        type="referral_volume_drop",
        params={"drop_pct": 0.10},
    ),
    AlertDef(
        id="early_quit",
        name="Early quit watch (cancelation > 30% under tenure bar)",
        type="early_quit_watch",
        params={},
    ),
]


def evaluate_alert(wh: Warehouse, alert: AlertDef, as_of: date, *, company: str | None = None) -> AlertHit:
    if alert.type == "cancelation_over_threshold":
        threshold = float(alert.params.get("threshold", 0.25))
        months = int(alert.params.get("months", 3))
        result = cancelation_rate(wh, as_of, months=months, company=company)
        rate = result.value
        triggered = rate is not None and rate > threshold
        pct = f"{rate:.1%}" if rate is not None else "n/a"
        return AlertHit(
            id=alert.id,
            name=alert.name,
            triggered=triggered,
            message=(
                f"Cancelation is {pct} over the last {months} closed months "
                f"(threshold {threshold:.0%}). "
                f"Numerator {result.details['numerator']} / "
                f"denominator {result.details['denominator']}."
                if rate is not None
                else result.unavailable or "Cancelation rate unavailable."
            ),
            evidence=result.to_dict(),
        )
    if alert.type == "referral_volume_drop":
        drop_pct = float(alert.params.get("drop_pct", 0.10))
        result = referral_volume_change(wh, as_of, company=company)
        change = result.value
        triggered = change is not None and change <= -drop_pct
        cur = result.details.get("current_referrals")
        prior = result.details.get("prior_referrals")
        chg = f"{change:.1%}" if change is not None else "n/a"
        return AlertHit(
            id=alert.id,
            name=alert.name,
            triggered=triggered,
            message=(
                f"Referral volume change {chg} "
                f"(current closed month {cur} vs prior {prior}; trigger ≤ −{drop_pct:.0%})."
            ),
            evidence=result.to_dict(),
        )
    if alert.type == "early_quit_watch":
        result = early_quit_watch(wh, as_of, company=company)
        n = int(result.value or 0)
        triggered = n > 0
        return AlertHit(
            id=alert.id,
            name=alert.name,
            triggered=triggered,
            message=f"{n} patient×discipline rows are over 30% cancelation while under the locked tenure bar.",
            evidence={"flagged_count": n, "sample": result.details.get("flagged", [])[:10]},
        )
    raise ValueError(f"Unknown alert type: {alert.type}")


def evaluate_alerts(
    wh: Warehouse,
    as_of: date,
    alerts: list[AlertDef] | None = None,
    *,
    company: str | None = None,
) -> list[AlertHit]:
    return [evaluate_alert(wh, alert, as_of, company=company) for alert in (alerts or DEFAULT_ALERTS)]
