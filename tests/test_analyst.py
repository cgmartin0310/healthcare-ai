from __future__ import annotations

from datetime import date

from analyst.alerts import DEFAULT_ALERTS, evaluate_alerts
from analyst.banner import PRODUCT_BANNER
from analyst.engine import Analyst
from analyst.schedule import CadenceConfig, is_due
from tests.conftest import appt_row, load_appts, load_refs, referral_row
from warehouse.metrics import payroll_present


def test_banner_is_persistent():
    assert "does not have a live future schedule" in PRODUCT_BANNER.lower()
    assert "closed-month" in PRODUCT_BANNER.lower()


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
                Location="Site B",
            )
        ],
    )
    out = Analyst(warehouse, tenant_id="t", as_of=as_of).ask(
        "Which payers have AR sitting past 30 days, by location?"
    )
    assert out["intent"] == "ar_past_30"
    assert "Acme Health" in out["answer"]
    assert "Site B" in out["answer"]
    assert "not invented" in out["answer"].lower() or "not in the described PREP" in out["answer"]


def test_analyst_answers_referrals(warehouse, as_of):
    load_refs(
        warehouse,
        [
            referral_row(ReferralId="1", DateTimeCreated=date(2026, 8, 2), ReferralSource="School District Example", **{"Completed?": 1}),
            referral_row(ReferralId="2", DateTimeCreated=date(2026, 8, 3), ReferralSource="School District Example", **{"Completed?": 0}),
            referral_row(ReferralId="3", DateTimeCreated=date(2026, 7, 2), ReferralSource="School District Example", **{"Completed?": 1}),
            referral_row(ReferralId="4", DateTimeCreated=date(2026, 7, 3), ReferralSource="School District Example", **{"Completed?": 1}),
            referral_row(ReferralId="5", DateTimeCreated=date(2026, 7, 4), ReferralSource="School District Example", **{"Completed?": 0}),
        ],
    )
    out = Analyst(warehouse, tenant_id="t", as_of=as_of).ask(
        "Referral-source drop-off — does volume support another therapist?"
    )
    assert out["intent"] == "referrals"
    assert "School District Example" in out["answer"]
    assert "staffing working model" in out["answer"].lower()


def test_analyst_refuses_payroll_invention(warehouse, as_of):
    assert payroll_present(warehouse) is False
    out = Analyst(warehouse, tenant_id="t", as_of=as_of).ask(
        "Which therapists are profitable after payroll?"
    )
    assert "payroll is not in this dump" in out["answer"].lower()
    assert "invented" in out["answer"].lower()


def test_analyst_caseload_without_fill_says_data_not_there(warehouse, as_of):
    load_appts(
        warehouse,
        [appt_row(ApptId="1", TherapistName="Therapist_01", ApptDate=date(2026, 8, 1))],
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
                Location="Site B",
            )
        ],
    )
    out = Analyst(warehouse, tenant_id="t", as_of=as_of).ask("What can I do to improve my business?")
    assert out["intent"] == "improve_business"
    assert out["suggestions"]
    for s in out["suggestions"]:
        assert "invent" not in s.lower() or "no additional action is invented" in s.lower() or "not invented" in s.lower()


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
