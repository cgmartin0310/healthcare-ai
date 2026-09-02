"""Locked metric definitions. These tests exist so defs cannot silently drift."""

from __future__ import annotations

from datetime import date

from tests.conftest import appt_row, load_appts, load_patients, load_refs, patient_row, referral_row
from warehouse.dates import last_closed_month
from warehouse.metrics import (
    active_book,
    ar_past_30_days,
    avg_collections,
    avg_paid,
    cancelation_rate,
    churn,
    days_to_pay,
    early_quit_watch,
    payments_total,
    primary_payer_patient_level,
    referrals,
)
from warehouse.staffing import DEFAULT_ROUNDING, demand_next_month, round_fte


def test_last_closed_month_mid_month():
    assert last_closed_month(date(2026, 9, 2)) == (date(2026, 8, 1), date(2026, 8, 31))


def test_last_closed_month_on_month_end_is_that_month():
    assert last_closed_month(date(2026, 8, 31)) == (date(2026, 8, 1), date(2026, 8, 31))


def test_cancelation_excludes_pending_and_waiting(warehouse, as_of):
    rows = (
        [appt_row(ApptId=f"C{i}", AppointmentStatus="Complete") for i in range(10)]
        + [appt_row(ApptId=f"X{i}", AppointmentStatus="Cancelled") for i in range(3)]
        + [appt_row(ApptId=f"N{i}", AppointmentStatus="No Show") for i in range(2)]
        + [appt_row(ApptId=f"P{i}", AppointmentStatus="Pending") for i in range(5)]
        + [appt_row(ApptId=f"W{i}", AppointmentStatus="Waiting") for i in range(1)]
    )
    load_appts(warehouse, rows)
    result = cancelation_rate(warehouse, as_of, months=1)
    assert result.details["complete"] == 10
    assert result.details["cancelled"] == 3
    assert result.details["no_show"] == 2
    assert result.details["pending_excluded"] == 5
    assert result.details["waiting_excluded"] == 1
    assert result.details["denominator"] == 15
    assert result.value == pytest_approx(5 / 15)


def pytest_approx(value: float) -> float:
    return value


def test_cancelation_formula_is_locked(warehouse, as_of):
    load_appts(
        warehouse,
        [
            appt_row(ApptId="1", AppointmentStatus="Complete"),
            appt_row(ApptId="2", AppointmentStatus="Cancelled"),
            appt_row(ApptId="3", AppointmentStatus="No Show"),
            appt_row(ApptId="4", AppointmentStatus="Pending"),
        ],
    )
    result = cancelation_rate(warehouse, as_of, months=1)
    assert result.value == 2 / 3
    assert result.details["formula"] == "(Cancelled + No Show) / (Complete + Cancelled + No Show)"


def test_active_book_ignores_patient_active(warehouse):
    load_appts(
        warehouse,
        [
            appt_row(ApptId="1", PatientId="P_active_flag_off", AppointmentStatus="Complete", ApptDate=date(2026, 8, 4)),
            appt_row(ApptId="2", PatientId="P_active_flag_on", AppointmentStatus="Cancelled", ApptDate=date(2026, 8, 4)),
        ],
    )
    load_patients(
        warehouse,
        [
            patient_row(PatientId="P_active_flag_off", PatientActive=False),
            patient_row(PatientId="P_active_flag_on", PatientActive=True),
        ],
    )
    result = active_book(warehouse, date(2026, 8, 1), date(2026, 8, 31))
    assert result.value == 1
    assert result.details["patient_active_true_but_not_in_book"] == 1
    assert "not used" in result.details["note"]


def test_churn_drops_new_patients_and_uses_closed_months(warehouse, as_of):
    # Prior closed = July, current closed = August.
    # Established patient: Completes in June (first DOS), July, not August → churned.
    # New in July: first DOS in July → dropped from prior cohort.
    # Stayer: Completes in June, July, August → not churned.
    rows = [
        appt_row(ApptId="e1", PatientId="EST", ApptDate=date(2026, 6, 10), AppointmentStatus="Complete"),
        appt_row(ApptId="e2", PatientId="EST", ApptDate=date(2026, 7, 10), AppointmentStatus="Complete"),
        appt_row(ApptId="s1", PatientId="STAY", ApptDate=date(2026, 6, 8), AppointmentStatus="Complete"),
        appt_row(ApptId="s2", PatientId="STAY", ApptDate=date(2026, 7, 8), AppointmentStatus="Complete"),
        appt_row(ApptId="s3", PatientId="STAY", ApptDate=date(2026, 8, 8), AppointmentStatus="Complete"),
        appt_row(ApptId="n1", PatientId="NEW", ApptDate=date(2026, 7, 12), AppointmentStatus="Complete"),
    ]
    load_appts(warehouse, rows)
    result = churn(warehouse, as_of)
    assert result.details["prior_month_start"] == "2026-07-01"
    assert result.details["current_month_start"] == "2026-08-01"
    assert result.details["prior_active"] == 2  # EST + STAY, not NEW
    assert result.details["churned"] == 1  # EST only
    assert result.value == 0.5


