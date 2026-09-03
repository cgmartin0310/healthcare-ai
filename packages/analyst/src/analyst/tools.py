"""Locked metric tools the model may call. No free DML. No invented numbers."""

from __future__ import annotations

import json
import re
from typing import Any

from warehouse.metrics import (
    ar_past_30_days,
    avg_collections,
    avg_paid,
    cancelation_rate,
    caseload_fill,
    churn,
    days_to_pay,
    snapshot,
)
from warehouse.staffing import forecast
from warehouse.store import Warehouse, json_default

ALLOWED_TABLES = frozenset({"APPOINTMENT", "PATIENT", "REFERRAL", "CLAIM_TXN"})
SELECT_ROW_CAP = 50
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|attach|copy|pragma|export|install|"
    r"load|replace|merge|truncate|grant|revoke|vacuum|checkpoint)\b",
    re.I,
)
_TABLE_REF = re.compile(r'\b(?:from|join)\s+"?([A-Za-z_][A-Za-z0-9_]*)"?', re.I)

LOCKED_DEFS = """
Locked metric definitions (do not redefine; do not invent a lookalike):
- Completes = AppointmentStatus='Complete'
- Cancelation % = (Cancelled + No Show) / (Complete + Cancelled + No Show). Pending/Waiting out. Closed months.
- Active book = ≥1 Complete in the calendar month. Not PATIENT.PatientActive.
- Churn grain = Company × Discipline × PatientId. Closed months only. Drop first-DOS on/after prior month start. Churned = prior active, not current active.
- Early quit watch = cancelation > 30% under tenure bar: PT / adult OT-ST < 3 months; child OT-ST < 6. Child vs adult from PATIENT.AgeBand at import (from DOB; DOB is not stored; child = age < 18). Not AgeGroup.
- Referrals = COUNT REFERRAL rows. Conversion = Completed?=1 / referrals. EVAL notes are not conversion.
- Payments = TotalPaid. AR/collections = InsPaid except dollar AR aged > 30 = SUM(InsBalance) on Completes, InsBalance>0, by PrimaryPayorName × LocationName, insurance only. Not billed−paid, not PatBalance, not Tableau NET AR.
- Avg Collections = InsPaid by payer, DOS=ApptDate, 60-day lag then 3 months back, includes zeros/partials.
- Avg Paid = InsPaid>0 only, last 3 months through as-of.
- Days to pay = DATEDIFF(day, ApptDate, FirstInsPayment) on Completes with InsPaid>0, exclude negatives, min 20 claims.
- When CLAIM_TXN (claim ledger: charges / payments / allowances / adjustments / refunds) is present, derive TotalPaid / InsPaid / InsBalance / FirstInsPayment from it. Else appointment rollups. If neither, say the data is not in the dump. There is no separate CHARGES table.
- Headcount = unique ProviderId (fallback ProviderName) with ≥1 Complete in last closed month.
- Payroll is not a PREP object. Do not invent profitability.
""".strip()

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "cancelation_rate",
            "description": "Locked cancelation % over the last N closed months. Prefer this for cancel / no-show questions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "months": {"type": "integer", "description": "Closed-month window, default 3"},
                    "company": {"type": "string"},
                    "location": {"type": "string"},
                    "discipline": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "churn",
            "description": "Locked churn: Company × Discipline × PatientId, closed months, first-DOS drop.",
            "parameters": {"type": "object", "properties": {"company": {"type": "string"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "referrals",
            "description": "Referral counts and conversion (Completed?=1 / COUNT rows) for the last N closed months.",
            "parameters": {
                "type": "object",
                "properties": {
                    "months": {"type": "integer", "description": "Default 1"},
                    "company": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "referral_volume_change",
            "description": "Last closed month vs prior closed month referral volume, including by source.",
            "parameters": {"type": "object", "properties": {"company": {"type": "string"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ar_past_30_days",
            "description": "Dollar AR aged > 30 days: SUM(InsBalance) on Completes, InsBalance>0, by PrimaryPayorName × LocationName. Insurance only. Use this for AR / aging / Site B collections questions.",
            "parameters": {"type": "object", "properties": {"company": {"type": "string"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "avg_paid",
            "description": "Avg InsPaid>0 by payer, last 3 months through as-of.",
            "parameters": {"type": "object", "properties": {"company": {"type": "string"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "avg_collections",
            "description": "Avg InsPaid including zeros/partials, 60-day lag then 3 months back.",
            "parameters": {"type": "object", "properties": {"company": {"type": "string"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "days_to_pay",
            "description": "Avg days ApptDate to FirstInsPayment on Completes with InsPaid>0, min 20 claims.",
            "parameters": {"type": "object", "properties": {"company": {"type": "string"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "staffing_forecast",
            "description": "Clinic×discipline FTE demand from last closed Completes + refs. Not a live schedule.",
            "parameters": {"type": "object", "properties": {"company": {"type": "string"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "caseload_fill",
            "description": "Months for a provider to reach weekly Complete target from Completes only.",
            "parameters": {"type": "object", "properties": {"company": {"type": "string"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "snapshot",
            "description": "Closed-month metric snapshot (cancel, churn, referrals, AR, collections, headcount, early-quit).",
            "parameters": {"type": "object", "properties": {"company": {"type": "string"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "alerts",
            "description": "Evaluate wired alerts: cancelation over 25%, referral −10%, early-quit watch.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "warehouse_select",
            "description": "Read-only SELECT on this tenant DuckDB only. Tables: APPOINTMENT, PATIENT, REFERRAL, CLAIM_TXN. Max 50 rows. Prefer locked metric tools when the question matches a locked definition. No DML. Ids only — there are no patient names.",
            "parameters": {
                "type": "object",
                "properties": {"sql": {"type": "string", "description": "A single SELECT or WITH … SELECT"}},
                "required": ["sql"],
            },
        },
    },
]


def _dump(payload: Any) -> str:
    return json.dumps(payload, default=json_default, indent=None)[:12_000]


def warehouse_select(wh: Warehouse, sql: str) -> dict[str, Any]:
    raw = (sql or "").strip().rstrip(";")
    if not raw:
        return {"error": "Empty SQL."}
    if ";" in raw:
        return {"error": "One statement only."}
    if _FORBIDDEN.search(raw):
        return {"error": "Only SELECT is allowed on this tenant warehouse."}
    head = raw.lstrip().lower()
    if not (head.startswith("select") or head.startswith("with")):
        return {"error": "Only SELECT / WITH … SELECT is allowed."}
    tables = {t.upper() for t in _TABLE_REF.findall(raw)}
    extra = tables - ALLOWED_TABLES
    if extra:
        return {"error": f"Tables not allowed: {sorted(extra)}. Use {sorted(ALLOWED_TABLES)}."}
    if not tables:
        return {"error": "SELECT must reference APPOINTMENT, PATIENT, REFERRAL, or CLAIM_TXN."}
    wrapped = f"SELECT * FROM ({raw}) AS _tool_q LIMIT {SELECT_ROW_CAP}"
    try:
        frame = wh.fetch_df(wrapped)
    except Exception as exc:
        return {"error": f"Query failed: {exc}"}
    rows = json.loads(frame.to_json(orient="records", date_format="iso"))
    return {"rows": rows, "row_count": len(rows), "capped_at": SELECT_ROW_CAP}


def run_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    warehouse: Warehouse,
    as_of,
    company: str | None,
    alerts_fn,
) -> tuple[Any, str]:
    """Execute one locked tool. Returns (payload, error_or_empty)."""
    args = dict(arguments or {})
    args.pop("as_of", None)
    if "company" not in args and company:
        args["company"] = company
    try:
        if name == "cancelation_rate":
            months = int(args.get("months") or 3)
            result = cancelation_rate(
                warehouse,
                as_of,
                months=months,
                company=args.get("company"),
                location=args.get("location"),
                discipline=args.get("discipline"),
            )
            return result.to_dict(), ""
        if name == "churn":
            return churn(warehouse, as_of, company=args.get("company")).to_dict(), ""
        if name == "referrals":
            months = int(args.get("months") or 1)
            return referrals(warehouse, as_of, months=months, company=args.get("company")).to_dict(), ""
        if name == "referral_volume_change":
            from warehouse.metrics import referral_volume_change

            return referral_volume_change(warehouse, as_of, company=args.get("company")).to_dict(), ""
        if name == "ar_past_30_days":
            return ar_past_30_days(warehouse, as_of, company=args.get("company")).to_dict(), ""
        if name == "avg_paid":
            return avg_paid(warehouse, as_of, company=args.get("company")).to_dict(), ""
        if name == "avg_collections":
            return avg_collections(warehouse, as_of, company=args.get("company")).to_dict(), ""
        if name == "days_to_pay":
            return days_to_pay(warehouse, as_of, company=args.get("company")).to_dict(), ""
        if name == "staffing_forecast":
            return forecast(warehouse, as_of, company=args.get("company")), ""
        if name == "caseload_fill":
            return caseload_fill(warehouse, as_of, company=args.get("company")).to_dict(), ""
        if name == "snapshot":
            return snapshot(warehouse, as_of, company=args.get("company")), ""
        if name == "alerts":
            return alerts_fn(), ""
        if name == "warehouse_select":
            return warehouse_select(warehouse, str(args.get("sql") or "")), ""
    except Exception as exc:
        return {"error": str(exc)}, str(exc)
    return {"error": f"Unknown tool: {name}"}, f"Unknown tool: {name}"


def dump_tool_result(payload: Any) -> str:
    return _dump(payload)
