"""FastAPI process bound to $PORT. Wraps integration_engine + warehouse + analyst."""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
from typing import Any

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from analyst.banner import PRODUCT_BANNER
from analyst.engine import Analyst
from analyst.tenant import parse_as_of, warehouse_path
from integration_engine.load import load_mapped_file
from integration_engine.mapper import confirm_mapping, mapping_from_dict, propose_mapping
from warehouse.store import Warehouse
from web.auth import (
    COOKIE_MAX_AGE,
    COOKIE_NAME,
    User,
    login as auth_login,
    seed_demo,
    sign_session,
    signup as auth_signup,
    user_from_cookie,
)

INDEX_HTML = (Path(__file__).with_name("index.html")).read_text(encoding="utf-8")
SAMPLE_QUESTIONS = [
    "Is cancelation over 25% in the last three months?",
    "Which payers have AR sitting past 30 days, by location?",
    "Referral-source drop-off — does volume support another therapist?",
    "How long does a new clinician take to fill a caseload?",
    "Which therapists are profitable after payroll?",
    "What can I do to improve my business?",
]
ENTITIES = ("APPOINTMENT", "REFERRAL", "PATIENT", "CLAIM_TXN")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    seed_demo()
    yield


app = FastAPI(title="Clinic Analyst", docs_url=None, redoc_url=None, lifespan=lifespan)
_PENDING: dict[str, dict[str, Any]] = {}


def fixtures_dir() -> Path:
    cwd = Path.cwd() / "fixtures" / "synthetic"
    if cwd.exists():
        return cwd
    return Path(__file__).resolve().parents[4] / "fixtures" / "synthetic"


def open_wh(tenant_id: str) -> Warehouse:
    return Warehouse(warehouse_path(tenant_id))


def current_user(request: Request) -> User:
    user = user_from_cookie(request.cookies.get(COOKIE_NAME))
    if not user:
        raise HTTPException(status_code=401, detail="Login required.")
    return user


def _set_session(response: Response, user: User) -> None:
    response.set_cookie(
        COOKIE_NAME,
        sign_session(user),
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=False,
        path="/",
    )


def _public_user(user: User) -> dict[str, str]:
    return {
        "email": user.email,
        "tenant_id": user.tenant_id,
        "tenant_name": user.tenant_name,
        "role": user.role,
    }


def _strip_samples(mapping: dict[str, Any]) -> dict[str, Any]:
    for col in mapping.get("columns", []):
        col["sample_values"] = []
    return mapping


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return INDEX_HTML


class AuthBody(BaseModel):
    email: str
    password: str
    clinic_name: str | None = None


class AskBody(BaseModel):
    question: str = Field(min_length=1)
    as_of: str | None = None


class ConfirmBody(BaseModel):
    upload_id: str


class LoadBody(BaseModel):
    upload_id: str
    mode: str = "append"


class DemoBody(BaseModel):
    as_of: str | None = None


@app.post("/api/signup")
def api_signup(body: AuthBody, response: Response) -> dict[str, Any]:
    try:
        user = auth_signup(body.email, body.password, body.clinic_name or "")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    _set_session(response, user)
    return {"banner": PRODUCT_BANNER, "user": _public_user(user)}


@app.post("/api/login")
def api_login(body: AuthBody, response: Response) -> dict[str, Any]:
    try:
        user = auth_login(body.email, body.password)
    except ValueError as exc:
        raise HTTPException(401, str(exc)) from exc
    _set_session(response, user)
    return {"banner": PRODUCT_BANNER, "user": _public_user(user)}


@app.post("/api/logout")
def api_logout(response: Response) -> dict[str, str]:
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"status": "ok"}


@app.get("/api/me")
def api_me(user: User = Depends(current_user)) -> dict[str, Any]:
    return {"banner": PRODUCT_BANNER, "user": _public_user(user)}


