# DEPRECATED — manual web form only (no CLI/API)

Use `docs/AGENT_REGISTRY_AUTOMATION.md` instead. Kept for reference if you submit AAD manually once.

# AI Agents Directory — submission pack (legacy)

**Submit here:** [https://aiagentsdirectory.com/submit-agent](https://aiagentsdirectory.com/submit-agent)

**Free tier:** $0 — permanent listing (free tier may require AAD badge on your site — see their sponsor page).

---

## Copy-paste fields

### Agent name
```
Conservative BTC Agent
```

### Tagline / short description (1 line)
```
Exchange-neutral BTC perp signal API — pay success fee on profit only, not on losses.
```

### Full description
```
Conservative BTC Agent publishes live BTC perpetual signal cycles from a transparent research pipeline. Subscribers execute on their own exchange (Hyperliquid, Bitfinex, Bybit, etc.) using exchange-neutral percentage offsets — not copy-pasted absolute prices.

How it works:
• Poll the Signal API for ENSE intents when a new cycle opens
• Place limit entry from your local mark at receipt
• Arm exchange-native stop-loss at fill (mandatory)
• Report lifecycle events (ORDER_PLACED → FILLED → EXIT)
• Success fee: 10% of profit on close only; $0 on loss; waived if fee < $0.20

Built by Doxxed Crypto Founder platform. Admin showcase bot for observation; hire tier available for isolated instances (2,000 DDollar / 7 days).

Not financial advice. Signals are informational; you execute and bear all risk.
```

### Website / product URL
```
https://doxxedcrypto.digital/agent-hub/conservative-btc
```

### AgentCard / metadata URL
```
https://doxxedcrypto.digital/.well-known/agent-card.json
```

### API documentation
```
https://doxxedcrypto.digital/docs/signal-api
```

### API base (mandate — public)
```
https://doxed-founders-website-production.up.railway.app/api/trading-agents/conservative-btc/signals/mandate
```

### Logo / icon (square — upload as logo/icon)
```
https://doxxedcrypto.digital/icons/conservative-btc-agent.png
```
Local file: `apps/web/public/icons/conservative-btc-agent.png`

### Thumbnail / banner (wide — upload as cover/thumbnail)
```
https://doxxedcrypto.digital/icons/conservative-btc-agent-thumbnail.png
```
Local file: `apps/web/public/icons/conservative-btc-agent-thumbnail.png`

### Company / builder
```
Doxxed Crypto Founder
```

### Company site
```
https://doxxedcrypto.digital
```

---

## Suggested categories (pick 2–3 on their form)

| Category | Why |
|----------|-----|
| **Research** | Transparent research pipeline, public showcase |
| **Data Analysis** | Market regime, edge scoring, signal intents |
| **Workflow** | Signal lifecycle API for automated subscribers |
| **Digital Workers** | Autonomous signal publisher agent |

---

## Tags / keywords
```
BTC, bitcoin, trading, crypto, perpetual, signals, API, exchange-agnostic, Hyperliquid, Bitfinex, fintech, AI agent, automated trading, success fee
```

---

## Pricing model
```
Freemium + success fee
• Observe showcase: free
• Signal API key: free (sign in)
• Success fee: 10% of subscriber profit on winning closes only
• Hire isolated instance: 2,000 DDollar (~7 days)
```

---

## Key features (bullets)
```
• Exchange-neutral ENSE signal intents (any perp venue)
• Mandatory stop-loss-at-fill for subscribers
• Success-fee billing only on profitable closes
• Live public showcase dashboard (sanitized)
• AgentCard + subscriber docs
• Admin-owned showcase; hire tier for private copy
```

---

## After submit

1. Mark **AGENTSCAN** in Admin → Agent registrations
2. Optional: [Fast Track $19](https://aiagentsdirectory.com/sponsor) to skip badge wait

---

## No wallet needed for this listing

Skip SAID/Phantom for now — this directory is a web form only.
