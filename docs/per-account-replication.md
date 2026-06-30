# Per-Account Replication Design

**Goal:** When accounts sign up on the website, each account trades the same
strategy as the showcase bot (`services/btc-conservative-agent/bot.py` on
`:7002`), with each trade accounted for **per-account** using the same
per-$20-leg paper-book principle the showcase bot already uses.

This is a **design doc + minimal hook**, not a full multi-tenant build. The
existing infrastructure already does most of this; the design names the seams
and the one new table.

---

## 1. What already exists (do not rebuild)

- **Showcase bot is the single signal source.** It runs the strategy, emits
  `ORDER_PLACED` / `FILLED` / `POSITION_CLOSED` events via the
  `SHOWCASE_RELAY_WEBHOOK_URL` webhook to `apps/api/.../showcase-relay-event`,
  and exposes per-leg state on `/api/relay-state` (`positions`, `orders`,
  `trades`, and now `paper_book`).
- **Per-user execution already exists.**
  `apps/api/src/trading-agents/signal-subscriber-execution.service.ts` polls
  the bot for intents and, per `TradingAgentInstance`, places real Bitfinex
  orders (hire users) or sim mirrors (relay-sim users) via
  `BitfinexTradingClient` / `BitfinexSimTradingClient`.
- **Per-user paper book already exists for relay-sim users.**
  `copy-relay-sim.service.ts` keeps a `CopyRelaySimState['ledger']` per user
  with per-leg fills/exits. The relay-fidelity mapper already compares per-user
  relay fills against showcase legs.
- **Prisma model:** `TradingAgentInstance` (agentId+userId) holds
  `exchangeProvider`, `dashboardState.copyRelaySim`, and per-user credentials.

So replication is **not** a new pipeline — it is making the existing per-user
execution path the default for every signed-up account, with a per-user paper
book surfaced to the website.

---

## 2. The design

### 2.1 Account → instance → paper book

```
Website signup → User row
              → TradingAgentInstance row (agentId=conservative-btc, userId, exchangeProvider)
                  ├─ exchangeCredentials (encrypted, per-user Bitfinex keys) — hire path
                  └─ dashboardState.copyRelaySim.ledger — the per-user paper book
```

Every signed-up account gets a `TradingAgentInstance` for the
`conservative-btc` agent on signup (provisioned by
`trading-agent-instances.service`). The instance starts in **relay-sim mode**
(paper) by default — no real funds, no API keys required. The user can later
"hire" the agent (connect Bitfinex keys) to switch to live copy.

### 2.2 Per-user paper book (the accounting primitive)

The showcase bot's `paper_book` (per-$20-leg ledger) is the **template**. Each
per-user paper book is the same shape, maintained by the subscriber execution
service:

```
PerUserPaperBook {
  userId, agentId, instanceId,
  mode: 'relay_sim' | 'live_copy',
  startingBalanceUsd,
  legs: [
    {
      legId,            // matches showcase trade_id (the source leg)
      tradeId,
      status: 'PENDING' | 'OPEN' | 'CLOSED',
      direction, qty, leverage,
      entryPrice, entryTs,
      currentPrice, unrealizedPnlUsd, unrealizedPnlPct,
      exitPrice, realizedPnlUsd, exitReason, exitTs,
      bitfinexOrderId,        // live_copy only
      slippageUsd,            // entry delta vs showcase entry
      syncScoreDeltaPct,      // entry/exit delta vs showcase leg
    }
  ],
  summary: { openCount, realizedPnlUsd, unrealizedPnlUsd, syncScorePct }
}
```

This is exactly `CopyRelaySimState['ledger']` + the per-leg fields
`relay-fidelity.mapper` already computes (`entryDeltaPct`, `exitDeltaPct`,
`entryLagSec`). The design work is mostly **exposing** it, not building it.

### 2.3 The per-leg matching rule (same as showcase)

Each showcase `$20` leg has a `trade_id`. The subscriber maps its own per-user
leg to that `trade_id` (via `classifyTradeIdMatch`). When the showcase closes
leg `T-123`, the subscriber closes the user's leg whose `legId` matches `T-123`
against that user's own entry — not the merged Bitfinex position. This is the
same principle the showcase bot uses internally: per-leg accounting survives
Bitfinex's position netting.

