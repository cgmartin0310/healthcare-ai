"""Three synthetic clinic profiles: export dialects, mapping, metrics, de-id."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from integration_engine.deid import apply_deid, get_or_create_deid_secret
from integration_engine.load import load_mapped_file
from integration_engine.mapper import confirm_mapping, propose_mapping
from tests.conftest import AS_OF
from warehouse.metrics import (
    ar_past_30_days,
    cancelation_rate,
    caseload_fill,
    churn,
    days_to_pay,
    referrals,
)
from warehouse.schema import PREP_TABLES
from warehouse.store import Warehouse
from web.profiles import PROFILES, profile_files, profile_dir


WAREHOUSE_NAMES = {c.name for table in PREP_TABLES.values() for c in table.columns}
JUNK_HINTS = (
    "exportbatch",
    "rowhash",
    "chartnote",
    "extract_id",
    "extractid",
    "intake_queue",
    "intakequeue",
    "routerflag",
    "batchtag",
    "chart_tag",
    "charttag",
)


def _iter_sample_files() -> list[Path]:
    files = []
    for pid in PROFILES:
        for _entity, path in profile_files(pid):
            files.append(path)
    return files


def _read_headers(path: Path) -> list[str]:
    if path.suffix.lower() == ".xlsx":
        frame = pd.read_excel(path, nrows=0)
        return [str(c) for c in frame.columns]
    return [h.strip() for h in path.read_text(encoding="utf-8").splitlines()[0].split(",")]


def test_no_sample_file_uses_warehouse_headers():
    found = False
    for path in _iter_sample_files():
        assert path.exists(), f"missing sample file {path}"
        found = True
        headers = _read_headers(path)
        assert headers, path
        overlap = set(headers) & WAREHOUSE_NAMES
        assert not overlap, f"{path.name} uses warehouse headers verbatim: {sorted(overlap)}"
    assert found


def test_each_profile_maps_required_and_leaves_junk_unmapped():
    for pid in PROFILES:
        for entity, path in profile_files(pid):
            proposal = propose_mapping(path, entity=entity)
            assert not proposal.unmapped_required, f"{pid} {entity}: {proposal.unmapped_required}"
            confirm_mapping(proposal)
            for col in proposal.columns:
                src = str(col.source).replace(" ", "").lower()
                if any(h.replace("_", "") in src or src in h for h in JUNK_HINTS if len(h) > 4):
                    if any(j in src for j in ("exportbatch", "rowhash", "chartnote", "extractid", "intakequeue", "routerflag", "batchtag", "charttag")):
                        assert not col.target_column, f"junk {col.source} was mapped to {col.target_column}"


def _load_profile(tmp_path, pid: str) -> Warehouse:
    wh = Warehouse(tmp_path / f"{pid}.duckdb")
    for entity, path in profile_files(pid):
        proposal = confirm_mapping(propose_mapping(path, entity=entity, tenant_id=pid, as_of=AS_OF))
        load_mapped_file(
            wh,
            path,
            proposal,
            tenant_id=pid,
            tenant_company="Example Clinic (synthetic)",
            mode="replace",
            as_of=AS_OF,
        )
    return wh


def test_each_profile_metrics_and_caseload_ramp(tmp_path):
    for pid in PROFILES:
        wh = _load_profile(tmp_path, pid)
        fill = caseload_fill(wh, AS_OF, company=None)
        filled = [r for r in (fill.value or []) if r.get("months_to_fill") is not None]
        assert len(filled) >= 3, f"{pid} filled={len(filled)} unavailable={fill.unavailable}"
        assert any(1 <= int(r["months_to_fill"]) <= 6 for r in filled), f"{pid} no 1-6 month ramp: {filled}"
        cancel = cancelation_rate(wh, AS_OF, months=3)
        assert cancel.value is not None and cancel.unavailable is None
        ch = churn(wh, AS_OF)
        assert ch.value is not None and ch.unavailable is None
        refs = referrals(wh, AS_OF, months=1)
        assert refs.value and refs.value.get("referrals", 0) > 0
        assert refs.value.get("conversion") is not None
        assert refs.value.get("conversion") < 1
        ar = ar_past_30_days(wh, AS_OF)
        assert ar.value, f"{pid} AR empty {ar.unavailable}"
        dtp = days_to_pay(wh, AS_OF)
        assert dtp.value is not None, f"{pid} days-to-pay empty {dtp.unavailable} {dtp.details}"
        assert dtp.unavailable is None
        wh.close()


def test_each_profile_deid_receipt_and_warehouse_ids_only(tmp_path):
    secret = get_or_create_deid_secret("deid-check")
    for pid in PROFILES:
        for entity, path in profile_files(pid):
            if entity not in {"APPOINTMENT", "PATIENT"}:
                continue
            raw = pd.read_excel(path) if path.suffix.lower() == ".xlsx" else pd.read_csv(path)
            _frame, receipt = apply_deid(raw, secret, AS_OF, source_filename=path.name)
            dropped = {c.lower() for c in receipt.columns_dropped}
            blob = " ".join(dropped)
            assert any(tok in blob for tok in ("name", "first", "last", "given", "family", "pt_first", "pt first"))
            assert any(tok in blob for tok in ("phone", "email"))
            assert any(tok in blob for tok in ("street", "address", "city", "zip"))
            assert any("mrn" in tok or "member" in tok for tok in dropped)
            assert receipt.columns_hashed
            assert receipt.age_band_derived_from_dob is True
            assert receipt.dob_stored is False
        wh = _load_profile(tmp_path, pid)
        appt = wh.fetch_table("APPOINTMENT")
        for banned in ("FirstName", "LastName", "Name", "Address", "Street", "DOB", "Phone", "Email"):
            if banned in appt.columns:
                assert appt[banned].isna().all() or (appt[banned].astype(str) == "").all()
        if "DOB" in wh.fetch_table("PATIENT").columns:
            assert wh.fetch_table("PATIENT")["DOB"].isna().all()
        wh.close()


def test_messy_values_normalize_and_junk_not_force_mapped(tmp_path):
    from integration_engine.normalize import normalize_discipline, normalize_number, normalize_status

    assert normalize_status("CANX") == "Cancelled"
    assert normalize_status("NS") == "No Show"
    assert normalize_status("COMPLETED") == "Complete"
    assert normalize_discipline("OCC") == "OT"
    assert normalize_discipline("SLP") == "ST"
    assert normalize_number("$1,234.50") == 1234.5
    path = profile_files("harbor")[0][1]
    proposal = propose_mapping(path, entity="APPOINTMENT")
    junk = [c for c in proposal.columns if c.source in {"ExportBatch", "RowHash"}]
    assert junk
    assert all(c.target_column is None for c in junk)
