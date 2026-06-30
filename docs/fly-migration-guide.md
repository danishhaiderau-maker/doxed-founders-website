# Fly.io migration guide — showcase BTC bot

This guide moves the **doxxedcrypto.digital showcase BTC bot** (`services/btc-conservative-agent/`) off your home PC + Cloudflare tunnel onto Fly.io, and rewires the Home Command Center Start/Stop buttons to control that remote Fly bot.

Everything in this repo is already deploy-ready. You only need to provide a Fly.io account + token + secrets, then run **one `fly deploy`**.

---

## What's already in the repo

| File | Purpose |
|---|---|
| `services/btc-conservative-agent/Dockerfile` | Python 3.11 slim image, installs `requirements.txt`, exposes 7002. |
| `services/btc-conservative-agent/fly-entrypoint.sh` | Symlinks `research/`, `research_accumulator/`, `research_archive/` → persistent volume, starts analyzer `:9001` in background, then bot `:7002` in foreground. |
| `services/btc-conservative-agent/fly.toml` | App `doxed-btc-bot`, region `syd`, 24/7 (no auto-stop), volume `bot_data` at `/app/data`, 512MB `shared-cpu-1x`. |
| `services/btc-conservative-agent/.dockerignore` | Excludes `__pycache__`, logs, `.home-*`, runtime JSONL, `.git`, node stuff. |
| `deploy/scripts/fly-setup-volume.ps1` (+ `.sh`) | One-time: creates `bot_data` volume + prints machine IDs. |
| `apps/api/src/trading-agents/fly-control.service.ts` | Calls Fly Machines REST API to start/stop the bot machine, polls state, probes `/api/ping`. |
| `apps/api/.../trading-agents.controller.ts` | `POST /api/trading-agents/conservative-btc/fly-control` (AdminGuard) + `GET` status + `GET /api/trading-agents/showcase-host`. |
| `apps/web/.../agent-admin-showcase-control.tsx` | Start/Stop buttons switch to the Fly path when `SHOWCASE_HOST=fly`. Local path kept as fallback. |
| `docs/fly-migration-guide.md` | This file. |

---

## Steps you run (PowerShell, in the repo root)

### 1. Fly.io account + flyctl
1. Sign up at https://fly.io with GitHub.
2. Install flyctl:
   ```powershell
   irm https://fly.io/install.ps1 | iex
   ```
3. Authenticate:
   ```powershell
   flyctl auth login
   ```

### 2. Create the app
```powershell
flyctl apps create doxed-btc-bot
```
(Use exactly `doxed-btc-bot` — the `fly.toml` app name + Railway `FLY_APP_NAME` must match.)

### 3. Create the persistent volume + capture a machine ID
```powershell
powershell -ExecutionPolicy Bypass -File deploy\scripts\fly-setup-volume.ps1
```
This creates `bot_data` (1GB, `syd`) and prints the machine list. **Copy a machine ID** — you'll set it as `FLY_MACHINE_ID` on Railway in step 6.

> The volume is also referenced in `fly.toml` (`[mounts] source = "bot_data"`). The Dockerfile + entrypoint mount it at `/app/data` and symlink the writable research dirs there, so CSV/JSONL/runtime JSON survive redeploy.

### 4. Set the bot's secrets on Fly
```powershell
flyctl secrets set --app doxed-btc-bot `
  BITFINEX_API_KEY=... `
  BITFINEX_API_SECRET=... `
  BITFINEX_LIVE_ENABLED=false `
  DDOLLAR_GATE_URL=... `
  DDOLLAR_GATE_TOKEN=... `
  DDOLLAR_GATE_MIN=... `
  PORT=7002 `
  DASHBOARD_BIND_HOST=0.0.0.0 `
  RESEARCH_DASHBOARD_PORT=9001 `
  RESEARCH_DASHBOARD_BIND_HOST=0.0.0.0 `
  SHOWCASE_AGENT=1