### 2.4 Allocation (how much per leg per user)

- **Default:** each user gets the same `$20` per leg as the showcase (the
  showcase's `qty` at its leverage). Configurable per-user via
  `instance.dashboardState.copyTradeConfig.allocationPerLegUsd`.
- **Cap:** `loadSubscriberMaxMarginUsd` already enforces a per-user max margin.
  The subscriber's `EntryEligibility` (`availableUsd`, `slotsRemaining`) stops
  new legs when the cap is hit.
- **Concurrency:** `resolveMaxConcurrentCopySignals` caps open legs per user
  (default matches the showcase's 20).

### 2.5 Modes & transitions

```
relay_sim (paper, default on signup)
   │  user connects Bitfinex keys (hire)
   ▼
live_copy (real funds, dashboardState.exchangeProvider='bitfinex')
   │  user disconnects / stopRelaySim
   ▼
relay_sim (or fully stopped)
```

`copy-relay-sim.service.startRelaySim/stopRelaySim` already implements the
transition; `signal-subscriber-execution` swaps `BitfinexSimTradingClient` for
`BitfinexTradingClient` based on `instance.exchangeProvider`. No new code path
— just make signup provision a relay-sim instance automatically.

---

## 3. The minimal hook (what to wire now)

1. **On signup, provision a relay-sim `TradingAgentInstance`** for
   `conservative-btc`. One row insert in `trading-agent-instances.service`:
   `exchangeProvider='sim'`, `dashboardState.copyRelaySim = emptyCopyRelaySimState(...)`,
   `status=PAUSED` (relay sim active, live blocked). This is the only new
   write path needed for "every account trades the same strategy."

2. **Expose per-user paper book on the website.** The
   `instance-view.mapper.ts` / `mapSubscriberExchangeLiveBook` already shapes
   the subscriber book for the dashboard. Add a `paper_book` field on the
   instance view that mirrors the showcase bot's `paper_book` shape (§2.2) so
   the Positions/Orders/Trades tables can render per-leg data per-user with the
   same component the web worker is wiring for the showcase.

3. **Start the relay sim automatically** for provisioned instances (or prompt
   the user to press Start on the dashboard). Starting is what makes the
   "relay sim was offline" audit (B1 in `bitfinex-sim-diagnosis.md`) stop
   counting against sync score — once a user has fills, the orphan audit
   switches from `showcase_without_relay_offline` to matched rows.

4. **Sync-score per user.** `relay-fidelity.mapper.buildRelayFidelitySnapshot`
   already produces per-user `entryDeltaPct` / `exitDeltaPct` / lags. Surface
   `summary.syncScorePct` on the instance view so each user sees how closely
   their book matches the showcase.

---

## 4. What is explicitly NOT built now

- No new execution engine. The showcase bot stays the single signal source;
  per-user execution reuses `signal-subscriber-execution`.
- No multi-tenant isolation of the bot itself. The bot runs once on `:7002`;
  per-user isolation is at the API/subscriber layer (per-user clients,
  per-user ledgers, per-user credentials).
- No changes to the bot's paper book. The showcase `paper_book` is the
  template; per-user books live in the API layer.
- No real-funds automation. Live copy requires the user to connect Bitfinex
  keys (hire flow) AND the showcase bot to have `BITFINEX_LIVE_ENABLED=true`
  (see `bitfinex-sim-diagnosis.md` §6). Two separate, deliberate gates.

---

## 5. Failure modes & safety

- **Showcase bot down:** subscriber execution has no new signals; per-user
  books keep marking existing legs to market against the last known price.
  No new legs open.
- **User's Bitfinex keys revoked:** `BitfinexTradingClient` calls fail; the
  subscriber logs the error on the instance, marks the instance `ERROR`, and
  stops placing orders for that user. Other users unaffected.
- **Bitfinex merges the user's 4×$20:** the per-user paper book still records
  each leg against its own `entryPrice`; the merged exchange position is
  reconciled via `bitfinex_live_executor.reconcile_exchange_state`-style
  drift detection (already in the subscriber via `fetch_positions`).
- **Slow fills:** per-user sync score degrades; the dashboard surfaces it via
  `entryDeltaPct`/`exitDeltaPct`. User can stop their sim or disconnect.
