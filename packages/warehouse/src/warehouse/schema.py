"""PREP-shaped warehouse objects.

Snowflake identifiers in PREP are mixed-case and MUST be quoted (e.g. "ApptDate").
No full DDL dump exists. Only columns required by locked metrics (plus the
minimum keys to load a row) are defined here. Do not add parallel metric models.

Boom ClinicId → Company is documented for schema fidelity only. Demo tenants
use generic clinic names and must not display CST/AOT/KID/PTA as the product.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


# Reference only. Do not use these brands as default demo-tenant display names.
BOOM_CLINIC_ID_TO_COMPANY = {
    8: "CST",
    9: "AOT",
    22: "KID",
    24: "PTA",
}


@dataclass(frozen=True)
class Column:
    name: str
    duckdb_type: str
    required: bool
    purpose: str
    synonyms: tuple[str, ...] = ()
    notes: str = ""


@dataclass(frozen=True)
class Table:
    name: str
    columns: tuple[Column, ...]
    grain: str

    def column(self, name: str) -> Column:
        for col in self.columns:
            if col.name == name:
                return col
        raise KeyError(name)

    @property
    def required_columns(self) -> tuple[Column, ...]:
        return tuple(c for c in self.columns if c.required)


APPOINTMENT = Table(
    name="APPOINTMENT",
    grain="one visit / appointment row",
    columns=(
        Column(
            "ApptId",
            "VARCHAR",
            True,
            "Visit key",
            ("appt_id", "appointment_id", "visit_id", "encounter_id"),
        ),
        Column(
            "ApptDate",
            "DATE",
            True,
            "Date of service (DOS)",
            ("appt_date", "date_of_service", "dos", "visit_date", "appointment_date", "service_date"),
        ),
        Column(
            "AppointmentStatus",
            "VARCHAR",
            True,
            "Complete / Cancelled / No Show / Pending / Waiting",
            ("appointment_status", "status", "visit_status", "appt_status"),
            "Locked Completes use AppointmentStatus='Complete' (Status='Complete').",
        ),
        Column(
            "Company",
            "VARCHAR",
            True,
            "Clinic / company grain",
            ("company", "clinic", "clinic_name", "organization", "org", "clinic_id"),
        ),
        Column(
            "Discipline",
            "VARCHAR",
            True,
            "OT / PT / ST",
            ("discipline", "therapy_type", "discipline_code", "service_line", "specialty"),
        ),
        Column(
            "PatientId",
            "VARCHAR",
            True,
            "Patient key (id only; no name/address)",
            ("patient_id", "patient_num", "pt_id", "client_id"),
        ),
        Column(
            "TherapistName",
            "VARCHAR",
            False,
            "Rendering clinician (id-like display name, not a patient)",
            ("therapist_name", "therapist", "rendering_provider", "clinician", "provider"),
        ),
        Column(
            "LocationName",
            "VARCHAR",
            False,
            "Site / office. Needed for AR-by-location and primary location.",
            ("location_name", "location", "site", "office", "clinic_location", "facility"),
        ),
        Column(
            "PrimaryPayorName",
            "VARCHAR",
            False,
            "Visit-level primary payer",
            ("primary_payor_name", "primary_payer_name", "payer", "payor", "insurance_name", "primary_payer"),
        ),
        Column(
            "InsPaid",
            "DOUBLE",
            False,
            "Insurance paid. AR/collections use InsPaid except dollar AR aged > 30 (InsBalance).",
            ("ins_paid", "insurance_paid", "ins_payment"),
        ),
        Column(
            "InsBalance",
            "DOUBLE",
            False,
            "Insurance balance. Dollar AR aged > 30 = SUM(InsBalance) on Completes.",
            ("ins_balance", "insurance_balance"),
            "Not billed − paid. Not PatBalance. Not Tableau NET AR.",
        ),
        Column(
            "TotalPaid",
            "DOUBLE",
            False,
            "Total paid. Payments use TotalPaid only. Do not mix with InsPaid.",
            ("total_paid", "payment_total", "amount_paid"),
        ),
        Column(
            "FirstInsPayment",
            "DATE",
            False,
            "First insurance payment date. Days-to-pay uses this.",
            ("first_ins_payment", "first_ins_pmt_date", "first_insurance_payment"),
        ),
        Column(
            "Telehealth",
            "BOOLEAN",
            False,
            "Visit telehealth flag. No locked telehealth metric is implemented.",
            ("telehealth", "is_telehealth", "virtual"),
        ),
    ),
)

# Early-quit child vs adult is derived from PATIENT.DOB. Do not store AgeGroup.
CHILD_AGE_YEARS = 18

PATIENT = Table(
    name="PATIENT",
    grain="Company × PatientId",
    columns=(
        Column(
            "PatientId",
            "VARCHAR",
            True,
            "Patient key",
            ("patient_id", "patient_num", "pt_id", "client_id"),
        ),
        Column(
            "Company",
            "VARCHAR",
            True,
            "Clinic / company",
            ("company", "clinic", "clinic_name", "organization"),
        ),
        Column(
            "PatientActive",
            "BOOLEAN",
            False,
            "NOT operationally active. Active book ignores this column.",
            ("patient_active", "is_active_flag", "active", "is_active"),
        ),
        Column(
            "DOB",
            "DATE",
            False,
            "Date of birth. Early-quit bars derive child vs adult from this.",
            ("dob", "date_of_birth", "birth_date", "birthdate"),
            "Not shown on default screens. Child = age < 18 at last closed month end.",
        ),
    ),
)

REFERRAL = Table(
    name="REFERRAL",
    grain="one referral row (referrals in = count of rows)",
    columns=(
        Column(
            "ReferralId",
            "VARCHAR",
            False,
            "Optional source key. Referrals = COUNT rows, not this id.",
            ("referral_id", "ref_id"),
        ),
        Column(
            "DateTimeCreated",
            "TIMESTAMP",
            True,
            "Referrals in timestamp",
            ("datetime_created", "ref_created_at", "created_at", "referral_date", "date_created"),
        ),
        Column(
            "Completed?",
            "INTEGER",
            True,
            "Converted = 1 (eval date populated). Not EVAL notes.",
            ("completed?", "eval_completed", "converted", "completed", "is_converted"),
        ),
        Column(
            "Company",
            "VARCHAR",
            False,
            "Clinic / company",
            ("company", "clinic", "clinic_name"),
        ),
        Column(
            "Discipline",
            "VARCHAR",
            False,
            "OT / PT / ST",
            ("discipline", "therapy_type", "discipline_code"),
        ),
        Column(
            "LocationName",
            "VARCHAR",
            False,
            "Site / office",
            ("location_name", "location", "site", "office"),
        ),
        Column(
            "Source",
            "VARCHAR",
            False,
            "Referral source. Often blank. KID often uses PCP Name in the dump; that is not this field.",
            ("source", "referral_source", "referred_by"),
            "Do not use REFERRAL_SOURCES.\"Org Name\" (CST-only) as the generic source.",
        ),
    ),
)

CLAIM_TXN = Table(
    name="CLAIM_TXN",
    grain="one insurance claim transaction (optional payment source of truth)",
    columns=(
        Column(
            "TxnId",
            "VARCHAR",
            True,
            "Transaction key",
            ("txn_id", "transaction_id", "payment_id", "line_id"),
        ),
        Column(
            "ApptId",
            "VARCHAR",
            True,
            "Visit key",
            ("appt_id", "appointment_id", "visit_id", "encounter_id"),
        ),
        Column(
            "PatientId",
            "VARCHAR",
            True,
            "Patient key",
            ("patient_id", "patient_num", "pt_id", "client_id"),
        ),
        Column(
            "Company",
            "VARCHAR",
            True,
            "Clinic / company",
            ("company", "clinic", "clinic_name"),
        ),
        Column(
            "PostedDate",
            "DATE",
            True,
            "Date the txn posted",
            ("posted_date", "posted_on", "post_date", "txn_date", "payment_date"),
        ),
        Column(
            "Payer",
            "VARCHAR",
            True,
            "Payer on the txn. Insurance only for InsPaid / InsBalance.",
            ("payer", "payor", "payer_name", "insurance_name", "primary_payor_name"),
        ),
        Column(
            "TxnType",
            "VARCHAR",
            True,
            "charge | allowance | payment | adjustment | refund",
            ("txn_type", "transaction_type", "type", "line_type"),
        ),
        Column(
            "Amount",
            "DOUBLE",
            True,
            "Signed-magnitude amount. Charge positive; others reduce balance unless refund.",
            ("amount", "txn_amount", "payment_amount"),
        ),
        Column(
            "LocationName",
            "VARCHAR",
            False,
            "Site / office",
            ("location_name", "location", "site", "office"),
        ),
        Column(
            "Discipline",
            "VARCHAR",
            False,
            "OT / PT / ST",
            ("discipline", "therapy_type", "discipline_code"),
        ),
    ),
)

PREP_TABLES: dict[str, Table] = {
    APPOINTMENT.name: APPOINTMENT,
    PATIENT.name: PATIENT,
    REFERRAL.name: REFERRAL,
    CLAIM_TXN.name: CLAIM_TXN,
}

TXN_TYPES = ("charge", "allowance", "payment", "adjustment", "refund")
TXN_TYPE_ALIASES = {
    "charge": "charge",
    "chg": "charge",
    "allowance": "allowance",
    "contractual": "allowance",
    "writeoff": "allowance",
    "write off": "allowance",
    "payment": "payment",
    "pmt": "payment",
    "paid": "payment",
    "adjustment": "adjustment",
    "adj": "adjustment",
    "refund": "refund",
}

# Canonical appointment statuses stored in the warehouse.
STATUS_COMPLETE = "Complete"
STATUS_CANCELLED = "Cancelled"
STATUS_NO_SHOW = "No Show"
STATUS_PENDING = "Pending"
STATUS_WAITING = "Waiting"

CANCELATION_NUMERATOR = (STATUS_CANCELLED, STATUS_NO_SHOW)
CANCELATION_DENOMINATOR = (STATUS_COMPLETE, STATUS_CANCELLED, STATUS_NO_SHOW)

STATUS_ALIASES = {
    "complete": STATUS_COMPLETE,
    "completed": STATUS_COMPLETE,
    "cancelled": STATUS_CANCELLED,
    "canceled": STATUS_CANCELLED,
    "cancel": STATUS_CANCELLED,
    "no show": STATUS_NO_SHOW,
    "noshow": STATUS_NO_SHOW,
    "no-show": STATUS_NO_SHOW,
    "pending": STATUS_PENDING,
    "waiting": STATUS_WAITING,
}

DISCIPLINE_ALIASES = {
    "ot": "OT",
    "occupational": "OT",
    "occupational therapy": "OT",
    "pt": "PT",
    "physical": "PT",
    "physical therapy": "PT",
    "st": "ST",
    "speech": "ST",
    "speech therapy": "ST",
    "slp": "ST",
}

def age_band_from_dob(dob: date | None, as_of: date) -> str:
    """Child vs adult for locked early-quit bars. Child = age < 18. Missing DOB → Adult."""
    if dob is None:
        return "Adult"
    years = as_of.year - dob.year - ((as_of.month, as_of.day) < (dob.month, dob.day))
    return "Child" if years < CHILD_AGE_YEARS else "Adult"


def qident(name: str) -> str:
    """Quote a mixed-case PREP identifier for DuckDB / Snowflake."""
    return '"' + name.replace('"', '""') + '"'


def quoted_table(table: str) -> str:
    return qident(table)


def quoted_column(column: str) -> str:
    return qident(column)
