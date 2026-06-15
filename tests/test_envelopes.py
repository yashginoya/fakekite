"""Milestone 3: error envelope shapes/codes + new REST endpoint coverage."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from fakekite.rest.envelopes import ERROR_TYPE_STATUS, ErrorInjection
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


def _place(client) -> str:
    return client.post("/orders/regular", data=ORDER_FORM, headers=AUTH_HEADER).json()["data"][
        "order_id"
    ]


# -- error envelope shape + code for every error_type -----------------------
@pytest.mark.parametrize("error_type,status", sorted(ERROR_TYPE_STATUS.items()))
def test_error_envelope_for_each_type(app_factory, error_type, status) -> None:
    app = app_factory()
    state = app.state.fk
    state.error_injections["orders.list"] = ErrorInjection(
        error_type=error_type, message=f"forced {error_type}"
    )
    resp = TestClient(app).get("/orders", headers=AUTH_HEADER)
    assert resp.status_code == status
    body = resp.json()
    assert body == {
        "status": "error",
        "message": f"forced {error_type}",
        "error_type": error_type,
    }
    assert resp.headers["X-Kite-Version"] == "3"


def test_injection_custom_status_code(app_factory) -> None:
    app = app_factory()
    app.state.fk.error_injections["orders.list"] = ErrorInjection(
        error_type="UserException", message="blocked", status_code=400
    )
    resp = TestClient(app).get("/orders", headers=AUTH_HEADER)
    assert resp.status_code == 400  # override of the 403 default
    assert resp.json()["error_type"] == "UserException"


def test_injection_consumed_after_count(app_factory) -> None:
    app = app_factory()
    client = TestClient(app)
    app.state.fk.error_injections["orders.list"] = ErrorInjection(
        error_type="GeneralException", message="once", count=1
    )
    assert client.get("/orders", headers=AUTH_HEADER).status_code == 500
    # Consumed: next call succeeds.
    assert client.get("/orders", headers=AUTH_HEADER).status_code == 200


# -- modify / cancel --------------------------------------------------------
def test_modify_order(app_factory) -> None:
    app = app_factory(order_update_delay_ms=0)
    client = TestClient(app)
    # Use OPEN so the order is modifiable.
    app.state.fk.config.orders.default_state = "OPEN"
    order_id = _place(client)
    resp = client.put(
        f"/orders/regular/{order_id}", data={"quantity": "5", "price": "1490"}, headers=AUTH_HEADER
    )
    assert resp.status_code == 200
    order = client.get("/orders", headers=AUTH_HEADER).json()["data"][0]
    assert order["quantity"] == 5
    assert order["price"] == 1490.0
    assert order["modified"] is True


def test_cancel_order(app_factory) -> None:
    app = app_factory()
    client = TestClient(app)
    app.state.fk.config.orders.default_state = "OPEN"
    order_id = _place(client)
    resp = client.delete(f"/orders/regular/{order_id}", headers=AUTH_HEADER)
    assert resp.status_code == 200
    order = client.get("/orders", headers=AUTH_HEADER).json()["data"][0]
    assert order["status"] == "CANCELLED"
    assert order["cancelled_quantity"] == 1
    assert order["pending_quantity"] == 0


def test_cancel_completed_order_fails(app_factory) -> None:
    app = app_factory()
    client = TestClient(app)  # default_state COMPLETE
    order_id = _place(client)
    resp = client.delete(f"/orders/regular/{order_id}", headers=AUTH_HEADER)
    assert resp.status_code == 400
    assert resp.json()["error_type"] == "OrderException"


# -- history / trades -------------------------------------------------------
def test_order_history_is_list_of_states(app_factory) -> None:
    client = TestClient(app_factory())
    order_id = _place(client)
    resp = client.get(f"/orders/{order_id}", headers=AUTH_HEADER)
    assert resp.status_code == 200
    states = resp.json()["data"]
    assert isinstance(states, list)
    assert states[0]["status"] == "PUT ORDER REQ RECEIVED"
    assert states[-1]["status"] == "COMPLETE"


def test_trades_for_completed_order(app_factory) -> None:
    client = TestClient(app_factory())
    order_id = _place(client)
    # global trades
    all_trades = client.get("/trades", headers=AUTH_HEADER).json()["data"]
    assert len(all_trades) == 1
    assert all_trades[0]["order_id"] == order_id
    assert isinstance(all_trades[0]["trade_id"], str)
    # per-order trades
    one = client.get(f"/orders/{order_id}/trades", headers=AUTH_HEADER).json()["data"]
    assert len(one) == 1
    assert one[0]["order_id"] == order_id


def test_open_order_has_no_trade(app_factory) -> None:
    app = app_factory()
    app.state.fk.config.orders.default_state = "OPEN"
    client = TestClient(app)
    _place(client)
    assert client.get("/trades", headers=AUTH_HEADER).json()["data"] == []


# -- market quotes ----------------------------------------------------------
def test_quote_full(app_factory) -> None:
    client = TestClient(app_factory())
    resp = client.get("/quote?i=NSE:INFY&i=NSE:SBIN", headers=AUTH_HEADER)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert set(data) == {"NSE:INFY", "NSE:SBIN"}
    infy = data["NSE:INFY"]
    assert infy["instrument_token"] == 408065
    assert "ohlc" in infy and set(infy["ohlc"]) == {"open", "high", "low", "close"}
    assert "depth" in infy and len(infy["depth"]["buy"]) == 5
    assert isinstance(infy["last_price"], (int, float))


def test_quote_index_has_no_depth(app_factory) -> None:
    client = TestClient(app_factory())
    data = client.get("/quote?i=NSE:NIFTY 50", headers=AUTH_HEADER).json()["data"]
    nifty = data["NSE:NIFTY 50"]
    assert nifty["depth"]["buy"] == [] and nifty["depth"]["sell"] == []


def test_quote_ohlc_and_ltp(app_factory) -> None:
    client = TestClient(app_factory())
    ohlc = client.get("/quote/ohlc?i=NSE:INFY", headers=AUTH_HEADER).json()["data"]["NSE:INFY"]
    assert set(ohlc) == {"instrument_token", "last_price", "ohlc"}
    ltp = client.get("/quote/ltp?i=NSE:INFY", headers=AUTH_HEADER).json()["data"]["NSE:INFY"]
    assert set(ltp) == {"instrument_token", "last_price"}


def test_quote_unknown_instrument_omitted(app_factory) -> None:
    client = TestClient(app_factory())
    data = client.get("/quote/ltp?i=NSE:NOPE", headers=AUTH_HEADER).json()["data"]
    assert data == {}


# -- instruments CSV --------------------------------------------------------
def test_instruments_csv(app_factory) -> None:
    client = TestClient(app_factory())
    resp = client.get("/instruments", headers=AUTH_HEADER)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    lines = resp.text.strip().splitlines()
    header = lines[0].split(",")
    assert header == [
        "instrument_token",
        "exchange_token",
        "tradingsymbol",
        "name",
        "last_price",
        "expiry",
        "strike",
        "tick_size",
        "lot_size",
        "instrument_type",
        "segment",
        "exchange",
    ]
    assert len(lines) == 5  # header + 4 instruments


def test_instruments_filtered_by_exchange(app_factory) -> None:
    client = TestClient(app_factory())
    lines = client.get("/instruments/CDS", headers=AUTH_HEADER).text.strip().splitlines()
    assert len(lines) == 2  # header + USDINR only
    assert lines[1].split(",")[2] == "USDINR25JUNFUT"


# -- user -------------------------------------------------------------------
def test_user_profile(app_factory) -> None:
    client = TestClient(app_factory())
    data = client.get("/user/profile", headers=AUTH_HEADER).json()["data"]
    assert data["user_id"] == "AB1234"
    assert "CNC" in data["products"]
    assert "MARKET" in data["order_types"]


def test_user_margins(app_factory) -> None:
    client = TestClient(app_factory())
    data = client.get("/user/margins", headers=AUTH_HEADER).json()["data"]
    assert set(data) == {"equity", "commodity"}
    assert set(data["equity"]) == {"enabled", "net", "available", "utilised"}
    seg = client.get("/user/margins/equity", headers=AUTH_HEADER).json()["data"]
    assert set(seg) == {"enabled", "net", "available", "utilised"}


def test_user_margins_bad_segment(app_factory) -> None:
    client = TestClient(app_factory())
    resp = client.get("/user/margins/bogus", headers=AUTH_HEADER)
    assert resp.status_code == 400
    assert resp.json()["error_type"] == "InputException"


# -- portfolio --------------------------------------------------------------
def test_holdings_shape(app_factory) -> None:
    client = TestClient(app_factory())
    holdings = client.get("/portfolio/holdings", headers=AUTH_HEADER).json()["data"]
    assert len(holdings) == 2  # INFY + SBIN (NSE equities, not index/CDS)
    h = holdings[0]
    for k in ("tradingsymbol", "average_price", "last_price", "pnl", "day_change", "quantity"):
        assert k in h


def test_positions_derived_from_orders(app_factory) -> None:
    client = TestClient(app_factory())  # default COMPLETE
    # No orders yet -> empty net/day.
    empty = client.get("/portfolio/positions", headers=AUTH_HEADER).json()["data"]
    assert empty == {"net": [], "day": []}
    # Place a buy; it should appear as a net position.
    _place(client)
    data = client.get("/portfolio/positions", headers=AUTH_HEADER).json()["data"]
    assert len(data["net"]) == 1
    pos = data["net"][0]
    assert pos["tradingsymbol"] == "INFY"
    assert pos["quantity"] == 1
    assert pos["buy_quantity"] == 1
    assert data["day"][0]["tradingsymbol"] == "INFY"


# -- rate limiting (default off) --------------------------------------------
def test_rate_limit_off_by_default(app_factory) -> None:
    client = TestClient(app_factory())
    # Many quote calls succeed when limiting is disabled.
    for _ in range(5):
        assert client.get("/quote/ltp?i=NSE:INFY", headers=AUTH_HEADER).status_code == 200


def test_rate_limit_trips_429(app_factory) -> None:
    app = app_factory()
    app.state.fk.config.rate_limit.enabled = True
    client = TestClient(app)
    # /quote limit is 1 req/sec; the 2nd call in the same second is throttled.
    codes = [client.get("/quote/ltp?i=NSE:INFY", headers=AUTH_HEADER).status_code for _ in range(3)]
    assert codes[0] == 200
    assert 429 in codes
    throttled = client.get("/quote/ltp?i=NSE:INFY", headers=AUTH_HEADER)
    if throttled.status_code == 429:
        assert throttled.json()["status"] == "error"
