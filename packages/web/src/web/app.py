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
from analyst.llm import llm_available, tools_notice, xai_model
from analyst.tenant import parse_as_of, warehouse_path
from integration_engine.deid import SAFE_HARBOR_NOTICE
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
from web.demo_load import (
    DEMO_DEFAULT_AS_OF,
    demo_caseload_readiness,
    demo_not_ready,
    fixtures_dir,
    load_synthetic_demo,
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
_CHATS: dict[str, list[dict[str, str]]] = {}
_CHAT_CAP = 40


def _chat_state(user: User) -> dict[str, Any]:
    return {
        "mode": "tools" if llm_available() else "fallback",
        "model": xai_model() if llm_available() else None,
        "notice": tools_notice(),
        "messages": list(_CHATS.get(user.user_id, [])),
    }


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


def _warehouse_empty(tenant_id: str) -> bool:
    with open_wh(tenant_id) as wh:
        return wh.count("APPOINTMENT") == 0


def _session_payload(user: User) -> dict[str, Any]:
    return {
        "banner": PRODUCT_BANNER,
        "user": _public_user(user),
        "chat": _chat_state(user),
        "warehouse_empty": _warehouse_empty(user.tenant_id),
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


class ChatBody(BaseModel):
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
    return _session_payload(user)


@app.post("/api/login")
def api_login(body: AuthBody, response: Response) -> dict[str, Any]:
    try:
        user = auth_login(body.email, body.password)
    except ValueError as exc:
        raise HTTPException(401, str(exc)) from exc
    _set_session(response, user)
    return _session_payload(user)


@app.post("/api/logout")
def api_logout(request: Request, response: Response) -> dict[str, str]:
    user = user_from_cookie(request.cookies.get(COOKIE_NAME))
    if user:
        _CHATS.pop(user.user_id, None)
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"status": "ok"}


@app.get("/api/me")
def api_me(user: User = Depends(current_user)) -> dict[str, Any]:
    return _session_payload(user)


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
    as_of = parse_as_of(None, tenant_id=user.tenant_id)
    proposal = propose_mapping(dest, entity=ent, tenant_id=user.tenant_id, as_of=as_of)
    upload_id = uuid.uuid4().hex
    _PENDING[upload_id] = {"path": str(dest), "mapping": proposal.to_dict(), "tenant_id": user.tenant_id}
    receipt = proposal.deid_receipt
    return {
        "banner": PRODUCT_BANNER,
        "notice": SAFE_HARBOR_NOTICE,
        "upload_id": upload_id,
        "mapping": _strip_samples(proposal.to_dict()),
        "deid_receipt": receipt,
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
    return {
        "banner": PRODUCT_BANNER,
        "notice": SAFE_HARBOR_NOTICE,
        "upload_id": body.upload_id,
        "mapping": _strip_samples(confirmed.to_dict()),
        "deid_receipt": confirmed.deid_receipt,
    }


@app.post("/api/load")
def api_load(body: LoadBody, user: User = Depends(current_user)) -> dict[str, Any]:
    rec = _PENDING.get(body.upload_id)
    if not rec or rec.get("tenant_id") != user.tenant_id:
        raise HTTPException(404, "Unknown upload. Propose and confirm first.")
    mapping = mapping_from_dict(rec["mapping"])
    if not mapping.confirmed:
        raise HTTPException(400, "Mapping is not confirmed. Confirm before load.")
    mode = body.mode if body.mode in {"replace", "append"} else "append"
    as_of = parse_as_of(None, tenant_id=user.tenant_id)
    with open_wh(user.tenant_id) as wh:
        counts = load_mapped_file(
            wh,
            rec["path"],
            mapping,
            tenant_id=user.tenant_id,
            tenant_company=user.tenant_name,
            mode=mode,
            as_of=as_of,
        )
    return {
        "banner": PRODUCT_BANNER,
        "notice": SAFE_HARBOR_NOTICE,
        "loaded": counts,
        "mode": mode,
        "warehouse": str(warehouse_path(user.tenant_id)),
        "warehouse_empty": _warehouse_empty(user.tenant_id),
        "deid_receipt": mapping.deid_receipt,
    }


@app.post("/api/ask")
def api_ask(body: AskBody, user: User = Depends(current_user)) -> dict[str, Any]:
    as_of = parse_as_of(body.as_of, tenant_id=user.tenant_id)
    with open_wh(user.tenant_id) as wh:
        analyst = Analyst(wh, tenant_id=user.tenant_id, as_of=as_of)
        return analyst.ask(body.question)


@app.get("/api/chat")
def api_chat_get(user: User = Depends(current_user)) -> dict[str, Any]:
    return {"banner": PRODUCT_BANNER, "chat": _chat_state(user)}


@app.post("/api/chat")
def api_chat(body: ChatBody, user: User = Depends(current_user)) -> dict[str, Any]:
    thread = _CHATS.setdefault(user.user_id, [])
    as_of = parse_as_of(body.as_of, tenant_id=user.tenant_id)
    with open_wh(user.tenant_id) as wh:
        analyst = Analyst(wh, tenant_id=user.tenant_id, as_of=as_of)
        result = analyst.ask(body.question, history=thread)
    thread.append({"role": "user", "content": body.question.strip()})
    thread.append({"role": "assistant", "content": result["answer"]})
    _CHATS[user.user_id] = thread[-_CHAT_CAP:]
    result["chat"] = _chat_state(user)
    return result


@app.post("/api/chat/clear")
def api_chat_clear(user: User = Depends(current_user)) -> dict[str, Any]:
    _CHATS.pop(user.user_id, None)
    return {"status": "ok", "chat": _chat_state(user)}


@app.post("/api/demo")
def api_demo(body: DemoBody | None = None, user: User = Depends(current_user)) -> dict[str, Any]:
    as_of = parse_as_of((body.as_of if body else None) or DEMO_DEFAULT_AS_OF, tenant_id=user.tenant_id)
    fixtures = fixtures_dir()
    if not fixtures.exists():
        raise HTTPException(500, f"Synthetic fixtures not found at {fixtures}")
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
    try:
        with open_wh(user.tenant_id) as wh:
            mapped.extend(
                load_synthetic_demo(wh, tenant_id=user.tenant_id, tenant_company=user.tenant_name)
            )
            readiness = demo_caseload_readiness(wh, as_of)
            if demo_not_ready(readiness):
                raise HTTPException(
                    500,
                    {
                        "error": (
                            "Demo load did not produce a usable caseload. "
                            "Completes with ProviderId/ProviderName and a months-to-fill number are required."
                        ),
                        **readiness,
                    },
                )
            analyst = Analyst(wh, tenant_id=user.tenant_id, as_of=as_of)
            answers = [analyst.ask(q) for q in SAMPLE_QUESTIONS]
    except FileNotFoundError as exc:
        raise HTTPException(500, str(exc)) from exc
    return {
        "banner": PRODUCT_BANNER,
        "note": "SYNTHETIC EXAMPLE DATA — tied to no real clinic. Loaded only into your tenant DuckDB.",
        "warehouse": str(warehouse_path(user.tenant_id)),
        "as_of": as_of.isoformat(),
        "mapped": mapped,
        "answers": answers,
        "warehouse_empty": False,
        **readiness,
    }
