# Kite Connect v3 — verified wire-format reference

Transcribed from the official docs on 2026-06-14. When in doubt, fetch the live
pages below and match them. **Do not invent offsets or keys.**

Official docs:
- Index: https://kite.trade/docs/connect/v3/
- Response structure: https://kite.trade/docs/connect/v3/response-structure/
- Exceptions/errors: https://kite.trade/docs/connect/v3/exceptions/
- User: https://kite.trade/docs/connect/v3/user/
- Orders: https://kite.trade/docs/connect/v3/orders/
- Portfolio: https://kite.trade/docs/connect/v3/portfolio/
- Market quotes & instruments: https://kite.trade/docs/connect/v3/market-quotes/
- WebSocket streaming: https://kite.trade/docs/connect/v3/websocket/
- Postbacks/WebHooks: https://kite.trade/docs/connect/v3/postbacks/

Production hosts (the terminal overrides these to point at us):
- REST: `https://api.kite.trade`
- WebSocket: `wss://ws.kite.trade`

Required REST headers from the client: `X-Kite-Version: 3` and
`Authorization: token api_key:access_token`.

---

## 1. Envelopes

Success:
```json
{ "status": "success", "data": <object or array> }
```

Error (with matching HTTP status code):
```json
{ "status": "error", "message": "Human readable message", "error_type": "GeneralException" }
```

### error_type ↔ HTTP code
| error_type        | typical code | meaning                                            |
| ----------------- | ------------ | -------------------------------------------------- |
| `TokenException`  | 403          | session expired/invalid; client must re-login      |
| `UserException`   | 400/403      | user-account related                               |
| `OrderException`  | 400          | order placement failure / corrupt fetch            |
| `InputException`  | 400          | missing/invalid parameters                         |
| `MarginException` | 400          | insufficient funds                                 |
| `HoldingException`| 400          | insufficient holdings for sell                     |
| `NetworkException`| 502/503/504  | API could not reach the OMS                        |
| `DataException`   | 500          | internal error parsing OMS response                |
| `GeneralException`| 500          | unclassified                                       |

### Common HTTP codes
`400` bad params · `403` session expired · `404` not found · `405` method not allowed ·
`410` gone · `429` rate limited · `500` server error · `502` OMS down · `503` unavailable ·
`504` gateway timeout.

### Rate limits (per API key)
| endpoint              | limit        |
| --------------------- | ------------ |
| Quote                 | 1 req/sec    |
| Historical candle     | 3 req/sec    |
| Order placement       | 10 req/sec   |
| All other endpoints   | 10 req/sec   |

