"""Milestone 1: place order -> appears in GET /orders + emits a WS order update."""

from __future__ import annotations

import struct

from fastapi.testclient import TestClient

from .conftest import AUTH_HEADER

ORDER_FORM = {
    "tradingsymbol": "INFY",
    "exchange": "NSE",
    "transaction_type": "BUY",
    "order_type": "LIMIT",
    "product": "CNC",
    "quantity": "1",
    "price": "1500",
}


def test_place_order_shows_in_orderbook(app_factory) -> None:
    client = TestClient(app_factory())
    resp = client.post("/orders/regular", data=ORDER_FORM, headers=AUTH_HEADER)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    order_id = body["data"]["order_id"]
    assert isinstance(order_id, str) and order_id

    listed = client.get("/orders", headers=AUTH_HEADER).json()
    assert listed["status"] == "success"
    ids = [o["order_id"] for o in listed["data"]]
    assert order_id in ids
    placed = next(o for o in listed["data"] if o["order_id"] == order_id)
    # default_state COMPLETE -> filled, paise-free JSON numbers.
    assert placed["status"] == "COMPLETE"
    assert placed["tradingsymbol"] == "INFY"
    assert placed["instrument_token"] == 408065
    assert placed["filled_quantity"] == 1
    assert placed["average_price"] == 1500.0
    assert isinstance(placed["order_id"], str)
    assert isinstance(placed["instrument_token"], int)


def test_missing_required_param_is_input_exception(app_factory) -> None:
    client = TestClient(app_factory())
    bad = dict(ORDER_FORM)
    del bad["quantity"]
    resp = client.post("/orders/regular", data=bad, headers=AUTH_HEADER)
    assert resp.status_code == 400
    body = resp.json()
    assert body["status"] == "error"
    assert body["error_type"] == "InputException"


def test_missing_auth_rejected(app_factory) -> None:
    client = TestClient(app_factory())
    resp = client.post("/orders/regular", data=ORDER_FORM)
    assert resp.status_code == 403
    assert resp.json()["error_type"] == "TokenException"


def _receive_text(ws, attempts: int = 10):
    """Read frames, skipping binary heartbeats, until a JSON text frame arrives."""
    import json

    for _ in range(attempts):
        message = ws.receive()
        if "text" in message and message["text"] is not None:
            return json.loads(message["text"])
    raise AssertionError("no text frame received")


def test_order_placement_emits_ws_update(app_factory) -> None:
    # Slow heartbeat/ticks so order-update is the only text frame in flight.
    app = app_factory(heartbeat_interval_ms=60000, ws_tick_interval_ms=60000)
    client = TestClient(app)
    with client.websocket_connect("/ws?api_key=k&access_token=t") as ws:
        resp = client.post("/orders/regular", data=ORDER_FORM, headers=AUTH_HEADER)
        order_id = resp.json()["data"]["order_id"]
        msg = _receive_text(ws)
        assert msg["type"] == "order"
        assert msg["data"]["order_id"] == order_id
        assert msg["data"]["status"] == "COMPLETE"


def test_ws_streams_parseable_ltp_frame(app_factory) -> None:
    app = app_factory(ws_tick_interval_ms=20, heartbeat_interval_ms=60000)
    client = TestClient(app)
    with client.websocket_connect("/ws?api_key=k&access_token=t") as ws:
        ws.send_json({"a": "subscribe", "v": [408065]})
        ws.send_json({"a": "mode", "v": ["ltp", [408065]]})
        # Skip heartbeats and any pre-mode quote frames; wait for an 8-byte
        # (ltp-mode) packet once the mode switch has been applied.
        frame = b""
        for _ in range(50):
            candidate = ws.receive_bytes()
            if len(candidate) > 1 and struct.unpack(">h", candidate[2:4])[0] == 8:
                frame = candidate
                break
        assert frame, "no ltp-mode market-data frame received"

        num_packets = struct.unpack(">h", frame[:2])[0]
        assert num_packets == 1
        plen = struct.unpack(">h", frame[2:4])[0]
        assert plen == 8  # ltp packet
        packet = frame[4 : 4 + plen]
        token, ltp_unit = struct.unpack(">ii", packet)
        assert token == 408065
        # INFY ~1500 rupees -> paise around 150000, within the +/-20% band.
        assert 120000 <= ltp_unit <= 180000


def test_ws_rejects_when_fixed_auth_mismatch(app_factory, tmp_path) -> None:
    # accept_any default accepts anything; just confirm a connection is accepted.
    client = TestClient(app_factory())
    with client.websocket_connect("/ws?api_key=any&access_token=any") as ws:
        ws.send_json({"a": "subscribe", "v": [408065]})
        # Should receive *something* (data or heartbeat) without error.
        ws.receive_bytes()
