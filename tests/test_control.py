"""Milestone 4: control plane + live latency overrides."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from tests.conftest import AUTH_HEADER

ORDER_FORM = {
    "tradingsymbol": "INFY",
    "exchange": "NSE",
    "transaction_type": "BUY",
    "order_type": "LIMIT",
    "product": "CNC",
    "quantity": "1",
    "price": "1500",
}


def _place(client):
    return client.post("/orders/regular", data=ORDER_FORM, headers=AUTH_HEADER)


def _receive_text(ws, attempts: int = 12):
    import json

    for _ in range(attempts):
        msg = ws.receive()
        if "text" in msg and msg["text"] is not None:
            return json.loads(msg["text"])
    raise AssertionError("no text frame received")


# -- reset ------------------------------------------------------------------
def test_reset_clears_state(app_factory) -> None:
    client = TestClient(app_factory())
    _place(client)
    assert len(client.get("/orders", headers=AUTH_HEADER).json()["data"]) == 1
    assert client.post("/_control/reset").json()["data"]["reset"] is True
    assert client.get("/orders", headers=AUTH_HEADER).json()["data"] == []
    assert client.get("/trades", headers=AUTH_HEADER).json()["data"] == []


# -- latency (takes effect without restart) ---------------------------------
def test_latency_override_takes_effect(app_factory) -> None:
    client = TestClient(app_factory())
    # Baseline: fast.
    t0 = time.perf_counter()
    client.get("/orders", headers=AUTH_HEADER)
    baseline = time.perf_counter() - t0

    resp = client.post("/_control/latency", json={"rest_ms": 200})
    assert resp.status_code == 200
    assert resp.json()["data"]["rest_ms"] == 200

    t0 = time.perf_counter()
    client.get("/orders", headers=AUTH_HEADER)
    delayed = time.perf_counter() - t0

    assert delayed >= 0.18, f"expected >=180ms, got {delayed * 1000:.0f}ms"
    assert delayed > baseline


def test_latency_rejects_unknown_field(app_factory) -> None:
    client = TestClient(app_factory())
    resp = client.post("/_control/latency", json={"nope": 1})
    assert resp.status_code == 400
    assert resp.json()["error_type"] == "InputException"


def test_latency_rejects_bad_value(app_factory) -> None:
    client = TestClient(app_factory())
    resp = client.post("/_control/latency", json={"rest_ms": -5})
    assert resp.status_code == 400


# -- disconnect -------------------------------------------------------------
# NOTE: driving a real WS close through Starlette's TestClient while issuing an
# HTTP control call on the same client deadlocks its portal, so we exercise the
# endpoint against stand-in connections that record close(). The real end-to-end
# socket drop (server sends close code 1000) is verified live in the README/dev
# smoke test against a running uvicorn server.
class _DummyConn:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.closed_with: int | None = None

    async def _close(self, code: int = 1000) -> None:
        self.closed_with = code

    @property
    def ws(self):
        conn = self

        class _WS:
            async def close(self_inner, code: int = 1000) -> None:
                await conn._close(code)

        return _WS()


def test_disconnect_all(app_factory) -> None:
    app = app_factory()
    a, b = _DummyConn("alice"), _DummyConn("bob")
    app.state.fk.ws_clients.extend([a, b])
    resp = TestClient(app).post("/_control/disconnect")
    assert resp.json()["data"]["disconnected"] == 2
    assert a.closed_with == 1000 and b.closed_with == 1000


def test_disconnect_one_by_api_key(app_factory) -> None:
    app = app_factory()
    a, b = _DummyConn("alice"), _DummyConn("bob")
    app.state.fk.ws_clients.extend([a, b])
    resp = TestClient(app).post("/_control/disconnect", json={"api_key": "alice"})
    assert resp.json()["data"]["disconnected"] == 1
    assert a.closed_with == 1000
    assert b.closed_with is None  # bob untouched


# -- force_reject -----------------------------------------------------------
def test_force_reject_next_order(app_factory) -> None:
    client = TestClient(app_factory())  # default_state COMPLETE
    assert client.post("/_control/force_reject", json={"count": 1}).status_code == 200

    rejected = _place(client)
    assert rejected.status_code == 200  # placement still 200; the order itself is rejected
    order = client.get("/orders", headers=AUTH_HEADER).json()["data"][0]
    assert order["status"] == "REJECTED"
    assert order["status_message"]  # reject message populated
    assert order["average_price"] == 0

    # Next order reverts to the configured default (COMPLETE).
    _place(client)
    latest = client.get("/orders", headers=AUTH_HEADER).json()["data"][-1]
    assert latest["status"] == "COMPLETE"


def test_force_reject_reflected_in_ws_update(app_factory) -> None:
    app = app_factory(heartbeat_interval_ms=60000, ws_tick_interval_ms=60000)
    client = TestClient(app)
    client.post("/_control/force_reject", json={"count": 1})
    with client.websocket_connect("/ws?api_key=k&access_token=t") as ws:
        _place(client)
        msg = _receive_text(ws)
        assert msg["type"] == "order"
        assert msg["data"]["status"] == "REJECTED"


# -- order_state ------------------------------------------------------------
def test_order_state_runtime_change(app_factory) -> None:
    client = TestClient(app_factory())  # starts COMPLETE
    assert (
        client.post("/_control/order_state", json={"state": "OPEN"}).json()["data"]["default_state"]
        == "OPEN"
    )
    _place(client)
    order = client.get("/orders", headers=AUTH_HEADER).json()["data"][0]
    assert order["status"] == "OPEN"
    assert order["pending_quantity"] == 1


def test_order_state_invalid(app_factory) -> None:
    client = TestClient(app_factory())
    resp = client.post("/_control/order_state", json={"state": "PARTIAL"})
    assert resp.status_code == 400
    assert resp.json()["error_type"] == "InputException"


# -- error injection via control endpoint -----------------------------------
def test_error_injection_via_control(app_factory) -> None:
    client = TestClient(app_factory())
    client.post(
        "/_control/error",
        json={"endpoint": "orders.list", "error_type": "TokenException", "message": "expired"},
    )
    resp = client.get("/orders", headers=AUTH_HEADER)
    assert resp.status_code == 403
    assert resp.json() == {"status": "error", "message": "expired", "error_type": "TokenException"}
    # Consumed after one trigger (default count=1).
    assert client.get("/orders", headers=AUTH_HEADER).status_code == 200


def test_error_injection_clear(app_factory) -> None:
    client = TestClient(app_factory())
    client.post(
        "/_control/error",
        json={"endpoint": "orders.list", "error_type": "GeneralException", "count": -1},
    )
    assert client.get("/orders", headers=AUTH_HEADER).status_code == 500
    # Infinite until cleared.
    assert client.get("/orders", headers=AUTH_HEADER).status_code == 500
    client.post("/_control/error", json={"endpoint": "orders.list", "clear": True})
    assert client.get("/orders", headers=AUTH_HEADER).status_code == 200


def test_control_not_rate_limited(app_factory) -> None:
    app = app_factory()
    app.state.fk.config.rate_limit.enabled = True
    client = TestClient(app)
    # Hammer the control plane; never throttled.
    for _ in range(20):
        assert client.post("/_control/reset").status_code == 200
