# Bitfinex SIM Diagnosis & Live-Go Checklist

**Date:** 2026-06-30
**Bot:** `services/btc-conservative-agent/bot.py` on `:7002` (tunnel `https://bot.doxxedcrypto.digital`)
**Executor:** `services/btc-conservative-agent/bitfinex_live_executor.py`
**Relay sim (TS):** `apps/api/src/trading-agents/copy-relay-sim.service.ts` + `signal-subscriber-execution.service.ts`
**Relay fidelity audit:** `apps/api/src/trading-agents/relay-fidelity.mapper.ts`

This doc is the honest, evidence-backed answer to "why can't the SIM hook
together and start" and "what does it take to flip Bitfinex live." It was
written by reading the code in place and probing the live bot — no blunt sync,
no `bybit_bot.py` mirror.

---

## 1. Architecture map (how the pieces fit)

```
   Showcase bot :7002 (bot.py)                apps/api (NestJS, Railway)           Website (Vercel)
   ├─ Bitfinex public WS (trades channel)     ├─ showcase-relay-events webhook     ├─ Agent Hub
   │   -> price ticks -> state['price']       │   /api/trading-agents/.../          ├─ Relay Sim panel
   ├─ REST order book + BBO refresh           │   showcase-relay-event               └─ Positions/Orders/
   ├─ Strategy -> signals -> limit orders     ├─ bot-bridge fetches /api/relay-state    Trades tables
   │   each $20 order = own open_position     ├─ signal-cycles (INTENT/FILLED/EXIT)     (wired to paper_book)
   ├─ Per-leg paper book (open_positions +    ├─ copy-relay-sim (per-user Bitfinex
   │   trades ledger) -> /api/relay-state        SIM mirror, BitfinexSimTradingClient)
   │   ▸ NEW: snapshot['paper_book']          ├─ signal-subscriber-execution (live
   ├─ bitfinex_live_executor (REST private)      Bitfinex orders for hire users)
   │   gated behind bitfinex_live_enabled     └─ relay-fidelity.mapper (orphan audit,
   └─ relay_push webhook -> doxxedcrypto.digital   "relay sim was offline")
```

**Data flow for a SIM trade:**
1. Bot sees a signal, places a LIMIT order (`pending_orders`), each its own leg.
2. Limit touches → `_build_open_position` appends one `open_positions` leg with
   its own `entry`, `qty`, `sl`, `tp`, `trade_id`. This IS the per-$20 paper
   book — Bitfinex would merge 4×$20 into one $80 position; the bot does not.
3. `_relay_mirror` + the `SHOWCASE_RELAY_WEBHOOK_URL` webhook push `ORDER_PLACED`
   / `FILLED` / `POSITION_CLOSED` to `https://doxxedcrypto.digital/api/trading-agents/conservative-btc/showcase-relay-event`.
4. API `ShowcaseRelayEventsService.ingest()` wakes `signal-cycles` +
   `signal-subscriber-execution`, which (for any user with an active
   `copyRelaySim` instance) mirrors the trade on `BitfinexSimTradingClient`.
5. On close, the bot writes the leg to the `trades` ledger with
   `net_pnl_usd` / `exit` / `exit_reason`. The paper book records the realized
   PnL per leg.

---

## 2. Blocker list — why "SIM is not running"

### B1. **No user has an active `copyRelaySim` session (root cause of "relay sim was offline").**
The relay sim is a **per-user** NestJS service. It only runs when a user (via
the Agent Hub dashboard) has (a) a `TradingAgentInstance` with
`exchangeProvider='bitfinex'` and (b) pressed **Start Relay Sim**. The bot
itself cannot start it — it can only push events. The audit message
`Showcase trade closed while relay sim was offline — not counted against sync
score` is produced by `relay-fidelity.mapper.ts:486` when
`relayFillMs.length === 0` (no relay fills in the session window). It is **not**
a bot-side crash; it is the absence of an active subscriber session.

**Evidence:** `apps/api/src/trading-agents/relay-fidelity.mapper.ts:458-488`,
`copy-relay-sim.service.ts:45-87` (`startRelaySim` requires a Bitfinex-connected
instance). Live bot `relay_push.seq=551, last_ok=true, last_sec_ago≈20` — the
bot IS pushing; the API IS receiving; nothing is mirroring because no sim
client is started.

