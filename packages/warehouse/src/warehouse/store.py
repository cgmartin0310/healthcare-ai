"""DuckDB warehouse using quoted PREP identifiers.

Local/dev default is DuckDB (columnar, fast, simple). The same quoted
identifiers are the documented path to Snowflake BOOMREPORTING.PREP.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

import duckdb
import pandas as pd

from warehouse.schema import PREP_TABLES, qident, quoted_table


class Warehouse:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.con = duckdb.connect(str(self.path))
        self.ensure_schema()

    def close(self) -> None:
        self.con.close()

    def __enter__(self) -> "Warehouse":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def ensure_schema(self) -> None:
        for table in PREP_TABLES.values():
            expected = [c.name for c in table.columns]
            existing = self._table_columns(table.name)
            if existing is not None and existing != expected:
                # Remaps (Location→LocationName, AgeGroup→DOB) cannot ALTER in place.
                self.con.execute(f"DROP TABLE {quoted_table(table.name)}")
                existing = None
            if existing is None:
                cols = ", ".join(
                    f"{qident(c.name)} {c.duckdb_type}" for c in table.columns
                )
                self.con.execute(
                    f"CREATE TABLE {quoted_table(table.name)} ({cols})"
                )

    def _table_columns(self, table_name: str) -> list[str] | None:
        row = self.con.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_name = ?",
            [table_name],
        ).fetchone()
        if not row or int(row[0] or 0) == 0:
            return None
        described = self.con.execute(f"DESCRIBE {quoted_table(table_name)}").fetchall()
        return [str(r[0]) for r in described]

    def replace_table(self, table_name: str, frame: pd.DataFrame) -> int:
        table = PREP_TABLES[table_name]
        ordered = pd.DataFrame()
        for col in table.columns:
            if col.name in frame.columns:
                ordered[col.name] = frame[col.name]
            else:
                ordered[col.name] = pd.NA
        self.con.execute(f"DELETE FROM {quoted_table(table_name)}")
        if len(ordered) == 0:
            return 0
        self.con.register("_load_df", ordered)
        col_list = ", ".join(qident(c.name) for c in table.columns)
        self.con.execute(
            f"INSERT INTO {quoted_table(table_name)} ({col_list}) SELECT {col_list} FROM _load_df"
        )
        self.con.unregister("_load_df")
        return len(ordered)

    def append_table(self, table_name: str, frame: pd.DataFrame) -> int:
        table = PREP_TABLES[table_name]
        existing = self.fetch_table(table_name)
        combined = pd.concat([existing, frame], ignore_index=True)
        # Drop exact duplicate keys when the table has a natural id.
        key = None
        if table_name == "APPOINTMENT" and "ApptId" in combined.columns:
            key = "ApptId"
        elif table_name == "PATIENT" and {"PatientId", "Company"}.issubset(combined.columns):
            combined = combined.drop_duplicates(subset=["Company", "PatientId"], keep="last")
        elif table_name == "REFERRAL" and "ReferralId" in combined.columns:
            key = "ReferralId"
        elif table_name == "CLAIM_TXN" and "TxnId" in combined.columns:
            key = "TxnId"
        if key:
            combined = combined.drop_duplicates(subset=[key], keep="last")
        return self.replace_table(table_name, combined)

    def fetch_table(self, table_name: str) -> pd.DataFrame:
        table = PREP_TABLES[table_name]
        col_list = ", ".join(qident(c.name) for c in table.columns)
        return self.con.execute(
            f"SELECT {col_list} FROM {quoted_table(table_name)}"
        ).fetchdf()

    def count(self, table_name: str) -> int:
        row = self.con.execute(
            f"SELECT COUNT(*) FROM {quoted_table(table_name)}"
        ).fetchone()
        return int(row[0]) if row else 0

    def execute(self, sql: str, params: Iterable[Any] | None = None) -> duckdb.DuckDBPyConnection:
        if params is None:
            return self.con.execute(sql)
        return self.con.execute(sql, list(params))

    def fetch_df(self, sql: str, params: Iterable[Any] | None = None) -> pd.DataFrame:
        return self.execute(sql, params).fetchdf()

    def fetch_one(self, sql: str, params: Iterable[Any] | None = None) -> tuple[Any, ...] | None:
        return self.execute(sql, params).fetchone()


def json_default(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(type(value))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=json_default) + "\n")
