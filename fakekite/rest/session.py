"""User endpoints (KITE_SPEC.md §6, shapes from kite.trade/docs/connect/v3/user).

  GET /user/profile
  GET /user/margins[/:segment]

These serve a deterministic seeded fixture matching the documented object shapes.
``/user/margins`` returns both the ``equity`` and ``commodity`` blocks; the
``/:segment`` variant returns just that one block as ``data`` (per Kite).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from ..auth import Credentials, authenticate
from ..state import AppState
from .envelopes import KiteError, maybe_inject, success

router = APIRouter()

USER_ID = "AB1234"

PROFILE = {
    "user_id": USER_ID,
    "user_type": "individual",
    "email": "user@example.com",
    "user_name": "Test User",
    "user_shortname": "Test",
    "broker": "ZERODHA",
    "exchanges": ["NSE", "BSE", "NFO", "CDS", "BCD", "MCX", "MF"],
    "products": ["CNC", "NRML", "MIS", "MTF"],
    "order_types": ["MARKET", "LIMIT", "SL", "SL-M"],
    "avatar_url": None,
    "meta": {"demat_consent": "physical"},
}


def _margin_block(net: float, cash: float) -> dict[str, Any]:
    return {
        "enabled": True,
        "net": net,
        "available": {
            "adhoc_margin": 0,
            "cash": cash,
            "opening_balance": cash,
            "live_balance": net,
            "collateral": 0,
            "intraday_payin": 0,
        },
        "utilised": {
            "debits": round(cash - net, 2),
            "exposure": 0,
            "m2m_realised": 0,
            "m2m_unrealised": 0,
            "option_premium": 0,
            "payout": 0,
            "span": 0,
            "holding_sales": 0,
            "turnover": 0,
            "liquid_collateral": 0,
            "stock_collateral": 0,
            "delivery": 0,
        },
    }


def _margins() -> dict[str, Any]:
    return {
        "equity": _margin_block(net=99725.05, cash=245431.60),
        "commodity": _margin_block(net=100661.70, cash=100661.70),
    }


@router.get("/user/profile")
async def user_profile(
    request: Request, creds: Credentials = Depends(authenticate)
) -> dict[str, Any]:
    state: AppState = request.app.state.fk
    maybe_inject(state, "user.profile")
    return success(dict(PROFILE))


@router.get("/user/margins")
async def user_margins(
    request: Request, creds: Credentials = Depends(authenticate)
) -> dict[str, Any]:
    state: AppState = request.app.state.fk
    maybe_inject(state, "user.margins")
    return success(_margins())


@router.get("/user/margins/{segment}")
async def user_margins_segment(
    segment: str, request: Request, creds: Credentials = Depends(authenticate)
) -> dict[str, Any]:
    state: AppState = request.app.state.fk
    maybe_inject(state, "user.margins")
    blocks = _margins()
    if segment not in blocks:
        raise KiteError(f"Unknown margin segment: {segment!r}.", "InputException", 400)
    return success(blocks[segment])
