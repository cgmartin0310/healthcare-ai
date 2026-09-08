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
    completes_by_provider,
    days_to_pay,
    headcount,
    referrals,
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

_COMPANY_PARAM = {
    "type": "string",
    "description": (
        "Optional APPOINTMENT.Company value already stored in THIS tenant warehouse. "
        "Do not pass tenant_id or the UI clinic label unless that exact string is Company. "
        "Omit to query the whole tenant — the warehouse is already isolated."
    ),
}

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
- Completes by clinician = Completes (AppointmentStatus='Complete') ranked by clinician over the last N closed months (default 1). Display ProviderName. ProviderId is a join key only. Not payroll.
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
                    "company": _COMPANY_PARAM,
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
            "parameters": {"type": "object", "properties": {"company": _COMPANY_PARAM}},
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
                    "company": _COMPANY_PARAM,
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "referral_volume_change",
            "description": "Last closed month vs prior closed month referral volume, including by source.",
            "parameters": {"type": "object", "properties": {"company": _COMPANY_PARAM}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ar_past_30_days",
            "description": "Dollar AR aged > 30 days: SUM(InsBalance) on Completes, InsBalance>0, by PrimaryPayorName × LocationName. Insurance only. Use this for AR / aging / Site B collections questions.",
            "parameters": {"type": "object", "properties": {"company": _COMPANY_PARAM}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "avg_paid",
            "description": "Avg InsPaid>0 by payer, last 3 months through as-of.",
            "parameters": {"type": "object", "properties": {"company": _COMPANY_PARAM}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "avg_collections",
            "description": "Avg InsPaid including zeros/partials, 60-day lag then 3 months back.",
            "parameters": {"type": "object", "properties": {"company": _COMPANY_PARAM}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "days_to_pay",
            "description": "Avg days ApptDate to FirstInsPayment on Completes with InsPaid>0, min 20 claims.",
            "parameters": {"type": "object", "properties": {"company": _COMPANY_PARAM}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "staffing_forecast",
            "description": "Clinic×discipline FTE demand from last closed Completes + refs. Not a live schedule.",
            "parameters": {"type": "object", "properties": {"company": _COMPANY_PARAM}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "caseload_fill",
            "description": "Months for a provider to reach weekly Complete target from Completes only. Display ProviderName, not hashed ProviderId.",
            "parameters": {"type": "object", "properties": {"company": _COMPANY_PARAM}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "headcount",
            "description": "Unique clinicians with ≥1 Complete in last closed month, including Completes and ProviderName. ProviderId is a join key only.",
            "parameters": {"type": "object", "properties": {"company": _COMPANY_PARAM}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "completes_by_provider",
            "description": (
                "Completes (AppointmentStatus='Complete') per clinician, ranked. "
                "Use for most productive / most Completes / busiest therapist / closed appointments by provider. "
                "Display provider_name. months = last N closed months (default 1). Not payroll."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "company": _COMPANY_PARAM,
                    "months": {"type": "integer", "description": "Closed-month window, default 1"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "snapshot",
            "description": "Closed-month metric snapshot. Call only if the user asked for a snapshot. Do not use this to answer a single ranking question.",
            "parameters": {"type": "object", "properties": {"company": _COMPANY_PARAM}},
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
            "name": "export_csv",
            "description": (
                "Write a tenant-scoped CSV of query/tool results and return a download URL. "
                "Use after completes_by_provider, ar_past_30_days, or referrals when the user "
                "asks to download/export a CSV. For Excel/xlsx/spreadsheet, use export_table "
                "with format=xlsx. No patient names/addresses. Display ProviderName."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "Short file label, e.g. completes_by_therapist"},
                    "source": {
                        "type": "string",
                        "description": "completes_by_provider | ar_past_30_days | referrals | rows | sql",
                    },
                    "rows": {"type": "array", "description": "List of objects to write when source=rows"},
                    "columns": {"type": "array", "items": {"type": "string"}},
                    "sql": {"type": "string", "description": "Read-only SELECT when source=sql"},
                    "months": {"type": "integer", "description": "Closed-month window when source is a locked metric"},
                    "company": _COMPANY_PARAM,
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "export_table",
            "description": (
                "Write a tenant-scoped CSV or Excel (.xlsx) of query/tool results and return a "
                "download URL. Use format=xlsx when they ask for excel/xlsx/spreadsheet. "
                "Pass source=completes_by_provider and months=N for Completes by clinician. "
                "Always follow with a short prose reply that includes the raw url."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "Short file label"},
                    "format": {"type": "string", "description": "csv or xlsx"},
                    "source": {
                        "type": "string",
                        "description": "completes_by_provider | ar_past_30_days | referrals | rows | sql",
                    },
                    "rows": {"type": "array", "description": "List of objects when source=rows"},
                    "columns": {"type": "array", "items": {"type": "string"}},
                    "sql": {"type": "string"},
                    "months": {"type": "integer", "description": "Closed-month window for completes_by_provider"},
                    "company": _COMPANY_PARAM,
                },
            },
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


def warehouse_select(wh: Warehouse, sql: str, *, row_cap: int | None = None) -> dict[str, Any]:
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
    cap = int(row_cap or SELECT_ROW_CAP)
    wrapped = f"SELECT * FROM ({raw}) AS _tool_q LIMIT {cap}"
    try:
        frame = wh.fetch_df(wrapped)
    except Exception as exc:
        return {"error": f"Query failed: {exc}"}
    rows = json.loads(frame.to_json(orient="records", date_format="iso"))
    return {"rows": rows, "row_count": len(rows), "capped_at": cap}


def known_appointment_companies(warehouse: Warehouse) -> set[str]:
    """Company values actually stored on APPOINTMENT in this tenant warehouse."""
    if warehouse.count("APPOINTMENT") == 0:
        return set()
    try:
        frame = warehouse.fetch_df(f'SELECT DISTINCT "Company" AS company FROM "APPOINTMENT"')
    except Exception:
        return set()
    out: set[str] = set()
    for raw in frame["company"].tolist():
        if raw is None:
            continue
        text = str(raw).strip()
        if text and text.lower() not in {"nan", "none", "<na>"}:
            out.add(text)
    return out


def resolve_company_filter(warehouse: Warehouse, requested: str | None) -> str | None:
    """Drop a model-supplied company that would zero this tenant.

    Warehouse is already per-tenant. Only keep company when it equals an
    APPOINTMENT.Company value in THIS warehouse. Default: no company filter.
    """
    if requested is None:
        return None
    text = str(requested).strip()
    if not text:
        return None
    known = known_appointment_companies(warehouse)
    return text if text in known else None


def rows_for_export(
    source: str,
    *,
    warehouse: Warehouse,
    as_of,
    company: str | None,
    months: int = 1,
) -> tuple[list[dict[str, Any]], list[str], str]:
    """Build export rows from a locked metric. No invented numbers."""
    src = (source or "").strip().lower()
    if src in {"completes_by_provider", "completes", "therapist", "clinician"}:
        result = completes_by_provider(warehouse, as_of, company=company, months=months)
        rows = []
        for rec in result.value or []:
            rows.append(
                {
                    "provider_name": rec.get("provider_name") or "",
                    "completes": rec.get("completes"),
                    "patients": rec.get("patients"),
                    "discipline": rec.get("discipline"),
                }
            )
        return rows, ["provider_name", "completes", "patients", "discipline"], "completes_by_therapist"
    if src in {"ar_past_30_days", "ar", "aging"}:
        result = ar_past_30_days(warehouse, as_of, company=company)
        rows = list(result.value or [])
        cols = ["payer", "location", "claims", "ins_balance", "avg_age_days"]
        return rows, cols, "ar_past_30"
    if src in {"referrals", "referral"}:
        from warehouse.metrics import referrals

        refs = referrals(warehouse, as_of, months=1, company=company)
        rows = list((refs.details or {}).get("by_source") or [])
        return rows, ["source", "referrals", "converted", "conversion"], "referrals_last_month"
    return [], [], src or "export"


def run_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    warehouse: Warehouse,
    as_of,
    company: str | None,
    alerts_fn,
    tenant_id: str | None = None,
) -> tuple[Any, str]:
    """Execute one locked tool. Returns (payload, error_or_empty)."""
    args = dict(arguments or {})
    args.pop("as_of", None)
    requested = args.get("company")
    if requested is None or str(requested).strip() == "":
        requested = company
    args["company"] = resolve_company_filter(warehouse, requested)
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
        if name == "headcount":
            return headcount(warehouse, as_of, company=args.get("company")).to_dict(), ""
        if name == "completes_by_provider":
            months = int(args.get("months") or 1)
            return completes_by_provider(
                warehouse, as_of, company=args.get("company"), months=months
            ).to_dict(), ""
        if name == "snapshot":
            return snapshot(warehouse, as_of, company=args.get("company")), ""
        if name == "alerts":
            return alerts_fn(), ""
        if name == "warehouse_select":
            return warehouse_select(warehouse, str(args.get("sql") or "")), ""
        if name in {"export_csv", "export_table"}:
            from analyst.exports import EXPORT_ROW_CAP, write_export

            if not tenant_id:
                return {"error": f"{name} requires a tenant."}, f"{name} requires a tenant."
            source = str(args.get("source") or "rows")
            filename = str(args.get("filename") or source or "export")
            fmt = str(args.get("format") or ("csv" if name == "export_csv" else "xlsx"))
            months = int(args.get("months") or 1)
            rows: list[dict[str, Any]] = []
            columns = [str(c) for c in (args.get("columns") or [])]
            if source == "sql" or args.get("sql"):
                selected = warehouse_select(
                    warehouse, str(args.get("sql") or ""), row_cap=EXPORT_ROW_CAP
                )
                if selected.get("error"):
                    return selected, str(selected["error"])
                rows = list(selected.get("rows") or [])
            elif source == "rows" and args.get("rows"):
                raw_rows = args.get("rows") or []
                rows = [r for r in raw_rows if isinstance(r, dict)]
            else:
                rows, default_cols, default_name = rows_for_export(
                    source,
                    warehouse=warehouse,
                    as_of=as_of,
                    company=args.get("company"),
                    months=months,
                )
                if not columns:
                    columns = default_cols
                if filename in {"", "rows", "sql"}:
                    filename = default_name
            if not rows:
                return {"error": "No rows to export."}, "No rows to export."
            written = write_export(
                tenant_id, rows=rows, columns=columns or None, filename=filename, fmt=fmt
            )
            return written, ""
    except Exception as exc:
        return {"error": str(exc)}, str(exc)
    return {"error": f"Unknown tool: {name}"}, f"Unknown tool: {name}"


def dump_tool_result(payload: Any) -> str:
    return _dump(payload)
