"""De-identification gate: drop PHI, hash ids, generalize dates, receipt. Not HIPAA legal copy."""

from __future__ import annotations

from datetime import date

from analyst.cli import main as clinic_analyst
from integration_engine.deid import (
    SAFE_HARBOR_NOTICE,
    apply_deid,
    deid_file,
    get_or_create_deid_secret,
    hash_identifier,
)
from integration_engine.load import load_mapped_file
from integration_engine.mapper import confirm_mapping, propose_mapping
from warehouse.metrics import cancelation_rate
from warehouse.store import Warehouse


def _phi_csv(path) -> None:
    path.write_text(
        "Name,Address,SSN,ApptId,PatientId,ApptDate,AppointmentStatus,Company,Discipline\n"
        "Jane Roe,123 Main St,111-22-3333,A-RAW-1,P-RAW-1,2026-08-10,Complete,Example Clinic,OT\n"
        "Jane Roe,123 Main St,111-22-3333,A-RAW-2,P-RAW-1,2026-08-17,Cancelled,Example Clinic,OT\n"
        "Jane Roe,123 Main St,111-22-3333,A-RAW-3,P-RAW-1,2026-08-24,No Show,Example Clinic,OT\n"
    )


def test_deid_drops_phi_hashes_id_generalizes_date():
    secret = b"unit-test-secret-aaaaaaaaaaaaaaaa"
    frame = __import__("pandas").read_csv(
        __import__("io").StringIO(
            "Name,Address,SSN,PatientId,ApptDate\n"
            "Jane Roe,123 Main St,111-22-3333,P-RAW-1,2026-08-10\n"
        )
    )
    redacted, receipt = apply_deid(frame, secret, date(2026, 9, 2), source_filename="visits.csv")
    cols = {c.lower() for c in redacted.columns}
    assert "name" not in cols
    assert "address" not in cols
    assert "ssn" not in cols
    assert "PatientId" in redacted.columns
    assert redacted["PatientId"].iloc[0] == hash_identifier(secret, "P-RAW-1")
    assert redacted["PatientId"].iloc[0] != "P-RAW-1"
    assert redacted["ApptDate"].iloc[0] == date(2026, 8, 1)
    assert set(receipt.columns_dropped) >= {"Name", "Address", "SSN"}
    assert "PatientId" in receipt.columns_hashed
    assert "ApptDate" in receipt.dates_generalized
    assert receipt.notice == SAFE_HARBOR_NOTICE
    assert receipt.dob_stored is False
    assert "Jane" not in str(receipt.to_dict())
    assert "111-22-3333" not in str(receipt.to_dict())


def test_same_patient_id_hashes_consistently_within_tenant_differs_across(tmp_path, monkeypatch):
    monkeypatch.setenv("CLINIC_ANALYST_DATA_DIR", str(tmp_path))
    a = get_or_create_deid_secret("clinic-a")
    b = get_or_create_deid_secret("clinic-b")
    assert a != b
    assert hash_identifier(a, "P-RAW-1") == hash_identifier(a, "P-RAW-1")
    assert hash_identifier(a, "P-RAW-1") != hash_identifier(b, "P-RAW-1")


