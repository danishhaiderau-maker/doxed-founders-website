# Conservative BTC Agent (3-Factor Research Bot)

Python trading agent that powers **Agent Hub → Conservative BTC Agent** live dashboard on Doxxed Crypto.

## How it connects

1. This service exposes `GET /api/state` (Flask on port 5000).
2. Railway API reads that URL via `TRADING_AGENT_BOT_URL` and maps it into Agent Hub.
3. The Next.js dashboard polls the API every 15 seconds — no platform redeploy needed when you change strategy code.

```
Browser → Doxxed API → TRADING_AGENT_BOT_URL/api/state → this bot
```

## Run locally

```powershell
cd services/btc-conservative-agent
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
# Fill BYBIT_API_KEY, BYBIT_SECRET, DEEPSEEK_API_KEY
python bot.py
```

Open `http://localhost:5000` for the bot’s own dashboard.  
Point your local API at it:

```env
TRADING_AGENT_BOT_URL=http://localhost:5000
```

Then open `http://localhost:3000/agent-hub/conservative-btc`.

## Deploy (Railway second service)

1. New Railway service → root directory `services/btc-conservative-agent`
2. Set env vars from `.env.example`
3. Generate public URL (e.g. `https://btc-bot-production.up.railway.app`)
4. On the **API** service, set:

```env
TRADING_AGENT_BOT_URL=https://btc-bot-production.up.railway.app
```

5. Redeploy API. Agent Hub shows **LIVE — connected to your research bot**.

Check bridge health: `GET /api/trading-agents/bot/status`

## Can I change the code?

**Yes.** `bot.py` is yours — edit thresholds, exits, AI prompts, filters, etc.  
After changes:

- **Local:** stop and restart `python bot.py`
- **Railway:** push and redeploy the bot service only

Agent Hub picks up new stats on the next poll. You do **not** need to redeploy Vercel for strategy tweaks.

## Files

| File | Purpose |
|------|---------|
| `bot.py` | Full 3-factor Bybit 15m research bot + Flask `/api/state` |
| `requirements.txt` | Python deps |
| `.env.example` | Required secrets |
| `railway.toml` | Railway deploy config |

## Notes

- Default mode is **RESEARCH** (simulated fills). Live arming is off unless you toggle in the bot UI.
- The platform never receives your exchange keys — only aggregated state JSON.
- If the bot is offline, Agent Hub falls back to CoinGecko demo data (banner shows **DEMO**).
