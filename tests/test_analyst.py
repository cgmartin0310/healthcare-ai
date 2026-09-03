from __future__ import annotations

from datetime import date

from analyst.alerts import DEFAULT_ALERTS, evaluate_alerts
from analyst.banner import PRODUCT_BANNER
from analyst.engine import EMPTY_WAREHOUSE, Analyst
from analyst.schedule import CadenceConfig, is_due
from tests.conftest import appt_row, load_appts, load_refs, referral_row
from warehouse.metrics import payroll_present


def test_banner_is_persistent():
    assert "does not have a live future schedule" in PRODUCT_BANNER.lower()
    assert "closed-month" in PRODUCT_BANNER.lower()


def test_empty_warehouse_returns_visits_banner(warehouse, as_of):
    out = Analyst(warehouse, tenant_id="t", as_of=as_of).ask(
        "Is cancelation over 25% in the last three months?"
    )
    assert out["empty_warehouse"] is True
    assert out["intent"] == "empty_warehouse"
    assert out["answer"] == EMPTY_WAREHOUSE
    assert out["grounded"] is True


def test_analyst_answers_cancelation(warehouse, as_of):
    load_appts(
        warehouse,
        [appt_row(ApptId="1", AppointmentStatus="Complete")]
        + [appt_row(ApptId=f"c{i}", AppointmentStatus="Cancelled") for i in range(2)],
    )
    analyst = Analyst(warehouse, tenant_id="t", as_of=as_of)
    out = analyst.ask("Is cancelation over 25% in the last three months?")
    assert out["grounded"] is True
    assert out["banner"] == PRODUCT_BANNER
    assert out["intent"] == "cancelation"
    assert "66.7%" in out["answer"] or "66.6%" in out["answer"]
    assert "over 25%" in out["answer"]


def test_analyst_answers_ar_by_payer_location(warehouse, as_of):
    load_appts(
        warehouse,
        [
            appt_row(
                ApptId="1",
                ApptDate=date(2026, 6, 1),
                InsPaid=0,
                FirstInsPayment=None,
                PrimaryPayorName="Acme Health",
                LocationName="Site B",
                InsBalance=125.0,
            )
        ],
    )
    out = Analyst(warehouse, tenant_id="t", as_of=as_of).ask(
        "Which payers have AR sitting past 30 days, by location?"
    )
    assert out["intent"] == "ar_past_30"
    assert "Acme Health" in out["answer"]
    assert "Site B" in out["answer"]
    assert "InsBalance" in out["answer"]
    assert "$125.00" in out["answer"]
    assert "not Tableau NET AR" in out["answer"]
    assert "billed − paid" in out["answer"] or "billed - paid" in out["answer"]


def test_analyst_answers_referrals(warehouse, as_of):
    load_appts(warehouse, [appt_row(ApptId="visit-1")])
    load_refs(
        warehouse,
        [
            referral_row(ReferralId="1", DateTimeCreated=date(2026, 8, 2), Source="School District Example", **{"Completed?": 1}),
            referral_row(ReferralId="2", DateTimeCreated=date(2026, 8, 3), Source="School District Example", **{"Completed?": 0}),
            referral_row(ReferralId="3", DateTimeCreated=date(2026, 7, 2), Source="School District Example", **{"Completed?": 1}),
            referral_row(ReferralId="4", DateTimeCreated=date(2026, 7, 3), Source="School District Example", **{"Completed?": 1}),
            referral_row(ReferralId="5", DateTimeCreated=date(2026, 7, 4), Source="School District Example", **{"Completed?": 0}),
        ],
    )
    out = Analyst(warehouse, tenant_id="t", as_of=as_of).ask(
        "Referral-source drop-off — does volume support another therapist?"
    )
    assert out["intent"] == "referrals"
    assert "School District Example" in out["answer"]
    assert "staffing working model" in out["answer"].lower()


