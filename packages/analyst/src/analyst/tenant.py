"""Local tenant workspace: DuckDB file + ingest manifest + alert config."""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any

from warehouse.store import Warehouse

DEFAULT_TENANT = "example-clinic"


def data_dir() -> Path:
    return Path(os.environ.get("CLINIC_ANALYST_DATA_DIR", "./data")).resolve()


def tenant_dir(tenant_id: str) -> Path:
    path = data_dir() / "tenants" / tenant_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def warehouse_path(tenant_id: str) -> Path:
    """DuckDB path. WAREHOUSE_PATH (Render: /data/clinic.duckdb) wins when set."""
    env = os.environ.get("WAREHOUSE_PATH")
    if env:
        return Path(env)
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
