"""xAI Grok chat-completions client (OpenAI-compatible). Numbers come from tools only."""

from __future__ import annotations

import json
import os
from typing import Any

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


def tools_notice() -> str | None:
    if llm_available():
        return None
    return (
        "Chat-with-tools is off until XAI_API_KEY is set. "
        "Keyword routing is used instead so the warehouse still answers."
    )


def system_prompt(*, tenant_id: str, as_of: str) -> str:
    return (
        "You are this clinic's analyst. Talk like a colleague: the user can ask anything "
        "about operations or billing. Closed-month results are the truth grain. "
        f"{PRODUCT_BANNER} "
        "PHI: ids only. There are no patient names in the warehouse. Do not ask for or invent names.\n"
        f"This session is tenant {tenant_id} only. Never mix tenants. Never invent numbers.\n"
        f"As-of date: {as_of}.\n"
        f"{LOCKED_DEFS}\n"
        "Call locked metric tools for numbers. Prefer those tools over warehouse_select when "
        "the question matches a locked definition (cancelation, churn, referrals/conversion, "
        "AR/InsBalance, avg paid, avg collections, days to pay, staffing, caseload, snapshot, alerts).\n"
        "If a tool says the data is not in the dump, say that. Do not invent a substitute number. "
        "Payroll is not in this warehouse. Ground every figure in a tool result."
    )


def complete_chat(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
    """One chat.completions call. Monkeypatch this in tests. Do not invent a model id."""
    key = os.environ.get("XAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("XAI_API_KEY is not set")
    payload = {
        "model": xai_model(),
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
    }
    import httpx

    with httpx.Client(timeout=60.0) as client:
        res = client.post(
            f"{XAI_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=payload,
        )
        res.raise_for_status()
        return res.json()


def parse_tool_args(raw: str | None) -> dict[str, Any]:
    if not raw or not str(raw).strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}
