"""Import de-identification gate.

Not ARX, not OHDSI FHIR Anonymizer, not a legal HIPAA determination.
Safe Harbor-style: drop direct identifiers, HMAC remaining keys, generalize
month-grain dates, derive AgeBand from DOB and do not persist DOB.

The same rules run in ``clinic-analyst deid`` and again on every server ingest.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from warehouse.schema import age_band_from_dob
from warehouse.store import json_default

SAFE_HARBOR_NOTICE = (
    "Safe Harbor identifiers are stripped or hashed before load. "
    "This is not a legal determination that the file is de-identified."
)

# Preview-only HMAC when no tenant is supplied (CLI propose without --tenant).
# Server ingest always uses the per-tenant secret on /data.
_PREVIEW_SECRET = b"clinic-analyst-deid-preview-not-a-tenant-secret"

HASH_TARGETS = {
    "PatientId": (
        "patientid",
        "patient id",
        "patient_id",
        "patient num",
        "patient_num",
        "pt id",
        "pt_id",
        "client id",
        "client_id",
        "clientid",
        "member key",
        "member_key",
        "memberkey",
    ),
    "ProviderId": (
        "providerid",
        "provider id",
        "provider_id",
        "npi",
        "clinician id",
        "clinician_id",
        "clinician npi",
        "clinician_npi",
        "staff id",
        "staff_id",
        "staffid",
        "rendering id",
        "rendering_id",
    ),
    "ApptId": (
        "apptid",
        "appt id",
        "appt_id",
        "appointment id",
        "appointment_id",
        "visit id",
        "visit_id",
        "encounter id",
        "encounter_id",
        "encounterid",
        "encounter no",
        "encounter_no",
        "session id",
        "session_id",
        "sessionid",
    ),
    "ReferralId": (
        "referralid",
        "referral id",
        "referral_id",
        "ref id",
        "ref_id",
        "incoming ref id",
        "incoming_ref_id",
        "incomingrefid",
    ),
    "TxnId": (
        "txnid",
        "txn id",
        "txn_id",
        "transaction id",
        "transaction_id",
        "payment id",
        "payment_id",
        "line id",
        "line_id",
        "lineid",
    ),
    "ClaimId": ("claimid", "claim id", "claim_id", "claim number", "claim_number"),
}

DATE_TARGETS = {
    "ApptDate": (
        "apptdate",
        "appt date",
        "appt_date",
        "date of service",
        "date_of_service",
        "visit date",
        "visit_date",
        "appointment date",
        "appointment_date",
        "service date",
        "service_date",
        "svc date",
        "svc_date",
        "session date",
        "session_date",
        "sessiondate",
        "svc day",
        "svc_day",
        "svcday",
    ),
    "PostedDate": (
        "posteddate",
        "posted date",
        "posted_date",
        "posted on",
        "posted_on",
        "post date",
        "post_date",
        "postdate",
        "txn date",
        "txn_date",
        "payment date",
        "payment_date",
    ),
    "DOS": ("dos",),
    "DateTimeCreated": (
        "datetimecreated",
        "datetime created",
        "datetime_created",
        "ref created at",
        "ref_created_at",
        "created at",
        "created_at",
        "created on",
        "created_on",
        "createdon",
        "referral date",
        "referral_date",
        "date created",
        "date_created",
    ),
}

DOB_HEADERS = frozenset({"dob", "date of birth", "date_of_birth", "birth date", "birth_date", "birthdate"})

# Exact normalized headers that are never mapped / never stored.
_DROP_EXACT = frozenset(
    {
        "name",
        "first",
        "last",
        "firstname",
        "lastname",
        "first name",
        "last name",
        "middle",
        "middle name",
        "patient name",
        "client name",
        "full name",
        "legal name",
        "address",
        "street",
        "street address",
        "addr",
        "address 1",
        "address 2",
        "address1",
        "address2",
        "city",
        "zip",
        "zipcode",
        "zip code",
        "postal",
        "postal code",
        "state",
        "ssn",
        "ss",
        "social security",
        "social security number",
        "mrn",
        "medical record",
        "medical record number",
        "phone",
        "phone number",
        "mobile",
        "cell",
        "telephone",
        "fax",
        "email",
        "e mail",
        "account",
        "account number",
        "account id",
        "member id",
        "memberid",
        "subscriber",
        "subscriber id",
        "subscriberid",
        "insurance id",
        "insuranceid",
        "policy number",
        "policy id",
        "policyid",
        "given name",
        "givenname",
        "family name",
        "familyname",
        "pt first",
        "pt last",
        "pt_first",
        "pt_last",
    }
)

# "something name" is kept when it is an operational PREP dimension, not a person.
_KEEP_NAME_TOKENS = frozenset(
    {
        "payor",
        "payer",
        "provider",
        "therapist",
        "location",
        "company",
        "source",
        "discipline",
        "insurance",
        "primary",
        "secondary",
        "clinic",
        "staff",
        "rendering",
        "practice",
        "org",
    }
)

# Clinician display headers. Never drop as patient PHI.
CLINICIAN_DISPLAY_HEADERS = frozenset(
    {
        "therapist name",
        "therapistname",
        "therapist",
        "provider name",
        "providername",
        "provider",
        "rendering provider",
        "rendering name",
        "renderingname",
        "clinician",
        "staff name",
        "staffname",
    }
)


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(name).strip().lower()).strip()


def _sanitize_tenant_id(tenant_id: str) -> str:
    cleaned = "".join(ch for ch in tenant_id.lower() if ch.isalnum() or ch in "-_")
    if not cleaned or cleaned in {".", ".."}:
        raise ValueError("invalid tenant_id")
    return cleaned


def tenant_data_dir(tenant_id: str) -> Path:
    raw = os.environ.get("CLINIC_ANALYST_DATA_DIR", "./data")
    path = Path(raw).resolve() / "tenants" / _sanitize_tenant_id(tenant_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def deid_secret_path(tenant_id: str) -> Path:
    return tenant_data_dir(tenant_id) / "deid.hmac"


def get_or_create_deid_secret(tenant_id: str) -> bytes:
    """Per-tenant HMAC key on the data disk. Never committed."""
    path = deid_secret_path(tenant_id)
    if path.exists():
        return path.read_bytes()
    secret = secrets.token_bytes(32)
    path.write_bytes(secret)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return secret


def hash_identifier(secret: bytes, value: Any) -> str | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "<na>"}:
        return None
    return hmac.new(secret, text.encode("utf-8"), hashlib.sha256).hexdigest()


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "<na>"}:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    try:
        parsed = pd.to_datetime(value, errors="coerce")
        if pd.isna(parsed):
            return None
        return parsed.date()
    except (TypeError, ValueError):
        return None


def generalize_date(value: Any) -> date | None:
    parsed = _parse_date(value)
    if parsed is None:
        return None
    return date(parsed.year, parsed.month, 1)


def _is_hash_header(norm: str) -> bool:
    compact = norm.replace(" ", "")
    for target, syns in HASH_TARGETS.items():
        if norm == _norm(target) or compact == target.lower() or norm in syns or compact in {s.replace(" ", "") for s in syns}:
            return True
    return False


def _is_date_header(norm: str) -> bool:
    compact = norm.replace(" ", "")
    if compact == "dos" or norm == "dos":
        return True
    for target, syns in DATE_TARGETS.items():
        syn_norms = {_norm(s) for s in syns} | {_norm(target)}
        syn_compacts = {s.replace(" ", "") for s in syn_norms}
        if norm in syn_norms or compact in syn_compacts or compact == target.lower():
            return True
    return False


def _is_dob_header(norm: str) -> bool:
    compact = norm.replace(" ", "")
    return norm in DOB_HEADERS or compact in {s.replace(" ", "").replace("_", "") for s in DOB_HEADERS} or compact == "dateofbirth"


def is_clinician_display_header(name: str) -> bool:
    """TherapistName / provider display names are not patient PHI."""
    norm = _norm(name)
    compact = norm.replace(" ", "")
    return norm in CLINICIAN_DISPLAY_HEADERS or compact in CLINICIAN_DISPLAY_HEADERS


def _is_drop_header(norm: str) -> bool:
    if is_clinician_display_header(norm):
        return False
    if _is_hash_header(norm) or _is_date_header(norm):
        return False
    if _is_dob_header(norm):
        return True
    if norm in _DROP_EXACT:
        return True
    tokens = set(norm.split())
    if tokens & {"ssn", "email", "phone", "telephone", "mobile", "cell", "fax", "mrn"}:
        return True
    if "social" in tokens and "security" in tokens:
        return True
    if "medical" in tokens and "record" in tokens:
        return True
    if tokens & {"address", "street", "zip", "zipcode", "postal"}:
        return True
    if norm == "city" or norm.endswith(" city"):
        return True
    if tokens & {"subscriber", "member"} and tokens & {"id", "number", "name", "subscriber", "member"}:
        return True
    if "insurance" in tokens and tokens & {"id", "number"}:
        return True
    if tokens & {"account", "policy"} and tokens & {"id", "number", "account", "policy"}:
        return True
    if _is_person_name(norm, tokens):
        return True
    return False


def _is_person_name(norm: str, tokens: set[str]) -> bool:
    if norm in {
        "name",
        "first",
        "last",
        "firstname",
        "lastname",
        "first name",
        "last name",
        "full name",
        "legal name",
        "patient name",
        "client name",
        "middle name",
    }:
        return True
    if norm.endswith(" name") or (len(tokens) <= 3 and "name" in tokens):
        if tokens & _KEEP_NAME_TOKENS:
            return False
        return True
    return False


@dataclass
class DeidReceipt:
    notice: str = SAFE_HARBOR_NOTICE
    source_filename: str = ""
    row_count: int = 0
    column_count_in: int = 0
    column_count_out: int = 0
    columns_dropped: list[str] = field(default_factory=list)
    columns_hashed: list[str] = field(default_factory=list)
    dates_generalized: list[str] = field(default_factory=list)
    age_band_derived_from_dob: bool = False
    dob_stored: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def apply_deid(
    frame: pd.DataFrame,
    secret: bytes,
    as_of: date,
    *,
    source_filename: str = "",
    hash_ids: bool = True,
) -> tuple[pd.DataFrame, DeidReceipt]:
    """Drop PHI headers, hash remaining ids, generalize month-grain dates, derive AgeBand."""
    out = frame.copy()
    dropped: list[str] = []
    hashed: list[str] = []
    dates: list[str] = []
    derived = False
    dob_col: str | None = None

    for col in list(out.columns):
        norm = _norm(col)
        if _is_dob_header(norm):
            dob_col = str(col)
            continue
        if _is_drop_header(norm):
            dropped.append(str(col))
            out = out.drop(columns=[col])

    if dob_col is not None and dob_col in out.columns:
        bands = [_age_band_cell(v, as_of) for v in out[dob_col].tolist()]
        out["AgeBand"] = bands
        out = out.drop(columns=[dob_col])
        dropped.append(dob_col)
        derived = True

    for col in list(out.columns):
        norm = _norm(col)
        if _is_hash_header(norm) and hash_ids:
            out[col] = out[col].map(lambda v, s=secret: hash_identifier(s, v))
            hashed.append(str(col))
        elif _is_date_header(norm):
            out[col] = out[col].map(generalize_date)
            dates.append(str(col))

    receipt = DeidReceipt(
        source_filename=Path(source_filename).name if source_filename else "",
        row_count=int(len(out)),
        column_count_in=int(len(frame.columns)),
        column_count_out=int(len(out.columns)),
        columns_dropped=dropped,
        columns_hashed=hashed,
        dates_generalized=dates,
        age_band_derived_from_dob=derived,
        dob_stored=False,
    )
    return out, receipt


def _age_band_cell(value: Any, as_of: date) -> str | None:
    parsed = _parse_date(value)
    if parsed is None:
        return None
    return age_band_from_dob(parsed, as_of)


def hash_mapped_identifiers(tables: dict[str, pd.DataFrame], secret: bytes) -> list[str]:
    hashed: list[str] = []
    for mapped in tables.values():
        for col in HASH_TARGETS:
            if col in mapped.columns:
                mapped[col] = mapped[col].map(lambda v, s=secret: hash_identifier(s, v))
                hashed.append(col)
    return hashed


def generalize_mapped_dates(tables: dict[str, pd.DataFrame]) -> list[str]:
    touched: list[str] = []
    for mapped in tables.values():
        for col in DATE_TARGETS:
            if col in mapped.columns:
                mapped[col] = mapped[col].map(generalize_date)
                touched.append(col)
    return touched


def scrub_patient_dob(tables: dict[str, pd.DataFrame]) -> None:
    """Never persist PATIENT.DOB. AgeBand is the import-time child/adult flag."""
    mapped = tables.get("PATIENT")
    if mapped is None:
        return
    if "DOB" in mapped.columns:
        mapped["DOB"] = pd.NA
    if "AgeBand" in mapped.columns:
        mapped["AgeBand"] = mapped["AgeBand"].map(
            lambda v: v if v in {"Child", "Adult"} else (None if v is None or (isinstance(v, float) and pd.isna(v)) else str(v))
        )


def persist_receipt(tenant_id: str, receipt: DeidReceipt | dict[str, Any], source_filename: str) -> Path:
    folder = tenant_data_dir(tenant_id) / "deid_receipts"
    folder.mkdir(parents=True, exist_ok=True)
    payload = receipt.to_dict() if isinstance(receipt, DeidReceipt) else dict(receipt)
    payload["notice"] = SAFE_HARBOR_NOTICE
    payload["dob_stored"] = False
    # Never persist cell values — receipt is headers and counts only.
    payload.pop("samples", None)
    payload.pop("values", None)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(source_filename).name)[:80] or "upload"
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = folder / f"{stamp}_{safe}.json"
    path.write_text(json.dumps(payload, indent=2, default=json_default) + "\n")
    return path


def write_redacted_csv(frame: pd.DataFrame, dest: Path) -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(dest, index=False)
    return dest


def deid_file(
    path: str | Path,
    *,
    tenant_id: str | None,
    as_of: date,
    out_path: Path | None = None,
    receipt_path: Path | None = None,
) -> tuple[Path, Path, DeidReceipt]:
    """Local pre-send: redacted CSV + receipt next to the original."""
    from integration_engine.mapper import read_tabular

    src = Path(path)
    secret = get_or_create_deid_secret(tenant_id) if tenant_id else _PREVIEW_SECRET
    frame = read_tabular(src)
    redacted, receipt = apply_deid(frame, secret, as_of, source_filename=src.name, hash_ids=True)
    dest = out_path or src.with_name(f"{src.stem}.deid.csv")
    rec_dest = receipt_path or src.with_name(f"{src.stem}.deid.receipt.json")
    write_redacted_csv(redacted, dest)
    rec_dest.write_text(json.dumps(receipt.to_dict(), indent=2, default=json_default) + "\n")
    if tenant_id:
        persist_receipt(tenant_id, receipt, src.name)
    return dest, rec_dest, receipt
