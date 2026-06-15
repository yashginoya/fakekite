"""Order-update text frames broadcast over the WebSocket (KITE_SPEC.md §4f).

Whenever an order changes state we send a TEXT frame
``{"type": "order", "data": <order object>}`` to every connected ticker, after
the configured ``order_update_delay_ms`` so the terminal's async-update path is
exercised. Market data is binary; these updates are always text.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from ..latency import sleep_ms

if TYPE_CHECKING:
    from ..state import AppState


async def broadcast_text(state: AppState, payload: dict[str, Any]) -> None:
    """Send a JSON text frame to all connected tickers, dropping dead sockets."""
    frame = json.dumps(payload)
    for client in list(state.ws_clients):
        try:
            await client.ws.send_text(frame)
        except Exception:
            # Socket already gone; the connection's own cleanup will deregister it.
            pass


async def emit_order_update(state: AppState, order: dict[str, Any], delay_ms: int) -> None:
    """After ``delay_ms``, broadcast an order-update frame for a snapshot of ``order``."""
    snapshot = dict(order)
    await sleep_ms(delay_ms)
    await broadcast_text(state, {"type": "order", "data": snapshot})
