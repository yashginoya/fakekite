"""Control-plane endpoints under ``/_control`` — the test backdoor.

These are NOT part of the Kite API; they let a test harness force specific
behaviours from the outside without restarting:

  POST /_control/reset        clear the order/trade/injection state, re-seed market
  POST /_control/latency      live-update any latency field
  POST /_control/disconnect   drop all WS sockets, or just one api_key's
  POST /_control/force_reject  the next N placed orders land in REJECTED
  POST /_control/order_state  change orders.default_state at runtime
  POST /_control/error        set/clear a forced error for a named endpoint

They require no Kite auth (they are our own control surface) and are exempt from
the rate limiter.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from pydantic import ValidationError

from .config import LatencyConfig
from .rest.envelopes import ErrorInjection, KiteError, success
from .state import AppState

router = APIRouter(prefix="/_control")


async def _body(request: Request) -> dict[str, Any]:
    raw = await request.body()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise KiteError("Invalid JSON body.", "InputException", 400) from exc
    if not isinstance(data, dict):
        raise KiteError("Control body must be a JSON object.", "InputException", 400)
    return data


def _state(request: Request) -> AppState:
    return request.app.state.fk


@router.post("/reset")
async def reset(request: Request) -> dict[str, Any]:
    _state(request).reset()
    return success({"reset": True})


@router.post("/latency")
async def set_latency(request: Request) -> dict[str, Any]:
    state = _state(request)
    data = await _body(request)
    unknown = set(data) - set(LatencyConfig.model_fields)
    if unknown:
        raise KiteError(f"Unknown latency field(s): {sorted(unknown)}.", "InputException", 400)
    try:
        for field, value in data.items():
            setattr(state.config.latency, field, value)
    except ValidationError as exc:
        msg = exc.errors()[0]["msg"]
        raise KiteError(f"Invalid latency value: {msg}", "InputException", 400) from exc
    return success(state.config.latency.model_dump())


@router.post("/disconnect")
async def disconnect(request: Request) -> dict[str, Any]:
    state = _state(request)
    data = await _body(request)
    api_key = data.get("api_key")
    targets = [c for c in list(state.ws_clients) if api_key is None or c.api_key == api_key]
    for client in targets:
        try:
            await client.ws.close(code=1000)
        except Exception:
            pass
    return success({"disconnected": len(targets)})


@router.post("/force_reject")
async def force_reject(request: Request) -> dict[str, Any]:
    state = _state(request)
    data = await _body(request)
    count = data.get("count", 1)
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        raise KiteError("`count` must be a non-negative integer.", "InputException", 400)
    state.force_reject_remaining = count
    return success({"force_reject_remaining": count})


@router.post("/order_state")
async def order_state(request: Request) -> dict[str, Any]:
    state = _state(request)
    data = await _body(request)
    new_state = data.get("state")
    try:
        state.config.orders.default_state = new_state
    except ValidationError as exc:
        raise KiteError(
            f"Invalid order state: {new_state!r}. Allowed: COMPLETE, OPEN, REJECTED.",
            "InputException",
            400,
        ) from exc
    return success({"default_state": state.config.orders.default_state})


@router.post("/error")
async def set_error(request: Request) -> dict[str, Any]:
    state = _state(request)
    data = await _body(request)
    endpoint = data.get("endpoint")
    if not endpoint:
        raise KiteError("`endpoint` is required.", "InputException", 400)
    if data.get("clear"):
        state.error_injections.pop(endpoint, None)
        return success({"cleared": endpoint})
    error_type = data.get("error_type")
    if not error_type:
        raise KiteError("`error_type` is required (or pass clear=true).", "InputException", 400)
    injection = ErrorInjection(
        error_type=error_type,
        message=data.get("message", "Injected error."),
        status_code=data.get("status_code"),
        count=data.get("count", 1),
    )
    state.error_injections[endpoint] = injection
    return success({"endpoint": endpoint, "error_type": error_type, "count": injection.count})
