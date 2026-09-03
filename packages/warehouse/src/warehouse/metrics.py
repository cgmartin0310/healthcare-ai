"""Locked Clinic Analyst metric definitions.

Do not redefine these. If a metric cannot land on PREP without a second model,
refuse to compute a lookalike and say so.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from warehouse.dates import add_months, closed_months_back, last_closed_month, prior_closed_month
from warehouse.money import appointment_relation, money_source
from warehouse.schema import (
    CANCELATION_DENOMINATOR,
    CANCELATION_NUMERATOR,
    STATUS_COMPLETE,
    age_band_from_dob,
    qident,
    quoted_table,
)
from warehouse.store import Warehouse


def _in_list(values: tuple[str, ...]) -> str:
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)


def _provider_key_sql() -> str:
    """Prefer ProviderId; fall back to ProviderName. Not TherapistId."""
    return (
        f"COALESCE(NULLIF(TRIM(CAST({qident('ProviderId')} AS VARCHAR)), ''), "
        f"NULLIF(TRIM(CAST({qident('ProviderName')} AS VARCHAR)), ''))"
    )


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if hasattr(value, "to_pydatetime"):
        try:
            return value.to_pydatetime().date()
        except Exception:
            return None
    return None


@dataclass
class MetricResult:
    name: str
    as_of: date
    grain_note: str
    value: Any
    details: dict[str, Any] = field(default_factory=dict)
    unavailable: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def cancelation_rate(
    wh: Warehouse,
    as_of: date,
    months: int = 3,
    *,
    company: str | None = None,
    location: str | None = None,
    discipline: str | None = None,
) -> MetricResult:
    """(Cancelled + No Show) / (Complete + Cancelled + No Show). Pending/Waiting out.

    Window is the last `months` closed calendar months.
    """
    closed = closed_months_back(as_of, months)
    start, _ = closed[0]
    _, end = closed[-1]
    filters = [
        f'{qident("ApptDate")} >= ?',
        f'{qident("ApptDate")} <= ?',
    ]
    params: list[Any] = [start, end]
    if company:
        filters.append(f'{qident("Company")} = ?')
        params.append(company)
    if location:
        filters.append(f'{qident("LocationName")} = ?')
        params.append(location)
    if discipline:
        filters.append(f'{qident("Discipline")} = ?')
        params.append(discipline)
    where = " AND ".join(filters)
    sql = f"""
        SELECT
            SUM(CASE WHEN {qident("AppointmentStatus")} = '{STATUS_COMPLETE}' THEN 1 ELSE 0 END) AS complete,
            SUM(CASE WHEN {qident("AppointmentStatus")} = 'Cancelled' THEN 1 ELSE 0 END) AS cancelled,
            SUM(CASE WHEN {qident("AppointmentStatus")} = 'No Show' THEN 1 ELSE 0 END) AS no_show,
            SUM(CASE WHEN {qident("AppointmentStatus")} = 'Pending' THEN 1 ELSE 0 END) AS pending,
            SUM(CASE WHEN {qident("AppointmentStatus")} = 'Waiting' THEN 1 ELSE 0 END) AS waiting
        FROM {quoted_table("APPOINTMENT")}
        WHERE {where}
    """
    row = wh.fetch_one(sql, params)
    complete = int(row[0] or 0) if row else 0
    cancelled = int(row[1] or 0) if row else 0
    no_show = int(row[2] or 0) if row else 0
    pending = int(row[3] or 0) if row else 0
    waiting = int(row[4] or 0) if row else 0
    denom = complete + cancelled + no_show
    rate = (cancelled + no_show) / denom if denom else None
    return MetricResult(
        name="cancelation_rate",
        as_of=as_of,
        grain_note="closed months; Pending/Waiting excluded",
        value=rate,
        details={
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "complete": complete,
            "cancelled": cancelled,
            "no_show": no_show,
            "numerator": cancelled + no_show,
            "denominator": denom,
            "pending_excluded": pending,
            "waiting_excluded": waiting,
            "formula": "(Cancelled + No Show) / (Complete + Cancelled + No Show)",
        },
        unavailable=None if denom else "No Complete/Cancelled/No Show visits in the closed-month window.",
    )


def active_book(
    wh: Warehouse,
    month_start: date,
    month_end: date,
    *,
    company: str | None = None,
) -> MetricResult:
    """Active book = ≥1 Complete in the calendar month. Not PATIENT.PatientActive."""
    filters = [
        f'{qident("ApptDate")} >= ?',
        f'{qident("ApptDate")} <= ?',
        f'{qident("AppointmentStatus")} = ?',
    ]
    params: list[Any] = [month_start, month_end, STATUS_COMPLETE]
    if company:
        filters.append(f'{qident("Company")} = ?')
        params.append(company)
    sql = f"""
        SELECT COUNT(*) FROM (
            SELECT {qident("Company")}, {qident("Discipline")}, {qident("PatientId")}
            FROM {quoted_table("APPOINTMENT")}
            WHERE {" AND ".join(filters)}
            GROUP BY 1, 2, 3
        ) t
    """
    count = int((wh.fetch_one(sql, params) or [0])[0] or 0)
    # Prove PatientActive is not used: count patients marked active with zero completes.
    ignored = wh.fetch_one(
        f"""
        SELECT COUNT(*) FROM {quoted_table("PATIENT")} p
        WHERE COALESCE(p.{qident("PatientActive")}, FALSE) = TRUE
          AND NOT EXISTS (
            SELECT 1 FROM {quoted_table("APPOINTMENT")} a
            WHERE a.{qident("PatientId")} = p.{qident("PatientId")}
              AND a.{qident("Company")} = p.{qident("Company")}
              AND a.{qident("AppointmentStatus")} = ?
              AND a.{qident("ApptDate")} >= ?
              AND a.{qident("ApptDate")} <= ?
          )
        """,
        [STATUS_COMPLETE, month_start, month_end],
    )
    return MetricResult(
        name="active_book",
        as_of=month_end,
        grain_note="Company × Discipline × PatientId with ≥1 Complete in calendar month",
        value=count,
        details={
            "month_start": month_start.isoformat(),
            "month_end": month_end.isoformat(),
            "patient_active_true_but_not_in_book": int((ignored or [0])[0] or 0),
            "note": "PATIENT.PatientActive is not operationally active and is not used.",
        },
    )


def churn(wh: Warehouse, as_of: date, *, company: str | None = None) -> MetricResult:
    """Churn grain = Company × Discipline × PatientId.

    Prior = last closed month's previous month; current = last closed month.
    Closed months only (open 'this month' is not used as truth).
    Drop first-DOS on/after prior month start. New patients never enter the prior cohort.
    Churned = prior active, not current active.
    """
    current_start, current_end = last_closed_month(as_of)
    prior_start, prior_end = prior_closed_month(as_of)
    company_filter = ""
    params: list[Any] = [STATUS_COMPLETE]
    extra: list[Any] = []
    if company:
        company_filter = f'AND {qident("Company")} = ?'
        extra = [company]
    # First DOS = min Complete ApptDate per Company × Discipline × PatientId
    sql = f"""
        WITH first_dos AS (
            SELECT
                {qident("Company")} AS company,
                {qident("Discipline")} AS discipline,
                {qident("PatientId")} AS patient_id,
                MIN({qident("ApptDate")}) AS first_dos
            FROM {quoted_table("APPOINTMENT")}
            WHERE {qident("AppointmentStatus")} = ?
            {company_filter}
            GROUP BY 1, 2, 3
        ),
        prior_active AS (
            SELECT DISTINCT
                a.{qident("Company")} AS company,
                a.{qident("Discipline")} AS discipline,
                a.{qident("PatientId")} AS patient_id
            FROM {quoted_table("APPOINTMENT")} a
            JOIN first_dos f
              ON f.company = a.{qident("Company")}
             AND f.discipline = a.{qident("Discipline")}
             AND f.patient_id = a.{qident("PatientId")}
            WHERE a.{qident("AppointmentStatus")} = '{STATUS_COMPLETE}'
              AND a.{qident("ApptDate")} >= ?
              AND a.{qident("ApptDate")} <= ?
              AND f.first_dos < ?
              {company_filter.replace(qident("Company"), "a." + qident("Company"))}
        ),
        current_active AS (
            SELECT DISTINCT
                {qident("Company")} AS company,
                {qident("Discipline")} AS discipline,
                {qident("PatientId")} AS patient_id
            FROM {quoted_table("APPOINTMENT")}
            WHERE {qident("AppointmentStatus")} = '{STATUS_COMPLETE}'
              AND {qident("ApptDate")} >= ?
              AND {qident("ApptDate")} <= ?
              {company_filter}
        )
        SELECT
            (SELECT COUNT(*) FROM prior_active) AS prior_n,
            (SELECT COUNT(*) FROM current_active) AS current_n,
            (
                SELECT COUNT(*) FROM prior_active p
                WHERE NOT EXISTS (
                    SELECT 1 FROM current_active c
                    WHERE c.company = p.company
                      AND c.discipline = p.discipline
                      AND c.patient_id = p.patient_id
                )
            ) AS churned_n
    """
    query_params = (
        params
        + extra
        + [prior_start, prior_end, prior_start]
        + extra
        + [current_start, current_end]
        + extra
    )
    row = wh.fetch_one(sql, query_params)
    prior_n = int((row or [0])[0] or 0)
    current_n = int((row or [0, 0])[1] or 0)
    churned_n = int((row or [0, 0, 0])[2] or 0)
    rate = (churned_n / prior_n) if prior_n else None

    by_disc = wh.fetch_df(
        f"""
        WITH first_dos AS (
            SELECT
                {qident("Company")} AS company,
                {qident("Discipline")} AS discipline,
                {qident("PatientId")} AS patient_id,
                MIN({qident("ApptDate")}) AS first_dos
            FROM {quoted_table("APPOINTMENT")}
            WHERE {qident("AppointmentStatus")} = '{STATUS_COMPLETE}'
            {company_filter}
            GROUP BY 1, 2, 3
        ),
        prior_active AS (
            SELECT DISTINCT
                a.{qident("Company")} AS company,
                a.{qident("Discipline")} AS discipline,
                a.{qident("PatientId")} AS patient_id
            FROM {quoted_table("APPOINTMENT")} a
            JOIN first_dos f
              ON f.company = a.{qident("Company")}
             AND f.discipline = a.{qident("Discipline")}
             AND f.patient_id = a.{qident("PatientId")}
            WHERE a.{qident("AppointmentStatus")} = '{STATUS_COMPLETE}'
              AND a.{qident("ApptDate")} >= ?
              AND a.{qident("ApptDate")} <= ?
              AND f.first_dos < ?
              {company_filter.replace(qident("Company"), "a." + qident("Company"))}
        ),
        current_active AS (
            SELECT DISTINCT
                {qident("Company")} AS company,
                {qident("Discipline")} AS discipline,
                {qident("PatientId")} AS patient_id
            FROM {quoted_table("APPOINTMENT")}
            WHERE {qident("AppointmentStatus")} = '{STATUS_COMPLETE}'
              AND {qident("ApptDate")} >= ?
              AND {qident("ApptDate")} <= ?
              {company_filter}
        )
        SELECT
            p.discipline,
            COUNT(*) AS prior_n,
            SUM(CASE WHEN c.patient_id IS NULL THEN 1 ELSE 0 END) AS churned_n
        FROM prior_active p
        LEFT JOIN current_active c
          ON c.company = p.company AND c.discipline = p.discipline AND c.patient_id = p.patient_id
        GROUP BY 1
        ORDER BY 1
        """,
        extra + [prior_start, prior_end, prior_start] + extra + [current_start, current_end] + extra,
    )
    by_discipline = {}
    for rec in by_disc.to_dict(orient="records"):
        p_n = int(rec["prior_n"])
        c_n = int(rec["churned_n"])
        by_discipline[str(rec["discipline"])] = {
            "prior_active": p_n,
            "churned": c_n,
            "rate": (c_n / p_n) if p_n else None,
        }

    return MetricResult(
        name="churn",
        as_of=as_of,
        grain_note="Company × Discipline × PatientId; closed months only; first-DOS on/after prior start dropped",
        value=rate,
        details={
            "prior_month_start": prior_start.isoformat(),
            "prior_month_end": prior_end.isoformat(),
            "current_month_start": current_start.isoformat(),
            "current_month_end": current_end.isoformat(),
            "prior_active": prior_n,
            "current_active": current_n,
            "churned": churned_n,
            "by_discipline": by_discipline,
        },
        unavailable=None if prior_n else "No prior-month active cohort after first-DOS drop.",
    )


def early_quit_watch(wh: Warehouse, as_of: date, *, company: str | None = None) -> MetricResult:
    """Cancelation % > 30% while under tenure bar.

    Tenure bars (locked): PT / adult OT-ST < 3 months; child OT-ST < 6.
    DFlex % is not a quit warning and is not computed.
    Tenure = months from first Complete DOS to last_closed_month end.
    """
    _, current_end = last_closed_month(as_of)
    months = closed_months_back(as_of, 3)
    win_start, _ = months[0]
    company_filter = f'AND a.{qident("Company")} = ?' if company else ""
    params: list[Any] = [STATUS_COMPLETE]
    if company:
        params.append(company)
    params.extend([win_start, current_end])
    if company:
        params.append(company)

    sql = f"""
        WITH first_dos AS (
            SELECT
                {qident("Company")} AS company,
                {qident("Discipline")} AS discipline,
                {qident("PatientId")} AS patient_id,
                MIN({qident("ApptDate")}) AS first_dos
            FROM {quoted_table("APPOINTMENT")}
            WHERE {qident("AppointmentStatus")} = ?
            {company_filter.replace("a.", "")}
            GROUP BY 1, 2, 3
        ),
        visit_counts AS (
            SELECT
                a.{qident("Company")} AS company,
                a.{qident("Discipline")} AS discipline,
                a.{qident("PatientId")} AS patient_id,
                SUM(CASE WHEN a.{qident("AppointmentStatus")} = 'Complete' THEN 1 ELSE 0 END) AS complete,
                SUM(CASE WHEN a.{qident("AppointmentStatus")} = 'Cancelled' THEN 1 ELSE 0 END) AS cancelled,
                SUM(CASE WHEN a.{qident("AppointmentStatus")} = 'No Show' THEN 1 ELSE 0 END) AS no_show
            FROM {quoted_table("APPOINTMENT")} a
            WHERE a.{qident("ApptDate")} >= ?
              AND a.{qident("ApptDate")} <= ?
              {company_filter}
            GROUP BY 1, 2, 3
        )
        SELECT
            v.company, v.discipline, v.patient_id,
            v.complete, v.cancelled, v.no_show,
            f.first_dos,
            p.{qident("DOB")} AS dob
        FROM visit_counts v
        JOIN first_dos f
          ON f.company = v.company AND f.discipline = v.discipline AND f.patient_id = v.patient_id
        LEFT JOIN {quoted_table("PATIENT")} p
          ON p.{qident("PatientId")} = v.patient_id AND p.{qident("Company")} = v.company
    """
    frame = wh.fetch_df(sql, params)
    flagged: list[dict[str, Any]] = []
    missing_dob = 0
    for rec in frame.to_dict(orient="records"):
        complete = int(rec["complete"] or 0)
        cancelled = int(rec["cancelled"] or 0)
        no_show = int(rec["no_show"] or 0)
        denom = complete + cancelled + no_show
        if denom == 0:
            continue
        rate = (cancelled + no_show) / denom
        first = _as_date(rec["first_dos"])
        if first is None:
            continue
        dob = _as_date(rec["dob"])
        if dob is None:
            missing_dob += 1
        tenure_months = (current_end.year - first.year) * 12 + (current_end.month - first.month)
        disc = str(rec["discipline"])
        age_band = age_band_from_dob(dob, current_end)
        if disc == "PT" or (disc in {"OT", "ST"} and age_band == "Adult"):
            bar = 3
        elif disc in {"OT", "ST"} and age_band == "Child":
            bar = 6
        else:
            bar = 3
        under_bar = tenure_months < bar
        if rate > 0.30 and under_bar:
            flagged.append(
                {
                    "patient_id": rec["patient_id"],
                    "discipline": disc,
                    "age_band": age_band,
                    "tenure_months": tenure_months,
                    "tenure_bar_months": bar,
                    "cancelation_rate": rate,
                    "complete": complete,
                    "cancelled": cancelled,
                    "no_show": no_show,
                }
            )
    return MetricResult(
        name="early_quit_watch",
        as_of=as_of,
        grain_note="patient×discipline cancelation > 30% under locked tenure bar; child vs adult from PATIENT.DOB; DFlex is not used",
        value=len(flagged),
        details={
            "flagged": flagged[:50],
            "flagged_count": len(flagged),
            "bars": {
                "PT": "< 3 months",
                "adult_OT_ST": "< 3 months",
                "child_OT_ST": "< 6 months",
            },
            "age_band_source": "derived from PATIENT.DOB (child = age < 18 at last closed month end); not a warehouse column",
            "patients_missing_dob_defaulted_adult": missing_dob,
        },
    )


def referrals(wh: Warehouse, as_of: date, months: int = 1, *, company: str | None = None) -> MetricResult:
    """Referrals = COUNT REFERRAL rows. Conversion = converted / referrals. Converted = Completed?=1."""
    closed = closed_months_back(as_of, months)
    start, _ = closed[0]
    _, end = closed[-1]
    filters = [f'{qident("DateTimeCreated")} >= ?', f'{qident("DateTimeCreated")} < ?']
    params: list[Any] = [start, end + timedelta(days=1)]
    if company:
        filters.append(f'{qident("Company")} = ?')
        params.append(company)
    sql = f"""
        SELECT
            COUNT(*) AS referrals,
            SUM(CASE WHEN {qident("Completed?")} = 1 THEN 1 ELSE 0 END) AS converted
        FROM {quoted_table("REFERRAL")}
        WHERE {" AND ".join(filters)}
    """
    row = wh.fetch_one(sql, params)
    ref_n = int((row or [0])[0] or 0)
    converted = int((row or [0, 0])[1] or 0)
    conv = (converted / ref_n) if ref_n else None
    by_source = wh.fetch_df(
        f"""
        SELECT
            COALESCE(NULLIF(TRIM(CAST({qident("Source")} AS VARCHAR)), ''), '(blank)') AS source,
            COUNT(*) AS referrals,
            SUM(CASE WHEN {qident("Completed?")} = 1 THEN 1 ELSE 0 END) AS converted
        FROM {quoted_table("REFERRAL")}
        WHERE {" AND ".join(filters)}
        GROUP BY 1
        ORDER BY referrals DESC
        """,
        params,
    )
    sources = []
    for rec in by_source.to_dict(orient="records"):
        r = int(rec["referrals"])
        c = int(rec["converted"] or 0)
        sources.append(
            {
                "source": rec["source"],
                "referrals": r,
                "converted": c,
                "conversion": (c / r) if r else None,
            }
        )
    return MetricResult(
        name="referrals",
        as_of=as_of,
        grain_note="COUNT REFERRAL rows; Converted = Completed?=1; EVAL notes are not conversion",
        value={"referrals": ref_n, "converted": converted, "conversion": conv},
        details={
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "by_source": sources,
        },
        unavailable=None if ref_n else "No REFERRAL rows in the closed-month window.",
    )


def referral_volume_change(wh: Warehouse, as_of: date, *, company: str | None = None) -> MetricResult:
    """Last closed month vs prior closed month referral counts (and by source)."""
    current = referrals(wh, as_of, months=1, company=company)
    prior_end = prior_closed_month(as_of)[1]
    prior = referrals(wh, prior_end, months=1, company=company)
    cur_n = int(current.value["referrals"]) if current.value else 0
    prior_n = int(prior.value["referrals"]) if prior.value else 0
    change = None
    if prior_n:
        change = (cur_n - prior_n) / prior_n
    source_changes = []
    prior_map = {s["source"]: s for s in prior.details["by_source"]}
    all_sources = {s["source"] for s in current.details["by_source"]} | set(prior_map)
    cur_map = {s["source"]: s for s in current.details["by_source"]}
    for source in sorted(all_sources):
        c = cur_map.get(source, {"referrals": 0})["referrals"]
        p = prior_map.get(source, {"referrals": 0})["referrals"]
        pct = ((c - p) / p) if p else None
        source_changes.append({"source": source, "current": c, "prior": p, "change": pct})
    return MetricResult(
        name="referral_volume_change",
        as_of=as_of,
        grain_note="closed month vs prior closed month; COUNT REFERRAL rows",
        value=change,
        details={
            "current_referrals": cur_n,
            "prior_referrals": prior_n,
            "by_source": source_changes,
            "current_window": current.details,
            "prior_window": prior.details,
        },
        unavailable=None if prior_n else "No referrals in the prior closed month.",
    )


def primary_payer_patient_level(
    wh: Warehouse,
    window_start: date,
    window_end: date,
    *,
    company: str | None = None,
) -> MetricResult:
    """Patient-level primary payer = latest Complete in the window (ApptDate DESC, ApptId DESC)."""
    filters = [
        f'{qident("AppointmentStatus")} = ?',
        f'{qident("ApptDate")} >= ?',
        f'{qident("ApptDate")} <= ?',
    ]
    params: list[Any] = [STATUS_COMPLETE, window_start, window_end]
    if company:
        filters.append(f'{qident("Company")} = ?')
        params.append(company)
    sql = f"""
        SELECT {qident("PrimaryPayorName")} AS payer, COUNT(*) AS patients
        FROM (
            SELECT
                {qident("Company")},
                {qident("PatientId")},
                {qident("PrimaryPayorName")},
                ROW_NUMBER() OVER (
                    PARTITION BY {qident("Company")}, {qident("PatientId")}
                    ORDER BY {qident("ApptDate")} DESC, {qident("ApptId")} DESC
                ) AS rn
            FROM {quoted_table("APPOINTMENT")}
            WHERE {" AND ".join(filters)}
        ) t
        WHERE rn = 1
        GROUP BY 1
        ORDER BY patients DESC
    """
    frame = wh.fetch_df(sql, params)
    rows = [
        {"payer": rec["payer"], "patients": int(rec["patients"])}
        for rec in frame.to_dict(orient="records")
    ]
    return MetricResult(
        name="primary_payer_patient_level",
        as_of=window_end,
        grain_note="latest Complete in window, ApptDate DESC, ApptId DESC",
        value=rows,
        details={"window_start": window_start.isoformat(), "window_end": window_end.isoformat()},
    )


def avg_collections(wh: Warehouse, as_of: date, *, company: str | None = None) -> MetricResult:
    """InsPaid by payer, DOS=ApptDate, window start 60 days ago going back 3 months.

    Includes zeros and partials. Completes only (DOS collections on completed visits).
    """
    window_end = as_of - timedelta(days=60)
    window_start = add_months(window_end, -3)
    filters = [
        f'{qident("AppointmentStatus")} = ?',
        f'{qident("ApptDate")} >= ?',
        f'{qident("ApptDate")} < ?',
    ]
    params: list[Any] = [STATUS_COMPLETE, window_start, window_end + timedelta(days=1)]
    if company:
        filters.append(f'{qident("Company")} = ?')
        params.append(company)
    sql = f"""
        SELECT
            COALESCE({qident("PrimaryPayorName")}, '(unknown)') AS payer,
            COUNT(*) AS claims,
            AVG(COALESCE({qident("InsPaid")}, 0)) AS avg_ins_paid,
            SUM(COALESCE({qident("InsPaid")}, 0)) AS sum_ins_paid
        FROM {appointment_relation(wh)}
        WHERE {" AND ".join(filters)}
        GROUP BY 1
        ORDER BY avg_ins_paid DESC
    """
    frame = wh.fetch_df(sql, params)
    rows = [
        {
            "payer": rec["payer"],
            "claims": int(rec["claims"]),
            "avg_ins_paid": float(rec["avg_ins_paid"] or 0),
            "sum_ins_paid": float(rec["sum_ins_paid"] or 0),
        }
        for rec in frame.to_dict(orient="records")
    ]
    src = money_source(wh)
    if src == "none":
        return MetricResult(
            name="avg_collections",
            as_of=as_of,
            grain_note="InsPaid including zeros/partials; DOS=ApptDate; 60-day lag then 3 months back",
            value=[],
            details={"source": src},
            unavailable="InsPaid is not in the dump (no CLAIM_TXN and no appointment rollup).",
        )
    return MetricResult(
        name="avg_collections",
        as_of=as_of,
        grain_note="InsPaid including zeros/partials; DOS=ApptDate; 60-day lag then 3 months back",
        value=rows,
        details={
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "uses": "InsPaid",
            "not": "TotalPaid",
            "source": src,
        },
        unavailable=None if rows else "No Completes in the collections lag window.",
    )


def avg_paid(wh: Warehouse, as_of: date, *, company: str | None = None) -> MetricResult:
    """InsPaid>0 only, last 3 months through today."""
    window_start = add_months(as_of, -3)
    filters = [
        f'{qident("AppointmentStatus")} = ?',
        f'{qident("ApptDate")} >= ?',
        f'{qident("ApptDate")} <= ?',
        f'{qident("InsPaid")} > 0',
    ]
    params: list[Any] = [STATUS_COMPLETE, window_start, as_of]
    if company:
        filters.append(f'{qident("Company")} = ?')
        params.append(company)
    sql = f"""
        SELECT
            COALESCE({qident("PrimaryPayorName")}, '(unknown)') AS payer,
            COUNT(*) AS claims,
            AVG({qident("InsPaid")}) AS avg_ins_paid
        FROM {appointment_relation(wh)}
        WHERE {" AND ".join(filters)}
        GROUP BY 1
        ORDER BY avg_ins_paid DESC
    """
    frame = wh.fetch_df(sql, params)
    rows = [
        {
            "payer": rec["payer"],
            "claims": int(rec["claims"]),
            "avg_ins_paid": float(rec["avg_ins_paid"] or 0),
        }
        for rec in frame.to_dict(orient="records")
    ]
    src = money_source(wh)
    if src == "none":
        return MetricResult(
            name="avg_paid",
            as_of=as_of,
            grain_note="InsPaid>0 only; last 3 months through as_of",
            value=[],
            details={"source": src},
            unavailable="InsPaid is not in the dump (no CLAIM_TXN and no appointment rollup).",
        )
    return MetricResult(
        name="avg_paid",
        as_of=as_of,
        grain_note="InsPaid>0 only; last 3 months through as_of",
        value=rows,
        details={"window_start": window_start.isoformat(), "window_end": as_of.isoformat(), "uses": "InsPaid", "source": src},
        unavailable=None if rows else "No Completes with InsPaid>0 in the last 3 months.",
    )


def days_to_pay(wh: Warehouse, as_of: date, *, company: str | None = None) -> MetricResult:
    """DATEDIFF(day, ApptDate, FirstInsPayment) on Completes with InsPaid>0.

    Exclude negatives. Require min 20 claims or report insufficient.
    """
    filters = [
        f'{qident("AppointmentStatus")} = ?',
        f'{qident("InsPaid")} > 0',
        f'{qident("FirstInsPayment")} IS NOT NULL',
        f'{qident("ApptDate")} <= ?',
    ]
    params: list[Any] = [STATUS_COMPLETE, as_of]
    if company:
        filters.append(f'{qident("Company")} = ?')
        params.append(company)
    sql = f"""
        SELECT
            COALESCE({qident("PrimaryPayorName")}, '(unknown)') AS payer,
            COUNT(*) AS claims,
            AVG(DATEDIFF('day', {qident("ApptDate")}, {qident("FirstInsPayment")})) AS avg_days
        FROM {appointment_relation(wh)}
        WHERE {" AND ".join(filters)}
          AND DATEDIFF('day', {qident("ApptDate")}, {qident("FirstInsPayment")}) >= 0
        GROUP BY 1
        ORDER BY avg_days DESC
    """
    src = money_source(wh)
    if src == "none":
        return MetricResult(
            name="days_to_pay",
            as_of=as_of,
            grain_note="Completes with InsPaid>0; exclude negative datediff; min 20 claims",
            value=[],
            details={"source": src},
            unavailable="FirstInsPayment is not in the dump (no CLAIM_TXN and no appointment rollup).",
        )
    frame = wh.fetch_df(sql, params)
    rows = []
    insufficient = []
    for rec in frame.to_dict(orient="records"):
        claims = int(rec["claims"])
        item = {
            "payer": rec["payer"],
            "claims": claims,
            "avg_days": float(rec["avg_days"]) if rec["avg_days"] is not None else None,
        }
        if claims < 20:
            insufficient.append(item)
        else:
            rows.append(item)
    return MetricResult(
        name="days_to_pay",
        as_of=as_of,
        grain_note="Completes with InsPaid>0; exclude negative datediff; min 20 claims",
        value=rows,
        details={"insufficient_under_20_claims": insufficient},
        unavailable=None if rows else "Fewer than 20 Completes with InsPaid>0 and a non-negative FirstInsPayment per payer.",
    )


def payments_total(wh: Warehouse, start: date, end: date, *, company: str | None = None) -> MetricResult:
    """Payments still use TotalPaid."""
    filters = [f'{qident("ApptDate")} >= ?', f'{qident("ApptDate")} <= ?']
    params: list[Any] = [start, end]
    if company:
        filters.append(f'{qident("Company")} = ?')
        params.append(company)
    src = money_source(wh)
    if src == "none":
        return MetricResult(
            name="payments_total",
            as_of=end,
            grain_note="Payments = SUM(TotalPaid) on Completes. InsPaid is shown only as a non-mixed contrast.",
            value=None,
            details={"source": src, "do_not_mix": True},
            unavailable="TotalPaid is not in the dump (no CLAIM_TXN and no appointment rollup).",
        )
    sql = f"""
        SELECT
            SUM(COALESCE({qident("TotalPaid")}, 0)) AS total_paid,
            SUM(COALESCE({qident("InsPaid")}, 0)) AS ins_paid
        FROM {appointment_relation(wh)}
        WHERE {" AND ".join(filters)}
          AND {qident("AppointmentStatus")} = '{STATUS_COMPLETE}'
    """
    row = wh.fetch_one(sql, params)
    return MetricResult(
        name="payments_total",
        as_of=end,
        grain_note="Payments = SUM(TotalPaid) on Completes. InsPaid is shown only as a non-mixed contrast.",
        value=float((row or [0])[0] or 0),
        details={
            "total_paid": float((row or [0])[0] or 0),
            "ins_paid_contrast_only": float((row or [0, 0])[1] or 0),
            "do_not_mix": True,
            "source": src,
        },
    )


def ar_past_30_days(wh: Warehouse, as_of: date, *, company: str | None = None) -> MetricResult:
    """Dollar AR aged > 30 days.

    SUM(InsBalance) on Completes where InsBalance > 0 and ApptDate aged > 30 days,
    split by PrimaryPayorName × LocationName. Insurance only.

    Not billed − paid (there is no charge). Not PatBalance. Not Tableau NET AR.
    Expected-recovery (InsPaid × open-claim count) is a separate question.
    """
    cutoff = as_of - timedelta(days=30)
    filters = [
        f'{qident("AppointmentStatus")} = ?',
        f'{qident("ApptDate")} <= ?',
        f'{qident("InsBalance")} > 0',
        f"LOWER(COALESCE({qident('PrimaryPayorName')}, '')) NOT LIKE '%self%pay%'",
        f"LOWER(COALESCE({qident('PrimaryPayorName')}, '')) NOT LIKE '%self pay%'",
    ]
    params: list[Any] = [STATUS_COMPLETE, cutoff]
    if company:
        filters.append(f'{qident("Company")} = ?')
        params.append(company)
    sql = f"""
        SELECT
            COALESCE({qident("PrimaryPayorName")}, '(unknown)') AS payer,
            COALESCE({qident("LocationName")}, '(unknown)') AS location,
            COUNT(*) AS claims,
            SUM({qident("InsBalance")}) AS ins_balance,
            AVG(DATEDIFF('day', {qident("ApptDate")}, DATE '{as_of.isoformat()}')) AS avg_age_days
        FROM {appointment_relation(wh)}
        WHERE {" AND ".join(filters)}
        GROUP BY 1, 2
        ORDER BY ins_balance DESC
    """
    frame = wh.fetch_df(sql, params)
    rows = [
        {
            "payer": rec["payer"],
            "location": rec["location"],
            "claims": int(rec["claims"]),
            "ins_balance": float(rec["ins_balance"] or 0),
            "avg_age_days": float(rec["avg_age_days"] or 0),
        }
        for rec in frame.to_dict(orient="records")
    ]
    return MetricResult(
        name="ar_past_30_days",
        as_of=as_of,
        grain_note="SUM(InsBalance) on Completes, InsBalance>0, ApptDate aged >30 days, PrimaryPayorName × LocationName; insurance only",
        value=rows,
        details={
            "cutoff": cutoff.isoformat(),
            "uses": "InsBalance",
            "source": money_source(wh),
            "not": [
                "billed − paid",
                "PatBalance",
                "Tableau NET AR",
                "InsPaid × open-claim count (expected-recovery; separate question)",
            ],
        },
        unavailable=(
            "InsBalance is not in the dump (no CLAIM_TXN and no appointment rollup)."
            if money_source(wh) == "none"
            else (None if rows else "No Completes older than 30 days with InsBalance > 0.")
        ),
    )


def completes_in_month(
    wh: Warehouse,
    month_start: date,
    month_end: date,
    *,
    company: str | None = None,
    discipline: str | None = None,
) -> int:
    filters = [
        f'{qident("AppointmentStatus")} = ?',
        f'{qident("ApptDate")} >= ?',
        f'{qident("ApptDate")} <= ?',
    ]
    params: list[Any] = [STATUS_COMPLETE, month_start, month_end]
    if company:
        filters.append(f'{qident("Company")} = ?')
        params.append(company)
    if discipline:
        filters.append(f'{qident("Discipline")} = ?')
        params.append(discipline)
    row = wh.fetch_one(
        f"SELECT COUNT(*) FROM {quoted_table('APPOINTMENT')} WHERE {' AND '.join(filters)}",
        params,
    )
    return int((row or [0])[0] or 0)


def headcount(wh: Warehouse, as_of: date, *, company: str | None = None) -> MetricResult:
    """Unique ProviderId (else ProviderName) with ≥1 Complete in last closed month; one primary location."""
    start, end = last_closed_month(as_of)
    provider = _provider_key_sql()
    filters = [
        f'{qident("AppointmentStatus")} = ?',
        f'{qident("ApptDate")} >= ?',
        f'{qident("ApptDate")} <= ?',
        f"{provider} IS NOT NULL",
    ]
    params: list[Any] = [STATUS_COMPLETE, start, end]
    if company:
        filters.append(f'{qident("Company")} = ?')
        params.append(company)
    sql = f"""
        WITH loc AS (
            SELECT
                {provider} AS provider,
                {qident("LocationName")} AS location,
                {qident("Discipline")} AS discipline,
                COUNT(*) AS completes
            FROM {quoted_table("APPOINTMENT")}
            WHERE {" AND ".join(filters)}
            GROUP BY 1, 2, 3
        ),
        ranked AS (
            SELECT *,
                ROW_NUMBER() OVER (PARTITION BY provider ORDER BY completes DESC, location) AS rn
            FROM loc
        )
        SELECT provider, location, discipline, completes
        FROM ranked
        WHERE rn = 1
        ORDER BY provider
    """
    frame = wh.fetch_df(sql, params)
    rows = [
        {
            "provider": rec["provider"],
            "primary_location": rec["location"],
            "primary_discipline": rec["discipline"],
            "completes": int(rec["completes"]),
        }
        for rec in frame.to_dict(orient="records")
    ]
    return MetricResult(
        name="headcount",
        as_of=as_of,
        grain_note="unique ProviderId (fallback ProviderName) with ≥1 Complete in last closed month; one primary location",
        value=len(rows),
        details={"month_start": start.isoformat(), "month_end": end.isoformat(), "providers": rows},
    )


def caseload_fill(wh: Warehouse, as_of: date, *, company: str | None = None) -> MetricResult:
    """Months from a therapist's first Complete until trailing-4-week Completes reach FTE visits/week.

    Only report when Completes support it. Not a locked Tableau metric — derived.
    Payroll is not consulted.
    """
    from warehouse.staffing import VISITS_PER_WEEK_FTE

    provider = _provider_key_sql()
    filters = [f'{qident("AppointmentStatus")} = ?', f"{provider} IS NOT NULL"]
    params: list[Any] = [STATUS_COMPLETE]
    if company:
        filters.append(f'{qident("Company")} = ?')
        params.append(company)
    visits = wh.fetch_df(
        f"""
        SELECT
            {provider} AS provider,
            {qident("Discipline")} AS discipline,
            {qident("ApptDate")} AS appt_date
        FROM {quoted_table("APPOINTMENT")}
        WHERE {" AND ".join(filters)}
          AND {qident("ApptDate")} <= ?
        ORDER BY 1, 3
        """,
        params + [as_of],
    )
    if visits.empty:
        return MetricResult(
            name="caseload_fill",
            as_of=as_of,
            grain_note="derived from Completes only",
            value=[],
            unavailable="No Completes with ProviderId or ProviderName. Cannot measure caseload fill.",
        )
    filled = []
    insufficient = []
    for therapist, grp in visits.groupby("provider"):
        grp = grp.sort_values("appt_date")
        first = grp["appt_date"].iloc[0]
        if hasattr(first, "to_pydatetime"):
            first_d = first.to_pydatetime().date()
        elif hasattr(first, "date") and not isinstance(first, date):
            first_d = first.date()
        else:
            first_d = first
        disc = str(grp["discipline"].mode().iloc[0]) if not grp["discipline"].mode().empty else "OT"
        target = VISITS_PER_WEEK_FTE.get(disc, 35)
        dates = []
        for raw in grp["appt_date"]:
            if hasattr(raw, "to_pydatetime"):
                dates.append(raw.to_pydatetime().date())
            elif hasattr(raw, "date") and not isinstance(raw, date):
                dates.append(raw.date())
            else:
                dates.append(raw)
        reached = None
        cursor = first_d + timedelta(days=6)
        last = dates[-1]
        while cursor <= last:
            window_start = cursor - timedelta(days=6)
            n = sum(1 for d in dates if window_start <= d <= cursor)
            if n >= target:
                months = (cursor.year - first_d.year) * 12 + (cursor.month - first_d.month)
                reached = {
                    "therapist": therapist,
                    "discipline": disc,
                    "months_to_fill": months,
                    "target_weekly": target,
                    "trailing_7day_completes": n,
                }
                break
            cursor += timedelta(days=1)
        if reached:
            filled.append(reached)
        else:
            insufficient.append({"therapist": therapist, "discipline": disc, "reason": "never reached weekly complete target in the dump"})
    return MetricResult(
        name="caseload_fill",
        as_of=as_of,
        grain_note="first Complete to first trailing-7-day window at OT/PT 35 or ST 70 Completes/week",
        value=filled,
        details={"insufficient": insufficient, "n_filled": len(filled)},
        unavailable=None if filled else "Completes do not show any clinician reaching the staffing weekly-visit target.",
    )


def payroll_present(wh: Warehouse) -> bool:
    """Payroll is not a PREP object in this warehouse. Always False unless a dump mapped it — it cannot."""
    return False


def snapshot(wh: Warehouse, as_of: date, *, company: str | None = None) -> dict[str, Any]:
    start, end = last_closed_month(as_of)
    cancel = cancelation_rate(wh, as_of, months=3, company=company)
    return {
        "as_of": as_of.isoformat(),
        "last_closed_month": {"start": start.isoformat(), "end": end.isoformat()},
        "cancelation_rate": cancel.to_dict(),
        "active_book": active_book(wh, start, end, company=company).to_dict(),
        "churn": churn(wh, as_of, company=company).to_dict(),
        "referrals": referrals(wh, as_of, months=1, company=company).to_dict(),
        "referral_volume_change": referral_volume_change(wh, as_of, company=company).to_dict(),
        "ar_past_30_days": ar_past_30_days(wh, as_of, company=company).to_dict(),
        "avg_collections": avg_collections(wh, as_of, company=company).to_dict(),
        "avg_paid": avg_paid(wh, as_of, company=company).to_dict(),
        "days_to_pay": days_to_pay(wh, as_of, company=company).to_dict(),
        "headcount": headcount(wh, as_of, company=company).to_dict(),
        "early_quit_watch": early_quit_watch(wh, as_of, company=company).to_dict(),
    }
