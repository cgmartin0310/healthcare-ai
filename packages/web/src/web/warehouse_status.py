"""Tenant warehouse health: last updated, counts, mapped coverage."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from analyst.tenant import STATUS_FILENAME, tenant_dir, warehouse_path
from warehouse.schema import PREP_TABLES, STATUS_COMPLETE, qident, quoted_table
from warehouse.store import Warehouse

SKIP_COVERAGE = frozenset({"DOB"})


def _stamp(tenant_id: str) -> dict[str, Any]:
    path = tenant_dir(tenant_id) / STATUS_FILENAME
    if path.exists():
        try:
            payload = json.loads(path.read_text())
            if payload.get("last_updated"):
                return {
                    "last_updated": str(payload["last_updated"]),
                    "last_updated_source": str(payload.get("source") or "recorded"),
                }
        except (OSError, json.JSONDecodeError):
            pass
    duck = warehouse_path(tenant_id)
    if duck.exists():
        mtime = datetime.fromtimestamp(duck.stat().st_mtime, tz=timezone.utc).isoformat()
        return {"last_updated": mtime, "last_updated_source": "duckdb_mtime"}
    return {"last_updated": None, "last_updated_source": "none"}


def _non_null(wh: Warehouse, table: str, column: str) -> int:
    row = wh.fetch_one(
        f"""
        SELECT COUNT(*) FROM {quoted_table(table)}
        WHERE {qident(column)} IS NOT NULL
          AND TRIM(CAST({qident(column)} AS VARCHAR)) NOT IN ('', 'nan', 'None', '<NA>', '<na>')
        """
    )
    return int((row or [0])[0] or 0)


def _table_coverage(wh: Warehouse, table: str) -> dict[str, Any]:
    spec = PREP_TABLES[table]
    n_rows = wh.count(table)
    columns = []
    empty_required: list[str] = []
    filled = 0
    considered = 0
    for col in spec.columns:
        if col.name in SKIP_COVERAGE:
            continue
        considered += 1
        nonempty = _non_null(wh, table, col.name) if n_rows else 0
        if nonempty > 0:
            filled += 1
        elif col.required:
            empty_required.append(col.name)
        columns.append(
            {
                "name": col.name,
                "required": col.required,
                "non_null": nonempty,
                "rows": n_rows,
            }
        )
    pct = round(100.0 * filled / considered, 1) if considered else 0.0
    return {
        "rows": n_rows,
        "filled_columns": filled,
        "columns": considered,
        "pct": pct,
        "empty_required": empty_required,
        "fields": columns,
    }


def warehouse_status(tenant_id: str) -> dict[str, Any]:
    stamp = _stamp(tenant_id)
    with Warehouse(warehouse_path(tenant_id)) as wh:
        appt = wh.count("APPOINTMENT")
        completes = 0
        if appt:
            row = wh.fetch_one(
                f"""
                SELECT COUNT(*) FROM {quoted_table("APPOINTMENT")}
                WHERE {qident("AppointmentStatus")} = ?
                """,
                [STATUS_COMPLETE],
            )
            completes = int((row or [0])[0] or 0)
        tables = {name: _table_coverage(wh, name) for name in PREP_TABLES}
    filled = sum(t["filled_columns"] for t in tables.values())
    considered = sum(t["columns"] for t in tables.values())
    empty_required = {name: t["empty_required"] for name, t in tables.items() if t["empty_required"]}
    return {
        "tenant_id": tenant_id,
        "last_updated": stamp["last_updated"],
        "last_updated_source": stamp["last_updated_source"],
        "counts": {
            "APPOINTMENT": tables["APPOINTMENT"]["rows"],
            "PATIENT": tables["PATIENT"]["rows"],
            "REFERRAL": tables["REFERRAL"]["rows"],
            "CLAIM_TXN": tables["CLAIM_TXN"]["rows"],
            "Completes": completes,
        },
        "coverage": {
            "overall": {
                "filled_columns": filled,
                "columns": considered,
                "pct": round(100.0 * filled / considered, 1) if considered else 0.0,
            },
            "tables": {name: {k: v for k, v in spec.items() if k != "fields"} for name, spec in tables.items()},
            "empty_required": empty_required,
        },
        "empty": tables["APPOINTMENT"]["rows"] == 0,
        "note": "Warehouse is this tenant only. Closed-month results are the truth grain.",
    }
