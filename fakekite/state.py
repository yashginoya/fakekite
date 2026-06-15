"""In-memory shared state, touched only from the single event loop.

Plain dicts/lists — no threads, no locks. Everything an order touches (the
orderbook, per-order history, trades) lives here so REST reads and WS updates
share one source of truth. ``reset()`` clears it back to an empty book for the
``/_control/reset`` endpoint and for test isolation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .config import Config
from .market.generator import MarketGenerator
from .market.instruments import InstrumentMaster
from .rest.envelopes import ErrorInjection

if TYPE_CHECKING:
    from .ws.ticker import TickerConnection


@dataclass
class AppState:
    config: Config

    # One in-memory order table. order_id -> latest order object (dict).
    orders: dict[str, dict[str, Any]] = field(default_factory=dict)
    # order_id -> list of successive state snapshots (oldest first) for history.
    order_history: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # order_id -> list of trade objects (only for orders that reach COMPLETE).
    trades: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    # Connected WS tickers, for broadcasting order-update text frames.
    ws_clients: list[TickerConnection] = field(default_factory=list)

    # endpoint key -> pending forced error (control plane / error injection).
    error_injections: dict[str, ErrorInjection] = field(default_factory=dict)

    # Instrument master + price generator, built from config in __post_init__.
    instruments: InstrumentMaster = field(init=False)
    market: MarketGenerator = field(init=False)

    # Monotonic counters for generating unique ids (seeded from a base so the
    # ids look like Kite's 15-ish digit numeric strings).
    _order_seq: int = 0
    _trade_seq: int = 0

    # Control-plane knobs that can be flipped at runtime.
    force_reject_remaining: int = 0

    def __post_init__(self) -> None:
        self.instruments = InstrumentMaster.from_config(self.config.market)
        self.market = MarketGenerator(self.config, self.instruments)

    def reset(self) -> None:
        """Clear the orderbook, history and trades; re-seed the price generator."""
        self.orders.clear()
        self.order_history.clear()
        self.trades.clear()
        self.error_injections.clear()
        self.force_reject_remaining = 0
        self.market.reset()

    def next_order_id(self) -> str:
        self._order_seq += 1
        return str(150000000000000 + self._order_seq)

    def next_exchange_order_id(self) -> str:
        return str(250000000000000 + self._order_seq)

    def next_trade_id(self) -> str:
        self._trade_seq += 1
        return str(10000000 + self._trade_seq)

    def ordered_orders(self) -> list[dict[str, Any]]:
        """Orders in placement order (insertion order of the dict)."""
        return list(self.orders.values())

    def all_trades(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for tl in self.trades.values():
            out.extend(tl)
        return out
