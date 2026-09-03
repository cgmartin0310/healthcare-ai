"""Free-text chat: regex fallback without a key; mocked Grok tool-calls when a key is set."""

from __future__ import annotations

from datetime import date

from analyst.engine import Analyst
from analyst.tools import warehouse_select
from tests.conftest import appt_row, load_appts
from web.auth import DEMO_EMAIL, DEMO_PASSWORD, seed_demo
from fastapi.testclient import TestClient
from web.app import app


def test_fallback_answers_cancelation_without_key(warehouse, as_of, monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    load_appts(
        warehouse,
        [appt_row(ApptId="1", AppointmentStatus="Complete")]
        + [appt_row(ApptId=f"c{i}", AppointmentStatus="Cancelled") for i in range(2)],
    )
    out = Analyst(warehouse, tenant_id="t", as_of=as_of).ask(
        "Is cancelation over 25% in the last three months?"
    )
    assert out["mode"] == "fallback"
    assert out["intent"] == "cancelation"
    assert "66.7%" in out["answer"] or "66.6%" in out["answer"]
    assert out["tools_called"] == []


def test_mocked_llm_free_text_ar_calls_insbalance_tool(warehouse, as_of, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "test-not-a-real-key")
    load_appts(
        warehouse,
        [
            appt_row(
                ApptId="1",
                ApptDate=date(2026, 6, 1),
                InsPaid=0,
                PrimaryPayorName="Acme Health",
                LocationName="Site B",
                InsBalance=125.0,
            )
        ],
    )
    calls = {"n": 0}

    def fake_complete(messages, tools):
        calls["n"] += 1
        names = [t["function"]["name"] for t in tools]
        assert "ar_past_30_days" in names
        if calls["n"] == 1:
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_ar",
                                    "type": "function",
                                    "function": {"name": "ar_past_30_days", "arguments": "{}"},
                                }
                            ],
                        }
                    }
                ]
            }
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": (
                            "Site B's AR is high because Acme Health has $125.00 InsBalance "
                            "on Completes aged over 30 days. That is SUM(InsBalance), not billed − paid."
                        ),
                    }
                }
            ]
        }

    monkeypatch.setattr("analyst.engine.complete_chat", fake_complete)
    out = Analyst(warehouse, tenant_id="t", as_of=as_of).ask("why is Site B's AR high?")
    assert out["mode"] == "tools"
    assert out["intent"] == "tool_chat"
    assert "ar_past_30_days" in out["tools_called"]
    assert "InsBalance" in out["answer"]
    assert "125" in out["answer"]
    assert "improve my business" not in out["answer"].lower()
    ev = next(v for k, v in out["evidence"].items() if k.startswith("ar_past_30_days"))
    assert ev["name"] == "ar_past_30_days"
    assert ev["value"][0]["ins_balance"] == 125.0


def test_warehouse_select_is_read_only(warehouse):
    load_appts(warehouse, [appt_row(ApptId="1")])
    blocked = warehouse_select(warehouse, "DELETE FROM APPOINTMENT")
    assert "error" in blocked
    ok = warehouse_select(warehouse, 'SELECT "ApptId" FROM "APPOINTMENT"')
    assert ok["row_count"] == 1
    assert ok["rows"][0]["ApptId"] == "1"


def test_chat_thread_requires_auth_and_keeps_history(tmp_path, monkeypatch, as_of):
    monkeypatch.setenv("CLINIC_ANALYST_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CLINIC_ANALYST_SECRET", "test-secret")
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    seed_demo()
    with TestClient(app) as client:
        assert client.post("/api/chat", json={"question": "hi"}).status_code == 401
        client.post("/api/login", json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD})
        first = client.post(
            "/api/chat",
            json={"question": "Is cancelation over 25% in the last three months?", "as_of": as_of.isoformat()},
        )
        assert first.status_code == 200
        assert first.json()["mode"] == "fallback"
        assert "%" in first.json()["answer"]
        thread = first.json()["chat"]["messages"]
        assert thread[-2]["role"] == "user"
        assert thread[-1]["role"] == "assistant"
        listed = client.get("/api/chat")
        assert len(listed.json()["chat"]["messages"]) >= 2
        assert listed.json()["chat"]["notice"]
