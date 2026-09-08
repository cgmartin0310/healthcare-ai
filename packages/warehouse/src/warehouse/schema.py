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
            (
                "appt_id",
                "appointment_id",
                "visit_id",
                "encounter_id",
                "encounterid",
                "encounter_no",
                "session_id",
                "sessionid",
            ),
        ),
        Column(
            "ApptDate",
            "DATE",
            True,
            "Date of service (DOS)",
            (
                "appt_date",
                "date_of_service",
                "dos",
                "visit_date",
                "appointment_date",
                "service_date",
                "svc_date",
                "session_date",
                "sessiondate",
                "svc_day",
                "svcday",
            ),
        ),
        Column(
            "AppointmentStatus",
            "VARCHAR",
            True,
            "Complete / Cancelled / No Show / Pending / Waiting",
            (
                "appointment_status",
                "status",
                "visit_status",
                "appt_status",
                "appt_state",
                "session_status",
                "sessionstatus",
            ),
            "Locked Completes use AppointmentStatus='Complete' (Status='Complete').",
        ),
        Column(
            "Company",
            "VARCHAR",
            True,
            "Clinic / company grain. Overwritten from the logged-in tenant on load.",
            (
                "company",
                "clinic",
                "clinic_name",
                "organization",
                "org",
                "clinic_id",
                "practice",
                "practice_name",
                "practicename",
            ),
        ),
        Column(
            "Discipline",
            "VARCHAR",
            True,
            "OT / PT / ST",
            (
                "discipline",
                "therapy_type",
                "discipline_code",
                "service_line",
                "specialty",
                "disc",
                "modality",
            ),
        ),
        Column(
            "PatientId",
            "VARCHAR",
            True,
            "Patient key (id only; no name/address)",
            (
                "patient_id",
                "patient_num",
                "pt_id",
                "client_id",
                "clientid",
                "member_key",
                "memberkey",
            ),
        ),
        Column(
            "ProviderId",
            "VARCHAR",
            False,
            "Rendering clinician id. Headcount prefers this over display name.",
            (
                "provider_id",
                "providerid",
                "npi",
                "clinician_id",
                "clinician_npi",
                "cliniciannpi",
                "staff_id",
                "staffid",
                "rendering_id",
            ),
            "Not TherapistId. Boom TherapistName does not map here.",
        ),
        Column(
            "ProviderName",
            "VARCHAR",
            False,
            "Rendering clinician display name. Boom TherapistName maps here.",
            (
                "provider_name",
                "providername",
                "therapist_name",
                "therapistname",
                "therapist",
                "rendering_provider",
                "rendering_name",
                "clinician",
                "provider",
                "staff_name",
                "staffname",
            ),
            "Synonym for Boom TherapistName. Do not add a TherapistId column.",
        ),
        Column(
            "CPT",
            "VARCHAR",
            False,
            "Procedure code on the visit. Optional.",
            ("cpt", "cpt_code", "procedure_code", "proc_code"),
        ),
        Column(
            "LocationName",
            "VARCHAR",
            False,
            "Site / office. Needed for AR-by-location and primary location.",
            (
                "location_name",
                "location",
                "site",
                "office",
                "clinic_location",
                "facility",
                "campus",
            ),
        ),
        Column(
            "PrimaryPayorName",
            "VARCHAR",
            False,
            "Visit-level primary payer. Locked primary-payer grain stays here.",
            (
                "primary_payor_name",
                "primary_payer_name",
                "payer",
                "payor",
                "insurance_name",
                "primary_payer",
                "prim_ins",
                "coverage",
            ),
        ),
        Column(
            "SecondaryPayorName",
            "VARCHAR",
            False,
            "COB secondary payer on the visit. Not CurrentPayer. No coverage table.",
            (
                "secondary_payor_name",
                "secondary_payer_name",
                "secondary_payer",
                "secondary_payor",
                "cob_payer",
            ),
            "CLAIM_TXN.Payer remains the payer on each transaction (primary or secondary).",
        ),
        Column(
            "InsPaid",
            "DOUBLE",
            False,
            "Insurance paid. AR/collections use InsPaid except dollar AR aged > 30 (InsBalance).",
            (
                "ins_paid",
                "insurance_paid",
                "ins_payment",
                "ins_pmt",
                "ins_paid_amt",
                "paid_ins",
                "paidins",
            ),
        ),
        Column(
            "InsBalance",
            "DOUBLE",
            False,
            "Insurance balance. Dollar AR aged > 30 = SUM(InsBalance) on Completes.",
            (
                "ins_balance",
                "insurance_balance",
                "ar_balance",
                "ins_ar",
                "open_ar",
                "openar",
            ),
            "Not billed − paid. Not PatBalance. Not Tableau NET AR.",
        ),
        Column(
            "TotalPaid",
            "DOUBLE",
            False,
            "Total paid. Payments use TotalPaid only. Do not mix with InsPaid.",
            (
                "total_paid",
                "payment_total",
                "amount_paid",
                "collected",
                "tot_paid",
                "total_collected",
                "totalcollected",
            ),
        ),
        Column(
            "FirstInsPayment",
            "DATE",
            False,
            "First insurance payment date. Days-to-pay uses this.",
            (
                "first_ins_payment",
                "first_ins_pmt_date",
                "first_insurance_payment",
                "paid_on",
                "first_pmt",
                "first_paid_date",
                "firstpaiddate",
            ),
        ),
        Column(
            "Telehealth",
            "BOOLEAN",
            False,
            "Visit telehealth flag. No locked telehealth metric is implemented.",
            (
                "telehealth",
                "is_telehealth",
                "virtual",
                "tele",
                "virtual_yn",
                "is_video",
                "isvideo",
            ),
        ),
    ),
)

