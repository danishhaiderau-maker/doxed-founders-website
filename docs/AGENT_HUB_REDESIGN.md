# Agent Hub — Marketplace Redesign (Cursor spec)

Design principle for every `/agent-hub` screen:

> Show which agent is making money, why it is making money, who built it, and let users activate it in under 60 seconds.

## Three user modes

| Mode | Who | What |
|------|-----|------|
| **Observe** | Everyone | Watch admin showcase — trades, journey, reasoning. No control. |
| **Paper trade** | Signed-in | DDollar / simulated (future) |
| **Hire** | Signed-in | Own Bitfinex (recommended) + own AI keys. Isolated from admin. |

Admin showcase (`PlatformSettings` + Railway bot) must **never** leak to hire flows.

---

## `/agent-hub` — Agent Marketplace

### Layout (top → bottom)

1. **Hero stats** — Active agents, simulated volume, avg win rate, live builders
2. **Beta warning** — Max $500, experimental, high risk
3. **Category tabs** — Discover · Trading · Research · Content · Scout
4. **Bubble map** — Size = followers + trades + performance (reuse discover golden-angle layout)
5. **Featured card** — `conservative-btc` with Observe / Hire / Follow
6. **Rankings** — Top traders · Most followed · Best builders
7. **Live activity feed** — Trade events, not git commits

### Components

- `agent-marketplace-stats.tsx`
- `agent-bubble-map.tsx`
- `agent-marketplace-card.tsx`

---

## `/agent-hub/conservative-btc` — Agent profile

### Layout

1. **Profile hero** — LIVE badge, builder @handle, strategy, position, return, Observe / Hire / Follow
2. **Trust layer** — Doxxed builder, GitHub, X, public track record, exchange connected
3. **Trade journey** — Vertical clickable nodes (BUY → ADD → EXIT) with reason + X share
4. **Live mission control** — Existing transparency panels (thinking, edge, activity)
5. **Admin only** — Research bot raw JSON tab

### Components

- `agent-profile-hero.tsx`
- `agent-trust-layer.tsx`
- `agent-trade-journey.tsx`
- `live-mission-control.tsx` (existing)

---

## `/agent-hub/[slug]/hire` — Hire wizard

Steps: **Exchange → API keys → AI → Risk & activate**

- Bitfinex **Recommended** (zero fees banner)
- Per-exchange API guide drawer (`exchange-api-guides.ts`)
- Required vs forbidden permissions list
- Encrypted credentials copy
- Risk checkbox: max $500, beta, afford to lose
- Activate creates `TradingAgentInstance` with user's keys only

---

## BTC bot sync from research repo

Source: `danishhaiderau-maker/bybit-15m-research-bot` → `bybit_bot.py`  
Target: `services/btc-conservative-agent/bot.py` (Railway)

```bash
npm run sync:btc-research-bot
```

GitHub Action: `.github/workflows/sync-btc-research-bot.yml` (every 6h + manual + repository_dispatch).

For instant sync on research push, add to **bybit-15m-research-bot** repo:

```yaml
# .github/workflows/notify-main-repo.yml
on:
  push:
    paths: [bybit_bot.py]
jobs:
  dispatch:
    runs-on: ubuntu-latest
    steps:
      - run: |
          curl -X POST \
            -H "Authorization: token ${{ secrets.MAIN_REPO_PAT }}" \
            https://api.github.com/repos/danishhaiderau-maker/doxed-founders-website/dispatches \
            -d '{"event_type":"research-bot-updated"}'
```

Set `RESEARCH_BOT_SYNC_TOKEN` secret on main repo (PAT with read access to private research repo).

---

## Future (not in this pass)

- Per-user bot worker on Railway (hire runtime execution)
- Paper trade mode UI
- Clone agent template flow
- Conviction timeline chart component

## Conservative BTC control flow backlog

This is intentionally a separate product pass from money-path and research-data
fixes.  The visual redesign must consume the proven state machine; it must not
invent a second control path.

### Product principles

- One primary action: **Start showcase** / **Stop showcase**. It starts and
  stops bridge, bot, analyzer, tunnel, and mirror in the tested order.
- Show one plain-language state at a time: Starting, Collecting research,
  Paused (shadow collecting), Ready for relay, Live copying, or Needs attention.
- Separate money boundaries visually: Showcase paper, Paused Shadow, and
  Bitfinex Live Copy must never share a PnL card or ambiguous status color.
- Keep advanced service controls in a diagnostics drawer with timestamps,
  ownership, source revision, and recovery actions. They are not the normal UI.
- Every queued action must progress through visible acknowledgements or become
  a specific error; never leave a permanent “queued” toast.
- Use calm motion, progressive disclosure, generous spacing, and mobile-first
  cards. Animation explains state changes; it does not decorate them.

### Recommended interaction sequence

1. **System readiness** — one card checks dashboard owner, bot, analyzer,
   bridge, tunnel, revision, and flat/exposure status.
2. **Research mode** — live executed paper results and Paused Shadow results are
   adjacent but clearly labeled; every position/order/signal has a timestamp.
3. **Relay readiness** — a short checklist explains fresh-only arming,
   one-account net-position constraints, and current executor health.
4. **Explicit live confirmation** — smallest-canary choice, account name,
   maximum margin, and a hold-to-confirm action.
5. **Live journey** — Source signal → relay receipt → exchange submit → ack →
   fill → exit, with trade ID, lane, price, and latency at each node.
6. **Safe stop** — immediately disarm new entries, cancel verified-unfilled copy
   orders, and keep open-position exit management visible.

### Information architecture

- **Overview:** state, exposure, session result, primary action.
- **Research:** lane tiles, Paused Shadow statistics, ADX/prompt evidence.
- **Live copy:** Bitfinex net position, virtual lots, reconciliation, latency.
- **Timeline:** human-readable events with exact timestamps and IDs.
- **Diagnostics:** service-level controls, raw health, logs, and downloads.
