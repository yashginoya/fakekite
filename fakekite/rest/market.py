"""Market quote + instruments endpoints (KITE_SPEC.md §5, shapes from kite.trade).

  GET /quote          full quote incl. depth
  GET /quote/ohlc     LTP + OHLC
  GET /quote/ltp      LTP only
  GET /instruments[/:exchange]   CSV dump (not JSON)

Response keys for the quote endpoints are the ``exchange:tradingsymbol`` strings;
unknown instruments are omitted (matching Kite). Reads use the generator snapshot
and never advance the walk, and depth is synthesised deterministically so REST
reads don't perturb the seeded WS tick stream.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse

from ..auth import Credentials, authenticate
from ..config import InstrumentConfig
from ..state import AppState
from .envelopes import maybe_inject, success

router = APIRouter()

INSTRUMENTS_COLUMNS = [
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


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _requested_instruments(request: Request, state: AppState) -> list[tuple[str, InstrumentConfig]]:
    """Resolve the ?i=EXCHANGE:SYMBOL params to (key, instrument) pairs, skipping unknowns."""
    out: list[tuple[str, InstrumentConfig]] = []
    for key in request.query_params.getlist("i"):
        if ":" not in key:
            continue
        exchange, symbol = key.split(":", 1)
        inst = state.instruments.lookup(exchange, symbol)
        if inst is not None:
            out.append((key, inst))
    return out


def _depth(ltp: float, inst: InstrumentConfig) -> dict[str, list[dict[str, Any]]]:
    """Deterministic 5+5 depth (no rng, so the WS stream stays reproducible)."""
    if inst.is_index:
        return {"buy": [], "sell": []}
    buy: list[dict[str, Any]] = []
    sell: list[dict[str, Any]] = []
    for lvl in range(5):
        step = round(inst.tick_size * (lvl + 1), 4)
        qty = (lvl + 1) * 100 * inst.lot_size
        buy.append({"price": round(ltp - step, 4), "quantity": qty, "orders": lvl + 1})
        sell.append({"price": round(ltp + step, 4), "quantity": qty, "orders": lvl + 1})
    return {"buy": buy, "sell": sell}


def _full_quote(state: AppState, inst: InstrumentConfig) -> dict[str, Any]:
    st = state.market.snapshot(inst.token)
    ltp = st["last_price"]
    close = st["close"]
    return {
        "instrument_token": inst.token,
        "timestamp": _ts(),
        "last_trade_time": _ts(),
        "last_price": ltp,
        "last_quantity": st["last_traded_quantity"],
        "buy_quantity": st["total_buy_quantity"],
        "sell_quantity": st["total_sell_quantity"],
        "volume": st["volume"],
        "average_price": st["average_traded_price"],
        "oi": st["oi"],
        "oi_day_high": st["oi_day_high"],
        "oi_day_low": st["oi_day_low"],
        "net_change": round(ltp - close, 4),
        "lower_circuit_limit": round(close * 0.9, 1),
        "upper_circuit_limit": round(close * 1.1, 1),
        "ohlc": {
            "open": st["open"],
            "high": st["high"],
            "low": st["low"],
            "close": close,
        },
        "depth": _depth(ltp, inst),
    }


@router.get("/quote")
async def quote(request: Request, creds: Credentials = Depends(authenticate)) -> dict[str, Any]:
    state: AppState = request.app.state.fk
    maybe_inject(state, "quote")
    data = {key: _full_quote(state, inst) for key, inst in _requested_instruments(request, state)}
    return success(data)


@router.get("/quote/ohlc")
async def quote_ohlc(
    request: Request, creds: Credentials = Depends(authenticate)
) -> dict[str, Any]:
    state: AppState = request.app.state.fk
    maybe_inject(state, "quote.ohlc")
    data = {}
    for key, inst in _requested_instruments(request, state):
        st = state.market.snapshot(inst.token)
        data[key] = {
            "instrument_token": inst.token,
            "last_price": st["last_price"],
            "ohlc": {
                "open": st["open"],
                "high": st["high"],
                "low": st["low"],
                "close": st["close"],
            },
        }
    return success(data)


@router.get("/quote/ltp")
async def quote_ltp(request: Request, creds: Credentials = Depends(authenticate)) -> dict[str, Any]:
    state: AppState = request.app.state.fk
    maybe_inject(state, "quote.ltp")
    data = {}
    for key, inst in _requested_instruments(request, state):
        data[key] = {
            "instrument_token": inst.token,
            "last_price": state.market.snapshot(inst.token)["last_price"],
        }
    return success(data)


def _instrument_type(inst: InstrumentConfig) -> str:
    if "FUT" in inst.symbol:
        return "FUT"
    return "EQ"


def _instruments_csv(state: AppState, exchange: str | None) -> str:
    rows = [",".join(INSTRUMENTS_COLUMNS)]
    for inst in state.instruments.list:
        if exchange is not None and inst.exchange != exchange:
            continue
        last_price = round(state.market.ltp(inst.token), 2)
        row = [
            str(inst.token),
            str(inst.token >> 8),  # exchange_token (Kite: token >> 8)
            inst.symbol,
            inst.symbol,  # name
            f"{last_price}",
            "",  # expiry
            "0",  # strike
            f"{inst.tick_size}",
            str(inst.lot_size),
            _instrument_type(inst),
            inst.segment or inst.exchange,
            inst.exchange,
        ]
        rows.append(",".join(row))
    return "\n".join(rows) + "\n"


@router.get("/instruments")
async def instruments(
    request: Request, creds: Credentials = Depends(authenticate)
) -> PlainTextResponse:
    state: AppState = request.app.state.fk
    maybe_inject(state, "instruments")
    return PlainTextResponse(_instruments_csv(state, None), media_type="text/csv")


@router.get("/instruments/{exchange}")
async def instruments_for_exchange(
    exchange: str, request: Request, creds: Credentials = Depends(authenticate)
) -> PlainTextResponse:
    state: AppState = request.app.state.fk
    maybe_inject(state, "instruments")
    return PlainTextResponse(_instruments_csv(state, exchange), media_type="text/csv")