# Early-quit child vs adult is AgeBand at import (from DOB), not a warehouse AgeGroup.
# DOB is never persisted. Locked bars are unchanged.
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
            (
                "patient_id",
                "patient_num",
                "pt_id",
                "client_id",
                "clientid",
                "member_key",
                "memberkey",
            ),
        ),
        Column(
            "Company",
            "VARCHAR",
            True,
            "Clinic / company",
            (
                "company",
                "clinic",
                "clinic_name",
                "organization",
                "practice",
                "practice_name",
                "org",
            ),
        ),
        Column(
            "PatientActive",
            "BOOLEAN",
            False,
            "NOT operationally active. Active book ignores this column.",
            (
                "patient_active",
                "is_active_flag",
                "active",
                "is_active",
                "active_flag",
                "activeflag",
            ),
        ),
        Column(
            "DOB",
            "DATE",
            False,
            "Not persisted. De-id derives AgeBand from DOB at import, then drops DOB.",
            ("dob", "date_of_birth", "birth_date", "birthdate"),
            "Never stored in DuckDB. Not AgeGroup.",
        ),
        Column(
            "AgeBand",
            "VARCHAR",
            False,
            "Child or Adult at import as-of (child = age < 18). Early-quit bars only. Not AgeGroup.",
            ("age_band", "ageband"),
            "Optional. Locked tenure bars are unchanged: PT / adult OT-ST < 3 months; child OT-ST < 6.",
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
            ("referral_id", "ref_id", "incoming_ref_id", "incomingrefid"),
        ),
        Column(
            "DateTimeCreated",
            "TIMESTAMP",
            True,
            "Referrals in timestamp",
            (
                "datetime_created",
                "ref_created_at",
                "created_at",
                "referral_date",
                "date_created",
                "created_on",
                "createdon",
            ),
        ),
        Column(
            "Completed?",
            "INTEGER",
            True,
            "Converted = 1 (eval date populated). Not EVAL notes. Derived from EvalDate when that column is mapped.",
            (
                "completed?",
                "eval_completed",
                "converted",
                "completed",
                "is_converted",
                "eval_done",
                "evaldone",
            ),
        ),
        Column(
            "PatientId",
            "VARCHAR",
            False,
            "Optional patient key on the referral row",
            ("patient_id", "patient_num", "pt_id", "client_id", "clientid", "member_key", "memberkey"),
        ),
        Column(
            "EvalDate",
            "DATE",
            False,
            "Eval date. When present, Completed? is 1 if EvalDate is populated.",
            (
                "eval_date",
                "evaldate",
                "evaluation_date",
                "date_of_eval",
                "eval_on",
                "evalon",
            ),
        ),
        Column(
            "Company",
            "VARCHAR",
            False,
            "Clinic / company",
            ("company", "clinic", "clinic_name", "practice", "practice_name", "org"),
        ),
        Column(
            "Discipline",
            "VARCHAR",
            False,
            "OT / PT / ST",
            ("discipline", "therapy_type", "discipline_code", "disc", "modality"),
        ),
        Column(
            "LocationName",
            "VARCHAR",
            False,
            "Site / office",
            ("location_name", "location", "site", "office", "facility", "campus"),
        ),
        Column(
            "Source",
            "VARCHAR",
            False,
            "Referral source. Often blank. KID often uses PCP Name in the dump; that is not this field.",
            ("source", "referral_source", "referred_by", "referralsource"),
            "Do not use REFERRAL_SOURCES.\"Org Name\" (CST-only) as the generic source.",
        ),
    ),
)

