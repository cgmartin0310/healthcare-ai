"""Catalog of synthetic clinic profiles (exports, not warehouse-shaped).

Tune volumes / payers / cancel bands in scripts/generate_synthetic.py.
This module is the path + label contract used by demo load, download, and tests.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

DEFAULT_PROFILE = "harbor"

PROFILES: dict[str, dict[str, Any]] = {
    "harbor": {
        "id": "harbor",
        "name": "Harbor Pediatric Therapy",
        "blurb": "Peds OT/ST, two sites, school-year seasonality. Synthetic example — not a real clinic.",
        "folder": "harbor_pediatric",
        "files": {
            "APPOINTMENT": "SYNTHETIC_EXAMPLE_visits.csv",
            "REFERRAL": "SYNTHETIC_EXAMPLE_referrals.csv",
            "PATIENT": "SYNTHETIC_EXAMPLE_patients.xlsx",
            "CLAIM_TXN": "SYNTHETIC_EXAMPLE_ledger.csv",
        },
    },
    "riverbend": {
        "id": "riverbend",
        "name": "Riverbend Physical Therapy",
        "blurb": "Adult PT, three sites, higher visits per patient. Synthetic example — not a real clinic.",
        "folder": "riverbend_pt",
        "files": {
            "APPOINTMENT": "SYNTHETIC_EXAMPLE_visits.csv",
            "REFERRAL": "SYNTHETIC_EXAMPLE_referrals.csv",
            "PATIENT": "SYNTHETIC_EXAMPLE_patients.csv",
            "CLAIM_TXN": "SYNTHETIC_EXAMPLE_ledger.xlsx",
        },
    },
    "northside": {
        "id": "northside",
        "name": "Northside Behavioral Health",
        "blurb": "Mental-health group, telehealth-heavy, different payer mix. Synthetic example — not a real clinic.",
        "folder": "northside_bh",
        "files": {
            "APPOINTMENT": "SYNTHETIC_EXAMPLE_sessions.xlsx",
            "REFERRAL": "SYNTHETIC_EXAMPLE_referrals.csv",
            "PATIENT": "SYNTHETIC_EXAMPLE_members.csv",
            "CLAIM_TXN": "SYNTHETIC_EXAMPLE_ledger.csv",
        },
    },
}


def fixtures_root() -> Path:
    cwd = Path.cwd() / "fixtures" / "synthetic"
    if cwd.exists():
        return cwd
    return Path(__file__).resolve().parents[4] / "fixtures" / "synthetic"


def profile_dir(profile_id: str) -> Path:
    spec = PROFILES[profile_id]
    return fixtures_root() / "profiles" / spec["folder"]


def profile_files(profile_id: str) -> list[tuple[str, Path]]:
    spec = PROFILES[profile_id]
    root = profile_dir(profile_id)
    return [(entity, root / name) for entity, name in spec["files"].items()]


def list_profiles() -> list[dict[str, Any]]:
    out = []
    for pid, spec in PROFILES.items():
        files = []
        for entity, path in profile_files(pid):
            files.append({"entity": entity, "filename": path.name, "exists": path.exists()})
        out.append(
            {
                "id": spec["id"],
                "name": spec["name"],
                "blurb": spec["blurb"],
                "default": pid == DEFAULT_PROFILE,
                "files": files,
            }
        )
    return out
