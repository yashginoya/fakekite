# Changelog

All notable work on **FakeKite** (a local mock of Zerodha's Kite Connect v3 API),
organized by the milestones from the original build plan. This is a build log for
future sessions — it records *what exists and why*, not just version bumps.

Status as of 2026-06-15: **all 4 milestones complete**, 61 pytest tests green,
`ruff check`/`format` clean. Run/test through the Python 3.14 venv
(`.venv/bin/...`) — the system `python3` is 3.10 and too old (project needs 3.11+).

Source of truth for the wire format: `KITE_SPEC.md`. Design rules: `CLAUDE.md`.

---

## Milestone 0 — Scaffold

Package skeleton, config system, and a boot path.

**Added**
- `pyproject.toml` — deps (`fastapi`, `uvicorn[standard]`, `pydantic>=2`, `pyyaml`)
  + `dev` extra (`pytest`, `httpx`, `ruff`); ruff config (ignores `B008` for the
  FastAPI `Depends()` idiom) and pytest config.
- `fakekite/__main__.py` — `python -m fakekite --config config.yaml` boots uvicorn;
  prints a clean `error:` + exit code 2 on bad config instead of a traceback.
- `fakekite/config.py` — pydantic v2 models for **all** of `config.example.yaml`
  (`server`, `auth`, `orders`, `latency`, `rate_limit`, `market.instruments[]`).
  `extra="forbid"` catches typos; readable `ConfigError`; cross-field checks
  (fixed-auth needs key+token, unique tokens/symbols, segment defaults to exchange).
- `fakekite/state.py` — `AppState` dataclass: one in-memory order table + history
  + trades, `reset()`, Kite-shaped numeric-string id generators. No threads/locks.
- `fakekite/app.py` — `create_app(config)` holding `AppState` on `app.state.fk`;
  health route `GET /` → 200.
- `tests/test_config.py` — config load/validation + health route.

**Notes**
- System `python3` is 3.10 (below the 3.11 floor). Built a 3.14 venv via `uv`;
  user confirmed this is fine. `requires-python = ">=3.11"`.
- `KITE_SPEC.md` lives at repo root (not `docs/`); left in place.

---

## Milestone 1 — Walking skeleton

End-to-end for one instrument: log in, place an order, see it, receive a tick.

**Added**
- `fakekite/auth.py` — `authenticate` REST dependency parsing
  `Authorization: token api_key:access_token`; `check_ws_auth` for WS query params.
  `accept_any` (default) takes anything well-formed; `fixed` checks the pair.
  Malformed/missing → `TokenException` 403.
- `fakekite/rest/envelopes.py` — success/error envelope helpers + `KiteError`
  (carries `error_type` → HTTP status from the spec table) + its exception handler.
- `fakekite/rest/orders.py` — `POST /orders/:variety` (validates required params +
  enums + price/trigger rules, builds the **full** order object per spec §3 into
  `orders.default_state`, records order + history + trade in the one table, fires
  the WS update, returns `{"data":{"order_id":"..."}}`); `GET /orders`. Form body
  parsed with stdlib `parse_qsl` — **no python-multipart dependency added**.
- `fakekite/ws/ticker.py` — WS endpoint at the configured `ws_path`; query-param
  auth; reader applies `subscribe`/`unsubscribe`/`mode`; pusher streams ticks at
  `ws_tick_interval_ms` and a 1-byte heartbeat when idle. Two asyncio tasks per
  connection, cleaned up on disconnect.
- `fakekite/ws/packets.py` — 8-byte LTP packet + frame envelope (big-endian).
- `fakekite/ws/order_updates.py` — `{"type":"order","data":...}` **text** broadcast
  after `order_update_delay_ms`.
- `fakekite/market/instruments.py` — token↔symbol master + `price_scale()`.
- `fakekite/market/generator.py` — seeded, tick-aligned, bounded random walk (LTP).
- `fakekite/latency.py` — `sleep_ms` / `rest_delay` (always `asyncio.sleep`).
- `tests/test_orders.py` — place → in GET /orders; WS ltp frame parseable; order
  update text frame on placement.

**Acceptance met:** token accepted → order placed → visible in orderbook → tick
received over the socket, localhost only.

---

## Milestone 2 — Full binary market data

All packet modes/shapes, exact byte sizes, full generator with depth.

**Added / changed**
- `fakekite/ws/packets.py` — complete packers, big-endian, exact sizes:
  - Tradeable: `ltp 8` / `quote 44` (11×int32) / `full 184` (quote + 5×int32
    OI/timestamps + 120-byte depth).
  - Index: `ltp 8` / `quote 28` / `full 32` — **no depth**.
  - Depth: 5 bid + 5 offer entries, each `int32 qty + int32 price + int16 orders +
    2 pad` (12B × 10). Integer fields unsigned (`>I`/`>H`) to match Kite's wire;
    index "price change" is the lone signed field (`>i`).
  - Prices scaled paise (×100) or currency (×1e7) via the caller's `scale`.
- `fakekite/market/generator.py` — full per-instrument state: LTP, day OHLC,
  accumulating volume, last-traded qty, avg price, buy/sell totals, OI (+day hi/lo),
  epoch timestamps, synthetic 5+5 depth (bids below LTP descending, offers above
  ascending). Index instruments carry no depth. `snapshot()` reads state without
  advancing the walk.
- `fakekite/ws/ticker.py` — frame builder multiplexes every subscribed
  `(token, mode)` into one frame via `pack_packet`. **Subscribe default mode set to
  `quote`** per spec (M1 had temporarily defaulted to `ltp`).
- `tests/test_packets.py` — pack→unpack round-trip for every mode/both shapes; exact
  byte lengths; paise + currency ×1e7 scaling; full-tradeable depth ordering +
  padding; index-full-has-no-depth; negative index change; multi-packet frame.

---

## Milestone 3 — Remaining REST + errors

The rest of the Kite surface plus an error-injection mechanism.

**Added**
- `fakekite/rest/orders.py` (extended) — `PUT` modify (quantity/price/trigger/type/
  validity; sets `modified`, appends `MODIFIED` history), `DELETE` cancel
  (→ `CANCELLED`, moves pending→cancelled), `GET /orders/:order_id` history (list of
  state snapshots, honors `emit_order_history`), `GET /trades`,
  `GET /orders/:order_id/trades`. Modify/cancel of a terminal-state order →
  `OrderException`; unknown order → `InputException`.
- `fakekite/rest/market.py` — `/quote` (full incl. deterministic 5+5 depth; index
  gets empty depth), `/quote/ohlc`, `/quote/ltp` (all keyed by
  `exchange:tradingsymbol`, multi-`i`, unknown instruments omitted);
  `/instruments[/:exchange]` CSV with the exact documented column order. REST reads
  use the non-advancing generator `snapshot` so they don't perturb the WS walk.
- `fakekite/rest/session.py` — `/user/profile`; `/user/margins` (equity+commodity)
  and `/user/margins/:segment` (single block as `data`). Shapes from the live docs.
- `fakekite/rest/portfolio.py` — `/portfolio/positions` derived from COMPLETE orders
  (net+day, full field set, Kite's `value = sell − buy`, pnl formula);
  `/portfolio/holdings` seeded per cash-segment equity instrument.
- `fakekite/rest/envelopes.py` (extended) — `ErrorInjection` dataclass +
  `maybe_inject(state, endpoint)` wired into every route under a stable key
  (`orders.place`, `orders.list`, `quote`, `user.profile`, …).
- `fakekite/rest/ratelimit.py` — optional `RateLimitMiddleware`, **default off**;
  per-second per-(api_key, endpoint-class) limits → 429 + envelope when enabled.
- `tests/test_envelopes.py` — parametrized envelope shape/code for all 9 error types,
  injection count/override, modify/cancel, history, trades, all quote shapes,
  index-no-depth, CSV column order + exchange filter, margins, derived positions,
  rate-limit on/off.

**Decisions flagged**
- 429 has no documented `error_type` in the spec; the limiter uses `NetworkException`
  (closest documented type) rather than inventing one.
- Positions are **derived** from COMPLETE orders; holdings are a **seeded fixture**
  (the spec explicitly allows either).

**Fidelity fix (made here)**
- WS pusher now wakes immediately on a subscription change (via an `asyncio.Event`)
  instead of waiting out the heartbeat interval — previously a subscribe arriving
  mid-idle-sleep lagged up to one heartbeat before data flowed.

---

## Milestone 4 — Control plane + latency

Force deterministic outcomes at runtime; wire latency everywhere.

**Added / changed**
- `fakekite/control.py` — `/_control/*` test backdoor (no Kite auth, exempt from the
  rate limiter):
  - `POST /_control/reset` — clear orders/trades/history/injections, re-seed market.
  - `POST /_control/latency` — live-update any latency field (validated).
  - `POST /_control/disconnect` — drop all WS sockets, or one `api_key`'s (code 1000).
  - `POST /_control/force_reject` — next N placed orders land REJECTED.
  - `POST /_control/order_state` — change `orders.default_state` at runtime.
  - `POST /_control/error` — set/clear a forced error for any endpoint (drives the
    M3 injection mechanism over HTTP).
- `fakekite/latency.py` (extended) — `rest_latency_dependency`, applied as a
  router-level dependency before every Kite REST handler. WS push/tick/order-update
  paths already read `config.latency` live.
- `fakekite/config.py` — `validate_assignment=True` so runtime mutations (latency,
  default_state) are validated like load-time.
- `fakekite/ws/ticker.py` — connection stores `api_key` so `/_control/disconnect` can
  target one client.
- `fakekite/rest/ratelimit.py` — bypasses `/` and `/_control/*`.
- `tests/test_control.py` — reset clears state; latency override takes effect without
  restart; force_reject flips next order to REJECTED (+ reflected in the WS update);
  order_state runtime change; error injection set/consume/clear via the endpoint;
  control plane not rate-limited.

**Deliverables**
- `README.md` — run command, curl REST examples, and a ~15-line WS full-mode parse
  snippet. All verified copy-paste-correct against a live uvicorn server.

**Testing caveat**
- The disconnect unit test exercises the endpoint against stand-in connection objects.
  Driving a real WebSocket close through Starlette's `TestClient` while making an HTTP
  control call on the same client deadlocks its portal (a harness limitation, not a
  server bug). The genuine end-to-end socket drop (server close code 1000) is verified
  live, not in the unit suite.

---

## Conventions for future sessions

- **Run/test:** `.venv/bin/python -m fakekite --config config.yaml`,
  `.venv/bin/pytest -q`, `.venv/bin/ruff check . && .venv/bin/ruff format .`.
- **Never invent** wire field names or byte offsets — consult `KITE_SPEC.md`, and for
  anything not covered fetch the official docs (URLs listed in the spec).
- Binary = market data; text = order updates/postbacks/errors. Big-endian, paise
  (×100), currency ×1e7. All artificial delays via `await asyncio.sleep`.
- One in-memory order table feeds every read and the WS order-update — keep it
  consistent.
- Ask before adding a dependency beyond the four, changing the on-wire format, or
  introducing threads/processes.
