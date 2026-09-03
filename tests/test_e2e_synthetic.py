"""End-to-end: each synthetic clinic profile maps, loads, and answers sample questions."""

from __future__ import annotations

from integration_engine.load import load_mapped_file
from integration_engine.mapper import confirm_mapping, propose_mapping
from analyst.engine import Analyst
from warehouse.store import Warehouse
from web.profiles import PROFILES, profile_files


def _load_profile(wh: Warehouse, profile_id: str, tenant: str) -> None:
    for entity, path in profile_files(profile_id):
        proposal = confirm_mapping(propose_mapping(path, entity=entity, tenant_id=tenant))
        load_mapped_file(wh, path, proposal, tenant_id=tenant, mode="replace")


def test_each_profile_loads_and_analyst_answers(tmp_path, as_of):
    for pid, spec in PROFILES.items():
        wh = Warehouse(tmp_path / f"{pid}.duckdb")
        _load_profile(wh, pid, f"clinic-{pid}")
        assert wh.count("APPOINTMENT") > 100
        assert wh.count("REFERRAL") > 20
        assert wh.count("PATIENT") > 20
        assert wh.count("CLAIM_TXN") > 20
        analyst = Analyst(wh, tenant_id=f"clinic-{pid}", as_of=as_of)
        cancel = analyst.ask("Is cancelation over 25% in the last three months?")
        ar = analyst.ask("Which payers have AR sitting past 30 days, by location?")
        refs = analyst.ask("Referral-source drop-off / does volume support another therapist?")
        caseload = analyst.ask("How long does a new clinician take to fill a caseload?")
        payroll = analyst.ask("Which therapists are profitable after payroll?")
        improve = analyst.ask("What can I do to improve my business?")
        alerts = analyst.alerts()
        assert cancel["intent"] == "cancelation"
        assert "%" in cancel["answer"]
        assert "Summit Mutual" in ar["answer"]
        assert spec["name"].split()[0] in refs["answer"] or "drop" in refs["answer"].lower() or "source" in refs["answer"].lower()
        assert "months" in caseload["answer"].lower()
        assert "No Completes with ProviderId or ProviderName" not in caseload["answer"]
        assert "payroll is not in this dump" in payroll["answer"].lower()
        assert improve["suggestions"]
        triggered = {a["id"]: a["triggered"] for a in alerts["alerts"]}
        assert "cancel_over_25" in triggered
        assert triggered["ref_drop_10"] is True
        wh.close()
