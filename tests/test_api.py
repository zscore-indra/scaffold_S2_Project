"""API tests for ExpenseFlow using FastAPI's TestClient against ``app.main:app``.

Each test runs against a fresh temporary SQLite database wired in through a
``get_db`` dependency override, so no test touches the real ``expenseflow.db``.
The external FX provider is never called: ``routes.get_fx_rate`` is monkeypatched
per test. API-key auth is switched on (via the ``API_KEY`` env var) so the
unauthenticated-write path can be exercised.
"""

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import routes
from app.db import Base, get_db
from app.main import app

API_KEY = "test-key"
AUTH = {"X-API-Key": API_KEY}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """A TestClient on a fresh temp SQLite DB, deterministic FX, and auth enabled."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}",
        connect_args={"check_same_thread": False},
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    # Deterministic FX: identity for the base currency, a fixed rate otherwise.
    monkeypatch.setattr(
        routes, "get_fx_rate", lambda source, base="INR": 1.0 if source == "INR" else 83.0
    )
    # Enable X-API-Key enforcement for writes.
    monkeypatch.setenv("API_KEY", API_KEY)

    # No `with`: skip the lifespan so the real expenseflow.db is never created.
    yield TestClient(app)
    app.dependency_overrides.clear()


def _create(client, description="Lunch", amount_minor=500, currency="INR"):
    """Create an expense (authenticated) and return the raw response."""
    return client.post(
        "/expenses",
        json={"description": description, "amount_minor": amount_minor, "currency": currency},
        headers=AUTH,
    )


def test_create_returns_pending_with_id(client):
    resp = _create(client)
    assert resp.status_code == 201
    body = resp.json()
    assert isinstance(body["id"], int)
    assert body["status"] == "pending"
    assert body["conversion_status"] == "converted"


def test_list_filters_by_status(client):
    pending = _create(client, "Pending").json()
    to_approve = _create(client, "Approve").json()
    to_reject = _create(client, "Reject").json()
    assert client.post(f"/expenses/{to_approve['id']}/approve", headers=AUTH).status_code == 200
    assert client.post(f"/expenses/{to_reject['id']}/reject", headers=AUTH).status_code == 200

    all_ids = {e["id"] for e in client.get("/expenses").json()}
    assert all_ids == {pending["id"], to_approve["id"], to_reject["id"]}

    approved = client.get("/expenses", params={"status": "approved"}).json()
    assert [e["id"] for e in approved] == [to_approve["id"]]

    still_pending = client.get("/expenses", params={"status": "pending"}).json()
    assert [e["id"] for e in still_pending] == [pending["id"]]


def test_get_missing_id_returns_404(client):
    assert client.get("/expenses/999999").status_code == 404


def test_approve_flips_status(client):
    eid = _create(client, "Taxi").json()["id"]
    resp = client.post(f"/expenses/{eid}/approve", headers=AUTH)
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"


def test_approve_already_approved_is_conflict(client):
    # ARCHITECTURE.md: only a `pending` expense may transition; re-deciding -> 409.
    eid = _create(client, "Hotel").json()["id"]
    assert client.post(f"/expenses/{eid}/approve", headers=AUTH).status_code == 200
    again = client.post(f"/expenses/{eid}/approve", headers=AUTH)
    assert again.status_code == 409


def test_write_without_api_key_is_401(client):
    resp = client.post(
        "/expenses",
        json={"description": "NoAuth", "amount_minor": 100, "currency": "INR"},
    )
    assert resp.status_code == 401


def test_fx_unavailable_sets_conversion_status(client, monkeypatch):
    def unreachable(source, base="INR"):
        raise httpx.ConnectError("FX provider unreachable")

    monkeypatch.setattr(routes, "get_fx_rate", unreachable)

    resp = _create(client, "Berlin trip", amount_minor=10000, currency="EUR")
    assert resp.status_code == 201
    body = resp.json()
    assert body["conversion_status"] == "fx_unavailable"
    assert body["amount_base_minor"] is None
    assert body["fx_rate"] is None
