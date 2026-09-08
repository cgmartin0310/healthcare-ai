#!/usr/bin/env python3
"""Generate SYNTHETIC / EXAMPLE clinic-export dumps.

Three clinic profiles, each in a realistic practice-management / billing dialect.
No shipped file uses warehouse column names. Tied to no real clinic. Not Boom data.

After Safe Harbor month-grain dates, caseload_fill's 7-day window sees a month's
Completes on day 1 — so ramps are expressed as monthly Complete counts vs the
locked weekly target (OT/PT 35, ST 70). Tune the PROFILE dicts below.
"""

from __future__ import annotations

import calendar
import csv
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
import random
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT_ROOT = ROOT / "fixtures" / "synthetic" / "profiles"
AS_OF = date(2026, 9, 2)
HISTORY_START = date(2025, 9, 1)
HISTORY_END = date(2026, 8, 31)

FIRSTS = [
    "Avery", "Blake", "Casey", "Drew", "Eden", "Finley", "Gray", "Harper",
    "Indigo", "Jordan", "Kai", "Logan", "Morgan", "Noelle", "Oakley", "Parker",
    "Quinn", "Riley", "Sage", "Tatum", "Uma", "Vale", "Wren", "Yael", "Zion",
]
LASTS = [
    "Example", "Sample", "Demo", "Fixture", "Synthetic", "Maple", "Cedar",
    "Birch", "Pine", "Oak", "Elm", "Willow", "Ash", "Poplar",
]
CLINICIAN_FIRST = [
    "Alex", "Sam", "Jordan", "Reese", "Cameron", "Taylor", "Jamie", "Morgan",
    "Casey", "Riley", "Quinn", "Avery", "Parker", "Drew", "Harper", "Rowan",
    "Skyler", "Emerson", "Finley", "Hayden",
]
CLINICIAN_LAST = [
    "Hale", "Patel", "Nguyen", "Brooks", "Khan", "Ortiz", "Singh", "Walsh",
    "Costa", "Bennett", "Ibrahim", "Cho", "Diaz", "Farley", "Grant", "Huang",
    "Ingram", "Jules", "Keene", "Lowe",
]

# status / discipline spellings the mapper+normalizer must land
STATUS_VARIANTS = {
    "Complete": ("Complete", "completed", "COMPLETED"),
    "Cancelled": ("Cancelled", "Canceled", "CANX"),
    "No Show": ("No Show", "NS", "no-show"),
    "Pending": ("Pending", "pending"),
    "Waiting": ("Waiting", "waiting"),
}
DISC_VARIANTS = {
    "OT": ("OT", "Occupational", "OCC"),
    "PT": ("PT", "Physical", "Physical"),
    "ST": ("ST", "Speech", "SLP"),
}


@dataclass(frozen=True)
class Profile:
    id: str
    folder: str
    display_name: str
    seed: int
    sites: tuple[str, ...]
    disciplines: tuple[str, ...]
    n_patients: int
    child_share: float
    telehealth_rate: float
    cancel_lo: float
    cancel_hi: float
    visits_per_patient_month: float
    payers: tuple[tuple[str, float], ...]  # name, share
    slow_payer: str
    ar_site: str
    drop_source: str
    sources: tuple[str, ...]
    dialect: str  # harbor | riverbend | northside
    tenured: int
    ramp: int
    under: int
    st_tenured: int = 0
    xlsx_entities: tuple[str, ...] = ()