CLAIM_TXN = Table(
    name="CLAIM_TXN",
    grain="one claim-ledger row (charge, allowance, payment, adjustment, or refund). Optional money source of truth. Not a separate CHARGES table.",
    columns=(
        Column(
            "TxnId",
            "VARCHAR",
            True,
            "Transaction key",
            (
                "txn_id",
                "transaction_id",
                "payment_id",
                "charge_id",
                "line_id",
                "lineid",
            ),
        ),
        Column(
            "ApptId",
            "VARCHAR",
            False,
            "Visit key when the dump has one. Kept when present; not required to load.",
            (
                "appt_id",
                "appointment_id",
                "visit_id",
                "encounter_id",
                "encounterid",
                "encounter_no",
                "session_id",
                "sessionid",
            ),
        ),
        Column(
            "ClaimId",
            "VARCHAR",
            False,
            "Optional claim key",
            ("claim_id", "claimid", "claim_number"),
        ),
        Column(
            "PatientId",
            "VARCHAR",
            True,
            "Patient key",
            (
                "patient_id",
                "patient_num",
                "pt_id",
                "client_id",
                "clientid",
                "member_key",
                "memberkey",
            ),
        ),
        Column(
            "Company",
            "VARCHAR",
            True,
            "Clinic / company. Overwritten from the logged-in tenant on every row.",
            ("company", "clinic", "clinic_name", "practice", "practice_name", "org"),
        ),
        Column(
            "PostedDate",
            "DATE",
            True,
            "Date the txn posted",
            (
                "posted_date",
                "posted_on",
                "post_date",
                "txn_date",
                "payment_date",
                "postdate",
            ),
        ),
        Column(
            "DOS",
            "DATE",
            False,
            "Optional date of service on the txn",
            ("dos", "date_of_service", "service_date"),
        ),
        Column(
            "Payer",
            "VARCHAR",
            True,
            "Payer on this txn (primary or secondary). Not visit CurrentPayer.",
            ("payer", "payor", "payer_name", "payor_name", "payorname", "insurance_name", "primary_payor_name"),
        ),
        Column(
            "DenialCode",
            "VARCHAR",
            False,
            "Optional denial / remark code",
            ("denial_code", "denialcode", "remark_code", "carc", "denial"),
        ),
        Column(
            "TxnType",
            "VARCHAR",
            True,
            "charge | allowance | payment | adjustment | refund",
            (
                "txn_type",
                "transaction_type",
                "type",
                "line_type",
                "charge_type",
                "txn_kind",
                "txnkind",
            ),
        ),
        Column(
            "Amount",
            "DOUBLE",
            True,
            "Signed-magnitude amount. Charge positive; others reduce balance unless refund.",
            (
                "amount",
                "txn_amount",
                "payment_amount",
                "charge_amount",
                "billed_amount",
                "amt",
            ),
        ),
        Column(
            "LocationName",
            "VARCHAR",
            False,
            "Site / office",
            ("location_name", "location", "site", "office", "facility", "campus"),
        ),
        Column(
            "Discipline",
            "VARCHAR",
            False,
            "OT / PT / ST",
            ("discipline", "therapy_type", "discipline_code", "disc", "modality"),
        ),
    ),
)

PREP_TABLES: dict[str, Table] = {
    APPOINTMENT.name: APPOINTMENT,
    PATIENT.name: PATIENT,
    REFERRAL.name: REFERRAL,
    CLAIM_TXN.name: CLAIM_TXN,
}

# Filled at load from the logged-in tenant. Do not require these in every upload.
STAMPED_FROM_TENANT = frozenset({"Company"})


def mapping_required_missing(table: Table, mapped_names: set[str]) -> list[str]:
    """Required warehouse fields still unmapped after tenant stamps / derivations."""
    missing: list[str] = []
    for col in table.required_columns:
        if col.name in mapped_names:
            continue
        if col.name in STAMPED_FROM_TENANT:
            continue
        if table.name == "REFERRAL" and col.name == "Completed?" and "EvalDate" in mapped_names:
            continue
        missing.append(f"{table.name}.{col.name}")
    return missing

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
    "canx": STATUS_CANCELLED,
    "no show": STATUS_NO_SHOW,
    "noshow": STATUS_NO_SHOW,
    "no-show": STATUS_NO_SHOW,
    "ns": STATUS_NO_SHOW,
    "pending": STATUS_PENDING,
    "waiting": STATUS_WAITING,
}

DISCIPLINE_ALIASES = {
    "ot": "OT",
    "occ": "OT",
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
