"""Staffing working model (locked). Forecast is clinic×discipline, not therapist-level.

Do not use a live schedule, waitlist, or slots engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from warehouse.dates import last_closed_month
from warehouse.metrics import churn, completes_in_month, headcount
from warehouse.schema import qident, quoted_table
from warehouse.store import Warehouse

VISITS_PER_WEEK_FTE = {"OT": 35, "PT": 35, "ST": 70}
VISITS_PER_NEW_WEEKLY = {"OT": 1.0, "PT": 1.0, "ST": 1.5}
GM_REVENUE = {"OT": 95.0, "PT": 95.0, "ST": 67.0}
CONVERSION_PLANNING = 0.50
WEEKS_PER_MONTH = 52 / 12
THIN_PRIOR_ACTIVE = 20
THIN_CHURN_PLUG = {"OT": 0.10, "ST": 0.10, "PT": 0.20}


@dataclass(frozen=True)
class Rounding:
    mode: str = "nearest_0_5"
    min_fte: float = 1.0


DEFAULT_ROUNDING = Rounding()


def round_fte(raw: float, rounding: Rounding = DEFAULT_ROUNDING) -> float:
    if rounding.mode == "nearest_0_5":
        stepped = round(raw * 2) / 2
    else:
        stepped = round(raw)
    return max(rounding.min_fte, stepped)


def demand_next_month(
    completes_last_closed: int,
    clinic_monthly_churn: float,
    refs_per_month: int,
    discipline: str,
    conversion: float = CONVERSION_PLANNING,
) -> float:
    """last closed Completes × (1 − churn) + (refs/mo × conversion × 52/12 × visits-per-new)."""
    retained = completes_last_closed * (1 - clinic_monthly_churn)
    visits_per_new = VISITS_PER_NEW_WEEKLY[discipline]
    new_visits = refs_per_month * conversion * WEEKS_PER_MONTH * visits_per_new
    return retained + new_visits


def forecast(wh: Warehouse, as_of: date, *, company: str | None = None, rounding: Rounding = DEFAULT_ROUNDING) -> dict[str, Any]:
    start, end = last_closed_month(as_of)
    churn_m = churn(wh, as_of, company=company)
    by_disc = churn_m.details.get("by_discipline", {})
    hc = headcount(wh, as_of, company=company)
    lines = []
    for discipline in ("OT", "PT", "ST"):
        completes = completes_in_month(wh, start, end, company=company, discipline=discipline)
        ref_sql = f"""
            SELECT COUNT(*) FROM {quoted_table("REFERRAL")}
            WHERE {qident("DateTimeCreated")} >= ?
              AND {qident("DateTimeCreated")} < ?
              AND {qident("Discipline")} = ?
        """
        ref_params: list[Any] = [start, end + timedelta(days=1), discipline]
        if company:
            ref_sql += f' AND {qident("Company")} = ?'
            ref_params.append(company)
        row = wh.fetch_one(ref_sql, ref_params)
        refs_n = int((row or [0])[0] or 0)
        prior_active = int(by_disc.get(discipline, {}).get("prior_active") or 0)
        observed = by_disc.get(discipline, {}).get("rate")
        if prior_active < THIN_PRIOR_ACTIVE:
            used_churn = THIN_CHURN_PLUG[discipline]
            churn_source = f"thin-data plug (prior-active {prior_active} < 20)"
        else:
            used_churn = float(observed) if observed is not None else THIN_CHURN_PLUG[discipline]
            churn_source = "observed clinic×discipline"
        dem = demand_next_month(completes, used_churn, refs_n, discipline)
        monthly_capacity = VISITS_PER_WEEK_FTE[discipline] * WEEKS_PER_MONTH
        raw_fte = dem / monthly_capacity if monthly_capacity else 0
        fte = round_fte(raw_fte, rounding)
        therapists = [
            t for t in hc.details["therapists"] if t["primary_discipline"] == discipline
        ]
        lines.append(
            {
                "discipline": discipline,
                "completes_last_closed_month": completes,
                "refs_last_closed_month": refs_n,
                "prior_active": prior_active,
                "churn_rate_used": used_churn,
                "churn_source": churn_source,
                "demand_visits_next_month": dem,
                "raw_fte": raw_fte,
                "fte_rounded": fte,
                "headcount": len(therapists),
                "gm_revenue_per_visit": GM_REVENUE[discipline],
                "conversion_planning": CONVERSION_PLANNING,
            }
        )
    return {
        "as_of": as_of.isoformat(),
        "last_closed_month": {"start": start.isoformat(), "end": end.isoformat()},
        "rounding": {"mode": rounding.mode, "min_fte": rounding.min_fte},
        "note": "Forecast churn is clinic×discipline, not therapist-level. No live schedule.",
        "by_discipline": lines,
        "headcount_total": hc.value,
    }
