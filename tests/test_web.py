"""HTTP wrapper tests. Auth required on warehouse routes. Tenants do not mix."""

from __future__ import annotations

from fastapi.testclient import TestClient

from analyst.tenant import warehouse_path
from warehouse.store import Warehouse
from web.auth import DEMO_EMAIL, DEMO_PASSWORD, seed_demo
from web.app import app


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("CLINIC_ANALYST_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CLINIC_ANALYST_SECRET", "test-secret")
    seed_demo()
    return TestClient(app)


def test_healthz():
    with TestClient(app) as client:
        res = client.get("/healthz")
        assert res.status_code == 200
        assert res.json() == {"status": "ok"}


def test_index_shows_banner_and_login():
    with TestClient(app) as client:
        res = client.get("/")
        assert res.status_code == 200
        assert "does not have a live future schedule" in res.text
        assert "patient name" not in res.text.lower()
        assert "Sign in" in res.text
        assert "Chat with the analyst" in res.text
        assert "Type any ops" in res.text
        assert "No visits loaded yet — run synthetic demo or upload files" in res.text
        assert "Safe Harbor identifiers are stripped or hashed before load" in res.text
        assert "not a legal determination" in res.text
        assert "HIPAA compliant" not in res.text
        assert "no HIPAA data" not in res.text.lower()


def test_warehouse_routes_require_auth(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        assert client.post("/api/ask", json={"question": "Is cancelation over 25%?"}).status_code == 401
        assert client.post("/api/demo", json={}).status_code == 401
        assert client.get("/api/me").status_code == 401
        assert client.post("/api/confirm", json={"upload_id": "x"}).status_code == 401
        assert client.post("/api/load", json={"upload_id": "x"}).status_code == 401


def test_seed_demo_loads_visits_and_cancelation_without_api_demo(tmp_path, monkeypatch):
    """Demo login must chat after seed_demo alone — no POST /api/demo."""
    monkeypatch.setenv("CLINIC_ANALYST_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CLINIC_ANALYST_SECRET", "test-secret")
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("CLINIC_ANALYST_AS_OF", raising=False)
    seed_demo()
    with Warehouse(warehouse_path("example-clinic")) as wh:
        assert wh.count("APPOINTMENT") > 100
        assert wh.count("REFERRAL") > 0
        assert wh.count("PATIENT") > 0
        assert wh.count("CLAIM_TXN") > 0
    seed_demo()
    with Warehouse(warehouse_path("example-clinic")) as wh:
        rows = wh.count("APPOINTMENT")
    assert rows > 100
    with TestClient(app) as client:
        login = client.post("/api/login", json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD})
        assert login.status_code == 200
        assert login.json()["user"]["tenant_id"] == "example-clinic"
        assert login.json()["warehouse_empty"] is False
        me = client.get("/api/me")
        assert me.json()["warehouse_empty"] is False
        res = client.post(
            "/api/ask",
            json={"question": "Is cancelation over 25% in the last three months?"},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["as_of"] == "2026-09-02"
        assert body["intent"] == "cancelation"
        assert body["grounded"] is True
        assert body.get("empty_warehouse") is not True
        assert "33.3%" in body["answer"] or "over 25%" in body["answer"]
        assert "190/571" in body["answer"]


def test_ask_against_synthetic_tenant(tmp_path, monkeypatch, as_of):
    with _client(tmp_path, monkeypatch) as client:
        login = client.post("/api/login", json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD})
        assert login.status_code == 200
        assert login.json()["user"]["tenant_id"] == "example-clinic"
        demo = client.post("/api/demo", json={"as_of": as_of.isoformat()})
        assert demo.status_code == 200
        res = client.post(
            "/api/ask",
            json={"question": "Is cancelation over 25% in the last three months?", "as_of": as_of.isoformat()},
        )
        assert res.status_code == 200
        body = res.json()
        assert "does not have a live future schedule" in body["banner"].lower()
        assert body["intent"] == "cancelation"
        assert body["grounded"] is True
        assert "33.3%" in body["answer"] or "over 25%" in body["answer"]


def test_second_clinic_isolated_empty_warehouse(tmp_path, monkeypatch, as_of):
    with _client(tmp_path, monkeypatch) as client:
        client.post("/api/login", json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD})
        client.post("/api/demo", json={"as_of": as_of.isoformat()})
        demo_ask = client.post(
            "/api/ask",
            json={"question": "Is cancelation over 25% in the last three months?", "as_of": as_of.isoformat()},
        )
        assert "33.3%" in demo_ask.json()["answer"]
        client.post("/api/logout")
        created = client.post(
            "/api/signup",
            json={
                "email": "second@example.clinic",
                "password": "second-clinic-99",
                "clinic_name": "Second Clinic",
            },
        )
        assert created.status_code == 200
        second_id = created.json()["user"]["tenant_id"]
        assert second_id != "example-clinic"
        assert created.json()["warehouse_empty"] is True
        other = client.post(
            "/api/ask",
            json={"question": "Is cancelation over 25% in the last three months?", "as_of": as_of.isoformat()},
        )
        assert other.status_code == 200
        assert other.json()["empty_warehouse"] is True
        assert other.json()["answer"] == "No visits loaded yet — run synthetic demo or upload files"
        assert "33.3%" not in other.json()["answer"]
        assert "190/571" not in other.json()["answer"]
        demo_path = warehouse_path("example-clinic")
        other_path = warehouse_path(second_id)
        assert demo_path != other_path
        with Warehouse(other_path) as wh:
            assert wh.count("APPOINTMENT") == 0
            assert wh.count("CLAIM_TXN") == 0
        with Warehouse(demo_path) as wh:
            assert wh.count("APPOINTMENT") > 100


def test_two_tenant_duckdb_files_do_not_mix(tmp_path, monkeypatch):
    monkeypatch.setenv("CLINIC_ANALYST_DATA_DIR", str(tmp_path))
    from tests.conftest import appt_row, load_appts

    a_path = warehouse_path("tenant-a")
    b_path = warehouse_path("tenant-b")
    assert a_path != b_path
    with Warehouse(a_path) as a:
        load_appts(a, [appt_row(ApptId="ONLY-A")])
        assert a.count("APPOINTMENT") == 1
    with Warehouse(b_path) as b:
        assert b.count("APPOINTMENT") == 0
        frame = b.fetch_table("APPOINTMENT")
        assert frame.empty
    with Warehouse(a_path) as a:
        ids = set(a.fetch_table("APPOINTMENT")["ApptId"])
        assert ids == {"ONLY-A"}


def test_ensure_demo_does_not_load_other_tenants(tmp_path, monkeypatch):
    monkeypatch.setenv("CLINIC_ANALYST_DATA_DIR", str(tmp_path))
    from web.demo_load import ensure_demo_warehouse_seeded

    other = warehouse_path("second-clinic")
    with Warehouse(other) as wh:
        assert ensure_demo_warehouse_seeded(wh, "second-clinic") is False
        assert wh.count("APPOINTMENT") == 0
        assert wh.count("CLAIM_TXN") == 0


def test_parse_as_of_defaults_demo_tenant(monkeypatch):
    from datetime import date

    from analyst.tenant import parse_as_of

    monkeypatch.delenv("CLINIC_ANALYST_AS_OF", raising=False)
    assert parse_as_of(None, tenant_id="example-clinic") == date(2026, 9, 2)
    assert parse_as_of("2026-01-15", tenant_id="example-clinic") == date(2026, 1, 15)
    assert parse_as_of(None, tenant_id="other-clinic") == date.today()