def test_churn_grain_is_company_discipline_patient(warehouse, as_of):
    rows = [
        appt_row(ApptId="a1", PatientId="P", Discipline="OT", ApptDate=date(2026, 6, 1), AppointmentStatus="Complete"),
        appt_row(ApptId="a2", PatientId="P", Discipline="OT", ApptDate=date(2026, 7, 1), AppointmentStatus="Complete"),
        appt_row(ApptId="b1", PatientId="P", Discipline="PT", ApptDate=date(2026, 6, 1), AppointmentStatus="Complete"),
        appt_row(ApptId="b2", PatientId="P", Discipline="PT", ApptDate=date(2026, 7, 1), AppointmentStatus="Complete"),
        appt_row(ApptId="b3", PatientId="P", Discipline="PT", ApptDate=date(2026, 8, 1), AppointmentStatus="Complete"),
    ]
    load_appts(warehouse, rows)
    result = churn(warehouse, as_of)
    assert result.details["prior_active"] == 2
    assert result.details["churned"] == 1
    assert result.details["by_discipline"]["OT"]["churned"] == 1
    assert result.details["by_discipline"]["PT"]["churned"] == 0


def test_early_quit_child_ot_uses_six_month_bar(warehouse, as_of):
    # First DOS in April (tenure to Aug = 4 months). Child OT bar is 6. Rate 3/4 = 75%.
    rows = [
        appt_row(ApptId="1", PatientId="C1", Discipline="OT", ApptDate=date(2026, 4, 2), AppointmentStatus="Complete"),
        appt_row(ApptId="2", PatientId="C1", Discipline="OT", ApptDate=date(2026, 8, 2), AppointmentStatus="Cancelled"),
        appt_row(ApptId="3", PatientId="C1", Discipline="OT", ApptDate=date(2026, 8, 9), AppointmentStatus="Cancelled"),
        appt_row(ApptId="4", PatientId="C1", Discipline="OT", ApptDate=date(2026, 8, 16), AppointmentStatus="No Show"),
    ]
    load_appts(warehouse, rows)
    load_patients(warehouse, [patient_row(PatientId="C1", AgeGroup="Child")])
    result = early_quit_watch(warehouse, as_of)
    assert result.value == 1
    assert result.details["flagged"][0]["tenure_bar_months"] == 6


def test_early_quit_adult_ot_uses_three_month_bar_and_clears_when_over(warehouse, as_of):
    # First DOS in January (tenure to Aug = 7). Adult OT bar is 3. High cancelation but over bar → not flagged.
    rows = [
        appt_row(ApptId="1", PatientId="A1", Discipline="OT", ApptDate=date(2026, 1, 5), AppointmentStatus="Complete"),
        appt_row(ApptId="2", PatientId="A1", Discipline="OT", ApptDate=date(2026, 8, 2), AppointmentStatus="Cancelled"),
        appt_row(ApptId="3", PatientId="A1", Discipline="OT", ApptDate=date(2026, 8, 9), AppointmentStatus="Cancelled"),
    ]
    load_appts(warehouse, rows)
    load_patients(warehouse, [patient_row(PatientId="A1", AgeGroup="Adult")])
    result = early_quit_watch(warehouse, as_of)
    assert result.value == 0


def test_referrals_count_rows_conversion_is_completed_flag(warehouse, as_of):
    load_refs(
        warehouse,
        [
            referral_row(ReferralId="1", DateTimeCreated=date(2026, 8, 2), **{"Completed?": 1}),
            referral_row(ReferralId="2", DateTimeCreated=date(2026, 8, 3), **{"Completed?": 0}),
            referral_row(ReferralId="3", DateTimeCreated=date(2026, 8, 4), **{"Completed?": 1}),
            referral_row(ReferralId="4", DateTimeCreated=date(2026, 7, 4), **{"Completed?": 1}),
        ],
    )
    result = referrals(warehouse, as_of, months=1)
    assert result.value["referrals"] == 3
    assert result.value["converted"] == 2
    assert result.value["conversion"] == 2 / 3


def test_primary_payer_is_latest_complete(warehouse):
    load_appts(
        warehouse,
        [
            appt_row(ApptId="1", PatientId="P", ApptDate=date(2026, 8, 1), PrimaryPayorName="Old Payer"),
            appt_row(ApptId="2", PatientId="P", ApptDate=date(2026, 8, 20), PrimaryPayorName="New Payer"),
            appt_row(ApptId="3", PatientId="P", ApptDate=date(2026, 8, 20), PrimaryPayorName="Even Newer", AppointmentStatus="Cancelled"),
        ],
    )
    result = primary_payer_patient_level(warehouse, date(2026, 8, 1), date(2026, 8, 31))
    assert result.value == [{"payer": "New Payer", "patients": 1}]