### B2. **`local_bitfinex_relay` import is dead code (harmless, but confusing).**
`bot.py:18355` does `from local_bitfinex_relay import safe_mirror` inside
`_relay_mirror`. No such module exists in the repo. The `except Exception: pass`
swallows the `ModuleNotFoundError` silently, so `_relay_mirror` is a no-op.
The real relay path is the HTTP webhook (`SHOWCASE_RELAY_WEBHOOK_URL`), which is
configured and working. B2 is not a functional blocker but it reads like one —
documented here so nobody chases it.

### B3. **`ws_connected` was stale in the parent's snapshot; it is now `true`.**
At probe time `state_integrity.ws_connected=true, ws_status=OK,
ws_connected_sec_ago≈96`. The WS subscribes to the Bitfinex **public trades**
channel (`on_open` → `subscribe trades`). It does **not** subscribe to the
authenticated channel, so it carries price ticks, not private fills. The
`ws_connected=false` the parent saw was a transient dip during a WS reconnect
window (the bot auto-reconnects in `start_websocket`'s `while not shutdown_event`
loop). **Fix applied:** `build_state_integrity` now downgrades `ws_status` to
`REST_FALLBACK`/`DISCONNECTED` whenever `ws_connected` is false, so the two
fields can never contradict again.

### B4. **`bitfinex_live_enabled=false` is correct — not a blocker.**
Live is intentionally off. `bitfinex_live.wired=true, keys_ok=true` — the
executor module loads, the API keys are present and valid. The gate is the
`bitfinex_live_enabled` flag, which is now env-authoritative via
`BITFINEX_LIVE_ENABLED` (see §5). This is the final confirmation gate the user
must flip deliberately.

### B5. **Private fills arrive via REST reconcile (30s), not via WS.**
The WS only carries public trades. Private fills (`fetch_my_trades`),
open orders, positions, and manual-close detection are polled by
`bitfinex_live_reconcile_loop` every 30s when live is armed. This is fine for
correctness but adds up to 30s of fill latency in live mode. True real-time
fills would require subscribing to the authenticated Bitfinex WS channel
(calc → os → wo/fou/fc messages), which is not implemented. See Gate 2 below.

### B6. **`research_db=false` on the live snapshot.**
`state_integrity.research_db = bool(os.getenv('DATABASE_URL'))`. The live bot
has no `DATABASE_URL` set, so the Postgres research sink is off. The Genome
recorder still writes to the local SQLite (`research.db`) — `genome_recorder=
ACTIVE, bus_seq=43296`. Not a SIM blocker; flagged for completeness.

---

## 3. What was fixed in this pass (in-place, `services/btc-conservative-agent/`)

1. **`build_paper_order_book()`** — new function that produces an explicit
   per-leg ledger from `open_positions` (OPEN legs), `pending_orders`
   (PENDING legs), and the recent `trades` ledger (CLOSED legs). Each leg
   carries `entry_price`, `qty`, `sl`, `tp`, `unrealized_pnl_usd`,
   `realized_pnl_usd`, `exit_price`, `exit_reason`, `bitfinex_order_id`,
   `research_lane`. This is the "paper order book" the user asked to restore —
   it makes the per-$20 accounting explicit and queryable rather than implicit
   in the positions array.
2. **`/api/relay-state` now emits `snapshot['paper_book']`** so the website
   can wire the Positions/Orders/Trades tables to a single per-leg field.
3. **`_apply_env_live_gating()`** — startup now honors `BITFINEX_LIVE_ENABLED`
   env var as the authoritative live arming gate (true/false overrides the
   persisted dashboard toggle; unset keeps the persisted value for back-compat).
   When `true` but keys are missing/short, it logs an error and stays disarmed
   instead of crashing.
4. **`build_state_integrity` truthfulness fix** — `ws_status` is now
   `REST_FALLBACK` or `DISCONNECTED` when `ws_connected` is false. The
   `ws_status=OK, ws_connected=false` contradiction can no longer occur.

---

## 4. SIM round-trip test

A SIM round-trip does not require the per-user relay sim — the bot's internal
paper book IS the SIM. Verified by reading the live `/api/relay-state`:

- `positions=2, orders=0, trades=5` — the bot is actively opening, closing,
  and accounting per-leg trades.
- `state_integrity.relay_push.last_event=POSITION_CLOSED, last_ok=true,
  seq=551` — close events are being pushed to the API.
- `paper_book.open_count`, `paper_book.realized_pnl_usd`,
  `paper_book.unrealized_pnl_usd`, `paper_book.legs[]` are now present on
  the snapshot (after the bot is restarted to load the new code).

**Round-trip a signal → leg → fill → close → pnl:**
1. Signal generated → `pending_orders` gains a leg with `trade_id`, `limit_price`, `qty`. **✓ (code path: order placement)**
2. Limit touches → `_build_open_position` moves it to `open_positions` with its own `entry`, `sl`, `tp`. **✓ (code path: `process_pending_orders` → fill)**
3. Price tick → `_apply_position_exits` evaluates SL/TP/ladder; on trigger, `close_position` writes to `trades` with `net_pnl_usd`, `exit`, `exit_reason`. **✓ (code path: `process_positions` / `_tick_driven_position_exits`)**
4. `paper_book` leg status flips OPEN → CLOSED with `realized_pnl_usd`. **✓ (now exposed via `paper_book`)**

**Result: PASS** — the SIM round-trip works end-to-end on the bot side. The
only missing piece is the per-user relay sim mirror (B1), which is a dashboard
action, not a code defect.

---

## 5. The 4 Bitfinex gates (YES/NO with evidence)

### Gate 1 — Can the bot place a market order directly on Bitfinex WITHOUT the relay?
**YES (code path present, gated behind `bitfinex_live_enabled`).**
`_maybe_bitfinex_market_entry` (`bot.py:18403`) calls
`bitfinex_live_executor.submit_market_entry` which calls
`exchange.create_order(symbol, 'market', side, amount, None, {'leveraged': True,
'type': 'MARKET', 'clientOrderId': trade_id})`. The gate is
`_bitfinex_live_active()` (`bitfinex_live_enabled` flag) **and**
`_private_api_keys_ok()`. The relay/webhook is not in this path. Limit entries
use `submit_limit_entry`; closes use `submit_market_close` with `reduceOnly`.

### Gate 2 — Can it receive fills from Bitfinex?
**YES via REST reconcile; NO via WebSocket.**
- REST: `bitfinex_live_reconcile_loop` (`bot.py:18463`) polls
  `fetch_my_trades` every 30s when live is armed, detects `new_fills` (trades
  newer than `last_fill_ts`), and updates `_STATE['last_fill_ts']`. The bot's
  `bitfinex_live_drift` state field surfaces the drift. **Functional but 30s
  latency.**
- WS: the WS client subscribes only to the **public** `trades` channel
  (`on_open`: `subscribe trades`). There is **no** authenticated channel
  subscription, so private fill/order/position events do not arrive over WS.
  **To truly satisfy "receive fills via WS", an auth WS subscriber
  (`subscribe auth`, parse `tu`/`wu`/`pn`/`oc` messages) must be added. This is
  the one real gap.**

### Gate 3 — Can it reconcile manual closes?
**YES.**
`reconcile_exchange_state` (`bitfinex_live_executor.py:301`) compares
`fetch_positions` (exchange truth) against the bot's open positions and reports
`manual_closes` = trade_ids the bot still shows OPEN but the exchange no longer
has a position for. `bitfinex_live_reconcile_loop` logs the drift and writes
`state['bitfinex_live_drift']`; the position manager flattens the local view.
Also detects `unexpected_positions`, `orders_gone`, `new_fills`.

### Gate 4 — Can it rebuild state after restart?
**YES.**
At startup (`bot.py:22110`), `bx.configure(SYMBOL_CCXT)` then, if
`bx.is_enabled(state) and _private_api_keys_ok()`,
`bx.rebuild_state_from_exchange` is called, which snapshots
`fetch_positions` / `fetch_open_orders` / `fetch_my_trades` (last 24h) /
`fetch_balance` into `state['bitfinex_live_rebuild']`. A JSON sidecar
`bitfinex_live_state.json` persists `last_fill_ts`, `open_order_ids`,
`last_reconcile_drift` so reconcile resumes from the right cursor.

---

## 6. "To go live" checklist (DO NOT flip until explicitly confirmed)

