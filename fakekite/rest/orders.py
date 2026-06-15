"""Order endpoints. Milestone 1: ``POST /orders/:variety`` and ``GET /orders``.

A placed order is built once, into the configured ``orders.default_state`` (or
REJECTED when forced), and stored in the single in-memory order table along with
its history and — for COMPLETE orders — a trade. Every read (GET /orders, and the
history/trade reads added in M3) and the WS order-update frame come from this one
table, so the views stay consistent.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any
from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, Request

from ..auth import Credentials, authenticate
from ..state import AppState
from ..ws.order_updates import emit_order_update
from .envelopes import KiteError, maybe_inject, success

router = APIRouter()

# Allowed values (KITE_SPEC.md §2).
VARIETIES = {"regular", "amo", "co", "iceberg", "auction"}
ORDER_TYPES = {"MARKET", "LIMIT", "SL", "SL-M"}
PRODUCTS = {"CNC", "NRML", "MIS", "MTF"}
VALIDITIES = {"DAY", "IOC", "TTL"}
TRANSACTION_TYPES = {"BUY", "SELL"}
EXCHANGES = {"NSE", "BSE", "NFO", "CDS", "BCD", "MCX"}

PLACED_BY = "AB1234"


async def collect_params(request: Request) -> dict[str, str]:
    """Merge query params and a urlencoded/JSON body into one string->string dict.

    Kite order endpoints take ``application/x-www-form-urlencoded`` bodies; we
    parse that with the stdlib (no python-multipart dependency) and also accept a
    JSON body or query params for convenience.
    """
    params: dict[str, str] = dict(request.query_params)
    body = await request.body()
    if body:
        ctype = request.headers.get("content-type", "")
        if "application/json" in ctype:
            import json

            try:
                data = json.loads(body)
                if isinstance(data, dict):
                    params.update({k: str(v) for k, v in data.items()})
            except ValueError:
                pass
        else:
            params.update(parse_qsl(body.decode("utf-8", errors="replace")))
    return params


def _require(params: dict[str, str], name: str) -> str:
    val = params.get(name)
    if val is None or val == "":
        raise KiteError(f"Missing required field `{name}`.", "InputException", 400)
    return val


def _require_choice(params: dict[str, str], name: str, choices: set[str]) -> str:
    val = _require(params, name)
    if val not in choices:
        raise KiteError(
            f"Invalid `{name}`: {val!r}. Allowed: {sorted(choices)}.",
            "InputException",
            400,
        )
    return val


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _time() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _validated_params(variety: str, params: dict[str, str]) -> dict[str, Any]:
    """Validate required order params and coerce types, raising InputException."""
    if variety not in VARIETIES:
        raise KiteError(f"Invalid variety: {variety!r}.", "InputException", 400)

    tradingsymbol = _require(params, "tradingsymbol")
    exchange = _require_choice(params, "exchange", EXCHANGES)
    transaction_type = _require_choice(params, "transaction_type", TRANSACTION_TYPES)
    order_type = _require_choice(params, "order_type", ORDER_TYPES)
    product = _require_choice(params, "product", PRODUCTS)
    validity = params.get("validity", "DAY")
    if validity not in VALIDITIES:
        raise KiteError(f"Invalid `validity`: {validity!r}.", "InputException", 400)

    qty_raw = _require(params, "quantity")
    try:
        quantity = int(qty_raw)
    except ValueError as exc:
        raise KiteError("`quantity` must be an integer.", "InputException", 400) from exc
    if quantity <= 0:
        raise KiteError("`quantity` must be positive.", "InputException", 400)

    def _num(name: str, default: float = 0.0) -> float:
        v = params.get(name)
        if v is None or v == "":
            return default
        try:
            return float(v)
        except ValueError as exc:
            raise KiteError(f"`{name}` must be a number.", "InputException", 400) from exc

    price = _num("price")
    trigger_price = _num("trigger_price")
    disclosed_quantity = int(_num("disclosed_quantity"))

    # Price/trigger requirements per order_type.
    if order_type in ("LIMIT", "SL") and price <= 0:
        raise KiteError(f"`price` is required for {order_type} orders.", "InputException", 400)
    if order_type in ("SL", "SL-M") and trigger_price <= 0:
        raise KiteError(
            f"`trigger_price` is required for {order_type} orders.", "InputException", 400
        )

    return {
        "tradingsymbol": tradingsymbol,
        "exchange": exchange,
        "transaction_type": transaction_type,
        "order_type": order_type,
        "product": product,
        "validity": validity,
        "quantity": quantity,
        "price": price,
        "trigger_price": trigger_price,
        "disclosed_quantity": disclosed_quantity,
        "tag": params.get("tag"),
    }


def build_order(state: AppState, variety: str, p: dict[str, Any], status: str) -> dict[str, Any]:
    """Construct the full order object (KITE_SPEC.md §3) for the given end state."""
    inst = state.instruments.lookup(p["exchange"], p["tradingsymbol"])
    if inst is None:
        raise KiteError(
            f"Unknown instrument {p['exchange']}:{p['tradingsymbol']}.",
            "InputException",
            400,
        )

    order_id = state.next_order_id()
    quantity = p["quantity"]
    now = _now()

    # Fill price: limit price for LIMIT/SL, else the current market LTP.
    if p["order_type"] in ("LIMIT", "SL"):
        fill_price = p["price"]
    else:
        fill_price = state.market.ltp(inst.token)

    order: dict[str, Any] = {
        "placed_by": PLACED_BY,
        "order_id": order_id,
        "exchange_order_id": state.next_exchange_order_id(),
        "parent_order_id": None,
        "status": status,
        "status_message": None,
        "status_message_raw": None,
        "order_timestamp": now,
        "exchange_update_timestamp": now,
        "exchange_timestamp": now,
        "variety": variety,
        "modified": False,
        "exchange": p["exchange"],
        "tradingsymbol": p["tradingsymbol"],
        "instrument_token": inst.token,
        "order_type": p["order_type"],
        "transaction_type": p["transaction_type"],
        "validity": p["validity"],
        "product": p["product"],
        "quantity": quantity,
        "disclosed_quantity": p["disclosed_quantity"],
        "price": p["price"],
        "trigger_price": p["trigger_price"],
        "average_price": 0.0,
        "filled_quantity": 0,
        "pending_quantity": quantity,
        "cancelled_quantity": 0,
        "market_protection": 0,
        "meta": {},
        "tag": p["tag"],
        "guid": f"guid-{order_id}",
    }

    if status == "COMPLETE":
        order["average_price"] = fill_price
        order["filled_quantity"] = quantity
        order["pending_quantity"] = 0
    elif status == "OPEN":
        order["average_price"] = 0.0
        order["filled_quantity"] = 0
        order["pending_quantity"] = quantity
    elif status == "REJECTED":
        order["average_price"] = 0.0
        order["filled_quantity"] = 0
        order["pending_quantity"] = 0
        order["exchange_order_id"] = None
        order["exchange_timestamp"] = None
        order["status_message"] = state.config.orders.reject_message
        order["status_message_raw"] = state.config.orders.reject_message

    return order


def build_history(order: dict[str, Any]) -> list[dict[str, Any]]:
    """Successive state snapshots, oldest first (KITE_SPEC.md §3)."""
    qty = order["quantity"]
    received = {
        **order,
        "status": "PUT ORDER REQ RECEIVED",
        "average_price": 0.0,
        "filled_quantity": 0,
        "pending_quantity": qty,
    }
    history = [received]
    if order["status"] == "COMPLETE":
        history.append(
            {
                **order,
                "status": "OPEN",
                "average_price": 0.0,
                "filled_quantity": 0,
                "pending_quantity": qty,
            }
        )
    history.append(dict(order))
    return history


def build_trade(state: AppState, order: dict[str, Any]) -> dict[str, Any]:
    """Trade object for a COMPLETE order (KITE_SPEC.md §3)."""
    return {
        "trade_id": state.next_trade_id(),
        "order_id": order["order_id"],
        "exchange": order["exchange"],
        "tradingsymbol": order["tradingsymbol"],
        "instrument_token": order["instrument_token"],
        "product": order["product"],
        "average_price": order["average_price"],
        "quantity": order["quantity"],
        "exchange_order_id": order["exchange_order_id"],
        "transaction_type": order["transaction_type"],
        "fill_timestamp": _now(),
        "order_timestamp": _time(),
        "exchange_timestamp": _now(),
    }


def _record_order(state: AppState, order: dict[str, Any]) -> None:
    """Insert an order plus its history and (if filled) trade into the table."""
    order_id = order["order_id"]
    state.orders[order_id] = order
    state.order_history[order_id] = build_history(order)
    if order["status"] == "COMPLETE":
        state.trades[order_id] = [build_trade(state, order)]


def _schedule_update(state: AppState, order: dict[str, Any]) -> None:
    asyncio.create_task(emit_order_update(state, order, state.config.latency.order_update_delay_ms))


def _get_order(state: AppState, order_id: str) -> dict[str, Any]:
    order = state.orders.get(order_id)
    if order is None:
        raise KiteError(f"Order {order_id} not found.", "InputException", 400)
    return order


@router.post("/orders/{variety}")
async def place_order(
    variety: str, request: Request, creds: Credentials = Depends(authenticate)
) -> dict[str, Any]:
    state: AppState = request.app.state.fk
    maybe_inject(state, "orders.place")
    params = await collect_params(request)
    validated = _validated_params(variety, params)

    # Forced rejection (control plane, M4) overrides the configured default state.
    if state.force_reject_remaining > 0:
        status = "REJECTED"
        state.force_reject_remaining -= 1
    else:
        status = state.config.orders.default_state

    order = build_order(state, variety, validated, status)
    _record_order(state, order)

    _schedule_update(state, order)
    return success({"order_id": order["order_id"]})


@router.put("/orders/{variety}/{order_id}")
async def modify_order(
    variety: str,
    order_id: str,
    request: Request,
    creds: Credentials = Depends(authenticate),
) -> dict[str, Any]:
    state: AppState = request.app.state.fk
    maybe_inject(state, "orders.modify")
    order = _get_order(state, order_id)
    if order["status"] in ("COMPLETE", "CANCELLED", "REJECTED"):
        raise KiteError(
            f"Cannot modify an order in state {order['status']}.", "OrderException", 400
        )

    params = await collect_params(request)
    now = _now()
    if "quantity" in params and params["quantity"]:
        try:
            qty = int(params["quantity"])
        except ValueError as exc:
            raise KiteError("`quantity` must be an integer.", "InputException", 400) from exc
        if qty <= 0:
            raise KiteError("`quantity` must be positive.", "InputException", 400)
        order["quantity"] = qty
        order["pending_quantity"] = qty
    for fld in ("price", "trigger_price"):
        if fld in params and params[fld]:
            try:
                order[fld] = float(params[fld])
            except ValueError as exc:
                raise KiteError(f"`{fld}` must be a number.", "InputException", 400) from exc
    if params.get("order_type") in ORDER_TYPES:
        order["order_type"] = params["order_type"]
    if params.get("validity") in VALIDITIES:
        order["validity"] = params["validity"]

    order["modified"] = True
    order["order_timestamp"] = now
    order["exchange_update_timestamp"] = now
    state.order_history[order_id].append({**order, "status": "MODIFIED"})
    state.order_history[order_id].append(dict(order))

    _schedule_update(state, order)
    return success({"order_id": order_id})


@router.delete("/orders/{variety}/{order_id}")
async def cancel_order(
    variety: str,
    order_id: str,
    request: Request,
    creds: Credentials = Depends(authenticate),
) -> dict[str, Any]:
    state: AppState = request.app.state.fk
    maybe_inject(state, "orders.cancel")
    order = _get_order(state, order_id)
    if order["status"] in ("COMPLETE", "CANCELLED", "REJECTED"):
        raise KiteError(
            f"Cannot cancel an order in state {order['status']}.", "OrderException", 400
        )

    now = _now()
    order["status"] = "CANCELLED"
    order["cancelled_quantity"] = order["pending_quantity"]
    order["pending_quantity"] = 0
    order["order_timestamp"] = now
    order["exchange_update_timestamp"] = now
    state.order_history[order_id].append(dict(order))

    _schedule_update(state, order)
    return success({"order_id": order_id})


@router.get("/orders")
async def list_orders(
    request: Request, creds: Credentials = Depends(authenticate)
) -> dict[str, Any]:
    state: AppState = request.app.state.fk
    maybe_inject(state, "orders.list")
    return success(state.ordered_orders())


@router.get("/orders/{order_id}")
async def order_history(
    order_id: str, request: Request, creds: Credentials = Depends(authenticate)
) -> dict[str, Any]:
    state: AppState = request.app.state.fk
    maybe_inject(state, "orders.history")
    if order_id not in state.orders:
        raise KiteError(f"Order {order_id} not found.", "InputException", 400)
    if not state.config.orders.emit_order_history:
        # History disabled: return just the current state as a single-element list.
        return success([dict(state.orders[order_id])])
    return success(state.order_history.get(order_id, []))


@router.get("/orders/{order_id}/trades")
async def order_trades(
    order_id: str, request: Request, creds: Credentials = Depends(authenticate)
) -> dict[str, Any]:
    state: AppState = request.app.state.fk
    maybe_inject(state, "orders.order_trades")
    if order_id not in state.orders:
        raise KiteError(f"Order {order_id} not found.", "InputException", 400)
    return success(state.trades.get(order_id, []))


@router.get("/trades")
async def list_trades(
    request: Request, creds: Credentials = Depends(authenticate)
) -> dict[str, Any]:
    state: AppState = request.app.state.fk
    maybe_inject(state, "trades.list")
    return success(state.all_trades())
