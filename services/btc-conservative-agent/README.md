# Conservative BTC Agent — Execution Mirror (doxxedcrypto.digital)

Railway runs **`btc_conservative_agent.py`** — a lightweight execution wrapper.

The synced signal engine lives in **`bot.py`** (mirrored to `../btc-signal-engine/engine.py` on sync).

## Architecture

```text
Research (local)          Showcase (Railway)
bybit_bot.py              btc_conservative_agent.py  ← entry point
  + analyzer                + showcase_ui.py         ← execution dashboard
  + telemetry               + bot.py                 ← signal engine (synced)
  + CSV/jsonl                 (no research modules deployed)
```

## Deploy

Railway `startCommand`: `python btc_conservative_agent.py`

Research-only modules live in `research/` and are excluded via `.dockerignore`.

## Sync from research repo

```bash
npm run sync:btc-research-bot
```

Updates `bot.py`, mirrors `../btc-signal-engine/engine.py`, updates `manifest.json`.

## Verify

```bash
npm run verify:signal-parity
npm run verify:bitfinex-lock
```
