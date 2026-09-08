"""Resolve appointment money columns from CLAIM_TXN when present.

Locked metric formulas are unchanged. Only the source of TotalPaid / InsPaid /
InsBalance / FirstInsPayment switches. No PatBalance. InsBalance is not Tableau NET AR.
"""

from __future__ import annotations

from warehouse.schema import APPOINTMENT, qident, quoted_table
from warehouse.store import Warehouse


def claim_txn_present(wh: Warehouse) -> bool:
    try:
        return wh.count("CLAIM_TXN") > 0
    except Exception:
        return False


def money_source(wh: Warehouse) -> str:
    if claim_txn_present(wh):
        return "claim_txn"
    row = wh.fetch_one(
        f"""
        SELECT COUNT(*) FROM {quoted_table("APPOINTMENT")}
        WHERE {qident("InsPaid")} IS NOT NULL
           OR {qident("InsBalance")} IS NOT NULL
           OR {qident("TotalPaid")} IS NOT NULL
           OR {qident("FirstInsPayment")} IS NOT NULL
        """
    )
    if row and int(row[0] or 0) > 0:
        return "appointment"
    return "none"


def _rollup_sql() -> str:
    payer = qident("Payer")
    ins = f"LOWER(COALESCE({payer}, '')) NOT LIKE '%self%pay%'"
    return f"""
        SELECT
            {qident("ApptId")} AS appt_id,
            SUM(CASE WHEN {qident("TxnType")} = 'payment' THEN {qident("Amount")} ELSE 0 END)
              - SUM(CASE WHEN {qident("TxnType")} = 'refund' THEN {qident("Amount")} ELSE 0 END)
                AS total_paid,
            SUM(CASE WHEN {qident("TxnType")} = 'payment' AND {ins} THEN {qident("Amount")} ELSE 0 END)
              - SUM(CASE WHEN {qident("TxnType")} = 'refund' AND {ins} THEN {qident("Amount")} ELSE 0 END)
                AS ins_paid,
            MIN(CASE WHEN {qident("TxnType")} = 'payment' AND {ins} THEN {qident("PostedDate")} END)
                AS first_ins_payment,
            SUM(CASE WHEN {ins} THEN
                CASE {qident("TxnType")}
                    WHEN 'charge' THEN {qident("Amount")}
                    WHEN 'allowance' THEN -{qident("Amount")}
                    WHEN 'payment' THEN -{qident("Amount")}
                    WHEN 'adjustment' THEN -{qident("Amount")}
                    WHEN 'refund' THEN {qident("Amount")}
                    ELSE 0 END
                ELSE 0 END)
                AS ins_balance
        FROM {quoted_table("CLAIM_TXN")}
        GROUP BY 1
    """


def appointment_relation(wh: Warehouse) -> str:
    """FROM-target that looks like APPOINTMENT with resolved money columns."""
    if not claim_txn_present(wh):
        return quoted_table("APPOINTMENT")
    passthrough = []
    for col in APPOINTMENT.columns:
        if col.name in {"InsPaid", "InsBalance", "TotalPaid", "FirstInsPayment"}:
            continue
        passthrough.append(f"a.{qident(col.name)} AS {qident(col.name)}")
    return f"""(
        SELECT
            {", ".join(passthrough)},
            t.total_paid AS {qident("TotalPaid")},
            t.ins_paid AS {qident("InsPaid")},
            t.ins_balance AS {qident("InsBalance")},
            t.first_ins_payment AS {qident("FirstInsPayment")}
        FROM {quoted_table("APPOINTMENT")} a
        LEFT JOIN ({_rollup_sql()}) t ON t.appt_id = a.{qident("ApptId")}
    )"""
