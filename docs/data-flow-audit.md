# Data Flow Audit — Showcase Bot as Single Source of Truth

**Date:** 2026-06-29
**Author:** Cursor recovery pass
**North star:** `Bitfinex → Showcase Bot → /api/relay-state → Relay → Website / DB / Agent Hub / Genome / Analyzer`
No duplicate calculations anywhere. The bot already knows; everyone reads it.

## 1. Current topology

```
                       ┌───────────────────────────────┐
  Bitfinex WS+REST ──▶ │  services/btc-conservative-   │
  (tBTCF0:USTF0)       │  agent/bot.py  (:7002)        │
                       │  - signal engine              │
                       │  - sim fills / PnL / equity   │
                       │  - AI history                 │
                       │  - positions / orders / trades│
                       │  - genome bridge              │
                       │  - bitfinex_live_executor     │
                       └───────────────┬───────────────┘
                              │ /api/state (108KB, full)
                              │ /api/relay-state (48KB, subset)  ← Agent Hub / Relay
                              │ /api/sync_status /api/fresh_start
                              │ /api/bitfinex_live (toggle)
                       ┌──────▼──────────┐
                       │  Cloudflare     │  https://bot.doxxedcrypto.digital
                       │  named tunnel   │
                       └──────┬──────────┘
                              │
            ┌─────────────────┼─────────────────────────────┐
            ▼                 ▼                              ▼
   apps/api (NestJS)   apps/web (Next.js)          research/research_dashboard.py
   Railway/Neon        Vercel                       :9500  (home dashboard)
   - fetchTradingAgent - /agent-hub/[slug]          - reads bot CSVs/JSON
     Dashboard         - Start button → bridge       - genome outcomes
   - relay webhooks      :7810/cmd/start-all          (research/genome/)
   - copy relay sim
```

## 2. Where each metric is computed TODAY (and where it SHOULD be)

| Metric | Where computed today | Duplicate? | Canonical source |
|--------|----------------------|-----------|------------------|
| `price` | bot.py (WS + REST fallback) | No | bot snapshot |
| `equity` / `account_balance` | bot.py sim ledger | **YES** — NestJS also derives from DB trades | bot snapshot only |
| `sessionPnL` / `daily_pnl_usd` | bot.py | **YES** — Agent Hub falls back to DB sum | bot snapshot only |
| `tradeCount` | bot.py `trades` list | **YES** — DB `Trade` table count drifts | bot snapshot only |
| `winRate` | bot.py | **YES** — website computes from DB | bot snapshot only |
| `positions` | bot.py `open_positions` | No (live mirror via reconcile) | bot snapshot |
| `pending orders` | bot.py `pending_orders` | No | bot snapshot |
| `ai_history` / `last_ai` | bot.py | No | bot snapshot |
| `genome outcomes` | `research/genome/` files via `genome_bridge` | No (independent of trade source) | genome bridge stats |
| `last N hours` view | `:9500` research dashboard reads bot CSVs/JSON | Potential drift — see §4 | should call bot `/api/state` |

## 3. Duplicate calculations found (to collapse)

1. **Agent Hub PnL/Equity from DB** — `apps/api` `fetchTradingAgentDashboard` builds equity/trades/PnL
   from the Neon `Trade` table when the bot is unreachable. This is the source of the
   "511 / 0 / 0 / Offline" stale read: the DB has stale rows and the bot snapshot isn't
   being fetched (tunnel flap / NestJS timeout).
   **Fix:** prefer bot `/api/relay-state` for all live numbers; only use DB as a
   last-resort fallback and label it "cached".

2. **Website win-rate** — `apps/web` computes win rate from `recentTrades` array instead of
   reading `dashboard.winRate` from the bot. Collapse onto the snapshot field.

3. **`:9500` dashboard** reads bot-written JSON/CSV files directly. If those files lag the
   live in-memory snapshot, the dashboard shows stale genome/PnL. **Fix:** `:9500` should
   proxy `/api/state` from `:7002` for live counters and only use files for genome history.

## 4. Agent Hub "511 / 0 / 0 / Offline" + dead Start button — root cause

- The Next.js page calls `fetchTradingAgentDashboard(slug)` on `apps/api` (NestJS, Railway).
- `apps/api` reaches the home bot via the public tunnel `https://bot.doxxedcrypto.digital/api/relay-state`.
- The tunnel + bot are **flapping** (supervisor log shows `bot=True`/`bot=False` every tick,
  ~3 min per tick because `Test-TunnelPublicHealthy` probes the slow public URL).
