"""Tenant-scoped CSV exports. Operational fields only. No patient PHI."""

from __future__ import annotations

import csv
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from analyst.tenant import tenant_dir
from warehouse.store import json_default

EXPORT_TTL_SECONDS = 24 * 3600
EXPORT_ROW_CAP = 10_000
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
_PHI_HEADERS = frozenset(
    {
        "firstname",
        "lastname",
        "first name",
        "last name",
        "name",
        "address",
        "street",
        "city",
        "zip",
        "zipcode",
        "phone",
        "email",
        "dob",
        "dateofbirth",
        "mrn",
        "memberid",
        "member_id",
    }
)


def _exports_dir(tenant_id: str) -> Path:
    path = tenant_dir(tenant_id) / "exports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cleanup_exports(tenant_id: str) -> None:
    folder = tenant_dir(tenant_id) / "exports"
    if not folder.exists():
        return
    now = time.time()
    for path in folder.iterdir():
        try:
            if now - path.stat().st_mtime > EXPORT_TTL_SECONDS:
                path.unlink()
        except OSError:
            continue


def cleanup_all_exports() -> None:
    root = tenant_dir("example-clinic").parent
    if not root.exists():
        return
    for child in root.iterdir():
        if child.is_dir():
            cleanup_exports(child.name)


def _blocked_header(name: str) -> bool:
    compact = re.sub(r"[^a-z0-9]+", "", str(name).strip().lower())
    norm = re.sub(r"[^a-z0-9]+", " ", str(name).strip().lower()).strip()
    return compact in _PHI_HEADERS or norm in _PHI_HEADERS


def _sanitize_label(label: str) -> str:
    cleaned = _SAFE_NAME.sub("_", (label or "export").strip())[:40].strip("._-")
    return cleaned or "export"


def write_export(
    tenant_id: str,
    *,
    rows: list[dict[str, Any]],
    columns: list[str] | None = None,
    filename: str = "export",
) -> dict[str, Any]:
    cleanup_exports(tenant_id)
    if columns:
        cols = [c for c in columns if not _blocked_header(c)]
    elif rows:
        cols = [c for c in rows[0].keys() if not _blocked_header(str(c))]
    else:
        cols = []
    capped = rows[:EXPORT_ROW_CAP]
    export_id = uuid.uuid4().hex
    label = _sanitize_label(filename)
    csv_name = f"{export_id}.csv"
    folder = _exports_dir(tenant_id)
    csv_path = folder / csv_name
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        for row in capped:
            writer.writerow({c: "" if row.get(c) is None else row.get(c) for c in cols})
    meta = {
        "tenant_id": tenant_id,
        "export_id": export_id,
        "filename": f"{label}.csv",
        "columns": cols,
        "row_count": len(capped),
        "capped_at": EXPORT_ROW_CAP,
    }
    (folder / f"{export_id}.json").write_text(json.dumps(meta, indent=2, default=json_default) + "\n")
    return {
        "export_id": export_id,
        "filename": f"{label}.csv",
        "url": f"/api/exports/{export_id}.csv",
        "row_count": len(capped),
        "columns": cols,
    }


def read_export(tenant_id: str, export_id: str) -> tuple[Path, dict[str, Any]] | None:
    cleanup_exports(tenant_id)
    if not re.fullmatch(r"[a-f0-9]{32}", export_id):
        return None
    folder = _exports_dir(tenant_id)
    meta_path = folder / f"{export_id}.json"
    csv_path = folder / f"{export_id}.csv"
    if not meta_path.exists() or not csv_path.exists():
        return None
    try:
        meta = json.loads(meta_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if meta.get("tenant_id") != tenant_id:
        return None
    return csv_path, meta
