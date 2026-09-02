"""Local tenant workspace: DuckDB file + ingest manifest + alert config."""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any

from warehouse.store import Warehouse

DEFAULT_TENANT = "example-clinic"


def sanitize_tenant_id(tenant_id: str) -> str:
    cleaned = "".join(ch for ch in tenant_id.lower() if ch.isalnum() or ch in "-_")
    if not cleaned or cleaned in {".", ".."}:
        raise ValueError("invalid tenant_id")
    return cleaned


def data_dir() -> Path:
    raw = os.environ.get("CLINIC_ANALYST_DATA_DIR", "./data")
    path = Path(raw).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def tenant_dir(tenant_id: str) -> Path:
    path = data_dir() / "tenants" / sanitize_tenant_id(tenant_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def warehouse_path(tenant_id: str) -> Path:
    """One DuckDB file per clinic: {data_dir}/tenants/{tenant_id}/warehouse.duckdb."""
    return tenant_dir(tenant_id) / "warehouse.duckdb"


def open_warehouse(tenant_id: str) -> Warehouse:
    return Warehouse(warehouse_path(tenant_id))


def parse_as_of(value: str | None) -> date:
    raw = value or os.environ.get("CLINIC_ANALYST_AS_OF")
    if raw:
        return date.fromisoformat(raw)
    return date.today()


def write_tenant_config(tenant_id: str, payload: dict[str, Any]) -> Path:
    path = tenant_dir(tenant_id) / "tenant.json"
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path