- When the tunnel probe times out, `apps/api` falls back to the Neon DB, which has stale
  equity 511 / 0 trades / 0 PnL → exactly what the user sees.
- The **Start button** calls the home bridge at `http://127.0.0.1:7810/cmd/start-all`.
  That bridge (`home-stack-launcher.ps1`) was NOT listening (only Ollama :11434 was up),
  and a browser on the public site cannot reach `127.0.0.1` of the user's PC anyway. The
  button therefore appears dead. The bridge must run on the home PC AND the website must
  route Start through `apps/api` → bot relay webhook, not directly to localhost.

## 5. Bitfinex P0 gates — evidence

`bot.py` imported `bitfinex_live_executor` in 7 places but **the module did not exist**
(repo grep returned no file). Every `_maybe_bitfinex_*` hook therefore silently no-oped
behind `except Exception` / `is_enabled() == False`. This was the single root cause of all
four P0 gates failing.

**Fix applied this pass:**
- Created `services/btc-conservative-agent/bitfinex_live_executor.py` implementing:
  `configure`, `is_enabled`, `live_status`, `submit_market_entry`, `submit_limit_entry`,
  `submit_market_close`, `cancel_exchange_order`, `fetch_open_orders`, `fetch_positions`,
  `fetch_my_trades`, `fetch_balance`, `reconcile_exchange_state`, `rebuild_state_from_exchange`.
- Wired startup rebuild (`rebuild_state_from_exchange`) in `main()` when live is armed.
- Added `bitfinex_live_reconcile_loop` background thread (30s cadence) for fill ingestion
  + manual-close detection.

| Gate | Before | After |
|------|--------|-------|
| 1. Place market order on Bitfinex without relay | NO (module missing) | YES — `submit_market_entry` via ccxt private client |
| 2. Receive fills | NO | YES — `fetch_my_trades` polled by reconcile loop, `last_fill_ts` tracked |
| 3. Reconcile manual closes | NO | YES — `reconcile_exchange_state` reports `manual_closes` drift |
| 4. Rebuild state after restart | NO | YES — `rebuild_state_from_exchange` called at startup when armed |

**Caveat:** gates 1–4 are now *wired* and *compile-clean*. Live verification requires the
operator to set `BITFINEX_API_KEY` / `BITFINEX_API_SECRET` in `home-bot.env` and toggle
`bitfinex_live_enabled=true` via `/api/bitfinex_live`. Taking real funds live needs explicit
user confirmation.

## 6. State Integrity block (added)

Both `/api/state` and `/api/relay-state` now emit `snapshot["state_integrity"]` built by
`build_state_integrity()`:

```
snapshot_seq, snapshot_ts, bot_version, exchange, symbol,
ws_connected, ws_status, ws_connected_sec_ago,
rest_healthy, price_age_sec, book_age_sec,
orders_synced, positions_synced, trades_synced, last_fill_sec_ago,
execution_paused, live_armed, bitfinex_live_enabled, bitfinex_live,
genome_recorder, genome_bus_seq, genome_stats,
research_db, relay_push { configured, url, seq, last_event, last_ok, last_sec_ago },
tunnel_url, analyzer_url, last_fresh_reset_ts
```

Every downstream viewer (website, Agent Hub, relay, `:9500` dashboard, 24h monitor) should
read this block to prove it is seeing the same live bot state.

## 7. PowerShell respawn / CPU drain — finding

`.home-stack-supervisor.log` (442 KB) shows the supervisor is running and the bot is
**flapping healthy/unhealthy every tick**. Each tick takes ~3 minutes because
`Test-TunnelPublicHealthy` does a live HTTP probe to `https://bot.doxxedcrypto.digital`.
The supervisor itself does not respawn every 5 s, but the flapping bot + repeated tunnel
probes are the CPU/Sluggishness drain. The 5 s window flicker is most likely a crashed
subprocess (bot or cloudflared) being restarted in a tight loop — see
`scripts/find-powershell-spawn-loop.ps1` to pin the exact spawner live.

## 8. Recommended next steps (for the user to confirm)

- Confirm Bitfinex API credentials are in `doxedcryptofounder-secrets/vault/home-bot.env`.
- Confirm before toggling `bitfinex_live_enabled=true` with real funds.
- Approve deleting the large `signal_replay.jsonl.N` rotation files (55+ copies) and
  stray report JSONs in `services/btc-conservative-agent/` root — they are the main cause
  of Cursor/Glob timeouts and should be gitignored + cleaned.
