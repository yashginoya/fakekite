# FakeKite — Kite Connect v3 mock broker

## What this is
A local **test double** of Zerodha's Kite Connect v3 API (REST + WebSocket). The
purpose is to point our in-house trading terminal at `localhost` instead of the
real broker so we can exercise it end to end and surface bugs in the terminal's
broker-integration code.

This is **not** a clone of Zerodha behaviour and **not** a matching engine.
Optimize for two things, in order:
1. **Wire fidelity** — the terminal must not be able to tell it is talking to a fake.
2. **Deterministic, configurable behaviour** — we can force specific outcomes
   (instant fill, resting order, rejection, latency, disconnect) from config or a
   control endpoint.

## Stack
- Python 3.11+, asyncio, **single process / single event loop**.
- FastAPI + uvicorn for both REST routes and the WebSocket endpoint.
- Dependencies kept minimal: `fastapi`, `uvicorn[standard]`, `pydantic>=2`, `pyyaml`.
  Binary packing uses stdlib `struct`. No numpy, no extra deps without a reason.
- Target OS: Ubuntu / Linux.

## Source of truth for the wire format
- The verified spec is in **`docs/KITE_SPEC.md`** (transcribed from the official
  docs). Use it.
- For anything not covered there, **fetch the official docs** (URLs are listed in
  `docs/KITE_SPEC.md`) and match them. **Never invent field names, JSON keys, or
  byte offsets.** A wrong offset turns every test into a false failure.

## Hard rules (do not violate)
- **Binary packets are big-endian.** Prices are in **paise** (rupees × 100).
  Currency-segment (CDS/BCD) prices use × 10,000,000. See the spec.
- **All artificial delays use `await asyncio.sleep(...)`.** Never `time.sleep` —
  it blocks the single event loop and stalls every connected client at once.
- **Shared state is plain in-memory dicts**, touched only from the event loop. No
  threads, no locks. Background work (the tick push loop) runs as an asyncio task
  on the same loop.
- **REST base URL and WS URL are independent settings.** Do not hardcode either.
  Both come from config. (The terminal must override both; if it can't, that's a
  terminal bug to report, not something to work around here.)
- **Auth model:** the terminal sends `Authorization: token api_key:access_token`
  on every REST call, and `api_key` + `access_token` as WS query params. Default
  config `auth.mode: accept_any` accepts anything. No interactive login / browser
  redirect is implemented or needed.
- **No matching engine.** A placed order goes straight to a configured terminal
  state (`COMPLETE` / `OPEN` / `REJECTED`). But state must be **consistent**: an
  order created by `POST /orders/:variety` must appear in `GET /orders`,
  `GET /orders/:order_id`, and (when filled) `GET /trades` and
  `GET /orders/:order_id/trades`, and must emit a WS order-update **text** frame.
  All reads and the WS update read from one in-memory order table.

## Message-type discipline (WebSocket)
- **Market data → binary frames.** Postbacks, order updates, errors, alerts →
  **text** frames (JSON). Never send market data as text or updates as binary.
- WS market-data frame = `int16 count`, then for each packet `int16 length` +
  packet bytes (all big-endian).
- Idle heartbeat is a **single 1-byte** frame sent every couple of seconds. The
  terminal is expected to ignore it.

## JSON conventions
- Success envelope: `{"status": "success", "data": ...}`.
- Error envelope: `{"status": "error", "message": "...", "error_type": "..."}`
  with the matching HTTP status code. See spec for the error_type ↔ code mapping.
- `price`, `average_price`, `trigger_price` are JSON **numbers**. `order_id`,
  `trade_id`, `exchange_order_id` are **strings**. `instrument_token` is a number.
- Match the documented response objects key-for-key. Do not rename or drop keys.

## Commands
- Run: `python -m fakekite --config config.yaml`
- Test: `pytest -q`
- Lint/format: `ruff check . && ruff format .`

## Layout
```
fakekite/
  pyproject.toml
  config.example.yaml
  docs/KITE_SPEC.md
  fakekite/
    __main__.py          # entrypoint: parse args, load config, run uvicorn
    config.py            # pydantic config models + loader/validator
    app.py               # ASGI app, wires REST + WS, owns shared state
    state.py             # in-memory orders/trades/positions/margins; reset()
    auth.py              # parse Authorization header / WS query params
    latency.py           # async delay helper (base + jitter); live overrides
    control.py           # /_control/* endpoints
    rest/
      session.py         # /session/token, /user/profile, /user/margins
      orders.py          # place/modify/cancel, GET /orders, /orders/{id}, /trades
      portfolio.py       # /portfolio/positions, /portfolio/holdings
      market.py          # /quote, /quote/ohlc, /quote/ltp, /instruments
      envelopes.py       # success/error envelope helpers + error injection
    ws/
      ticker.py          # WS endpoint: connect, subscribe/mode parse, push loop, heartbeat
      packets.py         # binary packer: ltp/quote/full, tradeable + index, depth
      order_updates.py   # order-update text frames + postback delay
    market/
      generator.py       # synthetic tick-size-aware random walk + OHLC + depth
      instruments.py     # token<->symbol master + CSV dump for /instruments
  tests/
    test_packets.py      # pack -> parse round-trip, byte-exact sizes
    test_orders.py       # place -> shows in GET /orders + emits WS update
    test_envelopes.py    # error envelope shapes + codes
```

## Gotchas
- Index instruments (`NIFTY 50`, `SENSEX`) use a **different, shorter** packet
  with **no market depth**. Don't pack depth for them. Mark them `is_index` in config.
- Tradeable packet sizes: ltp **8**, quote **44**, full **184** bytes.
  Index packet sizes: ltp **8**, quote **28**, full **32** bytes.
- Respect `tick_size` in the generator — prices must land on valid ticks (e.g. 0.05).
- `/instruments` returns a **CSV dump**, not JSON. Match the column order in the spec.
- Rate limits differ per endpoint (see spec). When tripped, return HTTP **429** with
  the error envelope so the terminal's backoff logic gets tested.
