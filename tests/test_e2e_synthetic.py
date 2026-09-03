"""End-to-end: two synthetic layouts map, confirm, load; analyst answers sample questions."""

from __future__ import annotations

from integration_engine.load import load_mapped_file
from integration_engine.mapper import confirm_mapping, propose_mapping
from analyst.engine import Analyst
from tests.conftest import FIXTURES
from warehouse.store import Warehouse


def _load_layout(wh: Warehouse, layout: str, tenant: str) -> None:
    files = {
        "layout_a": {
            "APPOINTMENT": FIXTURES / "layout_a" / "SYNTHETIC_EXAMPLE_appointments.csv",
            "REFERRAL": FIXTURES / "layout_a" / "SYNTHETIC_EXAMPLE_referrals.csv",
            "PATIENT": FIXTURES / "layout_a" / "SYNTHETIC_EXAMPLE_patients.csv",
        },
        "layout_b": {
            "APPOINTMENT": FIXTURES / "layout_b" / "SYNTHETIC_EXAMPLE_visits.csv",
            "REFERRAL": FIXTURES / "layout_b" / "SYNTHETIC_EXAMPLE_incoming_referrals.csv",
            "PATIENT": FIXTURES / "layout_b" / "SYNTHETIC_EXAMPLE_clients.csv",
        },
    }[layout]
    for entity, path in files.items():
        proposal = confirm_mapping(propose_mapping(path, entity=entity, tenant_id=tenant))
        load_mapped_file(wh, path, proposal, tenant_id=tenant, mode="replace")


def test_both_layouts_load_and_analyst_answers(tmp_path, as_of):
    answers = {}
    for layout in ("layout_a", "layout_b"):
        wh = Warehouse(tmp_path / f"{layout}.duckdb")
        _load_layout(wh, layout, "example-clinic")
        assert wh.count("APPOINTMENT") > 100
        assert wh.count("REFERRAL") > 20
        assert wh.count("PATIENT") == 80
        analyst = Analyst(wh, tenant_id="example-clinic", as_of=as_of)
        cancel = analyst.ask("Is cancelation over 25% in the last three months?")
        ar = analyst.ask("Which payers have AR sitting past 30 days, by location?")
        refs = analyst.ask("Referral-source drop-off / does volume support another therapist?")
        payroll = analyst.ask("Which therapists are profitable after payroll?")
        improve = analyst.ask("What can I do to improve my business?")
        alerts = analyst.alerts()
        assert cancel["intent"] == "cancelation"
        assert "over 25%" in cancel["answer"] or "not over 25%" in cancel["answer"]
        assert "Acme Health" in ar["answer"]
        assert "School District Example" in refs["answer"]
        assert "payroll is not in this dump" in payroll["answer"].lower()
        assert improve["suggestions"]
        triggered = {a["id"]: a["triggered"] for a in alerts["alerts"]}
        assert triggered["cancel_over_25"] is True
        assert triggered["ref_drop_10"] is True
        answers[layout] = cancel["answer"]
        wh.close()
    # Same semantic dump → same cancelation conclusion from both layouts.
    assert ("over 25%" in answers["layout_a"]) == ("over 25%" in answers["layout_b"])