# Easy to retune when Christopher sends calibration numbers.
PROFILES = (
    Profile(
        id="harbor",
        folder="harbor_pediatric",
        display_name="Harbor Pediatric Therapy",
        seed=101,
        sites=("Harbor East", "Harbor West"),
        disciplines=("OT", "ST"),
        n_patients=160,
        child_share=0.82,
        telehealth_rate=0.08,
        cancel_lo=0.14,
        cancel_hi=0.22,
        visits_per_patient_month=2.4,
        payers=(
            ("Lakeside Health Plan", 0.42),
            ("Summit Mutual", 0.33),
            ("Harbor Self-Pay", 0.25),
        ),
        slow_payer="Summit Mutual",
        ar_site="Harbor West",
        drop_source="Lakeside School District",
        sources=(
            "Lakeside School District",
            "Bay Pediatrics Group",
            "Parent / Self",
            "County Early Start",
        ),
        dialect="harbor",
        tenured=4,
        ramp=4,
        under=2,
        st_tenured=2,
        xlsx_entities=("PATIENT",),
    ),
    Profile(
        id="riverbend",
        folder="riverbend_pt",
        display_name="Riverbend Physical Therapy",
        seed=202,
        sites=("River North", "River South", "Mill Clinic"),
        disciplines=("PT",),
        n_patients=140,
        child_share=0.08,
        telehealth_rate=0.04,
        cancel_lo=0.16,
        cancel_hi=0.26,
        visits_per_patient_month=3.6,
        payers=(
            ("Prairie Advantage", 0.40),
            ("Summit Mutual", 0.35),
            ("Riverbend Self-Pay", 0.25),
        ),
        slow_payer="Summit Mutual",
        ar_site="Mill Clinic",
        drop_source="WorksWell Occupational Health",
        sources=(
            "WorksWell Occupational Health",
            "River Orthopedics",
            "Self / Direct",
            "Community Hospital PT",
        ),
        dialect="riverbend",
        tenured=3,
        ramp=3,
        under=2,
        xlsx_entities=("CLAIM_TXN",),
    ),
    Profile(
        id="northside",
        folder="northside_bh",
        display_name="Northside Behavioral Health",
        seed=303,
        sites=("North Campus", "Telehealth Hub"),
        disciplines=("OT",),  # locked FTE target is OT/PT/ST; BH maps onto OT
        n_patients=200,
        child_share=0.18,
        telehealth_rate=0.62,
        cancel_lo=0.12,
        cancel_hi=0.20,
        visits_per_patient_month=1.6,
        payers=(
            ("Northside Care Plus", 0.38),
            ("Summit Mutual", 0.32),
            ("Beacon Behavioral", 0.15),
            ("Northside Self-Pay", 0.15),
        ),
        slow_payer="Summit Mutual",
        ar_site="North Campus",
        drop_source="City EAP Partners",
        sources=(
            "City EAP Partners",
            "Northside Primary Care",
            "Self / Family",
            "County Behavioral",
        ),
        dialect="northside",
        tenured=4,
        ramp=4,
        under=12,
        xlsx_entities=("APPOINTMENT",),
    ),
)


def months_between(start: date, end: date) -> list[tuple[int, int]]:
    out = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        m += 1
        if m == 13:
            m, y = 1, y + 1
    return out


def month_days(year: int, month: int) -> list[date]:
    last = calendar.monthrange(year, month)[1]
    return [date(year, month, d) for d in range(1, last + 1)]


def pick_status(canonical: str, rng: random.Random) -> str:
    return rng.choice(STATUS_VARIANTS[canonical])


def pick_disc(canonical: str, rng: random.Random) -> str:
    return rng.choice(DISC_VARIANTS[canonical])


def fmt_date(day: date, rng: random.Random) -> str:
    style = rng.choice(("iso", "us", "mon"))
    if style == "iso":
        return day.isoformat()
    if style == "us":
        return day.strftime("%m/%d/%Y")
    return day.strftime("%d-%b-%Y")


def fmt_money(amount: float | None, rng: random.Random) -> str:
    if amount is None:
        return ""
    if rng.random() < 0.35:
        return f"${amount:,.2f}"
    return f"{amount:.2f}"


