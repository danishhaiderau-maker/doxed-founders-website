# Home BTC bot migration (Railway → home PC)

Stop paying for **btc-conservative-agent** on Railway. Run **one bot** at home; cloud keeps **Vercel + Neon + Railway API only**.

## Savings

| Before | After |
|--------|--------|
| Railway bot + API ($5–6+/day with egress) | **$0** bot on home |
| Bitfinex WebSocket egress on Railway | Egress on home ISP |
| Two copies of the script | **One** `bybit_bot.py` / synced `bot.py` |

## Stack after migration

```text
Home PC (16 GB)          Cloud
───────────────          ─────
bybit_bot.py :7800  →    Vercel (site)
Cloudflare Tunnel   →    Neon (DB)
relay webhooks      →    Railway (NestJS API only)
```

## Step 1 — On home PC (bot machine)

### A. Quick test (5 minutes)

```powershell
# Terminal 1 — bot (research repo or monorepo)
npm run print:home-bot-env          # from dev machine first; copy vault/home-bot.env to home PC
# load home-bot.env, start your bot on port 7800

# Terminal 2 — temporary public URL
powershell -ExecutionPolicy Bypass -File scripts/setup-home-bot-tunnel.ps1 -Quick
```

Copy the `https://….trycloudflare.com` URL.

### B. Production hostname

```powershell
powershell -ExecutionPolicy Bypass -File scripts/setup-home-bot-tunnel.ps1
# Creates bot.doxxedcrypto.digital → localhost:7800
```

## Step 2 — From repo machine (wire cloud)

```bash
npm run print:home-bot-env
npm run wire:home-bot -- https://bot.doxxedcrypto.digital --pause-railway-bot
```

This updates Neon + Railway API and **sleeps** Railway `btc-conservative-agent`.

Skip health check if tunnel not ready yet:

```bash
npm run wire:home-bot -- https://YOUR-URL --skip-health-check --pause-railway-bot
```

## Step 3 — Verify

```bash
curl https://bot.doxxedcrypto.digital/health
npm run prepare:bitfinex-relay-test
```

## Step 4 — Delete Railway bot (optional, after 24h stable)

Railway dashboard → **btc-conservative-agent** → Delete Service.

Or leave sleeping if sleep billing is $0.

## Env vars (home-bot.env)

| Variable | Purpose |
|----------|---------|
| `SHOWCASE_RELAY_WEBHOOK_URL` | Wake API on approve/place/close |
| `BOT_CONTROL_SECRET` | Admin pause/resume |
| `BITFINEX_*` / `DEEPSEEK_*` | From Admin Control |
| `DASHBOARD_PUBLIC_URL` | Public tunnel URL |

## One script rule

- **Live:** home `bybit_bot.py` (or monorepo `services/btc-conservative-agent/bot.py` after sync)
- **Repo sync:** `npm run sync:btc-research-bot` keeps CI parity — no second Railway deploy
