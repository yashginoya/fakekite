"""Milestone 2: binary packet pack -> parse round-trip, exact sizes, scaling.

Tokens from config.example.yaml:
  256265 NIFTY 50 (index, NSE)        412675 USDINR..FUT (tradeable, CDS, x1e7)
  408065 INFY (tradeable, NSE, x100)  779521 SBIN (tradeable, NSE)
"""

from __future__ import annotations

import struct

import pytest

from fakekite.config import Config, load_config
from fakekite.market.generator import MarketGenerator
from fakekite.market.instruments import InstrumentMaster, price_scale
from fakekite.ws.packets import build_market_frame, pack_packet
from tests.conftest import REPO_ROOT

NIFTY = 256265
INFY = 408065
USDINR = 412675


@pytest.fixture
def cfg() -> Config:
    return load_config(REPO_ROOT / "config.example.yaml")


@pytest.fixture
def gen(cfg: Config):
    instruments = InstrumentMaster.from_config(cfg.market)
    return MarketGenerator(cfg, instruments), instruments


def _scale(instruments: InstrumentMaster, token: int) -> int:
    return price_scale(instruments.by_token[token].exchange)


# -- exact sizes ------------------------------------------------------------
def test_tradeable_sizes(gen) -> None:
    g, instruments = gen
    st = g.step(INFY)
    scale = _scale(instruments, INFY)
    assert len(pack_packet(st, False, "ltp", scale)) == 8
    assert len(pack_packet(st, False, "quote", scale)) == 44
    assert len(pack_packet(st, False, "full", scale)) == 184


def test_index_sizes(gen) -> None:
    g, instruments = gen
    st = g.step(NIFTY)
    scale = _scale(instruments, NIFTY)
    assert len(pack_packet(st, True, "ltp", scale)) == 8
    assert len(pack_packet(st, True, "quote", scale)) == 28
    assert len(pack_packet(st, True, "full", scale)) == 32


# -- scaling ----------------------------------------------------------------
def test_paise_scaling_tradeable(gen) -> None:
    g, instruments = gen
    st = g.step(INFY)
    quote = pack_packet(st, False, "quote", 100)
    fields = struct.unpack(">11I", quote)
    token, ltp = fields[0], fields[1]
    assert token == INFY
    assert ltp == round(st["last_price"] * 100)
    # open/high/low/close at indices 7..10
    assert fields[7] == round(st["open"] * 100)
    assert fields[10] == round(st["close"] * 100)


def test_currency_scaling_1e7(gen) -> None:
    g, instruments = gen
    st = g.step(USDINR)
    scale = _scale(instruments, USDINR)
    assert scale == 10_000_000
    quote = pack_packet(st, False, "quote", scale)
    token, ltp = struct.unpack(">II", quote[:8])
    assert token == USDINR
    assert ltp == round(st["last_price"] * 10_000_000)
    assert abs(ltp / 1e7 - st["last_price"]) < 1e-6


# -- round trip: tradeable full incl. depth ---------------------------------
def test_tradeable_full_roundtrip_and_depth(gen) -> None:
    g, instruments = gen
    # Advance a few steps so depth/volume are populated.
    for _ in range(5):
        st = g.step(INFY)
    full = pack_packet(st, False, "full", 100)
    assert len(full) == 184

    header = struct.unpack(">11I", full[:44])
    assert header[0] == INFY
    assert header[1] == round(st["last_price"] * 100)
    assert header[4] == st["volume"]  # volume field

    lt_time, oi, oi_hi, oi_lo, exch_ts = struct.unpack(">5I", full[44:64])
    assert oi == st["oi"]
    assert exch_ts == st["exchange_timestamp"]

    # Depth: 10 entries of 12 bytes; first 5 bid, last 5 offer.
    bids, offers = [], []
    for i in range(10):
        off = 64 + i * 12
        qty, price, orders = struct.unpack(">IIH", full[off : off + 10])
        assert full[off + 10 : off + 12] == b"\x00\x00"  # padding
        (bids if i < 5 else offers).append((qty, price / 100, orders))

    bid_prices = [p for _, p, _ in bids]
    offer_prices = [p for _, p, _ in offers]
    # bids below ltp and descending; offers above ltp and ascending.
    assert all(p < st["last_price"] for p in bid_prices)
    assert all(p > st["last_price"] for p in offer_prices)
    assert bid_prices == sorted(bid_prices, reverse=True)
    assert offer_prices == sorted(offer_prices)


# -- round trip: index full carries no depth --------------------------------
def test_index_full_has_no_depth(gen) -> None:
    g, instruments = gen
    st = g.step(NIFTY)
    full = pack_packet(st, True, "full", 100)
    assert len(full) == 32  # would be 184 if depth were (wrongly) appended
    token, ltp, high, low, open_, close = struct.unpack(">6I", full[:24])
    change = struct.unpack(">i", full[24:28])[0]
    exch_ts = struct.unpack(">I", full[28:32])[0]
    assert token == NIFTY
    assert ltp == round(st["last_price"] * 100)
    assert high == round(st["high"] * 100)
    assert change == round((st["last_price"] - st["close"]) * 100)
    assert exch_ts == st["exchange_timestamp"]
    assert not st["bids"] and not st["offers"]


def test_index_change_can_be_negative(gen) -> None:
    g, instruments = gen
    # Walk until the price drops below the close reference, forcing change < 0.
    st = None
    for _ in range(200):
        st = g.step(NIFTY)
        if st["last_price"] < st["close"]:
            break
    assert st is not None and st["last_price"] < st["close"]
    quote = pack_packet(st, True, "quote", 100)
    change = struct.unpack(">i", quote[24:28])[0]
    assert change < 0


# -- frame envelope ---------------------------------------------------------
def test_multi_packet_frame(gen) -> None:
    g, instruments = gen
    p1 = pack_packet(g.step(INFY), False, "quote", 100)
    p2 = pack_packet(g.step(NIFTY), True, "quote", 100)
    frame = build_market_frame([p1, p2])
    count = struct.unpack(">h", frame[:2])[0]
    assert count == 2
    off = 2
    sizes = []
    for _ in range(count):
        plen = struct.unpack(">h", frame[off : off + 2])[0]
        off += 2 + plen
        sizes.append(plen)
    assert sizes == [44, 28]
    assert off == len(frame)