```
> `BITFINEX_LIVE_ENABLED=false` until you've verified the bot is healthy — keep paper/paper-relay mode for the first smoke test.

### 5. Deploy the bot to Fly (the one command)
```powershell
cd services\btc-conservative-agent
fly deploy
```
Then verify:
```powershell
flyctl status
flyctl logs --app doxed-btc-bot
# Public health probe (after the cert is wired in step 7):
curl https://bot.doxxedcrypto.digital/api/ping
```
The analyzer (`:9001`) boots slowly — `/api/analyzer/summary` may 502 for the first ~60s, that's normal.

### 6. Attach the public domain
```powershell
flyctl certs add bot.doxxedcrypto.digital --app doxed-btc-bot
```
Fly will print a CNAME target. In Cloudflare DNS, **replace the existing tunnel CNAME** for `bot` with the target Fly gives you. Wait for `flyctl certs show bot.doxxedcrypto.digital` to report `Ready`.

### 7. Generate a Fly API token for the website (Railway)
```powershell
flyctl auth token
```
Copy the token. On Railway (the API service), set:
```
FLY_API_TOKEN        = <token from flyctl auth token>
FLY_APP_NAME         = doxed-btc-bot
FLY_MACHINE_ID       = <machine id from step 3>
FLY_BOT_URL          = https://bot.doxxedcrypto.digital
SHOWCASE_HOST        = fly
```
`SHOWCASE_HOST=fly` is the switch that makes the website Start/Stop buttons call the Fly control endpoint instead of the local home-stack supervisor. Leave it `local` (or unset) to keep using the home PC path.

### 8. Redeploy Railway so the API picks up the new env
```powershell
npm run redeploy:railway
```
After it finishes, verify:
```powershell
curl https://doxxedcrypto.digital/api/health
curl https://doxxedcrypto.digital/api/trading-agents/showcase-host
#  -> {"host":"fly"}
```

### 9. Test Start/Stop from the website
1. Open https://doxxedcrypto.digital/agent-hub/conservative-btc (as admin).
2. The "Home PC command center" panel now shows a **Fly remote mode** badge.
3. Click **■ Stop showcase** → the Fly machine stops (preserved). Status chip should go offline.
4. Click **▶ Start showcase** → the API scales the machine up, polls until `started`, then probes `/api/ping`. Expect ~30–90s for full warm-up.

### 10. Cutover — retire the home PC stack
Once the Fly bot is stable for a day:
1. Stop the home PC bot (click Stop on the home launcher, or close the console windows).
2. Stop the cloudflared tunnel service (`scripts/stop-home-stack.ps1` or uninstall the named tunnel service).
3. Railway egress for bot relay drops to ~0 — the bot now lives on Fly, and the website reaches it at `https://bot.doxxedcrypto.digital` (Fly cert) or via the API proxy.

---

## Important notes / decisions

- **Region `syd`** — Sydney, closest to your AEST timezone and low-latency to Bitfinex.
- **Size `shared-cpu-1x` / 512MB** — cheapest (~$3–7/mo). If the analyzer engine OOMs during warm-up, bump in `fly.toml`:
  ```toml
  [[vm]]
    size = "shared-cpu-1x"
    memory = 1024
  ```
- **24/7 uptime** — `fly.toml` sets `auto_stop_machines = false`, `auto_start_machines = false`, `min_machines = 1`. The trading bot must never auto-sleep.
- **Port binding** — `bot.py` already binds `0.0.0.0` via `DASHBOARD_BIND_HOST` (default `0.0.0.0`). The Dockerfile also pins `DASHBOARD_BIND_HOST=0.0.0.0` and `DASHBOARD_PUBLIC_HOST=0.0.0.0` so the Flask app is reachable on Fly's internal port 7002. No `bot.py` logic changed.
- **Analyzer `:9001`** — runs in the same container as a background process (see `fly-entrypoint.sh`). The bot proxies `/api/analyzer/summary` + `/api/analyzer/genome` to `localhost:9001`. If the analyzer ever needs to be a separate Fly process, split it into its own app + machine and point the bot at the internal URL.
- **Requirements** — `services/btc-conservative-agent/requirements.txt` exists and is used as-is: `flask`, `ccxt`, `websocket-client`, `numpy`, `pytz`, `requests`. If the bot imports anything else at runtime that isn't in there, `fly deploy` will still succeed but the bot will crash at boot — check `fly logs` and add the missing dep.
- **No `fly deploy` was run by the agent** — there's no Fly token in this environment. Step 5 is yours to run.

## What you must provide before `fly deploy` works
1. A Fly.io account + `flyctl auth login`.
2. `flyctl apps create doxed-btc-bot` (step 2).
3. The volume (step 3 — the script creates it).
4. The bot secrets (step 4 — `BITFINEX_*`, `DDOLLAR_GATE_*`).
5. A Fly API token + machine ID + `SHOWCASE_HOST=fly` on Railway (steps 3 + 7) for the website Start/Stop to control Fly.

## Troubleshooting
- **`fly deploy` fails on `pip install`** — a bot import isn't in `requirements.txt`. Check `fly logs` for `ModuleNotFoundError`, add to `requirements.txt`, redeploy.
- **Start button says "machine started but /api/ping not yet reachable"** — the analyzer is warming up; wait 60s and refresh. The machine itself is `started`.
- **Stop button errors with "already stopped"** — handled idempotently; treated as success.
- **`/api/trading-agents/showcase-host` returns `local`** — `SHOWCASE_HOST` isn't set on Railway (or API not redeployed). Re-check step 7 + 8.
- **Bot OOMs / restarts** — bump `memory` to 1024 in `fly.toml`.
