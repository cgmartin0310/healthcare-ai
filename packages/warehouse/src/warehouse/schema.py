"""PREP-shaped warehouse objects.

Snowflake identifiers in PREP are mixed-case and MUST be quoted (e.g. "ApptDate").
No full DDL dump exists. Only columns required by locked metrics (plus the
minimum keys to load a row) are defined here. Do not add parallel metric models.

Boom ClinicId → Company is documented for schema fidelity only. Demo tenants
use generic clinic names and must not display CST/AOT/KID/PTA as the product.
"""

from __future__ import annotations

from dataclasses import dataclass, field


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
            "Location",
            "VARCHAR",
            False,
            "Site / office. Needed for AR-by-location and primary location.",
            ("location", "site", "office", "clinic_location", "facility"),
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
            "Insurance paid. AR/collections use InsPaid only.",
            ("ins_paid", "insurance_paid", "ins_payment"),
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

# AgeGroup is not named in a PREP DDL dump. Early-quit watch is locked and
# requires child vs adult OT-ST tenure bars. If Boom PREP uses a different
# column name, remap here — do not change the tenure bars.
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
            "AgeGroup",
            "VARCHAR",
            False,
            "Child / Adult. Required to apply locked early-quit tenure bars.",
            ("age_group", "age_band", "pediatric_adult"),
            "Inferred landing name — see PR note. Not a second metric model.",
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
            "Location",
            "VARCHAR",
            False,
            "Site / office",
            ("location", "site", "office"),
        ),
        Column(
            "ReferralSource",
            "VARCHAR",
            False,
            "Source. Needed for referral-source drop-off questions.",
            ("referral_source", "source", "referred_by"),
        ),
    ),
)

PREP_TABLES: dict[str, Table] = {
    APPOINTMENT.name: APPOINTMENT,
    PATIENT.name: PATIENT,
    REFERRAL.name: REFERRAL,
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

AGE_GROUP_ALIASES = {
    "child": "Child",
    "pediatric": "Child",
    "paediatric": "Child",
    "kid": "Child",
    "adult": "Adult",
}


def qident(name: str) -> str:
    """Quote a mixed-case PREP identifier for DuckDB / Snowflake."""
    return '"' + name.replace('"', '""') + '"'


def quoted_table(table: str) -> str:
    return qident(table)


def quoted_column(column: str) -> str:
    return qident(column)
