# FakeKite

A local **test double** of Zerodha's Kite Connect v3 API (REST + WebSocket).
Point your trading terminal at `localhost` instead of the real broker to exercise
its broker-integration code end to end. Wire-format faithful and deterministic;
**not** a matching engine — placed orders land in a configured terminal state.

See `KITE_SPEC.md` for the verified wire format and `CLAUDE.md` for design rules.

## Requirements

Python **3.11+**. The dependencies are `fastapi`, `uvicorn[standard]`,
`pydantic>=2`, `pyyaml` (plus `pytest`, `httpx`, `ruff` for development).

```bash
# with uv (recommended)
uv venv --python 3.11 .venv
uv pip install -e '.[dev]'

# or with pip
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
```

## Run

```bash
cp config.example.yaml config.yaml      # edit to taste
python -m fakekite --config config.yaml
# serves REST + WS on http://127.0.0.1:8000  (WS at ws://127.0.0.1:8000/ws)
```

Point the terminal's **REST base URL** at `http://127.0.0.1:8000` and its
**WebSocket URL** at `ws://127.0.0.1:8000/ws` (both are independent settings).
With the default `auth.mode: accept_any`, any `api_key`/`access_token` is accepted.

## REST: pointing a client at localhost (curl)

Every REST call carries `Authorization: token <api_key>:<access_token>`.

```bash
AUTH='Authorization: token mykey:mytoken'

# place an order -> {"status":"success","data":{"order_id":"150000000000001"}}
curl -s -X POST http://127.0.0.1:8000/orders/regular -H "$AUTH" \
  -d tradingsymbol=INFY -d exchange=NSE -d transaction_type=BUY \
  -d order_type=LIMIT -d product=CNC -d quantity=1 -d price=1500

curl -s http://127.0.0.1:8000/orders            -H "$AUTH"   # orderbook
curl -s http://127.0.0.1:8000/trades            -H "$AUTH"   # trades
curl -s "http://127.0.0.1:8000/quote?i=NSE:INFY" -H "$AUTH"  # full quote + depth
curl -s "http://127.0.0.1:8000/quote/ltp?i=NSE:INFY&i=NSE:SBIN" -H "$AUTH"
curl -s http://127.0.0.1:8000/user/margins      -H "$AUTH"
curl -s http://127.0.0.1:8000/portfolio/positions -H "$AUTH"
curl -s http://127.0.0.1:8000/instruments       -H "$AUTH"   # CSV dump
```

## WebSocket: subscribe and parse one full-mode packet (Python)

```python
import asyncio, json, struct, websockets

async def main():
    uri = "ws://127.0.0.1:8000/ws?api_key=k&access_token=t"
    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({"a": "subscribe", "v": [408065]}))   # INFY
        await ws.send(json.dumps({"a": "mode", "v": ["full", [408065]]}))
        while True:
            frame = await ws.recv()
            if not isinstance(frame, bytes) or len(frame) <= 1:
                continue                                   # skip 1-byte heartbeats
            n = struct.unpack(">h", frame[:2])[0]          # packet count
            plen = struct.unpack(">h", frame[2:4])[0]      # first packet length
            pkt = frame[4:4 + plen]
            token, ltp = struct.unpack(">ii", pkt[:8])     # prices are in paise
            print(f"packets={n} len={plen} token={token} ltp={ltp / 100}")
            if plen == 184:                                # tradeable full: 5+5 depth
                qty, price, orders = struct.unpack(">IIH", pkt[64:74])
                print(f"  best bid: qty={qty} price={price / 100} orders={orders}")
            break

asyncio.run(main())
```

Packet sizes: tradeable `ltp 8 / quote 44 / full 184`, index `ltp 8 / quote 28 /
full 32`. Currency-segment (CDS/BCD) prices use ×10,000,000 instead of ×100.
Market data is **binary**; order updates / postbacks are **text** JSON frames
(`{"type":"order","data":{...}}`).

## Control plane (force deterministic outcomes at runtime, no restart)

```bash
curl -X POST http://127.0.0.1:8000/_control/reset                                  # clear state
curl -X POST http://127.0.0.1:8000/_control/latency      -d '{"rest_ms":250}'      # live latency
curl -X POST http://127.0.0.1:8000/_control/order_state   -d '{"state":"OPEN"}'    # default state
curl -X POST http://127.0.0.1:8000/_control/force_reject  -d '{"count":3}'         # next 3 -> REJECTED
curl -X POST http://127.0.0.1:8000/_control/disconnect    -d '{"api_key":"alice"}' # drop socket(s)
curl -X POST http://127.0.0.1:8000/_control/error \
  -d '{"endpoint":"orders.place","error_type":"NetworkException","count":1}'       # inject an error
```

Latency knobs (`config.yaml` or `/_control/latency`): `rest_ms`, `rest_jitter_ms`,
`ws_tick_interval_ms`, `ws_push_delay_ms`, `order_update_delay_ms`,
`heartbeat_interval_ms`. Rate limiting is off by default; enable
`rate_limit.enabled` to make tripped endpoints return HTTP 429.

## Tests / lint

```bash
pytest -q
ruff check . && ruff format .
```