def write_csv(path: Path, header: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_xlsx(path: Path, header: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows, columns=header)
    frame.to_excel(path, index=False)


def write_table(path: Path, header: list[str], rows: list[dict]) -> None:
    if path.suffix.lower() == ".xlsx":
        write_xlsx(path, header, rows)
    else:
        write_csv(path, header, rows)


def dialect_visit_row(profile: Profile, row: dict, rng: random.Random) -> dict:
    label = "SYNTHETIC_EXAMPLE"
    if profile.dialect == "harbor":
        return {
            "EncounterID": row["appt_id"],
            "SvcDay": fmt_date(row["day"], rng),
            "Status": pick_status(row["status"], rng),
            "PracticeName": profile.display_name,
            "TherapyType": pick_disc(row["disc"], rng),
            "ClientID": row["patient_id"],
            "ClinicianNPI": row["provider_id"],
            "Clinician": row["provider_name"],
            "Office": row["site"],
            "Payor": row["payer"],
            "InsPmt": fmt_money(row["ins_paid"], rng),
            "ARBalance": fmt_money(row["ins_balance"], rng),
            "Collected": fmt_money(row["total_paid"], rng),
            "PaidOn": fmt_date(row["first_ins"], rng) if row["first_ins"] else "",
            "Tele": "Y" if row["tele"] else "N",
            "FirstName": row["first"],
            "LastName": row["last"],
            "DateOfBirth": row["dob"].isoformat() if row.get("dob") else "",
            "Phone": row["phone"],
            "Email": row["email"],
            "Street": row["street"],
            "City": row["city"],
            "Zip": row["zip"],
            "MRN": row["mrn"],
            "MemberID": row["member_id"],
            "ExportBatch": label,
            "RowHash": f"junk-{row['appt_id']}",
        }
    if profile.dialect == "riverbend":
        return {
            "encounter_no": row["appt_id"],
            "svc_date": fmt_date(row["day"], rng),
            "appt_state": pick_status(row["status"], rng),
            "org": profile.display_name,
            "disc": pick_disc(row["disc"], rng),
            "pt_id": row["patient_id"],
            "rendering_id": row["provider_id"],
            "rendering_name": row["provider_name"],
            "facility": row["site"],
            "prim_ins": row["payer"],
            "ins_paid_amt": fmt_money(row["ins_paid"], rng),
            "ins_ar": fmt_money(row["ins_balance"], rng),
            "tot_paid": fmt_money(row["total_paid"], rng),
            "first_pmt": fmt_date(row["first_ins"], rng) if row["first_ins"] else "",
            "virtual_yn": "yes" if row["tele"] else "no",
            "pt_first": row["first"],
            "pt_last": row["last"],
            "dob": row["dob"].strftime("%m/%d/%Y") if row.get("dob") else "",
            "phone": row["phone"],
            "email": row["email"],
            "street": row["street"],
            "city": row["city"],
            "zip": row["zip"],
            "mrn": row["mrn"],
            "member_id": row["member_id"],
            "extract_id": label,
        }
    return {
        "SessionId": row["appt_id"],
        "SessionDate": fmt_date(row["day"], rng),
        "SessionStatus": pick_status(row["status"], rng),
        "Practice": profile.display_name,
        "Modality": pick_disc(row["disc"], rng),
        "MemberKey": row["patient_id"],
        "StaffId": row["provider_id"],
        "StaffName": row["provider_name"],
        "Campus": row["site"],
        "Coverage": row["payer"],
        "PaidIns": fmt_money(row["ins_paid"], rng),
        "OpenAR": fmt_money(row["ins_balance"], rng),
        "TotalCollected": fmt_money(row["total_paid"], rng),
        "FirstPaidDate": fmt_date(row["first_ins"], rng) if row["first_ins"] else "",
        "IsVideo": "true" if row["tele"] else "false",
        "GivenName": row["first"],
        "FamilyName": row["last"],
        "DateOfBirth": row["dob"].isoformat() if row.get("dob") else "",
        "Phone": row["phone"],
        "Email": row["email"],
        "Street": row["street"],
        "City": row["city"],
        "Zip": row["zip"],
        "MRN": row["mrn"],
        "MemberID": row["member_id"],
        "BatchTag": label,
    }


def dialect_referral_row(profile: Profile, row: dict, rng: random.Random) -> dict:
    if profile.dialect == "harbor":
        return {
            "IncomingRefID": row["ref_id"],
            "CreatedOn": row["created"].strftime("%Y-%m-%d %H:%M:%S") if rng.random() < 0.5 else row["created"].strftime("%m/%d/%Y %H:%M"),
            "EvalDone": "Y" if row["converted"] else "N",
            "PracticeName": profile.display_name,
            "TherapyType": pick_disc(row["disc"], rng),
            "Office": row["site"],
            "ReferralSource": row["source"],
            "EvalOn": fmt_date(row["eval_date"], rng) if row["eval_date"] else "",
            "ClientID": row.get("patient_id") or "",
            "ChartNote": "SYNTHETIC_EXAMPLE internal note — do not map",
        }
    if profile.dialect == "riverbend":
        return {
            "ref_id": row["ref_id"],
            "created_on": row["created"].isoformat(sep=" "),
            "eval_done": "1" if row["converted"] else "0",
            "org": profile.display_name,
            "disc": pick_disc(row["disc"], rng),
            "facility": row["site"],
            "referral_source": row["source"],
            "eval_on": fmt_date(row["eval_date"], rng) if row["eval_date"] else "",
            "pt_id": row.get("patient_id") or "",
            "intake_queue": "SYNTHETIC_EXAMPLE",
        }
    return {
        "IncomingRefID": row["ref_id"],
        "CreatedOn": row["created"].isoformat(sep=" "),
        "EvalDone": "converted" if row["converted"] else "open",
        "Practice": profile.display_name,
        "Modality": pick_disc(row["disc"], rng),
        "Campus": row["site"],
        "ReferralSource": row["source"],
        "EvalOn": fmt_date(row["eval_date"], rng) if row["eval_date"] else "",
        "MemberKey": row.get("patient_id") or "",
        "RouterFlag": "SYNTHETIC_EXAMPLE",
    }


def dialect_patient_row(profile: Profile, p: dict) -> dict:
    if profile.dialect == "harbor":
        return {
            "ClientID": p["patient_id"],
            "ActiveFlag": "true" if p["active"] else "false",
            "DateOfBirth": p["dob"].isoformat(),
            "FirstName": p["first"],
            "LastName": p["last"],
            "Phone": p["phone"],
            "Email": p["email"],
            "Street": p["street"],
            "City": p["city"],
            "Zip": p["zip"],
            "MRN": p["mrn"],
            "MemberID": p["member_id"],
            "PracticeName": profile.display_name,
        }
    if profile.dialect == "riverbend":
        return {
            "pt_id": p["patient_id"],
            "is_active": "1" if p["active"] else "0",
            "dob": p["dob"].strftime("%m/%d/%Y"),
            "pt_first": p["first"],
            "pt_last": p["last"],
            "phone": p["phone"],
            "email": p["email"],
            "street": p["street"],
            "city": p["city"],
            "zip": p["zip"],
            "mrn": p["mrn"],
            "member_id": p["member_id"],
            "org": profile.display_name,
            "chart_tag": "SYNTHETIC_EXAMPLE",
        }
    return {
        "MemberKey": p["patient_id"],
        "ActiveFlag": "Y" if p["active"] else "N",
        "DateOfBirth": p["dob"].isoformat(),
        "GivenName": p["first"],
        "FamilyName": p["last"],
        "Phone": p["phone"],
        "Email": p["email"],
        "Street": p["street"],
        "City": p["city"],
        "Zip": p["zip"],
        "MRN": p["mrn"],
        "MemberID": p["member_id"],
        "Practice": profile.display_name,
    }


def dialect_txn_row(profile: Profile, row: dict, rng: random.Random) -> dict:
    amt = fmt_money(row["amount"], rng)
    posted = fmt_date(row["posted"], rng)
    if profile.dialect == "harbor":
        return {
            "LineID": row["txn_id"],
            "EncounterID": row["appt_id"],
            "ClientID": row["patient_id"],
            "PostDate": posted,
            "PayorName": row["payer"],
            "TxnKind": row["txn_type"],
            "Amt": amt,
            "Office": row["site"],
            "TherapyType": pick_disc(row["disc"], rng),
            "Denial": row.get("denial") or "",
            "ClaimNo": row.get("claim_id") or "",
            "PracticeName": profile.display_name,
            "ExportBatch": "SYNTHETIC_EXAMPLE",
        }
    if profile.dialect == "riverbend":
        return {
            "line_id": row["txn_id"],
            "encounter_no": row["appt_id"],
            "pt_id": row["patient_id"],
            "post_date": posted,
            "payer_name": row["payer"],
            "txn_kind": row["txn_type"],
            "amt": amt,
            "facility": row["site"],
            "disc": pick_disc(row["disc"], rng),
            "denial": row.get("denial") or "",
            "claim_number": row.get("claim_id") or "",
            "org": profile.display_name,
            "extract_id": "SYNTHETIC_EXAMPLE",
        }
    return {
        "LineID": row["txn_id"],
        "SessionId": row["appt_id"],
        "MemberKey": row["patient_id"],
        "PostDate": posted,
        "PayorName": row["payer"],
        "TxnKind": row["txn_type"],
        "Amt": amt,
        "Campus": row["site"],
        "Modality": pick_disc(row["disc"], rng),
        "Denial": row.get("denial") or "",
        "ClaimNo": row.get("claim_id") or "",
        "Practice": profile.display_name,
        "BatchTag": "SYNTHETIC_EXAMPLE",
    }


def build_providers(profile: Profile) -> list[dict]:
    providers = []
    n = profile.tenured + profile.ramp + profile.under + profile.st_tenured
    hire_months = [(2026, 1), (2026, 2), (2026, 3), (2026, 4)]
    idx = 0
    for i in range(profile.tenured):
        disc = profile.disciplines[i % len(profile.disciplines)]
        if disc == "ST":
            disc = "OT"
        providers.append(
            {
                "id": f"PRV{idx+1:02d}",
                "name": f"{CLINICIAN_FIRST[idx % len(CLINICIAN_FIRST)]} {CLINICIAN_LAST[idx % len(CLINICIAN_LAST)]}",
                "disc": disc,
                "site": profile.sites[idx % len(profile.sites)],
                "role": "tenured",
                "hire": HISTORY_START,
                "monthly": 42 if disc != "ST" else 72,
            }
        )
        idx += 1
    for i in range(profile.st_tenured):
        providers.append(
            {
                "id": f"PRV{idx+1:02d}",
                "name": f"{CLINICIAN_FIRST[idx % len(CLINICIAN_FIRST)]} {CLINICIAN_LAST[idx % len(CLINICIAN_LAST)]}",
                "disc": "ST",
                "site": profile.sites[idx % len(profile.sites)],
                "role": "tenured",
                "hire": HISTORY_START,
                "monthly": 72,
            }
        )
        idx += 1
    for i in range(profile.ramp):
        hy, hm = hire_months[i % len(hire_months)]
        providers.append(
            {
                "id": f"PRV{idx+1:02d}",
                "name": f"{CLINICIAN_FIRST[idx % len(CLINICIAN_FIRST)]} {CLINICIAN_LAST[idx % len(CLINICIAN_LAST)]}",
                "disc": "OT" if "OT" in profile.disciplines else profile.disciplines[0],
                "site": profile.sites[idx % len(profile.sites)],
                "role": "ramp",
                "hire": date(hy, hm, 1),
                "monthly": None,
                "ramp": (10, 18, 28, 42),
            }
        )
        idx += 1
    for i in range(profile.under):
        disc = profile.disciplines[i % len(profile.disciplines)]
        providers.append(
            {
                "id": f"PRV{idx+1:02d}",
                "name": f"{CLINICIAN_FIRST[idx % len(CLINICIAN_FIRST)]} {CLINICIAN_LAST[idx % len(CLINICIAN_LAST)]}",
                "disc": disc,
                "site": profile.sites[idx % len(profile.sites)],
                "role": "under",
                "hire": HISTORY_START,
                "monthly": 18 if disc != "ST" else 28,
            }
        )
        idx += 1
    assert len(providers) == n
    return providers


def monthly_target(provider: dict, year: int, month: int) -> int:
    start = date(year, month, 1)
    if start < provider["hire"]:
        return 0
    if provider["role"] == "ramp":
        delta = (year - provider["hire"].year) * 12 + (month - provider["hire"].month)
        ramp = provider["ramp"]
        if delta < 0:
            return 0
        if delta >= len(ramp):
            return ramp[-1]
        return ramp[delta]
    return int(provider["monthly"])


def seasonality(profile: Profile, month: int) -> float:
    if profile.id == "harbor" and month in {6, 7, 8}:
        return 0.72
    if profile.id == "northside" and month in {12, 1}:
        return 0.88
    return 1.0


def pick_payer(profile: Profile, rng: random.Random) -> str:
    roll = rng.random()
    acc = 0.0
    for name, share in profile.payers:
        acc += share
        if roll <= acc:
            return name
    return profile.payers[-1][0]


def build_profile(profile: Profile) -> dict[str, int]:
    rng = random.Random(profile.seed)
    providers = build_providers(profile)
    patients = []
    for i in range(profile.n_patients):
        child = rng.random() < profile.child_share
        dob = date(rng.randint(2014, 2021), rng.randint(1, 12), rng.randint(1, 28)) if child else date(
            rng.randint(1968, 1998), rng.randint(1, 12), rng.randint(1, 28)
        )
        first = FIRSTS[i % len(FIRSTS)]
        last = LASTS[(i * 3) % len(LASTS)]
        patients.append(
            {
                "patient_id": f"P{2000 + i}",
                "first": first,
                "last": last,
                "dob": dob,
                "child": child,
                "active": i % 5 != 0,
                "phone": f"555-01{i:02d}",
                "email": f"{first.lower()}.{last.lower()}{i}@example.invalid",
                "street": f"{100 + i} Example Way",
                "city": "Sampleville",
                "zip": f"{80000 + (i % 80):05d}",
                "mrn": f"MRN{i:05d}",
                "member_id": f"MEM{i:07d}",
                "provider": providers[i % len(providers)],
                "payer": pick_payer(profile, rng),
                "disc": providers[i % len(providers)]["disc"],
                "churn_after_july": i % 7 == 0,
            }
        )

    visits: list[dict] = []
    appt_n = 1
    month_list = months_between(HISTORY_START, HISTORY_END)
    for year, month in month_list:
        days = month_days(year, month)
        factor = seasonality(profile, month)
        for provider in providers:
            n_complete = int(round(monthly_target(provider, year, month) * factor))
            if n_complete <= 0:
                continue
            cancel_rate = profile.cancel_lo + (profile.cancel_hi - profile.cancel_lo) * (
                0.35 + 0.65 * ((month + profile.sites.index(provider["site"])) % 3) / 2
            )
            extra = max(1, int(round(n_complete * cancel_rate / max(0.05, 1 - cancel_rate))))
            n_cancel = max(1, extra // 2)
            n_ns = max(1, extra - n_cancel)
            n_pending = 2 if month == 8 else 1
            n_waiting = 1
            statuses = (
                ["Complete"] * n_complete
                + ["Cancelled"] * n_cancel
                + ["No Show"] * n_ns
                + ["Pending"] * n_pending
                + ["Waiting"] * n_waiting
            )
            rng.shuffle(statuses)
            panel = [p for p in patients if p["provider"]["id"] == provider["id"]] or patients
            for j, status in enumerate(statuses):
                patient = panel[j % len(panel)]
                if (
                    year == 2026
                    and month == 8
                    and patient["churn_after_july"]
                    and status == "Complete"
                ):
                    status = "Cancelled"
                day = days[j % len(days)]
                payer = patient["payer"]
                tele = rng.random() < profile.telehealth_rate or (
                    profile.id == "northside" and provider["site"] == "Telehealth Hub"
                )
                ins_paid = ins_balance = total_paid = None
                first_ins = None
                if status == "Complete":
                    charge = 125.0 if provider["disc"] == "ST" else 95.0
                    slow = payer == profile.slow_payer
                    unpaid = (
                        slow
                        and provider["site"] == profile.ar_site
                        and year == 2026
                        and month in {6, 7}
                        and j % 3 == 0
                    )
                    self_pay = "self" in payer.lower() and "pay" in payer.lower()
                    if unpaid:
                        ins_paid = 0.0
                        ins_balance = 140.0
                        total_paid = 0.0
                        first_ins = None
                    elif self_pay:
                        ins_paid = 0.0
                        ins_balance = 0.0
                        total_paid = charge
                        first_ins = None
                    else:
                        lag = rng.randint(50, 75) if slow else rng.randint(10, 22)
                        ins_paid = charge - (15.0 if slow else 0.0)
                        ins_balance = 15.0 if slow and j % 5 == 0 else 0.0
                        total_paid = ins_paid
                        first_ins = day + timedelta(days=lag)
                visits.append(
                    {
                        "appt_id": f"A{appt_n:05d}",
                        "day": day,
                        "status": status,
                        "disc": provider["disc"],
                        "patient_id": patient["patient_id"],
                    "first": patient["first"],
                    "last": patient["last"],
                    "dob": patient["dob"],
                    "phone": patient["phone"],
                    "email": patient["email"],
                    "street": patient["street"],
                    "city": patient["city"],
                    "zip": patient["zip"],
                    "mrn": patient["mrn"],
                    "member_id": patient["member_id"],
                    "provider_id": provider["id"],
                        "provider_name": provider["name"],
                        "site": provider["site"],
                        "payer": payer,
                        "ins_paid": ins_paid,
                        "ins_balance": ins_balance,
                        "total_paid": total_paid,
                        "first_ins": first_ins,
                        "tele": tele,
                    }
                )
                appt_n += 1
        # open-month noise is added after the loop

    for i, patient in enumerate(patients[:16]):
        visits.append(
            {
                "appt_id": f"A{appt_n:05d}",
                "day": date(2026, 9, 1 + (i % 8)),
                "status": "Pending",
                "disc": patient["disc"],
                "patient_id": patient["patient_id"],
                "first": patient["first"],
                "last": patient["last"],
                "dob": patient["dob"],
                "phone": patient["phone"],
                "email": patient["email"],
                "street": patient["street"],
                "city": patient["city"],
                "zip": patient["zip"],
                "mrn": patient["mrn"],
                "member_id": patient["member_id"],
                "provider_id": patient["provider"]["id"],
                "provider_name": patient["provider"]["name"],
                "site": patient["provider"]["site"],
                "payer": patient["payer"],
                "ins_paid": None,
                "ins_balance": None,
                "total_paid": None,
                "first_ins": None,
                "tele": False,
            }
        )
        appt_n += 1

    # Duplicate a few rows so the mapper still lands required fields.
    if len(visits) > 20:
        visits.extend(visits[3:6])

    referrals = []
    ref_n = 1
    for year, month in month_list:
        if date(year, month, 1) < date(2025, 11, 1):
            continue
        days = month_days(year, month)
        counts = {src: 12 for src in profile.sources}
        counts[profile.drop_source] = 22 if (year, month) != (2026, 8) else 8
        if (year, month) == (2026, 7):
            counts[profile.drop_source] = 20
        for src, n in counts.items():
            for i in range(n):
                converted = i % 5 != 0 and i % 5 != 1  # 60%
                created = datetime(year, month, days[i % len(days)].day, 9, i % 50)
                eval_day = (created.date() + timedelta(days=8)) if converted else None
                blank = i % 4 == 0
                referrals.append(
                    {
                        "ref_id": f"R{ref_n:04d}",
                        "created": created,
                        "converted": converted,
                        "disc": profile.disciplines[i % len(profile.disciplines)],
                        "site": profile.sites[i % len(profile.sites)],
                        "source": "" if blank else src,
                        "eval_date": eval_day,
                        "patient_id": patients[i % len(patients)]["patient_id"],
                    }
                )
                ref_n += 1

    txns = []
    txn_n = 1
    for visit in visits:
        if visit["status"] != "Complete":
            continue
        charge = 125.0 if visit["disc"] == "ST" else 95.0
        claim = f"CL{visit['appt_id'][1:]}"
        txns.append(
            {
                "txn_id": f"T{txn_n:05d}",
                "appt_id": visit["appt_id"],
                "patient_id": visit["patient_id"],
                "posted": visit["day"],
                "payer": visit["payer"],
                "txn_type": "charge",
                "amount": charge,
                "site": visit["site"],
                "disc": visit["disc"],
                "denial": "CO-45" if visit["ins_paid"] == 0.0 and visit["ins_balance"] else "",
                "claim_id": claim,
            }
        )
        txn_n += 1
        if visit["ins_paid"]:
            txns.append(
                {
                    "txn_id": f"T{txn_n:05d}",
                    "appt_id": visit["appt_id"],
                    "patient_id": visit["patient_id"],
                    "posted": visit["first_ins"] or visit["day"],
                    "payer": visit["payer"],
                    "txn_type": "payment",
                    "amount": visit["ins_paid"],
                    "site": visit["site"],
                    "disc": visit["disc"],
                    "denial": "",
                    "claim_id": claim,
                }
            )
            txn_n += 1
        elif visit["total_paid"]:
            txns.append(
                {
                    "txn_id": f"T{txn_n:05d}",
                    "appt_id": visit["appt_id"],
                    "patient_id": visit["patient_id"],
                    "posted": visit["day"],
                    "payer": visit["payer"],
                    "txn_type": "payment",
                    "amount": visit["total_paid"],
                    "site": visit["site"],
                    "disc": visit["disc"],
                    "denial": "",
                    "claim_id": claim,
                }
            )
            txn_n += 1
        if rng.random() < 0.08:
            txns.append(
                {
                    "txn_id": f"T{txn_n:05d}",
                    "appt_id": visit["appt_id"],
                    "patient_id": visit["patient_id"],
                    "posted": visit["day"],
                    "payer": visit["payer"],
                    "txn_type": "allowance",
                    "amount": 10.0,
                    "site": visit["site"],
                    "disc": visit["disc"],
                    "denial": "",
                    "claim_id": claim,
                }
            )
            txn_n += 1
        if rng.random() < 0.04:
            txns.append(
                {
                    "txn_id": f"T{txn_n:05d}",
                    "appt_id": visit["appt_id"],
                    "patient_id": visit["patient_id"],
                    "posted": visit["day"],
                    "payer": visit["payer"],
                    "txn_type": "adjustment",
                    "amount": 5.0,
                    "site": visit["site"],
                    "disc": visit["disc"],
                    "denial": "",
                    "claim_id": claim,
                }
            )
            txn_n += 1
        if rng.random() < 0.012:
            txns.append(
                {
                    "txn_id": f"T{txn_n:05d}",
                    "appt_id": visit["appt_id"],
                    "patient_id": visit["patient_id"],
                    "posted": visit["day"] + timedelta(days=20),
                    "payer": visit["payer"],
                    "txn_type": "refund",
                    "amount": 25.0,
                    "site": visit["site"],
                    "disc": visit["disc"],
                    "denial": "",
                    "claim_id": claim,
                }
            )
            txn_n += 1

    visit_rows = [dialect_visit_row(profile, v, rng) for v in visits]
    ref_rows = [dialect_referral_row(profile, r, rng) for r in referrals]
    pat_rows = [dialect_patient_row(profile, p) for p in patients]
    txn_rows = [dialect_txn_row(profile, t, rng) for t in txns]

    from web.profiles import PROFILES as CATALOG

    spec = CATALOG[profile.id]
    folder = OUT_ROOT / spec["folder"]
    folder.mkdir(parents=True, exist_ok=True)
    files = spec["files"]
    write_table(folder / files["APPOINTMENT"], list(visit_rows[0].keys()), visit_rows)
    write_table(folder / files["REFERRAL"], list(ref_rows[0].keys()), ref_rows)
    write_table(folder / files["PATIENT"], list(pat_rows[0].keys()), pat_rows)
    write_table(folder / files["CLAIM_TXN"], list(txn_rows[0].keys()), txn_rows)
    return {
        "visits": len(visit_rows),
        "referrals": len(ref_rows),
        "patients": len(pat_rows),
        "txns": len(txn_rows),
    }


def write_index() -> None:
    from web.profiles import DEFAULT_PROFILE, PROFILES as CATALOG, list_profiles

    payload = {
        "note": "SYNTHETIC EXAMPLE profiles — tied to no real clinic.",
        "default": DEFAULT_PROFILE,
        "profiles": list_profiles(),
    }
    path = OUT_ROOT / "index.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")


def build() -> None:
    for profile in PROFILES:
        counts = build_profile(profile)
        print(f"{profile.display_name}: {counts}")
    write_index()
    print(f"Wrote profiles under {OUT_ROOT}")


if __name__ == "__main__":
    build()
