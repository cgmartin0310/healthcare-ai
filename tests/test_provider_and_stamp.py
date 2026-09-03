"""Provider naming, tenant Company stamp, EvalDate→Completed?, COB column."""

from __future__ import annotations

from datetime import date

from integration_engine.load import load_mapped_file
from integration_engine.mapper import confirm_mapping, propose_mapping
from tests.conftest import appt_row, load_appts
from warehouse.metrics import headcount
from warehouse.schema import APPOINTMENT, CLAIM_TXN, REFERRAL


def test_no_therapist_id_or_current_payer():
    appt = {c.name for c in APPOINTMENT.columns}
    assert "TherapistId" not in appt
    assert "TherapistName" not in appt
    assert "CurrentPayer" not in appt
    assert "ProviderId" in appt
    assert "ProviderName" in appt
    assert "SecondaryPayorName" in appt
    assert "CPT" in appt
    assert {c.name for c in CLAIM_TXN.columns} >= {"ClaimId", "DOS", "DenialCode", "Payer"}


def test_company_not_required_in_upload_is_stamped(tmp_path, warehouse):
    src = tmp_path / "visits_no_company.csv"
    src.write_text(
        "visit_id,date_of_service,visit_status,therapy_type,patient_num\n"
        "A1,2026-08-10,Complete,OT,P1\n"
    )
    proposal = propose_mapping(src, entity="APPOINTMENT")
    assert not any(u.endswith(".Company") for u in proposal.unmapped_required)
    confirm_mapping(proposal)
    load_mapped_file(
        warehouse,
        src,
        proposal,
        tenant_id="second-clinic",
        tenant_company="Second Clinic",
        mode="replace",
    )
    frame = warehouse.fetch_table("APPOINTMENT")
    assert list(frame["Company"]) == ["Second Clinic"]
    from integration_engine.deid import get_or_create_deid_secret, hash_identifier

    secret = get_or_create_deid_secret("second-clinic")
    assert list(frame["ApptId"]) == [hash_identifier(secret, "A1")]
    appt_dates = [d.date() if hasattr(d, "date") else d for d in frame["ApptDate"]]
    assert appt_dates == [date(2026, 8, 1)]


def test_evaldate_derives_completed(tmp_path, warehouse):
    src = tmp_path / "refs_eval.csv"
    src.write_text(
        "ref_created_at,eval_date,clinic_name\n"
        "2026-08-02 09:00:00,2026-08-05,Example Clinic\n"
        "2026-08-03 09:00:00,,Example Clinic\n"
    )
    proposal = propose_mapping(src, entity="REFERRAL")
    assert "REFERRAL.Completed?" not in proposal.unmapped_required
    assert "EvalDate" in {c.name for c in REFERRAL.columns}
    confirm_mapping(proposal)
    load_mapped_file(
        warehouse, src, proposal, tenant_id="example-clinic", tenant_company="Example Clinic"
    )
    completed = [int(v) for v in warehouse.fetch_table("REFERRAL")["Completed?"]]
    assert completed == [1, 0]


def test_headcount_prefers_provider_id_over_name(warehouse, as_of):
    load_appts(
        warehouse,
        [
            appt_row(
                ApptId="1",
                ProviderId="PR-1",
                ProviderName="Alias A",
                AppointmentStatus="Complete",
                ApptDate=date(2026, 8, 10),
            ),
            appt_row(
                ApptId="2",
                ProviderId="PR-1",
                ProviderName="Alias B",
                AppointmentStatus="Complete",
                ApptDate=date(2026, 8, 11),
            ),
            appt_row(
                ApptId="3",
                ProviderId=None,
                ProviderName="Name Only",
                AppointmentStatus="Complete",
                ApptDate=date(2026, 8, 12),
            ),
        ],
    )
    result = headcount(warehouse, as_of)
    providers = {row["provider"] for row in result.details["providers"]}
    assert providers == {"PR-1", "Name Only"}
    assert result.value == 2
