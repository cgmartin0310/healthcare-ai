"""xAI Grok chat-completions client (OpenAI-compatible). Numbers come from tools only."""

from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx

from analyst.banner import PRODUCT_BANNER
from analyst.tools import LOCKED_DEFS, TOOL_SCHEMAS

# Verified from https://x.ai/api (Sep 2026): current chat model id is grok-4.6.
# Override with XAI_MODEL if the account serves a different published id (e.g. grok-3).
DEFAULT_XAI_MODEL = "grok-4.6"
XAI_BASE_URL = "https://api.x.ai/v1"


def llm_available() -> bool:
    return bool(os.environ.get("XAI_API_KEY", "").strip())


def xai_model() -> str:
    return os.environ.get("XAI_MODEL", DEFAULT_XAI_MODEL).strip() or DEFAULT_XAI_MODEL


WAREHOUSE_FALLBACK_NOTICE = "Grok tools down, answering from warehouse."
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_MAX_ATTEMPTS = 3  # first try + 2 retries


def tools_notice() -> str | None:
    if llm_available():
        return None
    return WAREHOUSE_FALLBACK_NOTICE


def system_prompt(*, tenant_id: str, as_of: str) -> str:
    return (
        "You are this clinic's analyst. Talk like a colleague. "
        "The first sentence is the answer (name + number). Then at most one grain note. "
        "Answer only the question asked. Do not paste a closed-month snapshot unless they "
        "asked for a snapshot. Do not mention payroll unless they asked about payroll or profit. "
        "Display clinician ProviderName. Never print a hashed ProviderId as the name. "
        "If ProviderName is missing, say clinician names are not in this dump.\n"
        f"{PRODUCT_BANNER} "
        "PHI: ids only. There are no patient names in the warehouse. Do not ask for or invent names.\n"
        f"This session is tenant {tenant_id} only. Never mix tenants. Never invent numbers. "
        "The warehouse is already this tenant. Do not pass company=tenant_id or the UI clinic "
        "label. Only pass company when it exactly equals an APPOINTMENT.Company value; "
        "when unsure, omit company.\n"
        f"As-of date: {as_of}.\n"
        f"{LOCKED_DEFS}\n"
        "Call locked metric tools for numbers. Prefer those tools over warehouse_select when "
        "the question matches a locked definition (cancelation, churn, referrals/conversion, "
        "AR/InsBalance, avg paid, avg collections, days to pay, staffing, caseload, "
        "completes_by_provider, snapshot, alerts).\n"
        "For most-productive / most Completes / busiest clinician, call completes_by_provider "
        "once and stop. Do not call snapshot. Do not mention payroll.\n"
        "If a tool says the data is not in the dump, say that in one sentence. "
        "Do not invent a substitute number. Payroll is not in this warehouse unless they asked. "
        "Ground every figure in a tool result."
    )


def complete_chat(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
    """One chat.completions call. Retry 429/5xx twice with backoff. Monkeypatch in tests."""
    key = os.environ.get("XAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("XAI_API_KEY is not set")
    payload = {
        "model": xai_model(),
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
    }
    last_exc: Exception | None = None
    with httpx.Client(timeout=60.0) as client:
        for attempt in range(_MAX_ATTEMPTS):
            try:
                res = client.post(
                    f"{XAI_BASE_URL}/chat/completions",
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json=payload,
                )
                if res.status_code in _RETRYABLE_STATUS and attempt < _MAX_ATTEMPTS - 1:
                    time.sleep(0.4 * (2**attempt))
                    continue
                res.raise_for_status()
                return res.json()
            except httpx.TimeoutException as exc:
                last_exc = exc
                if attempt < _MAX_ATTEMPTS - 1:
                    time.sleep(0.4 * (2**attempt))
                    continue
                raise
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                code = exc.response.status_code if exc.response is not None else 0
                if code in _RETRYABLE_STATUS and attempt < _MAX_ATTEMPTS - 1:
                    time.sleep(0.4 * (2**attempt))
                    continue
                raise
    if last_exc:
        raise last_exc
    raise RuntimeError("xAI chat failed after retries")


def parse_tool_args(raw: str | None) -> dict[str, Any]:
    if not raw or not str(raw).strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}
