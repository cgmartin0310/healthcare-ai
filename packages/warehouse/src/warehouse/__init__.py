"""Analysis warehouse shaped after Clinic Analyst PREP (quoted mixed-case identifiers)."""

from warehouse.dates import last_closed_month, month_bounds
from warehouse.store import Warehouse
from warehouse.schema import APPOINTMENT, CLAIM_TXN, PATIENT, REFERRAL, PREP_TABLES

__all__ = [
    "Warehouse",
    "APPOINTMENT",
    "PATIENT",
    "REFERRAL",
    "CLAIM_TXN",
    "PREP_TABLES",
    "last_closed_month",
    "month_bounds",
]
