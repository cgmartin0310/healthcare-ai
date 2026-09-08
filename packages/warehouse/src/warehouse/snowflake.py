"""Documented path from the local DuckDB warehouse to Snowflake PREP.

This module does not connect to Boom live data. Demo tenants stay local.
Snowflake PREP identifiers are mixed-case and MUST be quoted.
"""

from __future__ import annotations

from warehouse.schema import APPOINTMENT, PATIENT, REFERRAL, qident

SNOWFLAKE_DATABASE = "BOOMREPORTING"
SNOWFLAKE_SCHEMA = "PREP"


def qualified(table: str) -> str:
    return f"{SNOWFLAKE_DATABASE}.{SNOWFLAKE_SCHEMA}.{qident(table)}"


def example_cancelation_sql() -> str:
    """Same locked cancelation definition, quoted for Snowflake."""
    return f"""
SELECT
    SUM(IFF({qident("AppointmentStatus")} IN ('Cancelled', 'No Show'), 1, 0))
    / NULLIF(SUM(IFF({qident("AppointmentStatus")} IN ('Complete', 'Cancelled', 'No Show'), 1, 0)), 0)
        AS cancelation_rate
FROM {qualified("APPOINTMENT")}
WHERE {qident("ApptDate")} >= %s
  AND {qident("ApptDate")} <= %s
""".strip()


def objects_in_scope() -> tuple[str, ...]:
    return (APPOINTMENT.name, PATIENT.name, REFERRAL.name)
