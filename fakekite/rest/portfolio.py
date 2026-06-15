"""Portfolio endpoints (KITE_SPEC.md §7, shapes from kite.trade/.../portfolio).

  GET /portfolio/positions   net + day arrays, derived from COMPLETE orders
  GET /portfolio/holdings    seeded from the config's equity instruments

Positions are derived from the in-memory order table so they reflect what the
terminal has placed; holdings are a deterministic seeded fixture (they represent
prior-day delivery, not intraday orders).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from ..auth import Credentials, authenticate
from ..state import AppState
from .envelopes import maybe_inject, success

router = APIRouter()


def _build_positions(state: AppState) -> list[dict[str, Any]]:
    """Aggregate COMPLETE orders into net positions per instrument+product."""
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    for order in state.orders.values():
        if order["status"] != "COMPLETE":
            continue
        key = (order["exchange"], order["tradingsymbol"], order["product"])
        g = groups.setdefault(
            key,
            {
                "exchange": order["exchange"],
                "tradingsymbol": order["tradingsymbol"],
                "product": order["product"],
                "instrument_token": order["instrument_token"],
                "buy_quantity": 0,
                "buy_value": 0.0,
                "sell_quantity": 0,
                "sell_value": 0.0,
            },
        )
        qty = order["filled_quantity"]
        value = qty * order["average_price"]
        if order["transaction_type"] == "BUY":
            g["buy_quantity"] += qty
            g["buy_value"] += value
        else:
            g["sell_quantity"] += qty
            g["sell_value"] += value

    positions: list[dict[str, Any]] = []
    for g in groups.values():
        token = g["instrument_token"]
        inst = state.instruments.by_token.get(token)
        multiplier = inst.lot_size if inst else 1
        quantity = g["buy_quantity"] - g["sell_quantity"]
        buy_value = round(g["buy_value"], 2)
        sell_value = round(g["sell_value"], 2)
        last_price = round(state.market.ltp(token), 2) if inst else 0
        close_price = round(state.market.snapshot(token)["close"], 2) if inst else 0
        buy_price = round(buy_value / g["buy_quantity"], 2) if g["buy_quantity"] else 0
        sell_price = round(sell_value / g["sell_quantity"], 2) if g["sell_quantity"] else 0
        avg = round(abs(buy_value - sell_value) / quantity, 2) if quantity else 0
        value = round(sell_value - buy_value, 2)
        pnl = round((sell_value - buy_value) + quantity * last_price * multiplier, 2)
        positions.append(
            {
                "tradingsymbol": g["tradingsymbol"],
                "exchange": g["exchange"],
                "instrument_token": token,
                "product": g["product"],
                "quantity": quantity,
                "overnight_quantity": 0,
                "multiplier": multiplier,
                "average_price": avg,
                "close_price": close_price,
                "last_price": last_price,
                "value": value,
                "pnl": pnl,
                "m2m": pnl,
                "unrealised": pnl,
                "realised": 0,
                "buy_quantity": g["buy_quantity"],
                "buy_price": buy_price,
                "buy_value": buy_value,
                "buy_m2m": buy_value,
                "sell_quantity": g["sell_quantity"],
                "sell_price": sell_price,
                "sell_value": sell_value,
                "sell_m2m": sell_value,
                "day_buy_quantity": g["buy_quantity"],
                "day_buy_price": buy_price,
                "day_buy_value": buy_value,
                "day_sell_quantity": g["sell_quantity"],
                "day_sell_price": sell_price,
                "day_sell_value": sell_value,
            }
        )
    return positions


def _build_holdings(state: AppState) -> list[dict[str, Any]]:
    """One seeded holding per cash-segment equity instrument in config."""
    holdings: list[dict[str, Any]] = []
    for inst in state.instruments.list:
        if inst.is_index or inst.exchange not in ("NSE", "BSE"):
            continue
        last_price = round(state.market.ltp(inst.token), 2)
        close_price = round(state.market.snapshot(inst.token)["close"], 2)
        avg = round(inst.start_price * 0.9, 2)  # acquired ~10% cheaper
        qty = 10
        pnl = round((last_price - avg) * qty, 2)
        day_change = round(last_price - close_price, 2)
        holdings.append(
            {
                "tradingsymbol": inst.symbol,
                "exchange": inst.exchange,
                "instrument_token": inst.token,
                "isin": "INE000000000",
                "product": "CNC",
                "price": 0,
                "quantity": qty,
                "used_quantity": 0,
                "t1_quantity": 0,
                "realised_quantity": qty,
                "authorised_quantity": 0,
                "authorised_date": "",
                "authorisation": {},
                "opening_quantity": qty,
                "short_quantity": 0,
                "collateral_quantity": 0,
                "collateral_type": "",
                "discrepancy": False,
                "average_price": avg,
                "last_price": last_price,
                "close_price": close_price,
                "pnl": pnl,
                "day_change": day_change,
                "day_change_percentage": (
                    round(day_change / close_price * 100, 4) if close_price else 0
                ),
            }
        )
    return holdings


@router.get("/portfolio/positions")
async def positions(request: Request, creds: Credentials = Depends(authenticate)) -> dict[str, Any]:
    state: AppState = request.app.state.fk
    maybe_inject(state, "portfolio.positions")
    net = _build_positions(state)
    return success({"net": net, "day": list(net)})


@router.get("/portfolio/holdings")
async def holdings(request: Request, creds: Credentials = Depends(authenticate)) -> dict[str, Any]:
    state: AppState = request.app.state.fk
    maybe_inject(state, "portfolio.holdings")
    return success(_build_holdings(state))