Also: 200 orders/min, 10 orders/sec, 5000 orders/day, 25 modifications/order.
For the mock, the rate-limiting middleware should be **toggleable** and return HTTP
429 + error envelope when tripped (so the terminal's backoff can be tested), but
default OFF so it doesn't interfere with normal runs.

---

## 2. Order constants

| param              | values |
| ------------------ | ------ |
| `variety`          | `regular`, `amo`, `co`, `iceberg`, `auction` |
| `order_type`       | `MARKET`, `LIMIT`, `SL`, `SL-M` |
| `product`          | `CNC`, `NRML`, `MIS`, `MTF` |
| `validity`         | `DAY`, `IOC`, `TTL` |
| `transaction_type` | `BUY`, `SELL` |
| `exchange`         | `NSE`, `BSE`, `NFO`, `CDS`, `BCD`, `MCX` |

End states: `COMPLETE`, `OPEN`, `CANCELLED`, `REJECTED`.
Interim states (for order history): `PUT ORDER REQ RECEIVED`, `VALIDATION PENDING`,
`OPEN PENDING`, `MODIFY VALIDATION PENDING`, `MODIFY PENDING`, `TRIGGER PENDING`,
`CANCEL PENDING`, `AMO REQ RECEIVED`, `MODIFIED`.

---

## 3. Order endpoints

| method | path                         | purpose                          |
| ------ | ---------------------------- | -------------------------------- |
| POST   | `/orders/:variety`           | place order                      |
| PUT    | `/orders/:variety/:order_id` | modify open/pending order        |
| DELETE | `/orders/:variety/:order_id` | cancel open/pending order        |
| GET    | `/orders`                    | all orders for the day           |
| GET    | `/orders/:order_id`          | order history (list of states)   |
| GET    | `/trades`                    | all trades for the day           |
| GET    | `/orders/:order_id/trades`   | trades for one order             |

Place/modify/cancel all return:
```json
{ "status": "success", "data": { "order_id": "151220000000000" } }
```
`order_id` is a numeric **string**. Generate unique ids per placement.

### Order object (returned by GET /orders, each element)
```json
{
  "placed_by": "AB1234",
  "order_id": "100000000000000",
  "exchange_order_id": "200000000000000",
  "parent_order_id": null,
  "status": "COMPLETE",
  "status_message": null,
  "status_message_raw": null,
  "order_timestamp": "2026-06-14 09:18:57",
  "exchange_update_timestamp": "2026-06-14 09:18:58",
  "exchange_timestamp": "2026-06-14 09:15:38",
  "variety": "regular",
  "modified": false,
  "exchange": "NSE",
  "tradingsymbol": "INFY",
  "instrument_token": 408065,
  "order_type": "LIMIT",
  "transaction_type": "BUY",
  "validity": "DAY",
  "product": "CNC",
  "quantity": 1,
  "disclosed_quantity": 0,
  "price": 1500.0,
  "trigger_price": 0,
  "average_price": 1500.0,
  "filled_quantity": 1,
  "pending_quantity": 0,
  "cancelled_quantity": 0,
  "market_protection": 0,
  "meta": {},
  "tag": null,
  "guid": "xxxx"
}
```
Notes:
- For a `REJECTED` order, set `status_message`/`status_message_raw` (e.g. the
  margin-shortfall text), `average_price=0`, `filled_quantity=0`,
  `exchange_order_id=null`, `exchange_timestamp=null`.
- For `OPEN`, `filled_quantity=0`, `pending_quantity=quantity`, `average_price=0`.
- For `COMPLETE`, `filled_quantity=quantity`, `pending_quantity=0`,
  `average_price` = fill price.
- `GET /orders/:order_id` returns a **list** of the order's successive states
  (history), oldest first. For the mock, two or three states is enough
  (e.g. `PUT ORDER REQ RECEIVED` → `OPEN`/`COMPLETE`).

### Trade object (GET /trades, GET /orders/:id/trades)
```json
{
  "trade_id": "10000000",
  "order_id": "200000000000000",
  "exchange": "NSE",
  "tradingsymbol": "INFY",
  "instrument_token": 408065,
  "product": "CNC",
  "average_price": 1500.0,
  "quantity": 1,
  "exchange_order_id": "300000000000000",
  "transaction_type": "BUY",
  "fill_timestamp": "2026-06-14 09:16:39",
  "order_timestamp": "09:16:39",
  "exchange_timestamp": "2026-06-14 09:16:39"
}
```
Emit a trade only for orders that reach `COMPLETE`.

---

## 4. WebSocket streaming

Connect: `wss://ws.kite.trade?api_key=xxx&access_token=xxx` (we serve this on the
configured `ws_path`). The handshake query params carry auth.

Client → server requests are JSON text:
```json
{ "a": "subscribe",   "v": [408065, 884737] }
{ "a": "unsubscribe", "v": [408065] }
{ "a": "mode",        "v": ["full", [408065]] }
```
`a` ∈ {`subscribe`, `unsubscribe`, `mode`}. Modes: `ltp`, `quote`, `full`.
Default mode on subscribe (no explicit mode) follows Kite: treat as `quote`.

### 4a. Market-data frame (binary, big-endian)
```
[ int16 num_packets ]
  repeated num_packets times:
    [ int16 packet_length ][ packet_length bytes of packet ]
```

### 4b. Tradeable instrument packet
All fields `int32` big-endian unless noted. Prices in paise (value = rupees × 100);
for CDS/BCD currency instruments use rupees × 10,000,000.

| bytes    | field                                           |
| -------- | ----------------------------------------------- |
| 0–4      | instrument_token                                |
| 4–8      | last traded price        **(ltp ends → 8B)**    |
| 8–12     | last traded quantity                            |
| 12–16    | average traded price                            |
| 16–20    | volume traded for the day                       |
| 20–24    | total buy quantity                              |
| 24–28    | total sell quantity                             |
| 28–32    | open price of the day                           |
| 32–36    | high price of the day                           |
| 36–40    | low price of the day                            |
| 40–44    | close price            **(quote ends → 44B)**   |
| 44–48    | last traded timestamp (epoch seconds, int32)    |
| 48–52    | open interest                                   |
| 52–56    | open interest day high                          |
| 56–60    | open interest day low                           |
| 60–64    | exchange timestamp (epoch seconds, int32)       |
| 64–184   | market depth (10 entries × 12B) **(full → 184B)** |

### 4c. Index packet (NIFTY 50, SENSEX, etc. — no depth)
| bytes  | field                                          |
| ------ | ---------------------------------------------- |
| 0–4    | token                                          |
| 4–8    | last traded price       **(ltp ends → 8B)**    |
| 8–12   | high of the day                                |
| 12–16  | low of the day                                 |
| 16–20  | open of the day                                |
| 20–24  | close of the day                               |
| 24–28  | price change           **(quote ends → 28B)**  |
| 28–32  | exchange timestamp     **(full ends → 32B)**   |

### 4d. Market depth structure (full mode, tradeable only)
Bytes 64–184. Ten entries of 12 bytes each:
- `int32` quantity
- `int32` price (paise)
- `int16` orders
- 2 bytes padding (zero, skipped)

Entries 0–4 (bytes 64–124) = **bid** (buy) side.
Entries 5–9 (bytes 124–184) = **offer** (sell) side.

### 4e. Heartbeat
When idle, send a **single 1-byte** binary frame every ~2 s. Terminal ignores it.

### 4f. Postbacks / order updates (TEXT frames, JSON)
```json
{ "type": "order", "data": { <full order object, same shape as GET /orders element> } }
```
Other text types: `{"type":"error","data":"..."}`, `{"type":"message","data":"..."}`.
Emit a `type: order` frame whenever an order changes state. Apply the configured
`order_update_delay_ms` before sending so the terminal's async-update path is tested.

---

## 5. Market quotes (REST) — shapes to match
Fetch https://kite.trade/docs/connect/v3/market-quotes/ for exact JSON. Endpoints:
- `GET /quote?i=NSE:INFY` — full quote incl. depth.
- `GET /quote/ohlc?i=NSE:INFY` — LTP + OHLC.
- `GET /quote/ltp?i=NSE:INFY` — LTP only.
- `GET /instruments` and `GET /instruments/:exchange` — **CSV** dump. Columns
  (in order): `instrument_token, exchange_token, tradingsymbol, name, last_price,
  expiry, strike, tick_size, lot_size, instrument_type, segment, exchange`.

`i` accepts multiple values: `?i=NSE:INFY&i=NSE:SBIN`. Keys in the response are the
`exchange:tradingsymbol` strings.

## 6. User (REST) — shapes to match
Fetch https://kite.trade/docs/connect/v3/user/.
- `GET /user/profile` — user profile object.
- `GET /user/margins` and `GET /user/margins/:segment` — `equity` and `commodity`
  blocks with `enabled`, `net`, `available{...}`, `utilised{...}`.

## 7. Portfolio (REST) — shapes to match
Fetch https://kite.trade/docs/connect/v3/portfolio/.
- `GET /portfolio/positions` — `net` and `day` arrays.
- `GET /portfolio/holdings` — array of holdings.

For the mock these can be derived from the in-memory order/trade table or served
from a small seeded fixture; either is fine as long as the shapes match.
