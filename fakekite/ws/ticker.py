"""WebSocket ticker endpoint: connect, parse subscribe/mode, push market data.

Each connection runs two cooperating tasks on the single event loop:
  - a reader that applies ``subscribe`` / ``unsubscribe`` / ``mode`` requests;
  - a pusher that streams a binary market-data frame for the subscribed tokens
    every ``ws_tick_interval_ms``, or a 1-byte heartbeat when idle.

Milestone 1 packs ltp only; quote/full packing and multi-mode multiplexing land
in Milestone 2. The connection registers itself on ``AppState.ws_clients`` so
order-update text frames can be broadcast to it.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

from fastapi import WebSocket, WebSocketDisconnect

from ..auth import check_ws_auth
from ..latency import sleep_ms
from ..market.instruments import price_scale
from .packets import build_market_frame, pack_packet

if TYPE_CHECKING:
    from ..state import AppState

HEARTBEAT_FRAME = b"\x00"
VALID_MODES = {"ltp", "quote", "full"}
# Kite's default subscribe mode (no explicit mode) is "quote" (KITE_SPEC.md §4).
DEFAULT_MODE = "quote"


class TickerConnection:
    def __init__(self, ws: WebSocket, state: AppState, api_key: str | None = None) -> None:
        self.ws = ws
        self.state = state
        self.api_key = api_key
        # token -> mode for this connection.
        self.subs: dict[int, str] = {}
        # Set by the reader when subscriptions change, so the pusher can stop
        # idling on a heartbeat sleep and start streaming data immediately.
        self._changed = asyncio.Event()

    # -- request handling ---------------------------------------------------
    def _apply(self, msg: dict) -> None:
        action = msg.get("a")
        value = msg.get("v")
        if action == "subscribe" and isinstance(value, list):
            for token in value:
                self.subs.setdefault(int(token), DEFAULT_MODE)
        elif action == "unsubscribe" and isinstance(value, list):
            for token in value:
                self.subs.pop(int(token), None)
        elif action == "mode" and isinstance(value, list) and len(value) == 2:
            mode, tokens = value
            if mode in VALID_MODES and isinstance(tokens, list):
                for token in tokens:
                    self.subs[int(token)] = mode
        else:
            return
        self._changed.set()

    async def _reader(self) -> None:
        while True:
            raw = await self.ws.receive_text()
            try:
                msg = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if isinstance(msg, dict):
                self._apply(msg)

    # -- market-data push ---------------------------------------------------
    def _build_frame(self) -> bytes | None:
        packets: list[bytes] = []
        for token, mode in list(self.subs.items()):
            inst = self.state.instruments.by_token.get(token)
            if inst is None:
                continue
            state = self.state.market.step(token)
            packets.append(pack_packet(state, inst.is_index, mode, price_scale(inst.exchange)))
        if not packets:
            return None
        return build_market_frame(packets)

    async def _idle_wait(self, timeout_ms: int) -> None:
        """Sleep up to ``timeout_ms``, but wake early if subscriptions change."""
        try:
            await asyncio.wait_for(self._changed.wait(), timeout=max(timeout_ms, 1) / 1000.0)
        except TimeoutError:
            pass
        finally:
            self._changed.clear()

    async def _pusher(self) -> None:
        latency = self.state.config.latency
        while True:
            if self.subs:
                frame = self._build_frame()
                if frame is not None:
                    await sleep_ms(latency.ws_push_delay_ms)
                    await self.ws.send_bytes(frame)
                await sleep_ms(latency.ws_tick_interval_ms)
            else:
                await self.ws.send_bytes(HEARTBEAT_FRAME)
                await self._idle_wait(latency.heartbeat_interval_ms)

    # -- lifecycle ----------------------------------------------------------
    async def run(self) -> None:
        self.state.ws_clients.append(self)
        tasks = [
            asyncio.create_task(self._reader()),
            asyncio.create_task(self._pusher()),
        ]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if self in self.state.ws_clients:
                self.state.ws_clients.remove(self)


async def ticker_endpoint(websocket: WebSocket) -> None:
    state: AppState = websocket.app.state.fk
    api_key = websocket.query_params.get("api_key")
    access_token = websocket.query_params.get("access_token")
    if not check_ws_auth(state.config.auth, api_key, access_token):
        await websocket.close(code=1008)  # policy violation
        return
    await websocket.accept()
    conn = TickerConnection(websocket, state, api_key=api_key)
    try:
        await conn.run()
    except WebSocketDisconnect:
        pass
