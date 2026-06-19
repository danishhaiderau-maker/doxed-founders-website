# BTC Signal Engine

Shared strategy contract for **research** and **showcase** runtimes.

## Architecture

```text
bybit-15m-research-bot/bybit_bot.py
        ↓ sync-btc-research-bot.mjs
services/btc-signal-engine/engine.py   ← synced signal engine (full pipeline)
services/btc-conservative-agent/bot.py ← import alias (same engine until split completes)
        ↓
btc_conservative_agent.py              ← Railway entry (execution wrapper only)
```

Research wrapper (local): full `bybit_bot.py` + analyzer + telemetry.

Showcase wrapper (Railway): `btc_conservative_agent.py` — signal + dashboard + health only.

## manifest.json

Updated on every research sync. Dashboard displays engine version + signal hash for drift detection.
