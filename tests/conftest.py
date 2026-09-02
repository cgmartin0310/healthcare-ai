from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from warehouse.schema import APPOINTMENT, PATIENT, REFERRAL
from warehouse.store import Warehouse

AS_OF = date(2026, 9, 2)
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "synthetic"


@pytest.fixture
def as_of() -> date:
    return AS_OF


@pytest.fixture
def warehouse(tmp_path: Path) -> Warehouse:
    return Warehouse(tmp_path / "test.duckdb")


def appt_row(**overrides) -> dict:
    row = {c.name: None for c in APPOINTMENT.columns}
    row.update(
        {
            "ApptId": "A1",
            "ApptDate": date(2026, 8, 10),
            "AppointmentStatus": "Complete",
            "Company": "Example Clinic",
            "Discipline": "OT",
            "PatientId": "P1",
        }
    )
    row.update(overrides)
    return row


def patient_row(**overrides) -> dict:
    row = {c.name: None for c in PATIENT.columns}
    row.update(
        {
            "PatientId": "P1",
            "Company": "Example Clinic",
            "PatientActive": False,
            "AgeGroup": "Adult",
        }
    )
    row.update(overrides)
    return row


def referral_row(**overrides) -> dict:
    row = {c.name: None for c in REFERRAL.columns}
    row.update(
        {
            "ReferralId": "R1",
            "DateTimeCreated": date(2026, 8, 5),
            "Completed?": 0,
            "Company": "Example Clinic",
            "Discipline": "OT",
            "Location": "Site A",
            "ReferralSource": "Source X",
        }
    )
    row.update(overrides)
    return row


def load_appts(wh: Warehouse, rows: list[dict]) -> None:
    wh.replace_table("APPOINTMENT", pd.DataFrame(rows))


def load_patients(wh: Warehouse, rows: list[dict]) -> None:
    wh.replace_table("PATIENT", pd.DataFrame(rows))


def load_refs(wh: Warehouse, rows: list[dict]) -> None:
    wh.replace_table("REFERRAL", pd.DataFrame(rows))
