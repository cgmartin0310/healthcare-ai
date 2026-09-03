"""Value normalization onto PREP canonicals. Mapping is column-level; this is cell-level."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pandas as pd

from warehouse.schema import (
    BOOM_CLINIC_ID_TO_COMPANY,
    DISCIPLINE_ALIASES,
    STATUS_ALIASES,
    TXN_TYPE_ALIASES,
)


def _norm_key(value: Any) -> str:
    return " ".join(str(value).strip().lower().replace("_", " ").replace("-", " ").split())


def normalize_status(value: Any) -> Any:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    return STATUS_ALIASES.get(_norm_key(value), str(value).strip())


def normalize_discipline(value: Any) -> Any:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    return DISCIPLINE_ALIASES.get(_norm_key(value), str(value).strip().upper())


def normalize_completed(value: Any) -> Any:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, bool):
        return 1 if value else 0
    key = _norm_key(value)
    if key in {"1", "true", "yes", "y", "converted", "complete", "completed"}:
        return 1
    if key in {"0", "false", "no", "n", "open", "pending"}:
        return 0
    try:
        return 1 if int(float(value)) == 1 else 0
    except (TypeError, ValueError):
        return None


def normalize_company(value: Any) -> Any:
    """Map Boom ClinicId 8/9/22/24 when the source cell is that id. Demo files use names."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        as_int = int(float(value))
        if as_int in BOOM_CLINIC_ID_TO_COMPANY:
            return BOOM_CLINIC_ID_TO_COMPANY[as_int]
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def normalize_bool(value: Any) -> Any:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, bool):
        return value
    key = _norm_key(value)
    if key in {"1", "true", "yes", "y"}:
        return True
    if key in {"0", "false", "no", "n"}:
        return False
    return None


def normalize_date(value: Any) -> Any:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    ts = pd.to_datetime(value, errors="coerce")
    if pd.isna(ts):
        return None
    return ts.date()


def normalize_timestamp(value: Any) -> Any:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    ts = pd.to_datetime(value, errors="coerce")
    if pd.isna(ts):
        return None
    return ts.to_pydatetime()


def normalize_txn_type(value: Any) -> Any:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    return TXN_TYPE_ALIASES.get(_norm_key(value), str(value).strip().lower())


def normalize_number(value: Any) -> Any:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, str):
        value = value.replace("$", "").replace(",", "").strip()
        if value == "":
            return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


NORMALIZERS = {
    "AppointmentStatus": normalize_status,
    "Discipline": normalize_discipline,
    "Completed?": normalize_completed,
    "Company": normalize_company,
    "Telehealth": normalize_bool,
    "PatientActive": normalize_bool,
    "ApptDate": normalize_date,
    "FirstInsPayment": normalize_date,
    "DOB": normalize_date,
    "EvalDate": normalize_date,
    "DOS": normalize_date,
    "DateTimeCreated": normalize_timestamp,
    "InsPaid": normalize_number,
    "InsBalance": normalize_number,
    "TotalPaid": normalize_number,
    "Amount": normalize_number,
    "PostedDate": normalize_date,
    "TxnType": normalize_txn_type,
    "Payer": lambda v: None if v is None or (isinstance(v, float) and pd.isna(v)) else str(v).strip(),
}
