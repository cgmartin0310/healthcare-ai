#!/usr/bin/env python3
"""Generate clearly labeled SYNTHETIC example dumps (two column layouts).

Tied to no real clinic. Id-only / obviously fake. Not Boom brands.
"""

from __future__ import annotations

import calendar
import csv
from datetime import date, datetime, timedelta
from pathlib import Path
import random

ROOT = Path(__file__).resolve().parents[1]
OUT_A = ROOT / "fixtures" / "synthetic" / "layout_a"
OUT_B = ROOT / "fixtures" / "synthetic" / "layout_b"

COMPANY = "Example Clinic"
LOCATIONS = ["Site A", "Site B"]
DISCIPLINES = ["OT", "PT", "ST"]
PAYERS = ["Acme Health", "Beacon Plan", "Example Self-Pay"]
SEED = 42


def month_days(year: int, month: int) -> list[date]:
    last = calendar.monthrange(year, month)[1]
    return [date(year, month, d) for d in range(1, last + 1)]


def write_csv(path: Path, header: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)


def build() -> None:
    rng = random.Random(SEED)
    patients = []
    for i in range(1, 81):
        pid = f"P{1000 + i}"
        is_child = i % 3 == 0
        # PatientActive is deliberately NOT operationally active.
        active_flag = (i % 4 == 0)
        first_month = 1 if i <= 50 else (5 if i <= 65 else 7)
        patients.append(
            {
                "PatientId": pid,
                "Company": COMPANY,
                "PatientActive": "true" if active_flag else "false",
                "DOB": (date(2018, 3, 1) if is_child else date(1988, 3, 1)).isoformat(),
                "_child": is_child,
                "_first_month": first_month,
                "_disc": DISCIPLINES[i % 3],
            }
        )

    therapists = [f"Therapist_{n:02d}" for n in range(1, 8)] + ["Therapist_RAMP"]

    appointments: list[dict] = []
    appt_id = 1

    def add_appt(
        day: date,
        status: str,
        patient: dict,
        therapist: str,
        location: str,
        payer: str,
        *,
        ins_paid: float | None,
        total_paid: float | None,
        first_ins: date | None,
        tele: bool = False,
        ins_balance: float | None = None,
    ) -> None:
        nonlocal appt_id
        if ins_balance is None and status == "Complete" and payer != "Example Self-Pay":
            ins_balance = 0.0 if (ins_paid or 0) > 0 else None
        appointments.append(
            {
                "ApptId": f"A{appt_id:05d}",
                "ApptDate": day.isoformat(),
                "AppointmentStatus": status,
                "Company": COMPANY,
                "Discipline": patient["_disc"],
                "PatientId": patient["PatientId"],
                "TherapistName": therapist,
                "LocationName": location,
                "PrimaryPayorName": payer,
                "InsPaid": "" if ins_paid is None else f"{ins_paid:.2f}",
                "InsBalance": "" if ins_balance is None else f"{ins_balance:.2f}",
                "TotalPaid": "" if total_paid is None else f"{total_paid:.2f}",
                "FirstInsPayment": first_ins.isoformat() if first_ins else "",
                "Telehealth": "true" if tele else "false",
            }
        )
        appt_id += 1

    # Jan–May: established book, mixed statuses (~22% cancelation).
    for month in range(1, 6):
        days = month_days(2026, month)
        cohort = [p for p in patients if p["_first_month"] <= month]
        for patient in cohort:
            therapist = therapists[int(patient["PatientId"][1:]) % 7]
            location = LOCATIONS[int(patient["PatientId"][1:]) % 2]
            payer = PAYERS[int(patient["PatientId"][1:]) % 3]
            visits = 3 if patient["_disc"] != "ST" else 5
            for k in range(visits):
                day = days[(int(patient["PatientId"][1:]) + k * 5) % len(days)]
                roll = rng.random()
                if roll < 0.74:
                    status = "Complete"
                    ins = 85.0 if payer != "Example Self-Pay" else 0.0
                    total = ins + 15.0
                    first = day + timedelta(days=18 + (k % 10))
                elif roll < 0.88:
                    status = "Cancelled"
                    ins = total = None
                    first = None
                elif roll < 0.96:
                    status = "No Show"
                    ins = total = None
                    first = None
                else:
                    status = "Pending" if roll < 0.98 else "Waiting"
                    ins = total = None
                    first = None
                add_appt(day, status, patient, therapist, location, payer, ins_paid=ins, total_paid=total, first_ins=first)

    # Jun–Aug: controlled mix so cancelation stays over 25% after RAMP Completes.
    # Per month: 70 Complete, 36 Cancelled, 14 No Show, 8 Pending/Waiting.
    late_patients = [p for p in patients if p["_first_month"] <= 8]
    for month, extra_cancel in ((6, 0), (7, 0), (8, 4)):
        days = month_days(2026, month)
        statuses: list[str] = (
            ["Complete"] * 70
            + ["Cancelled"] * (36 + extra_cancel)
            + ["No Show"] * 14
            + ["Pending"] * 5
            + ["Waiting"] * 3
        )
        rng.shuffle(statuses)
        for i, status in enumerate(statuses):
            patient = late_patients[i % len(late_patients)]
            therapist = therapists[i % 7]
            location = LOCATIONS[i % 2]
            payer = PAYERS[i % 3]
            day = days[i % len(days)]
            if status == "Complete":
                # Most paid; a slice of Acme @ Site B left unpaid for AR.
                unpaid = payer == "Acme Health" and location == "Site B" and i % 4 == 0
                if unpaid:
                    ins = 0.0
                    total = 0.0
                    first = None
                    balance = 125.0
                else:
                    ins = 90.0 if payer != "Example Self-Pay" else 0.0
                    total = ins + 10.0
                    first = day + timedelta(days=14 + (i % 20))
                    balance = 0.0 if payer != "Example Self-Pay" else 0.0
                add_appt(
                    day,
                    status,
                    patient,
                    therapist,
                    location,
                    payer,
                    ins_paid=ins,
                    total_paid=total,
                    first_ins=first,
                    ins_balance=balance,
                )
            else:
                add_appt(day, status, patient, therapist, location, payer, ins_paid=None, total_paid=None, first_ins=None)

    # Dedicated AR stack: Completes in June (well past 30 days from 2026-09-02).
    ar_patient = patients[2]
    for i in range(18):
        day = date(2026, 6, 2 + (i % 20))
        add_appt(
            day,
            "Complete",
            ar_patient,
            "Therapist_03",
            "Site B",
            "Acme Health",
            ins_paid=0.0,
            total_paid=0.0,
            first_ins=None,
            ins_balance=125.0,
        )

    # Therapist_RAMP: OT Completes ramping to 35/week by June, then stop so
    # summer Completes do not dilute the locked cancelation window.
    ramp_patient_pool = [p for p in patients if p["_disc"] == "OT"]
    for month, weekly in ((3, 10), (4, 18), (5, 28), (6, 36)):
        days = month_days(2026, month)
        for week in range(4):
            week_start = min(week * 7, max(0, len(days) - 7))
            for i in range(weekly):
                day = days[week_start + (i % 7)]
                patient = ramp_patient_pool[(week * weekly + i) % len(ramp_patient_pool)]
                add_appt(
                    day,
                    "Complete",
                    {**patient, "_disc": "OT"},
                    "Therapist_RAMP",
                    "Site A",
                    "Beacon Plan",
                    ins_paid=95.0,
                    total_paid=110.0,
                    first_ins=day + timedelta(days=12),
                )

    # Early-quit watch: child OT patients under 6 months tenure, high cancelation.
    child_ot = [p for p in patients if p["_child"] and p["_disc"] == "OT"][:3]
    for p in child_ot:
        for i, status in enumerate(["Complete", "Cancelled", "Cancelled", "No Show", "Cancelled"] * 3):
            day = date(2026, 7, 1) + timedelta(days=i * 3)
            if day.month > 8:
                break
            add_appt(day, status, {**p, "_disc": "OT"}, "Therapist_02", "Site A", "Beacon Plan",
                     ins_paid=80.0 if status == "Complete" else None,
                     total_paid=90.0 if status == "Complete" else None,
                     first_ins=(day + timedelta(days=10)) if status == "Complete" else None)

    # Open-month noise (Sep 2026) — must not drive closed-month truth.
    for i in range(12):
        add_appt(
            date(2026, 9, 1),
            "Pending",
            patients[i],
            therapists[i % 7],
            "Site A",
            "Beacon Plan",
            ins_paid=None,
            total_paid=None,
            first_ins=None,
        )

    # Referrals: School District drops in August; others steady.
    referrals = []
    ref_id = 1
    monthly_sources = {
        3: {"Physician Group Alpha": 12, "School District Example": 14, "Parent/Self": 6, "Hospital Beta": 4},
        4: {"Physician Group Alpha": 12, "School District Example": 13, "Parent/Self": 6, "Hospital Beta": 4},
        5: {"Physician Group Alpha": 12, "School District Example": 12, "Parent/Self": 6, "Hospital Beta": 4},
        6: {"Physician Group Alpha": 12, "School District Example": 11, "Parent/Self": 6, "Hospital Beta": 4},
        7: {"Physician Group Alpha": 12, "School District Example": 10, "Parent/Self": 6, "Hospital Beta": 4},
        8: {"Physician Group Alpha": 12, "School District Example": 4, "Parent/Self": 6, "Hospital Beta": 4},
    }
    discs = ["OT", "PT", "ST"]
    for month, sources in monthly_sources.items():
        days = month_days(2026, month)
        for source, n in sources.items():
            for i in range(n):
                created = datetime(2026, month, days[i % len(days)].day, 9, 0, 0)
                converted = 1 if i % 2 == 0 else 0
                referrals.append(
                    {
                        "ReferralId": f"R{ref_id:04d}",
                        "DateTimeCreated": created.isoformat(sep=" "),
                        "Completed?": str(converted),
                        "Company": COMPANY,
                        "Discipline": discs[i % 3],
                        "LocationName": LOCATIONS[i % 2],
                        "Source": source if i != 3 else "",  # Source is often blank
                    }
                )
                ref_id += 1

    # Layout A: PREP-like names
    write_csv(
        OUT_A / "SYNTHETIC_EXAMPLE_appointments.csv",
        [
            "ApptId",
            "ApptDate",
            "AppointmentStatus",
            "Company",
            "Discipline",
            "PatientId",
            "TherapistName",
            "LocationName",
            "PrimaryPayorName",
            "InsPaid",
            "InsBalance",
            "TotalPaid",
            "FirstInsPayment",
            "Telehealth",
        ],
        appointments,
    )
    write_csv(
        OUT_A / "SYNTHETIC_EXAMPLE_referrals.csv",
        ["ReferralId", "DateTimeCreated", "Completed?", "Company", "Discipline", "LocationName", "Source"],
        referrals,
    )
    write_csv(
        OUT_A / "SYNTHETIC_EXAMPLE_patients.csv",
        ["PatientId", "Company", "PatientActive", "DOB"],
        [{k: p[k] for k in ("PatientId", "Company", "PatientActive", "DOB")} for p in patients],
    )

    # Layout B: different export names / date format / status labels / 1-0 flags
    visits = []
    for row in appointments:
        status = {
            "Complete": "completed",
            "Cancelled": "canceled",
            "No Show": "no-show",
            "Pending": "pending",
            "Waiting": "waiting",
        }[row["AppointmentStatus"]]
        disc = {"OT": "Occupational", "PT": "Physical", "ST": "Speech"}[row["Discipline"]]
        visits.append(
            {
                "visit_id": row["ApptId"],
                "date_of_service": row["ApptDate"],
                "visit_status": status,
                "clinic_name": row["Company"],
                "therapy_type": disc,
                "patient_num": row["PatientId"],
                "rendering_provider": row["TherapistName"],
                "site": row["LocationName"],
                "insurance_name": row["PrimaryPayorName"],
                "insurance_paid": row["InsPaid"],
                "insurance_balance": row["InsBalance"],
                "amount_paid": row["TotalPaid"],
                "first_ins_pmt_date": row["FirstInsPayment"],
                "is_telehealth": "yes" if row["Telehealth"] == "true" else "no",
            }
        )
    incoming = []
    for row in referrals:
        incoming.append(
            {
                "ref_id": row["ReferralId"],
                "ref_created_at": row["DateTimeCreated"],
                "eval_completed": "Y" if row["Completed?"] == "1" else "N",
                "clinic_name": row["Company"],
                "discipline_code": row["Discipline"],
                "office": row["LocationName"],
                "source": row["Source"],
            }
        )
    clients = [
        {
            "patient_num": p["PatientId"],
            "clinic_name": p["Company"],
            "is_active_flag": "1" if p["PatientActive"] == "true" else "0",
            "dob": p["DOB"],
        }
        for p in patients
    ]
    write_csv(
        OUT_B / "SYNTHETIC_EXAMPLE_visits.csv",
        [
            "visit_id",
            "date_of_service",
            "visit_status",
            "clinic_name",
            "therapy_type",
            "patient_num",
            "rendering_provider",
            "site",
            "insurance_name",
            "insurance_paid",
            "insurance_balance",
            "amount_paid",
            "first_ins_pmt_date",
            "is_telehealth",
        ],
        visits,
    )
    write_csv(
        OUT_B / "SYNTHETIC_EXAMPLE_incoming_referrals.csv",
        ["ref_id", "ref_created_at", "eval_completed", "clinic_name", "discipline_code", "office", "source"],
        incoming,
    )
    write_csv(
        OUT_B / "SYNTHETIC_EXAMPLE_clients.csv",
        ["patient_num", "clinic_name", "is_active_flag", "dob"],
        clients,
    )
    print(f"Wrote {len(appointments)} appointments, {len(referrals)} referrals, {len(patients)} patients")
    print(f"Layout A → {OUT_A}")
    print(f"Layout B → {OUT_B}")


if __name__ == "__main__":
    build()
