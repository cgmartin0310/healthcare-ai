"""HTTP wrapper tests. Does not redefine locked metrics."""

from __future__ import annotations

from fastapi.testclient import TestClient

from integration_engine.load import load_mapped_file
from integration_engine.mapper import confirm_mapping, propose_mapping
from tests.conftest import FIXTURES
from warehouse.store import Warehouse
from web.app import app, warehouse_file


def test_healthz():
    client = TestClient(app)
    res = client.get("/healthz")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_index_shows_banner():
    client = TestClient(app)
    res = client.get("/")
    assert res.status_code == 200
    assert "does not have a live future schedule" in res.text
    assert "patient name" not in res.text.lower()


def test_ask_against_synthetic_tenant(tmp_path, monkeypatch, as_of):
    db = tmp_path / "clinic.duckdb"
    monkeypatch.setenv("WAREHOUSE_PATH", str(db))
    assert warehouse_file() == db
    wh = Warehouse(db)
    for entity, rel in (
        ("APPOINTMENT", "layout_a/SYNTHETIC_EXAMPLE_appointments.csv"),
        ("REFERRAL", "layout_a/SYNTHETIC_EXAMPLE_referrals.csv"),
        ("PATIENT", "layout_a/SYNTHETIC_EXAMPLE_patients.csv"),
    ):
        path = FIXTURES / rel
        load_mapped_file(
            wh,
            path,
            confirm_mapping(propose_mapping(path, entity=entity)),
            tenant_id="example-clinic",
        )
    wh.close()

    client = TestClient(app)
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
