"""FastAPI process bound to $PORT. Wraps integration_engine + warehouse + analyst."""

from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from analyst.banner import PRODUCT_BANNER
from analyst.engine import Analyst
from analyst.tenant import DEFAULT_TENANT, parse_as_of
from integration_engine.load import load_mapped_file
from integration_engine.mapper import confirm_mapping, mapping_from_dict, propose_mapping
from warehouse.store import Warehouse

INDEX_HTML = (Path(__file__).with_name("index.html")).read_text(encoding="utf-8")
SAMPLE_QUESTIONS = [
    "Is cancelation over 25% in the last three months?",
    "Which payers have AR sitting past 30 days, by location?",
    "Referral-source drop-off — does volume support another therapist?",
    "How long does a new clinician take to fill a caseload?",
    "Which therapists are profitable after payroll?",
    "What can I do to improve my business?",
]

app = FastAPI(title="Clinic Analyst", docs_url=None, redoc_url=None)
_PENDING: dict[str, dict[str, Any]] = {}


def warehouse_file() -> Path:
    raw = os.environ.get("WAREHOUSE_PATH", "/data/clinic.duckdb")
    path = Path(raw)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        path = Path("./data/clinic.duckdb")
        path.parent.mkdir(parents=True, exist_ok=True)
    return path


def fixtures_dir() -> Path:
    cwd = Path.cwd() / "fixtures" / "synthetic"
    if cwd.exists():
        return cwd
    return Path(__file__).resolve().parents[4] / "fixtures" / "synthetic"


def open_wh() -> Warehouse:
    return Warehouse(warehouse_file())


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return INDEX_HTML


class AskBody(BaseModel):
    question: str = Field(min_length=1)
    as_of: str | None = None


class ConfirmBody(BaseModel):
    upload_id: str


class LoadBody(BaseModel):
    upload_id: str


class DemoBody(BaseModel):
    as_of: str | None = None


@app.post("/api/propose")
async def api_propose(
    file: UploadFile = File(...),
    entity: str | None = Form(None),
) -> dict[str, Any]:
    name = file.filename or "upload.csv"
    suffix = Path(name).suffix.lower()
    if suffix not in {".csv", ".xlsx", ".xls", ".txt"}:
        raise HTTPException(400, "Upload a CSV or xlsx file.")
    dest = Path(tempfile.mkdtemp(prefix="ca-upload-")) / Path(name).name
    dest.write_bytes(await file.read())
    ent = entity if entity in {"APPOINTMENT", "REFERRAL", "PATIENT"} else None
    proposal = propose_mapping(dest, entity=ent)
    upload_id = uuid.uuid4().hex
    _PENDING[upload_id] = {"path": str(dest), "mapping": proposal.to_dict()}
    mapping = proposal.to_dict()
    for col in mapping.get("columns", []):
        col["sample_values"] = []  # PHI off default screens
    return {
        "banner": PRODUCT_BANNER,
        "upload_id": upload_id,
        "mapping": mapping,
    }


@app.post("/api/confirm")
def api_confirm(body: ConfirmBody) -> dict[str, Any]:
    rec = _PENDING.get(body.upload_id)
    if not rec:
        raise HTTPException(404, "Unknown upload. Propose a mapping first.")
    try:
        confirmed = confirm_mapping(mapping_from_dict(rec["mapping"]))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    rec["mapping"] = confirmed.to_dict()
    mapping = confirmed.to_dict()
    for col in mapping.get("columns", []):
        col["sample_values"] = []
    return {"banner": PRODUCT_BANNER, "upload_id": body.upload_id, "mapping": mapping}


@app.post("/api/load")
def api_load(body: LoadBody) -> dict[str, Any]:
    rec = _PENDING.get(body.upload_id)
    if not rec:
        raise HTTPException(404, "Unknown upload. Propose and confirm first.")
    mapping = mapping_from_dict(rec["mapping"])
    if not mapping.confirmed:
        raise HTTPException(400, "Mapping is not confirmed. Confirm before load.")
    with open_wh() as wh:
        counts = load_mapped_file(
            wh,
            rec["path"],
            mapping,
            tenant_id=DEFAULT_TENANT,
            mode="replace",
        )
    return {"banner": PRODUCT_BANNER, "loaded": counts, "warehouse": str(warehouse_file())}


@app.post("/api/ask")
def api_ask(body: AskBody) -> dict[str, Any]:
    as_of = parse_as_of(body.as_of)
    with open_wh() as wh:
        analyst = Analyst(wh, tenant_id=DEFAULT_TENANT, as_of=as_of)
        return analyst.ask(body.question)


@app.post("/api/demo")
def api_demo(body: DemoBody | None = None) -> dict[str, Any]:
    as_of = parse_as_of((body.as_of if body else None) or "2026-09-02")
    fixtures = fixtures_dir()
    if not fixtures.exists():
        raise HTTPException(500, f"Synthetic fixtures not found at {fixtures}")
    layout_a = {
        "APPOINTMENT": fixtures / "layout_a" / "SYNTHETIC_EXAMPLE_appointments.csv",
        "REFERRAL": fixtures / "layout_a" / "SYNTHETIC_EXAMPLE_referrals.csv",
        "PATIENT": fixtures / "layout_a" / "SYNTHETIC_EXAMPLE_patients.csv",
    }
    layout_b = {
        "APPOINTMENT": fixtures / "layout_b" / "SYNTHETIC_EXAMPLE_visits.csv",
        "REFERRAL": fixtures / "layout_b" / "SYNTHETIC_EXAMPLE_incoming_referrals.csv",
        "PATIENT": fixtures / "layout_b" / "SYNTHETIC_EXAMPLE_clients.csv",
    }
    mapped: list[dict[str, Any]] = []
    for entity, path in layout_b.items():
        proposal = propose_mapping(path, entity=entity)
        mapped.append(
            {
                "layout": "B",
                "entity": entity,
                "columns": sum(1 for c in proposal.columns if c.target_column),
                "loaded": False,
            }
        )
    with open_wh() as wh:
        for entity, path in layout_a.items():
            proposal = confirm_mapping(propose_mapping(path, entity=entity))
            counts = load_mapped_file(
                wh, path, proposal, tenant_id=DEFAULT_TENANT, mode="replace"
            )
            mapped.append(
                {
                    "layout": "A",
                    "entity": entity,
                    "columns": sum(1 for c in proposal.columns if c.target_column),
                    "loaded": counts,
                }
            )
        analyst = Analyst(wh, tenant_id=DEFAULT_TENANT, as_of=as_of)
        answers = [analyst.ask(q) for q in SAMPLE_QUESTIONS]
    return {
        "banner": PRODUCT_BANNER,
        "note": "SYNTHETIC EXAMPLE DATA — tied to no real clinic. DuckDB warehouse, not Snowflake or Postgres.",
        "warehouse": str(warehouse_file()),
        "as_of": as_of.isoformat(),
        "mapped": mapped,
        "answers": answers,
    }
