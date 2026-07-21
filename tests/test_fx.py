"""Unit tests for `get_fx_rate`, with the httpx call mocked (no network)."""

import httpx
import pytest
from fastapi import HTTPException

from app import routes
from app.routes import get_fx_rate


class _FakeResponse:
    """Minimal stand-in for httpx.Response covering what get_fx_rate uses."""

    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=None)

    def json(self) -> dict:
        return self._payload


def _stub_get(monkeypatch, payload, status_code=200):
    """Replace routes.httpx.get and capture the args it was called with."""
    calls: list[dict] = []

    def fake_get(url, params=None, timeout=None):
        calls.append({"url": url, "params": params, "timeout": timeout})
        return _FakeResponse(payload, status_code)

    monkeypatch.setattr(routes.httpx, "get", fake_get)
    return calls


def test_foreign_currency_parses_rate(monkeypatch):
    calls = _stub_get(monkeypatch, {"base": "USD", "rates": {"INR": 96.23}})
    monkeypatch.delenv("FX_API_KEY", raising=False)

    rate = get_fx_rate("USD")

    assert rate == 96.23
    assert len(calls) == 1
    assert calls[0]["params"] == {"base": "USD", "symbols": "INR"}


def test_same_currency_short_circuits_without_network(monkeypatch):
    calls = _stub_get(monkeypatch, {"rates": {"INR": 999.0}})

    rate = get_fx_rate("INR")

    assert rate == 1.0
    assert calls == []  # no HTTP call made for the base currency


def test_api_key_is_added_when_env_set(monkeypatch):
    calls = _stub_get(monkeypatch, {"rates": {"INR": 96.23}})
    monkeypatch.setenv("FX_API_KEY", "secret-key")

    get_fx_rate("USD")

    assert calls[0]["params"]["access_key"] == "secret-key"


def test_missing_rates_key_raises_502(monkeypatch):
    _stub_get(monkeypatch, {"success": False, "error": {"type": "missing_access_key"}})

    with pytest.raises(HTTPException) as exc_info:
        get_fx_rate("USD")

    assert exc_info.value.status_code == 502


def test_missing_base_symbol_raises_502(monkeypatch):
    # Provider responds but doesn't include the requested base currency.
    _stub_get(monkeypatch, {"rates": {"EUR": 0.92}})

    with pytest.raises(HTTPException) as exc_info:
        get_fx_rate("USD")

    assert exc_info.value.status_code == 502


def test_http_error_propagates(monkeypatch):
    _stub_get(monkeypatch, {}, status_code=500)

    with pytest.raises(httpx.HTTPStatusError):
        get_fx_rate("USD")