def test_load_deid_file_has_no_phi_and_cancelation_still_computes(tmp_path, monkeypatch, as_of):
    monkeypatch.setenv("CLINIC_ANALYST_DATA_DIR", str(tmp_path))
    src = tmp_path / "phi_visits.csv"
    _phi_csv(src)
    proposal = propose_mapping(src, entity="APPOINTMENT", tenant_id="clinic-a", as_of=as_of)
    assert proposal.deid_receipt
    assert set(proposal.deid_receipt["columns_dropped"]) >= {"Name", "Address", "SSN"}
    assert "PatientId" in proposal.deid_receipt["columns_hashed"]
    confirmed = confirm_mapping(proposal)
    wh = Warehouse(tmp_path / "wh.duckdb")
    counts = load_mapped_file(
        wh, src, confirmed, tenant_id="clinic-a", tenant_company="Example Clinic", as_of=as_of
    )
    assert counts["APPOINTMENT"] == 3
    frame = wh.fetch_table("APPOINTMENT")
    assert "Name" not in frame.columns
    assert "Address" not in frame.columns
    assert "SSN" not in frame.columns
    assert "Jane Roe" not in frame.to_string()
    assert "111-22-3333" not in frame.to_string()
    assert "123 Main" not in frame.to_string()
    assert "P-RAW-1" not in set(frame["PatientId"].astype(str))
    assert "A-RAW-1" not in set(frame["ApptId"].astype(str))
    secret = get_or_create_deid_secret("clinic-a")
    assert set(frame["PatientId"]) == {hash_identifier(secret, "P-RAW-1")}
    days = {d.date() if hasattr(d, "date") else d for d in frame["ApptDate"]}
    assert days == {date(2026, 8, 1)}
    result = cancelation_rate(wh, as_of, months=1)
    assert result.value is not None
    assert abs(result.value - (2 / 3)) < 0.001
    receipts = list((tmp_path / "tenants" / "clinic-a" / "deid_receipts").glob("*.json"))
    assert receipts


def test_cli_deid_writes_redacted_csv_and_receipt(tmp_path, monkeypatch):
    monkeypatch.setenv("CLINIC_ANALYST_DATA_DIR", str(tmp_path))
    src = tmp_path / "phi_visits.csv"
    _phi_csv(src)
    assert clinic_analyst(["--tenant", "clinic-a", "--as-of", "2026-09-02", "deid", str(src)]) == 0
    redacted = tmp_path / "phi_visits.deid.csv"
    receipt = tmp_path / "phi_visits.deid.receipt.json"
    assert redacted.is_file()
    assert receipt.is_file()
    text = redacted.read_text()
    assert "Jane Roe" not in text
    assert "111-22-3333" not in text
    assert "P-RAW-1" not in text
    payload = __import__("json").loads(receipt.read_text())
    assert payload["notice"] == SAFE_HARBOR_NOTICE
    assert set(payload["columns_dropped"]) >= {"Name", "Address", "SSN"}
    assert "PatientId" in payload["columns_hashed"]
    assert "HIPAA compliant" not in payload["notice"]


def test_deid_file_helper_and_ageband_from_dob(tmp_path, monkeypatch, as_of):
    monkeypatch.setenv("CLINIC_ANALYST_DATA_DIR", str(tmp_path))
    src = tmp_path / "patients.csv"
    src.write_text("PatientId,Company,DOB,Name\nP1,Example Clinic,2018-03-01,Kid Name\n")
    dest, rec, receipt = deid_file(src, tenant_id="clinic-a", as_of=as_of)
    import pandas as pd

    frame = pd.read_csv(dest)
    assert "Name" not in frame.columns
    assert "DOB" not in frame.columns
    assert frame["AgeBand"].iloc[0] == "Child"
    assert receipt.age_band_derived_from_dob is True
    assert rec.is_file()


def test_early_quit_uses_ageband_without_stored_dob(warehouse, as_of):
    from tests.conftest import appt_row, load_appts, load_patients, patient_row
    from warehouse.metrics import early_quit_watch

    rows = [
        appt_row(ApptId="1", PatientId="C1", Discipline="OT", ApptDate=date(2026, 4, 2), AppointmentStatus="Complete"),
        appt_row(ApptId="2", PatientId="C1", Discipline="OT", ApptDate=date(2026, 8, 2), AppointmentStatus="Cancelled"),
        appt_row(ApptId="3", PatientId="C1", Discipline="OT", ApptDate=date(2026, 8, 9), AppointmentStatus="Cancelled"),
        appt_row(ApptId="4", PatientId="C1", Discipline="OT", ApptDate=date(2026, 8, 16), AppointmentStatus="No Show"),
    ]
    load_appts(warehouse, rows)
    load_patients(warehouse, [patient_row(PatientId="C1", DOB=None, AgeBand="Child")])
    result = early_quit_watch(warehouse, as_of)
    assert result.value == 1
    assert result.details["flagged"][0]["tenure_bar_months"] == 6
    stored = warehouse.fetch_table("PATIENT")
    assert stored["DOB"].isna().all()
