"""End-to-end tests for the expense submit / convert / approve-reject journey.

FX is stubbed so tests never hit the network; the DB is a per-test temp SQLite
file wired in via a dependency override.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import routes
from app.db import Base, get_db
from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """A TestClient backed by an isolated SQLite DB and a deterministic FX rate."""
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
    monkeypatch.setattr(
        routes, "get_fx_rate", lambda source, base="INR": 1.0 if source == "INR" else 83.0
    )
    # No `with`: skip lifespan so the real expenseflow.db is never created.
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_submit_converts_foreign_currency_to_base(client):
    resp = client.post(
        "/expenses",
        json={"description": "Hotel", "amount_minor": 10000, "currency": "usd"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["currency"] == "USD"  # normalised to uppercase
    assert body["amount_minor"] == 10000
    assert body["base_currency"] == "INR"
    assert body["fx_rate"] == 83.0
    assert body["amount_base_minor"] == 830000  # 10000 * 83.0
    assert body["status"] == "pending"


def test_submit_base_currency_is_identity(client):
    resp = client.post(
        "/expenses", json={"description": "Lunch", "amount_minor": 500, "currency": "INR"}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["fx_rate"] == 1.0
    assert body["amount_base_minor"] == 500


def test_approve_then_cannot_decide_again(client):
    created = client.post(
        "/expenses", json={"description": "Taxi", "amount_minor": 200, "currency": "INR"}
    ).json()
    eid = created["id"]

    approved = client.post(f"/expenses/{eid}/approve")
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"

    conflict = client.post(f"/expenses/{eid}/reject")
    assert conflict.status_code == 409


def test_reject_flow(client):
    created = client.post(
        "/expenses", json={"description": "Snacks", "amount_minor": 150, "currency": "INR"}
    ).json()
    resp = client.post(f"/expenses/{created['id']}/reject")
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"


def test_get_unknown_returns_404(client):
    assert client.get("/expenses/999").status_code == 404


def test_rejects_invalid_amount(client):
    resp = client.post(
        "/expenses", json={"description": "Bad", "amount_minor": 0, "currency": "INR"}
    )
    assert resp.status_code == 422
