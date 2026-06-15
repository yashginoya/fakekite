"""Instrument master built from config: token <-> exchange:symbol lookups.

The CSV dump for ``GET /instruments`` is added in Milestone 3; for now this just
provides the lookups the order and ticker paths need. We index the config's
``InstrumentConfig`` objects directly rather than copying their fields.
"""

from __future__ import annotations

from ..config import InstrumentConfig, MarketConfig

# Currency-segment exchanges price packets at rupees x 1e7; everything else x 100.
_CURRENCY_EXCHANGES = {"CDS", "BCD"}


def price_scale(exchange: str) -> int:
    """Integer factor to convert rupees to the on-wire price unit for this exchange."""
    return 10_000_000 if exchange in _CURRENCY_EXCHANGES else 100


class InstrumentMaster:
    def __init__(self, instruments: list[InstrumentConfig]) -> None:
        self.list = instruments
        self.by_token: dict[int, InstrumentConfig] = {i.token: i for i in instruments}
        self.by_symbol: dict[tuple[str, str], InstrumentConfig] = {
            (i.exchange, i.symbol): i for i in instruments
        }

    @classmethod
    def from_config(cls, market: MarketConfig) -> InstrumentMaster:
        return cls(list(market.instruments))

    def lookup(self, exchange: str, symbol: str) -> InstrumentConfig | None:
        return self.by_symbol.get((exchange, symbol))
