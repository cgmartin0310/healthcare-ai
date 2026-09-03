from __future__ import annotations

from integration_engine.mapper import confirm_mapping, propose_mapping
from integration_engine.normalize import normalize_discipline, normalize_status
from tests.conftest import FIXTURES


def test_layout_a_appointments_map_required_fields():
    path = FIXTURES / "layout_a" / "SYNTHETIC_EXAMPLE_appointments.csv"
    proposal = propose_mapping(path, entity="APPOINTMENT")
    bound = {c.target_column for c in proposal.columns if c.target_table == "APPOINTMENT"}
    by_source = {c.source: c.target_column for c in proposal.columns if c.target_column}
    for required in ("ApptId", "ApptDate", "AppointmentStatus", "Company", "Discipline", "PatientId"):
        assert required in bound
    assert by_source["TherapistName"] == "ProviderName"
    assert by_source["ProviderId"] == "ProviderId"
    assert "TherapistId" not in bound
    assert "FirstInsPayment" not in proposal.unmapped_required
    confirm_mapping(proposal)


def test_layout_b_appointments_map_despite_different_headers():
    path = FIXTURES / "layout_b" / "SYNTHETIC_EXAMPLE_visits.csv"
    proposal = propose_mapping(path, entity="APPOINTMENT")
    bound = {c.source: c.target_column for c in proposal.columns if c.target_column}
    assert bound["visit_id"] == "ApptId"
    assert bound["date_of_service"] == "ApptDate"
    assert bound["visit_status"] == "AppointmentStatus"
    assert bound["clinic_name"] == "Company"
    assert bound["therapy_type"] == "Discipline"
    assert bound["patient_num"] == "PatientId"
    assert bound["provider_id"] == "ProviderId"
    assert bound["rendering_provider"] == "ProviderName"
    assert bound["insurance_name"] == "PrimaryPayorName"
    assert bound["insurance_paid"] == "InsPaid"
    assert bound["insurance_balance"] == "InsBalance"
    assert bound["amount_paid"] == "TotalPaid"
    assert bound["site"] == "LocationName"
    confirm_mapping(proposal)


def test_layout_b_referrals_map_completed_flag():
    path = FIXTURES / "layout_b" / "SYNTHETIC_EXAMPLE_incoming_referrals.csv"
    proposal = propose_mapping(path, entity="REFERRAL")
    bound = {c.source: c.target_column for c in proposal.columns if c.target_column}
    assert bound["ref_created_at"] == "DateTimeCreated"
    assert bound["eval_completed"] == "Completed?"
    assert bound["source"] == "Source"
    assert bound["office"] == "LocationName"
    confirm_mapping(proposal)


def test_layout_b_patients_map_active_and_ageband_not_dob():
    path = FIXTURES / "layout_b" / "SYNTHETIC_EXAMPLE_clients.csv"
    proposal = propose_mapping(path, entity="PATIENT")
    bound = {c.source: c.target_column for c in proposal.columns if c.target_column}
    assert bound["is_active_flag"] == "PatientActive"
    assert bound.get("AgeBand") == "AgeBand"
    assert "dob" not in bound
    assert "DOB" not in bound.values()
    assert "AgeGroup" not in bound.values()
    assert proposal.deid_receipt
    assert "dob" in [c.lower() for c in proposal.deid_receipt["columns_dropped"]]
    assert proposal.deid_receipt["age_band_derived_from_dob"] is True
    confirm_mapping(proposal)


def test_secondary_payor_maps_and_is_not_current_payer(tmp_path):
    src = tmp_path / "visits_cob.csv"
    src.write_text(
        "visit_id,date_of_service,visit_status,clinic_name,therapy_type,patient_num,"
        "insurance_name,secondary_payer\n"
        "A1,2026-08-10,Complete,Example Clinic,OT,P1,Acme Health,Beacon Plan\n"
    )
    proposal = propose_mapping(src, entity="APPOINTMENT")
    bound = {c.source: c.target_column for c in proposal.columns if c.target_column}
    assert bound["insurance_name"] == "PrimaryPayorName"
    assert bound["secondary_payer"] == "SecondaryPayorName"
    assert "CurrentPayer" not in bound.values()
    confirm_mapping(proposal)


def test_status_and_discipline_aliases():
    assert normalize_status("canceled") == "Cancelled"
    assert normalize_status("no-show") == "No Show"
    assert normalize_status("completed") == "Complete"
    assert normalize_discipline("Occupational") == "OT"
    assert normalize_discipline("Speech") == "ST"


def test_charge_file_maps_onto_claim_txn_not_a_charges_table(tmp_path):
    from warehouse.schema import PREP_TABLES

    assert "CHARGES" not in PREP_TABLES
    src = tmp_path / "charges.csv"
    src.write_text(
        "line_id,posted_on,payer_name,txn_type,charge_amount,patient_num,clinic_name\n"
        "C1,2026-08-10,Acme Health,charge,95.00,P1,Example Clinic\n"
    )
    proposal = propose_mapping(src)
    assert proposal.entity_guess == "CLAIM_TXN"
    assert any("claim ledger" in n.lower() for n in proposal.notes)
    assert any("no separate charges table" in n.lower() for n in proposal.notes)
    bound = {c.source: c.target_column for c in proposal.columns if c.target_column}
    assert bound["line_id"] == "TxnId"
    assert bound["posted_on"] == "PostedDate"
    assert bound["txn_type"] == "TxnType"
    assert bound["charge_amount"] == "Amount"
    assert bound["payer_name"] == "Payer"
    confirm_mapping(proposal)


def test_confirm_rejects_missing_required(tmp_path):
    # A file that cannot map AppointmentStatus
    src = tmp_path / "emptyish.csv"
    src.write_text("foo,bar\n1,2\n")
    proposal = propose_mapping(src, entity="APPOINTMENT")
    assert proposal.unmapped_required
    try:
        confirm_mapping(proposal)
        raise AssertionError("should have refused confirm")
    except ValueError as exc:
        assert "unmapped" in str(exc).lower()
