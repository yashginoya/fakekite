"""Synthetic per-instrument market data (seeded, deterministic walk).

Each instrument keeps a small piece of mutable state: a tick-size-aware bounded
random walk for the LTP, day OHLC tracked across steps, an accumulating volume,
and — for tradeable (non-index) instruments — a synthesised 5+5 level depth book.
Index instruments carry no depth (KITE_SPEC.md §4c).

``step(token)`` advances one instrument and returns its current state dict; the
packers in :mod:`fakekite.ws.packets` read straight from that dict. ``ltp(token)``
reads the current price without advancing (used for order fills).
"""

from __future__ import annotations

import random
import time
from typing import Any

from ..config import Config, InstrumentConfig
from .instruments import InstrumentMaster

DEPTH_LEVELS = 5


class MarketGenerator:
    def __init__(self, config: Config, instruments: InstrumentMaster) -> None:
        self._config = config
        self._instruments = instruments
        self._state: dict[int, dict[str, Any]] = {}
        self.reset()

    def reset(self) -> None:
        """Re-seed and reset every instrument to its configured start price."""
        self._rng = random.Random(self._config.market.seed)
        self._state = {
            inst.token: self._init_state(inst) for inst in self._config.market.instruments
        }

    @staticmethod
    def _init_state(inst: InstrumentConfig) -> dict[str, Any]:
        sp = inst.start_price
        return {
            "token": inst.token,
            "is_index": inst.is_index,
            "last_price": sp,
            "open": sp,
            "high": sp,
            "low": sp,
            "close": sp,  # previous-day close reference
            "volume": 0,
            "last_traded_quantity": 0,
            "average_traded_price": sp,
            "total_buy_quantity": 0,
            "total_sell_quantity": 0,
            "oi": 0,
            "oi_day_high": 0,
            "oi_day_low": 0,
            "last_trade_time": 0,
            "exchange_timestamp": 0,
            "bids": [],
            "offers": [],
        }

    def _round_to_tick(self, price: float, inst: InstrumentConfig) -> float:
        return round(round(price / inst.tick_size) * inst.tick_size, 4)

    def ltp(self, token: int) -> float:
        return self._state[token]["last_price"]

    def snapshot(self, token: int) -> dict[str, Any]:
        """Current state without advancing the walk (used by REST /quote reads)."""
        return self._state[token]

    def step(self, token: int) -> dict[str, Any]:
        """Advance one instrument by one tick and return its state dict."""
        inst = self._instruments.by_token[token]
        st = self._state[token]

        price = st["last_price"] + self._rng.gauss(0.0, inst.volatility) * st["last_price"]
        price = min(max(price, inst.start_price * 0.8), inst.start_price * 1.2)
        price = self._round_to_tick(price, inst)

        st["last_price"] = price
        st["high"] = max(st["high"], price)
        st["low"] = min(st["low"], price)
        now = int(time.time())
        st["last_trade_time"] = now
        st["exchange_timestamp"] = now

        if not inst.is_index:
            ltq = self._rng.randint(1, 100) * inst.lot_size
            st["last_traded_quantity"] = ltq
            st["volume"] += ltq
            st["average_traded_price"] = self._round_to_tick(
                (st["average_traded_price"] + price) / 2.0, inst
            )
            st["total_buy_quantity"] = self._rng.randint(1, 100_000)
            st["total_sell_quantity"] = self._rng.randint(1, 100_000)
            oi = self._rng.randint(0, 1_000_000)
            st["oi"] = oi
            st["oi_day_high"] = max(st["oi_day_high"], oi)
            st["oi_day_low"] = oi if st["oi_day_low"] == 0 else min(st["oi_day_low"], oi)
            st["bids"], st["offers"] = self._depth(inst, price)

        return st

    def _depth(
        self, inst: InstrumentConfig, ltp: float
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        bids: list[dict[str, Any]] = []
        offers: list[dict[str, Any]] = []
        for lvl in range(DEPTH_LEVELS):
            step = inst.tick_size * (lvl + 1)
            bids.append(
                {
                    "quantity": self._rng.randint(1, 1000) * inst.lot_size,
                    "price": self._round_to_tick(max(ltp - step, inst.tick_size), inst),
                    "orders": self._rng.randint(1, 20),
                }
            )
            offers.append(
                {
                    "quantity": self._rng.randint(1, 1000) * inst.lot_size,
                    "price": self._round_to_tick(ltp + step, inst),
                    "orders": self._rng.randint(1, 20),
                }
            )
        return bids, offers
