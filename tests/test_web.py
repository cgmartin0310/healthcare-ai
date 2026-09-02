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


def test_warehouse_routes_require_auth(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        assert client.post("/api/ask", json={"question": "Is cancelation over 25%?"}).status_code == 401
        assert client.post("/api/demo", json={}).status_code == 401
        assert client.get("/api/me").status_code == 401
        assert client.post("/api/confirm", json={"upload_id": "x"}).status_code == 401
        assert client.post("/api/load", json={"upload_id": "x"}).status_code == 401


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
        other = client.post(
            "/api/ask",
            json={"question": "Is cancelation over 25% in the last three months?", "as_of": as_of.isoformat()},
        )
        assert other.status_code == 200
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
