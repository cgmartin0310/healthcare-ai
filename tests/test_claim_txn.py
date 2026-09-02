"""CLAIM_TXN is the payment source of truth when present. Fallback to appointment rollups."""

from __future__ import annotations

from datetime import date

import pandas as pd

from integration_engine.load import load_mapped_file
from integration_engine.mapper import confirm_mapping, propose_mapping
from tests.conftest import FIXTURES, appt_row, load_appts
from warehouse.metrics import ar_past_30_days, cancelation_rate, payments_total
from warehouse.money import money_source
from warehouse.schema import CLAIM_TXN


def test_claim_txn_is_optional_prep_table():
    names = {c.name for c in CLAIM_TXN.columns}
    assert {"TxnId", "ApptId", "PatientId", "Company", "PostedDate", "Payer", "TxnType", "Amount"} <= names
    assert "PatBalance" not in names


def test_appointment_fallback_when_no_claim_txn(warehouse, as_of):
    load_appts(
        warehouse,
        [
            appt_row(
                ApptId="1",
                ApptDate=date(2026, 6, 1),
                InsBalance=80.0,
                PrimaryPayorName="Acme Health",
                LocationName="Site B",
            )
        ],
    )
    assert money_source(warehouse) == "appointment"
    result = ar_past_30_days(warehouse, as_of)
    assert result.value[0]["ins_balance"] == 80.0
    assert result.details["source"] == "appointment"


def test_claim_txn_derives_ins_paid_balance_total(warehouse, as_of):
    load_appts(
        warehouse,
        [
            appt_row(
                ApptId="A9",
                ApptDate=date(2026, 6, 1),
                InsPaid=None,
                InsBalance=None,
                TotalPaid=None,
                FirstInsPayment=None,
                PrimaryPayorName="Acme Health",
                LocationName="Site B",
                AppointmentStatus="Complete",
            )
        ],
    )
    warehouse.replace_table(
        "CLAIM_TXN",
        pd.DataFrame(
            [
                {
                    "TxnId": "T1",
                    "ApptId": "A9",
                    "PatientId": "P1",
                    "Company": "Example Clinic",
                    "PostedDate": date(2026, 6, 1),
                    "Payer": "Acme Health",
                    "TxnType": "charge",
                    "Amount": 200.0,
                    "LocationName": "Site B",
                    "Discipline": "OT",
                },
                {
                    "TxnId": "T2",
                    "ApptId": "A9",
                    "PatientId": "P1",
                    "Company": "Example Clinic",
                    "PostedDate": date(2026, 6, 15),
                    "Payer": "Acme Health",
                    "TxnType": "payment",
                    "Amount": 50.0,
                    "LocationName": "Site B",
                    "Discipline": "OT",
                },
            ]
        ),
    )
    assert money_source(warehouse) == "claim_txn"
    ar = ar_past_30_days(warehouse, as_of)
    assert ar.details["source"] == "claim_txn"
    assert ar.value[0]["ins_balance"] == 150.0
    paid = payments_total(warehouse, date(2026, 6, 1), date(2026, 6, 30))
    assert paid.value == 50.0
    assert paid.details["total_paid"] == 50.0


def test_payments_layout_maps_to_claim_txn():
    path = FIXTURES / "layout_payments" / "SYNTHETIC_EXAMPLE_transactions.csv"
    proposal = propose_mapping(path, entity="CLAIM_TXN")
    bound = {c.source: c.target_column for c in proposal.columns if c.target_column}
    assert bound["txn_id"] == "TxnId"
    assert bound["visit_id"] == "ApptId"
    assert bound["txn_type"] == "TxnType"
    assert bound["amount"] == "Amount"
    assert bound["posted_on"] == "PostedDate"
    confirm_mapping(proposal)


def test_multi_file_visits_referrals_txns(warehouse):
    files = [
        ("APPOINTMENT", FIXTURES / "layout_a" / "SYNTHETIC_EXAMPLE_appointments.csv"),
        ("REFERRAL", FIXTURES / "layout_a" / "SYNTHETIC_EXAMPLE_referrals.csv"),
        ("CLAIM_TXN", FIXTURES / "layout_payments" / "SYNTHETIC_EXAMPLE_transactions.csv"),
    ]
    for entity, path in files:
        load_mapped_file(
            warehouse,
            path,
            confirm_mapping(propose_mapping(path, entity=entity)),
            tenant_id="example-clinic",
            mode="append",
        )
    assert warehouse.count("APPOINTMENT") > 100
    assert warehouse.count("REFERRAL") > 20
    assert warehouse.count("CLAIM_TXN") > 20
    assert money_source(warehouse) == "claim_txn"


def test_appointment_only_load_is_not_a_failed_load(warehouse, as_of):
    path = FIXTURES / "layout_a" / "SYNTHETIC_EXAMPLE_appointments.csv"
    load_mapped_file(
        warehouse,
        path,
        confirm_mapping(propose_mapping(path, entity="APPOINTMENT")),
        tenant_id="example-clinic",
        mode="replace",
    )
    assert warehouse.count("APPOINTMENT") > 100
    assert warehouse.count("REFERRAL") == 0
    assert warehouse.count("CLAIM_TXN") == 0
    cancel = cancelation_rate(warehouse, as_of, months=3)
    assert cancel.value is not None
    assert cancel.unavailable is None


def test_claim_txn_does_not_change_locked_cancelation(warehouse, as_of):
    load_appts(
        warehouse,
        [
            appt_row(ApptId="C1", AppointmentStatus="Complete"),
            appt_row(ApptId="X1", AppointmentStatus="Cancelled"),
            appt_row(ApptId="N1", AppointmentStatus="No Show"),
        ],
    )
    before = cancelation_rate(warehouse, as_of, months=1)
    warehouse.replace_table(
        "CLAIM_TXN",
        pd.DataFrame(
            [
                {
                    "TxnId": "T1",
                    "ApptId": "C1",
                    "PatientId": "P1",
                    "Company": "Example Clinic",
                    "PostedDate": date(2026, 8, 20),
                    "Payer": "Acme Health",
                    "TxnType": "payment",
                    "Amount": 10.0,
                    "LocationName": "Site A",
                    "Discipline": "OT",
                }
            ]
        ),
    )
    after = cancelation_rate(warehouse, as_of, months=1)
    assert after.details["formula"] == "(Cancelled + No Show) / (Complete + Cancelled + No Show)"
    assert after.value == before.value
    assert after.details["numerator"] == before.details["numerator"]
    assert after.details["denominator"] == before.details["denominator"]


def test_no_money_source_says_not_in_dump(warehouse, as_of):
    load_appts(warehouse, [appt_row(ApptId="1")])
    assert money_source(warehouse) == "none"
    ar = ar_past_30_days(warehouse, as_of)
    assert ar.unavailable and "not in the dump" in ar.unavailable
    paid = payments_total(warehouse, date(2026, 6, 1), date(2026, 6, 30))
    assert paid.unavailable and "not in the dump" in paid.unavailable
