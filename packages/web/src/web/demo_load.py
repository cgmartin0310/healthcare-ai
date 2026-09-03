"""Load synthetic demo files into a warehouse.

``seed_demo()`` uses this only for the ``example-clinic`` tenant when
APPOINTMENT is empty. ``POST /api/demo`` reuses the same file list so a
user can still load the labeled dump into their own tenant on purpose.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from integration_engine.load import load_mapped_file
from integration_engine.mapper import confirm_mapping, propose_mapping
from warehouse.metrics import NO_PROVIDER_CASELOAD, caseload_fill
from warehouse.store import Warehouse

DEMO_TENANT_ID = "example-clinic"
DEMO_TENANT_COMPANY = "Example Clinic (synthetic)"
# Synthetic files are generated through 2026-08; this as-of keeps August closed.
DEMO_DEFAULT_AS_OF = "2026-09-02"


def fixtures_dir() -> Path:
    cwd = Path.cwd() / "fixtures" / "synthetic"
    if cwd.exists():
        return cwd
    return Path(__file__).resolve().parents[4] / "fixtures" / "synthetic"


def demo_file_jobs() -> list[tuple[str, Path, str]]:
    root = fixtures_dir()
    return [
        ("APPOINTMENT", root / "layout_a" / "SYNTHETIC_EXAMPLE_appointments.csv", "replace"),
        ("REFERRAL", root / "layout_a" / "SYNTHETIC_EXAMPLE_referrals.csv", "replace"),
        ("PATIENT", root / "layout_a" / "SYNTHETIC_EXAMPLE_patients.csv", "replace"),
        ("CLAIM_TXN", root / "layout_payments" / "SYNTHETIC_EXAMPLE_transactions.csv", "replace"),
    ]


def load_synthetic_demo(
    wh: Warehouse,
    *,
    tenant_id: str,
    tenant_company: str,
) -> list[dict[str, Any]]:
    """Map and load layout_a visits/referrals/patients + layout_payments CLAIM_TXN."""
    mapped: list[dict[str, Any]] = []
    jobs = [(entity, path, mode) for entity, path, mode in demo_file_jobs() if path.exists()]
    if not jobs:
        raise FileNotFoundError(f"Synthetic fixtures not found at {fixtures_dir()}")
    for entity, path, mode in jobs:
        as_of = date.fromisoformat(DEMO_DEFAULT_AS_OF)
        if mode == "replace":
            wh.reset_table(entity)
        proposal = confirm_mapping(propose_mapping(path, entity=entity, tenant_id=tenant_id, as_of=as_of))
        counts = load_mapped_file(
            wh,
            path,
            proposal,
            tenant_id=tenant_id,
            tenant_company=tenant_company,
            mode=mode,
            as_of=as_of,
        )
        mapped.append(
            {
                "layout": "A" if entity != "CLAIM_TXN" else "ledger",
                "entity": entity,
                "columns": sum(1 for c in proposal.columns if c.target_column),
                "loaded": counts,
            }
        )
    return mapped


def demo_caseload_readiness(wh: Warehouse, as_of: date) -> dict[str, Any]:
    """Counts Christopher needs after Run synthetic demo. Used to fail a green-looking empty demo."""
    row = wh.fetch_one(
        """
        SELECT
          COUNT(*),
          COALESCE(SUM(CASE WHEN "AppointmentStatus" = 'Complete' THEN 1 ELSE 0 END), 0),
          COALESCE(SUM(CASE WHEN "AppointmentStatus" = 'Complete'
            AND COALESCE(
              NULLIF(TRIM(CAST("ProviderId" AS VARCHAR)), ''),
              NULLIF(TRIM(CAST("ProviderName" AS VARCHAR)), '')
            ) IS NOT NULL THEN 1 ELSE 0 END), 0)
        FROM "APPOINTMENT"
        """
    )
    companies = []
    if wh.count("APPOINTMENT") > 0:
        frame = wh.fetch_df('SELECT DISTINCT "Company" AS company FROM "APPOINTMENT"')
        companies = sorted(
            {
                str(v).strip()
                for v in frame["company"].tolist()
                if v is not None and str(v).strip() and str(v).strip().lower() not in {"nan", "none", "<na>"}
            }
        )
    result = caseload_fill(wh, as_of, company=None)
    filled = result.value if isinstance(result.value, list) else []
    n_filled = sum(1 for rec in filled if rec.get("months_to_fill") is not None)
    return {
        "appointment_rows": int((row or [0])[0] or 0),
        "completes": int((row or [0, 0])[1] or 0),
        "completes_with_provider": int((row or [0, 0, 0])[2] or 0),
        "distinct_company": companies,
        "caseload_n_filled": n_filled,
        "caseload_unavailable": result.unavailable,
    }


def demo_not_ready(stats: dict[str, Any]) -> bool:
    if int(stats.get("completes_with_provider") or 0) == 0:
        return True
    if stats.get("caseload_unavailable") == NO_PROVIDER_CASELOAD:
        return True
    if int(stats.get("caseload_n_filled") or 0) == 0:
        return True
    return False


def _demo_needs_reload(wh: Warehouse) -> bool:
    """Reload when empty, old TherapistName schema, Completes have no provider, or Company is stale."""
    if wh.count("APPOINTMENT") == 0:
        return True
    cols = wh._table_columns("APPOINTMENT") or []
    if "TherapistName" in cols:
        return True
    row = wh.fetch_one(
        """
        SELECT COUNT(*) FROM "APPOINTMENT"
        WHERE "AppointmentStatus" = 'Complete'
          AND COALESCE(
            NULLIF(TRIM(CAST("ProviderId" AS VARCHAR)), ''),
            NULLIF(TRIM(CAST("ProviderName" AS VARCHAR)), '')
          ) IS NOT NULL
        """
    )
    if int((row or [0])[0] or 0) == 0:
        return True
    if "Company" in cols:
        stamped = wh.fetch_one(
            'SELECT COUNT(*) FROM "APPOINTMENT" WHERE "Company" = ?',
            [DEMO_TENANT_COMPANY],
        )
        if int((stamped or [0])[0] or 0) == 0:
            return True
    return False


def ensure_demo_warehouse_seeded(wh: Warehouse, tenant_id: str) -> bool:
    """If this is the demo tenant and visits are missing or unusable, load synthetic CSVs.

    Idempotent when Completes already have ProviderId/ProviderName and Company
    matches the demo tenant display name. Never loads into other tenants.
    Replace-load so an old TherapistName schema or CSV Company cannot linger.
    """
    if tenant_id != DEMO_TENANT_ID:
        return False
    if not _demo_needs_reload(wh):
        return False
    load_synthetic_demo(wh, tenant_id=tenant_id, tenant_company=DEMO_TENANT_COMPANY)
    return True
