"""Load a confirmed mapping into the PREP-shaped warehouse."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from integration_engine.mapper import MappingProposal, mapping_from_dict, read_tabular
from integration_engine.normalize import NORMALIZERS
from warehouse.schema import PREP_TABLES
from warehouse.store import Warehouse, write_json


def apply_mapping(frame: pd.DataFrame, proposal: MappingProposal) -> dict[str, pd.DataFrame]:
    buckets: dict[str, dict[str, pd.Series]] = {}
    for source, (table, column) in proposal.bindings().items():
        if source not in frame.columns:
            raise ValueError(f"Mapped source column missing from file: {source}")
        series = frame[source]
        if column in NORMALIZERS:
            series = series.map(NORMALIZERS[column])
        buckets.setdefault(table, {})[column] = series
    out: dict[str, pd.DataFrame] = {}
    for table, cols in buckets.items():
        mapped = pd.DataFrame(cols)
        # Ensure all PREP columns exist
        for col in PREP_TABLES[table].columns:
            if col.name not in mapped.columns:
                mapped[col.name] = pd.NA
        out[table] = mapped
    return out


def load_mapped_file(
    warehouse: Warehouse,
    path: str | Path,
    proposal: MappingProposal | dict[str, Any],
    *,
    tenant_id: str,
    mode: str = "replace",
    manifest_path: Path | None = None,
) -> dict[str, int]:
    if isinstance(proposal, dict):
        proposal = mapping_from_dict(proposal)
    if not proposal.confirmed:
        raise ValueError("Mapping is not confirmed. Human confirm is required before load.")
    frame = read_tabular(path)
    tables = apply_mapping(frame, proposal)
    counts: dict[str, int] = {}
    for table_name, mapped in tables.items():
        if mode == "append":
            counts[table_name] = warehouse.append_table(table_name, mapped)
        else:
            counts[table_name] = warehouse.replace_table(table_name, mapped)
    if manifest_path:
        write_json(
            manifest_path,
            {
                "tenant_id": tenant_id,
                "source_path": str(Path(path)),
                "synthetic_example": proposal.synthetic_example,
                "entity_guess": proposal.entity_guess,
                "rows_loaded": counts,
                "note": "Example/synthetic dumps are tied to no real clinic.",
            },
        )
    return counts