def test_most_productive_is_completes_not_snapshot(warehouse, as_of, monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    load_appts(
        warehouse,
        [
            appt_row(
                ApptId="j1",
                ProviderId="PR-J",
                ProviderName="Jordan Lee",
                ApptDate=date(2026, 8, 10),
                PatientId="P1",
            ),
            appt_row(
                ApptId="j2",
                ProviderId="PR-J",
                ProviderName="Jordan Lee",
                ApptDate=date(2026, 8, 17),
                PatientId="P1",
            ),
            appt_row(
                ApptId="c1",
                ProviderId="PR-C",
                ProviderName="Casey Hale",
                ApptDate=date(2026, 8, 12),
                PatientId="P2",
            ),
        ],
    )
    out = Analyst(warehouse, tenant_id="t", as_of=as_of).ask("Which therapist is the most productive?")
    assert "Closed-month snapshot" not in out["answer"]
    assert "payroll is not in this dump" not in out["answer"].lower()
    assert "Jordan Lee" in out["answer"]
    assert "2" in out["answer"]
    assert "Complete" in out["answer"]
    assert out["intent"] == "completes_by_provider"


def test_improve_still_uses_snapshot_path(warehouse, as_of, monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    load_appts(warehouse, [appt_row(ApptId="1")])
    out = Analyst(warehouse, tenant_id="t", as_of=as_of).ask("What can I do to improve my business?")
    assert out["intent"] == "improve_business"
    assert "Closed-month snapshot" in out["answer"]


def test_simulated_429_answers_productive_from_warehouse(warehouse, as_of, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "test-not-a-real-key")
    load_appts(
        warehouse,
        [
            appt_row(
                ApptId="j1",
                ProviderId="PR-J",
                ProviderName="Jordan Lee",
                ApptDate=date(2026, 8, 10),
            ),
            appt_row(
                ApptId="j2",
                ProviderId="PR-J",
                ProviderName="Jordan Lee",
                ApptDate=date(2026, 8, 11),
            ),
        ],
    )

    def boom(_messages, _tools):
        raise RuntimeError("Client error '429 Too Many Requests' for url 'https://api.x.ai/v1/chat/completions'")

    monkeypatch.setattr("analyst.engine.complete_chat", boom)
    out = Analyst(warehouse, tenant_id="t", as_of=as_of).ask("Which therapist is the most productive?")
    assert out["mode"] == "fallback"
    assert out["tools_notice"]
    assert "warehouse" in out["tools_notice"].lower()
    assert "Closed-month snapshot" not in out["answer"]
    assert "payroll is not in this dump" not in out["answer"].lower()
    assert "Jordan Lee" in out["answer"]
    assert "Complete" in out["answer"]


def test_analyst_refuses_payroll_invention(warehouse, as_of):
    load_appts(warehouse, [appt_row(ApptId="visit-1")])
    assert payroll_present(warehouse) is False
    out = Analyst(warehouse, tenant_id="t", as_of=as_of).ask(
        "Which therapists are profitable after payroll?"
    )
    assert "payroll is not in this dump" in out["answer"].lower()
    assert "invented" in out["answer"].lower()


def test_analyst_caseload_without_fill_says_data_not_there(warehouse, as_of):
    load_appts(
        warehouse,
        [appt_row(ApptId="1", ProviderName="Therapist_01", ApptDate=date(2026, 8, 1))],
    )
    out = Analyst(warehouse, tenant_id="t", as_of=as_of).ask(
        "How long does a new clinician take to fill a caseload?"
    )
    assert "data is not there" in out["answer"].lower() or "do not show" in out["answer"].lower()


def test_improve_is_grounded(warehouse, as_of):
    load_appts(
        warehouse,
        [appt_row(ApptId="1", AppointmentStatus="Cancelled", ApptDate=date(2026, 8, 1))]
        + [
            appt_row(
                ApptId="ar1",
                ApptDate=date(2026, 6, 1),
                InsPaid=0,
                PrimaryPayorName="Acme Health",
                LocationName="Site B",
                InsBalance=80.0,
            )
        ],
    )
    out = Analyst(warehouse, tenant_id="t", as_of=as_of).ask("What can I do to improve my business?")
    assert out["intent"] == "improve_business"
    assert out["suggestions"]
    assert any("InsBalance" in s for s in out["suggestions"])
    for s in out["suggestions"]:
        assert "invent" not in s.lower() or "no additional action is invented" in s.lower()


def test_alerts_cancelation_and_referral_drop(warehouse, as_of):
    load_appts(
        warehouse,
        [appt_row(ApptId="1", AppointmentStatus="Complete")]
        + [appt_row(ApptId=f"c{i}", AppointmentStatus="Cancelled") for i in range(2)],
    )
    load_refs(
        warehouse,
        [referral_row(ReferralId="a", DateTimeCreated=date(2026, 8, 1))]
        + [referral_row(ReferralId=f"b{i}", DateTimeCreated=date(2026, 7, 1)) for i in range(5)],
    )
    hits = {h.id: h for h in evaluate_alerts(warehouse, as_of, DEFAULT_ALERTS[:2])}
    assert hits["cancel_over_25"].triggered is True
    assert hits["ref_drop_10"].triggered is True


def test_schedule_hooks_daily_weekly_monthly(as_of):
    from datetime import datetime

    monday = datetime(2026, 8, 31)  # Monday
    assert is_due(CadenceConfig(cadence="daily"), monday)
    assert is_due(CadenceConfig(cadence="weekly", weekday=0), monday)
    assert not is_due(CadenceConfig(cadence="weekly", weekday=2), monday)
    assert is_due(CadenceConfig(cadence="monthly", day_of_month=31), monday)