@app.post("/api/propose")
async def api_propose(
    file: UploadFile = File(...),
    entity: str | None = Form(None),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    name = file.filename or "upload.csv"
    suffix = Path(name).suffix.lower()
    if suffix not in {".csv", ".xlsx", ".xls", ".txt"}:
        raise HTTPException(400, "Upload a CSV or xlsx file.")
    dest = Path(tempfile.mkdtemp(prefix="ca-upload-")) / Path(name).name
    dest.write_bytes(await file.read())
    ent = entity if entity in ENTITIES else None
    proposal = propose_mapping(dest, entity=ent)
    upload_id = uuid.uuid4().hex
    _PENDING[upload_id] = {"path": str(dest), "mapping": proposal.to_dict(), "tenant_id": user.tenant_id}
    return {
        "banner": PRODUCT_BANNER,
        "upload_id": upload_id,
        "mapping": _strip_samples(proposal.to_dict()),
    }


@app.post("/api/confirm")
def api_confirm(body: ConfirmBody, user: User = Depends(current_user)) -> dict[str, Any]:
    rec = _PENDING.get(body.upload_id)
    if not rec or rec.get("tenant_id") != user.tenant_id:
        raise HTTPException(404, "Unknown upload. Propose a mapping first.")
    try:
        confirmed = confirm_mapping(mapping_from_dict(rec["mapping"]))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    rec["mapping"] = confirmed.to_dict()
    return {"banner": PRODUCT_BANNER, "upload_id": body.upload_id, "mapping": _strip_samples(confirmed.to_dict())}


@app.post("/api/load")
def api_load(body: LoadBody, user: User = Depends(current_user)) -> dict[str, Any]:
    rec = _PENDING.get(body.upload_id)
    if not rec or rec.get("tenant_id") != user.tenant_id:
        raise HTTPException(404, "Unknown upload. Propose and confirm first.")
    mapping = mapping_from_dict(rec["mapping"])
    if not mapping.confirmed:
        raise HTTPException(400, "Mapping is not confirmed. Confirm before load.")
    mode = body.mode if body.mode in {"replace", "append"} else "append"
    with open_wh(user.tenant_id) as wh:
        counts = load_mapped_file(
            wh,
            rec["path"],
            mapping,
            tenant_id=user.tenant_id,
            mode=mode,
        )
    return {
        "banner": PRODUCT_BANNER,
        "loaded": counts,
        "mode": mode,
        "warehouse": str(warehouse_path(user.tenant_id)),
    }


@app.post("/api/ask")
def api_ask(body: AskBody, user: User = Depends(current_user)) -> dict[str, Any]:
    as_of = parse_as_of(body.as_of)
    with open_wh(user.tenant_id) as wh:
        analyst = Analyst(wh, tenant_id=user.tenant_id, as_of=as_of)
        return analyst.ask(body.question)


@app.post("/api/demo")
def api_demo(body: DemoBody | None = None, user: User = Depends(current_user)) -> dict[str, Any]:
    as_of = parse_as_of((body.as_of if body else None) or "2026-09-02")
    fixtures = fixtures_dir()
    if not fixtures.exists():
        raise HTTPException(500, f"Synthetic fixtures not found at {fixtures}")
    files = [
        ("APPOINTMENT", fixtures / "layout_a" / "SYNTHETIC_EXAMPLE_appointments.csv", "replace"),
        ("REFERRAL", fixtures / "layout_a" / "SYNTHETIC_EXAMPLE_referrals.csv", "replace"),
        ("PATIENT", fixtures / "layout_a" / "SYNTHETIC_EXAMPLE_patients.csv", "replace"),
        ("CLAIM_TXN", fixtures / "layout_payments" / "SYNTHETIC_EXAMPLE_transactions.csv", "replace"),
    ]
    layout_b = {
        "APPOINTMENT": fixtures / "layout_b" / "SYNTHETIC_EXAMPLE_visits.csv",
        "REFERRAL": fixtures / "layout_b" / "SYNTHETIC_EXAMPLE_incoming_referrals.csv",
        "PATIENT": fixtures / "layout_b" / "SYNTHETIC_EXAMPLE_clients.csv",
    }
    mapped: list[dict[str, Any]] = []
    for entity, path in layout_b.items():
        if path.exists():
            proposal = propose_mapping(path, entity=entity)
            mapped.append(
                {
                    "layout": "B",
                    "entity": entity,
                    "columns": sum(1 for c in proposal.columns if c.target_column),
                    "loaded": False,
                }
            )
    with open_wh(user.tenant_id) as wh:
        for entity, path, mode in files:
            if not path.exists():
                continue
            proposal = confirm_mapping(propose_mapping(path, entity=entity))
            counts = load_mapped_file(
                wh, path, proposal, tenant_id=user.tenant_id, mode=mode
            )
            mapped.append(
                {
                    "layout": "A" if entity != "CLAIM_TXN" else "payments",
                    "entity": entity,
                    "columns": sum(1 for c in proposal.columns if c.target_column),
                    "loaded": counts,
                }
            )
        analyst = Analyst(wh, tenant_id=user.tenant_id, as_of=as_of)
        answers = [analyst.ask(q) for q in SAMPLE_QUESTIONS]
    return {
        "banner": PRODUCT_BANNER,
        "note": "SYNTHETIC EXAMPLE DATA — tied to no real clinic. Loaded only into your tenant DuckDB.",
        "warehouse": str(warehouse_path(user.tenant_id)),
        "as_of": as_of.isoformat(),
        "mapped": mapped,
        "answers": answers,
    }
