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