def test_avg_collections_includes_zeros_and_uses_lag_window(warehouse, as_of):
    # Window end = 2026-07-04 (as_of - 60d), start = 2026-04-04.
    load_appts(
        warehouse,
        [
            appt_row(ApptId="1", ApptDate=date(2026, 5, 1), InsPaid=100, PrimaryPayorName="Acme Health"),
            appt_row(ApptId="2", ApptDate=date(2026, 5, 2), InsPaid=0, PrimaryPayorName="Acme Health"),
            appt_row(ApptId="3", ApptDate=date(2026, 8, 1), InsPaid=999, PrimaryPayorName="Acme Health"),  # inside 60-day lag, out
        ],
    )
    result = avg_collections(warehouse, as_of)
    acme = next(r for r in result.value if r["payer"] == "Acme Health")
    assert acme["claims"] == 2
    assert acme["avg_ins_paid"] == 50


def test_avg_paid_excludes_zeros(warehouse, as_of):
    load_appts(
        warehouse,
        [
            appt_row(ApptId="1", ApptDate=date(2026, 8, 1), InsPaid=80, PrimaryPayorName="Acme Health"),
            appt_row(ApptId="2", ApptDate=date(2026, 8, 2), InsPaid=0, PrimaryPayorName="Acme Health"),
        ],
    )
    result = avg_paid(warehouse, as_of)
    acme = next(r for r in result.value if r["payer"] == "Acme Health")
    assert acme["claims"] == 1
    assert acme["avg_ins_paid"] == 80


def test_days_to_pay_excludes_negatives_and_requires_20(warehouse, as_of):
    rows = [
        appt_row(
            ApptId=f"p{i}",
            ApptDate=date(2026, 6, 1),
            InsPaid=50,
            FirstInsPayment=date(2026, 6, 11),
            PrimaryPayorName="Acme Health",
        )
        for i in range(19)
    ]
    rows.append(
        appt_row(
            ApptId="neg",
            ApptDate=date(2026, 6, 20),
            InsPaid=50,
            FirstInsPayment=date(2026, 6, 1),
            PrimaryPayorName="Acme Health",
        )
    )
    load_appts(warehouse, rows)
    result = days_to_pay(warehouse, as_of)
    assert result.value == []
    assert result.details["insufficient_under_20_claims"][0]["claims"] == 19


def test_days_to_pay_reports_when_20_non_negative(warehouse, as_of):
    rows = [
        appt_row(
            ApptId=f"p{i}",
            ApptDate=date(2026, 6, 1),
            InsPaid=50,
            FirstInsPayment=date(2026, 6, 11),
            PrimaryPayorName="Acme Health",
        )
        for i in range(20)
    ]
    load_appts(warehouse, rows)
    result = days_to_pay(warehouse, as_of)
    assert result.value[0]["claims"] == 20
    assert result.value[0]["avg_days"] == 10


def test_payments_use_total_paid_not_ins_paid(warehouse):
    load_appts(
        warehouse,
        [
            appt_row(ApptId="1", ApptDate=date(2026, 8, 1), TotalPaid=120, InsPaid=80),
            appt_row(ApptId="2", ApptDate=date(2026, 8, 2), TotalPaid=40, InsPaid=40),
        ],
    )
    result = payments_total(warehouse, date(2026, 8, 1), date(2026, 8, 31))
    assert result.value == 160
    assert result.details["ins_paid_contrast_only"] == 120
    assert result.details["do_not_mix"] is True


def test_ar_past_30_groups_by_payer_and_location(warehouse, as_of):
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
            ),
            appt_row(
                ApptId="2",
                ApptDate=date(2026, 6, 2),
                InsPaid=0,
                FirstInsPayment=None,
                PrimaryPayorName="Acme Health",
                Location="Site B",
            ),
            appt_row(
                ApptId="3",
                ApptDate=date(2026, 8, 20),
                InsPaid=0,
                FirstInsPayment=None,
                PrimaryPayorName="Acme Health",
                Location="Site B",
            ),  # not yet 30 days before 2026-09-02
            appt_row(
                ApptId="4",
                ApptDate=date(2026, 6, 3),
                InsPaid=80,
                FirstInsPayment=date(2026, 6, 20),
                PrimaryPayorName="Beacon Plan",
                Location="Site A",
            ),
        ],
    )
    result = ar_past_30_days(warehouse, as_of)
    assert len(result.value) == 1
    assert result.value[0]["payer"] == "Acme Health"
    assert result.value[0]["location"] == "Site B"
    assert result.value[0]["unpaid_completes"] == 2
    assert "not invented" in result.details["schema_gap"].lower() or "not invented" in result.details["schema_gap"]


def test_staffing_demand_and_rounding_are_locked():
    # Completes 100, churn 10%, refs 12, OT: visits-per-new = 1
    # retained = 90; new = 12 * 0.5 * (52/12) * 1 = 26
    dem = demand_next_month(100, 0.10, 12, "OT")
    assert abs(dem - (90 + 12 * 0.5 * (52 / 12) * 1)) < 1e-9
    assert round_fte(1.24, DEFAULT_ROUNDING) == 1.0  # nearest 0.5, min 1
    assert round_fte(1.26, DEFAULT_ROUNDING) == 1.5
    assert round_fte(0.1, DEFAULT_ROUNDING) == 1.0
