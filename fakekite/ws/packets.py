"""Binary market-data packer (big-endian). Source of truth: KITE_SPEC.md §4.

Packet sizes (asserted in tests):
  tradeable: ltp 8, quote 44, full 184
  index:     ltp 8, quote 28, full 32

Prices are integers in the on-wire unit: rupees x 100 (paise), or rupees x 1e7
for currency-segment (CDS/BCD) instruments. The caller passes ``scale`` (from
:func:`fakekite.market.instruments.price_scale`) and the state dict from the
generator. Integer fields are packed unsigned big-endian (`>I`/`>H`) to match
Kite's wire; the index "price change" is the one signed field (`>i`).

Frame envelope (§4a): [int16 num_packets] then per packet [int16 length][bytes].
"""

from __future__ import annotations

import struct

DEPTH_LEVELS = 5


def _u(value: float, scale: int) -> int:
    """Scale a rupee value to its unsigned integer on-wire price unit."""
    return int(round(value * scale))


# -- LTP (shared by tradeable and index, 8 bytes) ---------------------------
def pack_ltp(token: int, ltp_unit: int) -> bytes:
    return struct.pack(">II", token, ltp_unit)


# -- tradeable instruments --------------------------------------------------
def _pack_depth(state: dict, scale: int) -> bytes:
    """120 bytes: 5 bid then 5 offer entries, each int32 qty + int32 price +
    int16 orders + 2 padding bytes (KITE_SPEC.md §4d)."""
    out = bytearray()
    for level in list(state["bids"]) + list(state["offers"]):
        out += struct.pack(">IIHxx", level["quantity"], _u(level["price"], scale), level["orders"])
    return bytes(out)


def pack_tradeable(state: dict, mode: str, scale: int) -> bytes:
    token = state["token"]
    ltp = _u(state["last_price"], scale)
    if mode == "ltp":
        return struct.pack(">II", token, ltp)

    # quote: bytes 0-44 (11 int32).
    quote = struct.pack(
        ">11I",
        token,
        ltp,
        state["last_traded_quantity"],
        _u(state["average_traded_price"], scale),
        state["volume"],
        state["total_buy_quantity"],
        state["total_sell_quantity"],
        _u(state["open"], scale),
        _u(state["high"], scale),
        _u(state["low"], scale),
        _u(state["close"], scale),
    )
    if mode == "quote":
        return quote

    # full: + bytes 44-64 (5 int32) + 120 bytes depth = 184.
    extra = struct.pack(
        ">5I",
        state["last_trade_time"],
        state["oi"],
        state["oi_day_high"],
        state["oi_day_low"],
        state["exchange_timestamp"],
    )
    return quote + extra + _pack_depth(state, scale)


# -- index instruments (no depth) -------------------------------------------
def pack_index(state: dict, mode: str, scale: int) -> bytes:
    token = state["token"]
    ltp = _u(state["last_price"], scale)
    if mode == "ltp":
        return struct.pack(">II", token, ltp)

    # quote: bytes 0-28. token, ltp, high, low, open, close (6 int32) + change.
    change = int(round((state["last_price"] - state["close"]) * scale))
    quote = struct.pack(
        ">6I",
        token,
        ltp,
        _u(state["high"], scale),
        _u(state["low"], scale),
        _u(state["open"], scale),
        _u(state["close"], scale),
    ) + struct.pack(">i", change)
    if mode == "quote":
        return quote

    # full: + exchange timestamp = 32.
    return quote + struct.pack(">I", state["exchange_timestamp"])


def pack_packet(state: dict, is_index: bool, mode: str, scale: int) -> bytes:
    if is_index:
        return pack_index(state, mode, scale)
    return pack_tradeable(state, mode, scale)


# -- frame envelope ---------------------------------------------------------
def build_market_frame(packets: list[bytes]) -> bytes:
    out = bytearray(struct.pack(">h", len(packets)))
    for p in packets:
        out += struct.pack(">h", len(p))
        out += p
    return bytes(out)
