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


def test_sample_download_lists_profiles_and_zips():
    from fastapi.testclient import TestClient
    from web.app import app

    with TestClient(app) as client:
        listed = client.get("/api/samples")
        assert listed.status_code == 200
        ids = {p["id"] for p in listed.json()["profiles"]}
        assert ids == {"harbor", "riverbend", "northside"}
        zipped = client.get("/api/samples/harbor/zip")
        assert zipped.status_code == 200
        assert zipped.headers["content-type"].startswith("application/zip")
        assert zipped.content[:2] == b"PK"


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
        assert "CLAIM_TXN (charges / payments)" in res.text
        assert "charges, payments, adjustments, aging" in res.text
        assert "CLAIM_TXN (payments)</option>" not in res.text
        assert "payments/aging" not in res.text
        assert "Download sample files" in res.text
        assert "Harbor Pediatric Therapy" in res.text
        assert "Which therapist had the most Completes last month?" in res.text
        assert "1. Download sample files" not in res.text
        assert "3. Synthetic demo" not in res.text
        assert 'id="view-chat"' in res.text
        assert 'id="view-warehouse"' in res.text
        assert "Clear warehouse" in res.text
        assert "id=\"clear-warehouse\"" in res.text


def test_warehouse_routes_require_auth(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        assert client.post("/api/ask", json={"question": "Is cancelation over 25%?"}).status_code == 401
        assert client.post("/api/demo", json={}).status_code == 401
        assert client.get("/api/me").status_code == 401
        assert client.post("/api/confirm", json={"upload_id": "x"}).status_code == 401
        assert client.post("/api/load", json={"upload_id": "x"}).status_code == 401
        assert client.get("/api/warehouse/status").status_code == 401
        assert client.get("/api/exports/not-a-real.csv").status_code == 401
        assert client.post("/api/warehouse/clear", json={"confirm": "DELETE"}).status_code == 401


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
        assert body["intent"] == "cancelation"
        assert "%" in body["answer"]
        assert "190/571" not in body["answer"]


def test_ask_against_synthetic_tenant(tmp_path, monkeypatch, as_of):
    with _client(tmp_path, monkeypatch) as client:
        login = client.post("/api/login", json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD})
        assert login.status_code == 200
        assert login.json()["user"]["tenant_id"] == "example-clinic"
        demo = client.post("/api/demo", json={"as_of": as_of.isoformat()})
        assert demo.status_code == 200
        demo_body = demo.json()
        assert demo_body["completes_with_provider"] > 0
        assert demo_body["caseload_n_filled"] >= 3
        assert demo_body.get("caseload_n_ramped_1_to_6", 0) >= 1
        assert demo_body["caseload_unavailable"] in (None, "")
        assert demo_body["distinct_company"]
        caseload_answers = [a for a in demo_body["answers"] if "caseload" in a.get("question", "").lower()]
        assert caseload_answers
        assert "months" in caseload_answers[0]["answer"].lower()
        assert "No Completes with ProviderId or ProviderName" not in caseload_answers[0]["answer"]
        res = client.post(
            "/api/ask",
            json={"question": "Is cancelation over 25% in the last three months?", "as_of": as_of.isoformat()},
        )
        assert res.status_code == 200
        body = res.json()
        assert "does not have a live future schedule" in body["banner"].lower()
        assert body["intent"] == "cancelation"
        assert body["grounded"] is True
        assert "%" in body["answer"]


def test_api_demo_fails_when_caseload_reports_no_provider(tmp_path, monkeypatch, as_of):
    from warehouse.metrics import MetricResult

    def empty_caseload(wh, as_of_date, *, company=None):
        return MetricResult(
            name="caseload_fill",
            as_of=as_of_date,
            grain_note="derived from Completes only",
            value=[],
            unavailable="No Completes with ProviderId or ProviderName. Cannot measure caseload fill.",
        )

    monkeypatch.setattr("web.demo_load.caseload_fill", empty_caseload)
    with _client(tmp_path, monkeypatch) as client:
        client.post("/api/login", json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD})
        demo = client.post("/api/demo", json={"as_of": as_of.isoformat()})
        assert demo.status_code == 500
        detail = demo.json()["detail"]
        assert detail["completes_with_provider"] >= 0
        assert "appointment_rows" in detail
        assert "completes" in detail
        assert "distinct_company" in detail
        assert "caseload_n_filled" in detail
        assert detail["caseload_unavailable"] == (
            "No Completes with ProviderId or ProviderName. Cannot measure caseload fill."
        )


def test_second_clinic_isolated_empty_warehouse(tmp_path, monkeypatch, as_of):
    with _client(tmp_path, monkeypatch) as client:
        client.post("/api/login", json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD})
        client.post("/api/demo", json={"as_of": as_of.isoformat()})
        demo_ask = client.post(
            "/api/ask",
            json={"question": "Is cancelation over 25% in the last three months?", "as_of": as_of.isoformat()},
        )
        assert "%" in demo_ask.json()["answer"]
        assert demo_ask.json()["intent"] == "cancelation"
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
        assert "Summit Mutual" not in other.json()["answer"]
        assert other.json()["empty_warehouse"] is True
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


def test_warehouse_status_for_loaded_demo(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        client.post("/api/login", json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD})
        res = client.get("/api/warehouse/status")
        assert res.status_code == 200
        body = res.json()
        assert body["last_updated"]
        assert body["counts"]["APPOINTMENT"] > 100
        assert body["counts"]["Completes"] > 0
        assert "PATIENT" in body["counts"]
        assert "REFERRAL" in body["counts"]
        assert "CLAIM_TXN" in body["counts"]
        assert "overall" in body["coverage"]
        assert "tables" in body["coverage"]
        assert body["coverage"]["overall"]["columns"] > 0
        assert 0 <= body["coverage"]["overall"]["pct"] <= 100