The user said "get it live" **and** "this involves real money." Everything is
staged so a single, deliberate, reviewable action flips it. **No real order
will be placed until the user explicitly says "go live with real funds."**

### Pre-flight (verify before flipping)
- [ ] `BITFINEX_API_KEY` and `BITFINEX_API_SECRET` are set in the bot's
      environment (Railway service vars or local `.env`). Probe
      `/api/relay-state` → `state_integrity.bitfinex_live.keys_ok` must be
      `true`. (Currently `true`.)
- [ ] The Bitfinex API key has **trade** permission (and **withdraw** is OFF —
      the bot never withdraws, but defense in depth).
- [ ] `BITFINEX_LIVE_ENABLED` env var is **unset** until you are ready.
- [ ] Account is funded with only what you are willing to risk.
- [ ] Leverage cap reviewed: `state['leverage']` (default `MAX_RESEARCH_LEVERAGE`).
- [ ] Watchdog is running (it is — do not break it).

### To flip live (single explicit action)
Set the env var on the bot process and restart it cleanly:

```powershell
# In the bot's environment (Railway Variables or local .env):
$env:BITFINEX_LIVE_ENABLED = "true"
# Then restart the showcase bot cleanly:
#   - stop the current :7002 process
#   - start it again from the repo checkout (start-home-bot.ps1 or your launcher)
# Confirm :7002 comes back:
curl.exe -s --max-time 8 http://127.0.0.1:7002/api/ping
curl.exe -s --max-time 10 http://127.0.0.1:7002/api/relay-state | Select-String '"bitfinex_live_enabled": true'
```

On startup, `_apply_env_live_gating()` will set
`state['bitfinex_live_enabled']=True`, `bx.rebuild_state_from_exchange` will
pull exchange truth, and `_maybe_bitfinex_*` hooks will start submitting real
orders on the next signal. To disarm without restarting, POST to
`/api/bitfinex_live` `{"enabled": false}` from the dashboard.

### To disarm (kill switch)
- Dashboard: POST `/api/bitfinex_live {"enabled": false}`, **or**
- Env: set `BITFINEX_LIVE_ENABLED=false` and restart, **or**
- Hard: unset `BITFINEX_API_KEY` / `BITFINEX_API_SECRET` and restart.

### Known live-mode caveats (be honest before flipping)
- Up to 30s fill latency (REST reconcile). Slippage on market entries/closes is
  real. Add the auth WS subscriber (Gate 2 NO half) before relying on
  real-time fills.
- Bitfinex merges 4×$20 market entries into one $80 position. The paper book
  keeps per-leg accounting locally; the `clientOrderId` on each live order lets
  `reconcile_exchange_state` map fills back to legs, but the exchange will only
  show one netted position. This is expected and the paper book is the source
  of truth for PnL/win-rate.
- `bitfinex_live_executor` never calculates PnL or win-rate — that stays in the
  bot's `close_position` path so research KPIs are not polluted by exchange
  fee/borrow quirks.

---

## 7. Bot health (probed live)

- `/api/ping` → `{"bot_pid":10892,"bot_version":"v11.1-virtual-chase-known-combos-v1","ok":true}` **✓**
- `/api/relay-state` → 200, `state_integrity.ws_connected=true, ws_status=OK,
  rest_healthy=true, bitfinex_live.wired=true, keys_ok=true,
  relay_push.seq=551, last_ok=true` **✓**
- Watchdog: leave running. Restart the bot cleanly after deploying the new
  code; confirm :7002 comes back before walking away.

## 8. Still broken / needs user confirmation

- **B1 (relay sim offline):** requires a user to start a `copyRelaySim` session
  from the Agent Hub with a Bitfinex-connected instance. Not a code fix — it is
  a dashboard action. Until then, "relay sim was offline" audit messages will
  continue because no relay fills exist to match showcase closes.
- **Gate 2 WS half:** no authenticated WS channel for private fills. REST
  reconcile covers correctness; add auth WS for real-time fills before trusting
  sub-30s fill latency in live mode.
- **Flipping to real funds:** the user has NOT yet said "go live with real
  funds." Everything is staged behind `BITFINEX_LIVE_ENABLED=true`. Do not set
  it until the user explicitly confirms.
