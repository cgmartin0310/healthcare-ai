"""AI-assisted column mapping: propose → human confirm → load.

v1 is one-time file ingest. No EHR login, no portal scrape, no live feed.

The agent is a deterministic synonym + fuzzy + type-hint proposer. Optional
LLM rewrite is out of band and never required. A human must confirm before load.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import pandas as pd

from warehouse.schema import PREP_TABLES, Table


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.strip().lower()).strip()


def _syn_norm(name: str) -> str:
    return _norm(name)


@dataclass
class ColumnProposal:
    source: str
    target_table: str | None
    target_column: str | None
    confidence: float
    rationale: str
    sample_values: list[Any] = field(default_factory=list)


@dataclass
class MappingProposal:
    source_path: str
    entity_guess: str | None
    columns: list[ColumnProposal]
    unmapped_required: list[str]
    notes: list[str]
    confirmed: bool = False
    confirmed_at: str | None = None
    synthetic_example: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "entity_guess": self.entity_guess,
            "columns": [asdict(c) for c in self.columns],
            "unmapped_required": self.unmapped_required,
            "notes": self.notes,
            "confirmed": self.confirmed,
            "confirmed_at": self.confirmed_at,
            "synthetic_example": self.synthetic_example,
        }

    def bindings(self) -> dict[str, tuple[str, str]]:
        """source column → (table, column) for accepted mappings."""
        out: dict[str, tuple[str, str]] = {}
        for col in self.columns:
            if col.target_table and col.target_column:
                out[col.source] = (col.target_table, col.target_column)
        return out


def read_tabular(path: str | Path, nrows: int | None = None) -> pd.DataFrame:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(path, nrows=nrows)
    if suffix in {".csv", ".txt"}:
        return pd.read_csv(path, nrows=nrows)
    raise ValueError(f"Unsupported file type: {suffix}. Use CSV or xlsx.")


def _score_column(source: str, samples: list[Any], table: Table, column_name: str) -> tuple[float, str]:
    col = table.column(column_name)
    src_n = _norm(source)
    target_n = _norm(col.name)
    syns = {_syn_norm(s) for s in col.synonyms} | {target_n, _norm(col.name.replace("?", ""))}
    if src_n == target_n or src_n in syns:
        return 0.98, f"exact/synonym match to {table.name}.{col.name}"
    best_fuzzy = SequenceMatcher(None, src_n, target_n).ratio()
    for syn in syns:
        best_fuzzy = max(best_fuzzy, SequenceMatcher(None, src_n, syn).ratio())
    type_bonus = 0.0
    rationale = f"fuzzy={best_fuzzy:.2f} vs {table.name}.{col.name}"
    text_samples = [str(s).strip() for s in samples if pd.notna(s)]
    if col.duckdb_type == "DATE" and _looks_like_dates(text_samples):
        type_bonus = 0.08
        rationale += "; values look like dates"
    if col.duckdb_type == "DOUBLE" and _looks_like_numbers(text_samples):
        type_bonus = 0.05
        rationale += "; values look numeric"
    if col.name == "AppointmentStatus" and _looks_like_status(text_samples):
        type_bonus = 0.12
        rationale += "; values look like visit statuses"
    if col.name == "Completed?" and _looks_like_flags(text_samples):
        type_bonus = 0.10
        rationale += "; values look like 1/0 conversion flags"
    score = min(0.97, best_fuzzy + type_bonus)
    if score < 0.72:
        return 0.0, "below threshold"
    return score, rationale


def _looks_like_dates(samples: list[str]) -> bool:
    if not samples:
        return False
    ok = 0
    for s in samples[:12]:
        try:
            pd.to_datetime(s, errors="raise")
            ok += 1
        except Exception:
            continue
    return ok >= max(1, len(samples[:12]) // 2)


def _looks_like_numbers(samples: list[str]) -> bool:
    if not samples:
        return False
    ok = 0
    for s in samples[:12]:
        try:
            float(str(s).replace("$", "").replace(",", ""))
            ok += 1
        except Exception:
            continue
    return ok >= max(1, len(samples[:12]) // 2)


def _looks_like_status(samples: list[str]) -> bool:
    tokens = {"complete", "completed", "cancelled", "canceled", "no show", "noshow", "pending", "waiting"}
    return any(_norm(s) in tokens for s in samples)


def _looks_like_flags(samples: list[str]) -> bool:
    tokens = {"0", "1", "true", "false", "yes", "no"}
    return samples and all(_norm(s) in tokens for s in samples[:12] if s != "")


def _guess_entity(columns: list[str], filename: str) -> str | None:
    blob = " ".join(_norm(c) for c in columns) + " " + _norm(filename)
    scores = {
        "APPOINTMENT": 0,
        "REFERRAL": 0,
        "PATIENT": 0,
    }
    appt_hits = ("appt", "visit", "dos", "status", "payor", "payer", "ins paid", "therapist")
    ref_hits = ("referral", "referred", "datetime created", "completed", "eval completed", "source")
    pat_hits = ("patient active", "age group", "age band", "is active")
    for h in appt_hits:
        if h in blob:
            scores["APPOINTMENT"] += 1
    for h in ref_hits:
        if h in blob:
            scores["REFERRAL"] += 1
    for h in pat_hits:
        if h in blob:
            scores["PATIENT"] += 1
    if "referral" in blob and scores["REFERRAL"] >= 1:
        return "REFERRAL"
    if scores["PATIENT"] >= 2 and scores["APPOINTMENT"] == 0:
        return "PATIENT"
    best = max(scores, key=scores.get)
    return best if scores[best] else "APPOINTMENT"


def propose_mapping(path: str | Path, *, entity: str | None = None) -> MappingProposal:
    path = Path(path)
    frame = read_tabular(path, nrows=50)
    entity_guess = entity or _guess_entity(list(frame.columns), path.name)
    notes = [
        "PHI should stay out of the extract where possible.",
        "Default mapped tables do not require patient names, addresses, or claim lists on screen.",
        "Human confirm is required before load.",
    ]
    synthetic = "synthetic" in path.as_posix().lower() or "example" in path.name.lower()
    if synthetic:
        notes.append("Source path is labeled synthetic/example — not a real clinic dump.")

    used_targets: set[tuple[str, str]] = set()
    proposals: list[ColumnProposal] = []
    tables = [PREP_TABLES[entity_guess]] if entity_guess in PREP_TABLES else list(PREP_TABLES.values())

    for source in frame.columns:
        samples = [v for v in frame[source].head(8).tolist() if pd.notna(v)]
        best: tuple[float, str, str, str] | None = None  # score, rationale, table, col
        for table in tables:
            for col in table.columns:
                if (table.name, col.name) in used_targets:
                    continue
                score, rationale = _score_column(str(source), samples, table, col.name)
                if score <= 0:
                    continue
                if best is None or score > best[0]:
                    best = (score, rationale, table.name, col.name)
        if best and best[0] >= 0.72:
            used_targets.add((best[2], best[3]))
            proposals.append(
                ColumnProposal(
                    source=str(source),
                    target_table=best[2],
                    target_column=best[3],
                    confidence=round(best[0], 3),
                    rationale=best[1],
                    sample_values=_safe_samples(samples),
                )
            )
        else:
            proposals.append(
                ColumnProposal(
                    source=str(source),
                    target_table=None,
                    target_column=None,
                    confidence=0.0,
                    rationale="no PREP field above confidence threshold — left unmapped (not invented)",
                    sample_values=_safe_samples(samples),
                )
            )

    required = []
    if entity_guess in PREP_TABLES:
        mapped = {c.target_column for c in proposals if c.target_table == entity_guess}
        for col in PREP_TABLES[entity_guess].required_columns:
            if col.name not in mapped:
                required.append(f"{entity_guess}.{col.name}")

    return MappingProposal(
        source_path=str(path.resolve()),
        entity_guess=entity_guess,
        columns=proposals,
        unmapped_required=required,
        notes=notes,
        synthetic_example=synthetic,
    )


def _safe_samples(samples: list[Any]) -> list[Any]:
    out = []
    for s in samples[:5]:
        if isinstance(s, (date, datetime)):
            out.append(s.isoformat())
        else:
            text = str(s)
            # Never keep anything that looks like a street address or personal name-heavy cell.
            if re.search(r"\d{3,}\s+\w+\s+(st|street|ave|rd|road|blvd)", text, re.I):
                out.append("[redacted]")
            else:
                out.append(text)
    return out


def confirm_mapping(proposal: MappingProposal | dict[str, Any], *, confirmed_by: str = "human") -> MappingProposal:
    if isinstance(proposal, dict):
        proposal = mapping_from_dict(proposal)
    if proposal.unmapped_required:
        raise ValueError(
            "Cannot confirm: required PREP fields are unmapped: "
            + ", ".join(proposal.unmapped_required)
        )
    proposal.confirmed = True
    proposal.confirmed_at = datetime.utcnow().isoformat(timespec="seconds") + f"Z by {confirmed_by}"
    return proposal


def mapping_from_dict(payload: dict[str, Any]) -> MappingProposal:
    cols = [ColumnProposal(**c) for c in payload["columns"]]
    return MappingProposal(
        source_path=payload["source_path"],
        entity_guess=payload.get("entity_guess"),
        columns=cols,
        unmapped_required=list(payload.get("unmapped_required") or []),
        notes=list(payload.get("notes") or []),
        confirmed=bool(payload.get("confirmed")),
        confirmed_at=payload.get("confirmed_at"),
        synthetic_example=bool(payload.get("synthetic_example")),
    )


def load_mapping_json(path: str | Path) -> MappingProposal:
    return mapping_from_dict(json.loads(Path(path).read_text()))


def save_mapping_json(proposal: MappingProposal, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(proposal.to_dict(), indent=2) + "\n")
    return path
