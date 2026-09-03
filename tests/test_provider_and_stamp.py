"""Provider naming, tenant Company stamp, EvalDate→Completed?, COB column."""

from __future__ import annotations

from datetime import date

from integration_engine.load import load_mapped_file
from integration_engine.mapper import confirm_mapping, propose_mapping
from tests.conftest import appt_row, load_appts
from warehouse.metrics import headcount
from warehouse.schema import APPOINTMENT, CLAIM_TXN, REFERRAL
from warehouse.store import Warehouse


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


def _load_layout_a_appointments(tmp_path, monkeypatch, as_of, *, tenant_company: str):
    monkeypatch.setenv("CLINIC_ANALYST_DATA_DIR", str(tmp_path))
    from web.profiles import profile_files

    path = dict(profile_files("harbor"))["APPOINTMENT"]
    proposal = propose_mapping(path, entity="APPOINTMENT", tenant_id="clinic-a", as_of=as_of)
    confirmed = confirm_mapping(proposal)
    wh = Warehouse(tmp_path / "caseload.duckdb")
    load_mapped_file(
        wh,
        path,
        confirmed,
        tenant_id="clinic-a",
        tenant_company=tenant_company,
        as_of=as_of,
    )
    return path, proposal, wh


def test_stamp_overwrites_csv_company_with_tenant(tmp_path, warehouse):
    """Locked rule: Company is the logged-in tenant on every row, not the CSV clinic name."""
    src = tmp_path / "visits_csv_company.csv"
    src.write_text(
        "visit_id,date_of_service,visit_status,clinic_name,therapy_type,patient_num\n"
        "A1,2026-08-10,Complete,Example Clinic,OT,P1\n"
        "A2,2026-08-11,Complete,Example Clinic,PT,P2\n"
    )
    proposal = propose_mapping(src, entity="APPOINTMENT")
    confirm_mapping(proposal)
    load_mapped_file(
        warehouse,
        src,
        proposal,
        tenant_id="example-clinic",
        tenant_company="Example Clinic (synthetic)",
        mode="replace",
    )
    frame = warehouse.fetch_table("APPOINTMENT")
    assert set(frame["Company"].astype(str)) == {"Example Clinic (synthetic)"}
    assert "Example Clinic" not in set(frame["Company"].astype(str))


def test_caseload_fill_after_deid_layout_a(tmp_path, monkeypatch, as_of):
    """Harbor clinician display + ProviderId must produce months-to-fill, including a 1-6 month ramp."""
    from analyst.engine import Analyst
    from warehouse.metrics import caseload_fill

    path, proposal, wh = _load_layout_a_appointments(
        tmp_path, monkeypatch, as_of, tenant_company="Example Clinic"
    )
    by_source = {c.source: c.target_column for c in proposal.columns if c.target_column}
    assert by_source["Clinician"] == "ProviderName"
    assert "Clinician" not in (proposal.deid_receipt or {}).get("columns_dropped", [])
    frame = wh.fetch_table("APPOINTMENT")
    completes = frame[frame["AppointmentStatus"] == "Complete"]
    assert completes["ProviderName"].notna().all()
    assert completes["ProviderId"].notna().all()
    result = caseload_fill(wh, as_of, company=None)
    assert result.unavailable != "No Completes with ProviderId or ProviderName. Cannot measure caseload fill."
    assert result.value
    filled = [r for r in result.value if r.get("months_to_fill") is not None]
    assert len(filled) >= 3
    assert any(1 <= int(r["months_to_fill"]) <= 6 for r in filled)
    out = Analyst(wh, tenant_id="clinic-a", as_of=as_of).ask(
        "How long does a new clinician take to fill a caseload?"
    )
    assert "No Completes with ProviderId or ProviderName" not in out["answer"]
    assert "months" in out["answer"].lower()


def test_run_tool_drops_tenant_id_company_that_zeros_caseload(tmp_path, monkeypatch, as_of):
    """Grok passes tenant_id / display name; warehouse Company is the CSV clinic name."""
    from analyst.tools import run_tool
    from warehouse.metrics import caseload_fill

    _path, _proposal, wh = _load_layout_a_appointments(
        tmp_path, monkeypatch, as_of, tenant_company="Example Clinic"
    )
    companies = {str(v) for v in wh.fetch_table("APPOINTMENT")["Company"].dropna()}
    assert companies == {"Example Clinic"}
    for bogus in ("example-clinic", "Example Clinic (synthetic)"):
        direct = caseload_fill(wh, as_of, company=bogus)
        assert direct.unavailable
        assert "ProviderId" not in (direct.unavailable or "")
        assert "company" in (direct.unavailable or "").lower()
        payload, err = run_tool(
            "caseload_fill",
            {"company": bogus},
            warehouse=wh,
            as_of=as_of,
            company=None,
            alerts_fn=lambda: {},
        )
        assert err == ""
        assert payload.get("unavailable") != (
            "No Completes with ProviderId or ProviderName. Cannot measure caseload fill."
        )
        assert payload.get("value")
        assert any(r.get("months_to_fill") is not None for r in payload["value"])


def test_opens_migrates_therapist_name_into_provider_name(tmp_path, as_of):
    import duckdb

    path = tmp_path / "legacy.duckdb"
    con = duckdb.connect(str(path))
    con.execute(
        """
        CREATE TABLE "APPOINTMENT" (
            "ApptId" VARCHAR, "ApptDate" DATE, "AppointmentStatus" VARCHAR,
            "Company" VARCHAR, "Discipline" VARCHAR, "PatientId" VARCHAR,
            "TherapistName" VARCHAR, "LocationName" VARCHAR
        )
        """
    )
    con.execute(
        """
        INSERT INTO "APPOINTMENT" VALUES
        ('A1', DATE '2026-08-10', 'Complete', 'Example Clinic', 'OT', 'P1', 'Therapist_01', 'Site A')
        """
    )
    con.close()
    wh = Warehouse(path)
    cols = wh._table_columns("APPOINTMENT")
    assert cols is not None
    assert "ProviderName" in cols
    assert "ProviderId" in cols
    assert "TherapistName" not in cols
    frame = wh.fetch_table("APPOINTMENT")
    assert list(frame["ProviderName"]) == ["Therapist_01"]
    from warehouse.metrics import headcount

    hc = headcount(wh, as_of)
    assert hc.value == 1
    assert hc.details["providers"][0]["provider"] == "Therapist_01"