def test_export_csv_is_tenant_scoped(tmp_path, monkeypatch, as_of):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    with _client(tmp_path, monkeypatch) as client:
        client.post("/api/login", json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD})
        asked = client.post(
            "/api/ask",
            json={
                "question": "Download Completes by therapist as a CSV",
                "as_of": as_of.isoformat(),
            },
        )
        assert asked.status_code == 200
        answer = asked.json()["answer"]
        assert "Closed-month snapshot" not in answer
        assert "/api/exports/" in answer
        assert ".csv" in answer
        import re

        match = re.search(r"/api/exports/([A-Za-z0-9._-]+\.csv)", answer)
        assert match
        path = match.group(0)
        got = client.get(path)
        assert got.status_code == 200
        assert "text/csv" in got.headers.get("content-type", "")
        text = got.text
        assert "provider_name" in text.splitlines()[0] or "ProviderName" in text.splitlines()[0]
        assert len(text.splitlines()) > 1
        client.post("/api/logout")
        assert client.get(path).status_code == 401
        client.post(
            "/api/signup",
            json={
                "email": "other@example.clinic",
                "password": "other-clinic-99",
                "clinic_name": "Other Clinic",
            },
        )
        assert client.get(path).status_code == 404


def test_warehouse_clear_empties_this_tenant_only(tmp_path, monkeypatch):
    from tests.conftest import appt_row, load_appts
    from web.auth import seed_demo as seed_again

    with _client(tmp_path, monkeypatch) as client:
        client.post("/api/login", json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD})
        before = client.get("/api/warehouse/status").json()
        assert before["empty"] is False
        assert before["counts"]["APPOINTMENT"] > 100
        other_path = warehouse_path("other-keep")
        with Warehouse(other_path) as other:
            load_appts(other, [appt_row(ApptId="KEEP-OTHER")])
            assert other.count("APPOINTMENT") == 1
        refused = client.post("/api/warehouse/clear", json={"confirm": "nope"})
        assert refused.status_code == 400
        assert client.get("/api/warehouse/status").json()["counts"]["APPOINTMENT"] > 100
        cleared = client.post("/api/warehouse/clear", json={"confirm": "DELETE"})
        assert cleared.status_code == 200
        body = cleared.json()
        status = body.get("status") or body
        assert status["empty"] is True
        assert status["counts"]["APPOINTMENT"] == 0
        assert status["counts"]["PATIENT"] == 0
        assert status["counts"]["REFERRAL"] == 0
        assert status["counts"]["CLAIM_TXN"] == 0
        assert status["counts"]["Completes"] == 0
        after = client.get("/api/warehouse/status").json()
        assert after["empty"] is True
        assert after["counts"]["APPOINTMENT"] == 0
        assert after["last_updated"] is None
        assert after["last_updated_source"] == "cleared"
        seed_again()
        still = client.get("/api/warehouse/status").json()
        assert still["empty"] is True
        assert still["counts"]["APPOINTMENT"] == 0
        with Warehouse(other_path) as other:
            assert other.count("APPOINTMENT") == 1
            assert set(other.fetch_table("APPOINTMENT")["ApptId"]) == {"KEEP-OTHER"}
        ask = client.post("/api/ask", json={"question": "Is cancelation over 25%?"})
        assert ask.status_code == 200
        assert ask.json()["empty_warehouse"] is True
        me = client.get("/api/me").json()
        assert me["warehouse_empty"] is True


def test_warehouse_clear_then_load_sample_works(tmp_path, monkeypatch):
    from web.profiles import profile_files

    monkeypatch.delenv("XAI_API_KEY", raising=False)
    visits = dict(profile_files("harbor"))["APPOINTMENT"]
    with _client(tmp_path, monkeypatch) as client:
        client.post("/api/login", json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD})
        cleared = client.post("/api/warehouse/clear", json={"confirm": "DELETE"})
        assert cleared.status_code == 200
        assert client.get("/api/warehouse/status").json()["empty"] is True
        with visits.open("rb") as fh:
            proposed = client.post(
                "/api/propose",
                files={"file": (visits.name, fh, "text/csv")},
                data={"entity": "APPOINTMENT"},
            )
        assert proposed.status_code == 200
        upload_id = proposed.json()["upload_id"]
        confirmed = client.post("/api/confirm", json={"upload_id": upload_id})
        assert confirmed.status_code == 200
        loaded = client.post("/api/load", json={"upload_id": upload_id, "mode": "replace"})
        assert loaded.status_code == 200
        status = client.get("/api/warehouse/status").json()
        assert status["empty"] is False
        assert status["counts"]["APPOINTMENT"] > 100
        asked = client.post(
            "/api/ask",
            json={"question": "Is cancelation over 25% in the last three months?"},
        )
        assert asked.status_code == 200
        assert asked.json()["empty_warehouse"] is not True
        assert "%" in asked.json()["answer"]


def test_parse_as_of_defaults_demo_tenant(monkeypatch):
    from datetime import date

    from analyst.tenant import parse_as_of

    monkeypatch.delenv("CLINIC_ANALYST_AS_OF", raising=False)
    assert parse_as_of(None, tenant_id="example-clinic") == date(2026, 9, 2)
    assert parse_as_of("2026-01-15", tenant_id="example-clinic") == date(2026, 1, 15)
    assert parse_as_of(None, tenant_id="other-clinic") == date.today()
