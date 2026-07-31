# Fly.io production runbook — Conservative BTC

> **Current architecture (authoritative):** `doxed-btc-bot` on Fly.io is the
> only AI/strategy/paper-signal owner. The desktop `:7002` process is a
> read-only proxy to Fly and desktop `:9001` analyzes the incremental Fly data
> mirror. Railway hosts the website API and isolated copy-trade relay; it must
> not run a second bot. Cloudflare and home-PC bot launchers are retired while
> `config/fly-canonical.lock.json` is frozen.

The setup steps below are retained for disaster recovery and rebuilding the
existing Fly app. Normal releases use the guarded GitHub workflow
`.github/workflows/fly-bot-deploy.yml`; do not create a second Fly/Railway bot
service and do not run `bot.py` on the desktop.

---

## What's already in the repo

| File | Purpose |
|---|---|
| `services/btc-conservative-agent/Dockerfile` | Python 3.11 slim image, installs `requirements.txt`, exposes 7002. |
| `services/btc-conservative-agent/fly-entrypoint.sh` | Runs one bot on `:7002`, persists raw evidence, and starts the analyzer only when explicitly enabled. |
| `services/btc-conservative-agent/fly.toml` | App `doxed-btc-bot`, region `sin`, 24/7 (no auto-stop), volume `bot_data` at `/app/data`, 1024MB `shared-cpu-1x`. |
| `services/btc-conservative-agent/.dockerignore` | Excludes `__pycache__`, logs, `.home-*`, runtime JSONL, `.git`, node stuff. |
| `deploy/scripts/fly-setup-volume.ps1` (+ `.sh`) | One-time: creates `bot_data` volume + prints machine IDs. |
| `apps/api/src/trading-agents/fly-control.service.ts` | Calls Fly Machines REST API to start/stop the bot machine, polls state, probes `/api/ping`. |
| `apps/api/.../trading-agents.controller.ts` | `POST /api/trading-agents/conservative-btc/fly-control` (AdminGuard) + `GET` status + `GET /api/trading-agents/showcase-host`. |
| `apps/web/.../agent-admin-showcase-control.tsx` | Controls and status resolve to the canonical Fly URL. No local strategy fallback exists. |
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
This creates `bot_data` in the app's primary region (`sin`) and prints the
machine list. Keep the machine identifier for the website API's Fly control
configuration.

> The volume is also referenced in `fly.toml` (`[mounts] source = "bot_data"`). The Dockerfile + entrypoint mount it at `/app/data` and symlink the writable research dirs there, so CSV/JSONL/runtime JSON survive redeploy.

### 4. Set the paper-signal owner's secrets on Fly
```powershell
flyctl secrets set --app doxed-btc-bot `
  DEEPSEEK_API_KEY=... `
  BOT_ADMIN_TOKEN=... `
  BOT_CONTROL_SECRET=... `
  SHOWCASE_WEBHOOK_SECRET=... `
  SHOWCASE_RELAY_WEBHOOK_URL=https://doxxedcrypto.digital/api/trading-agents/conservative-btc/showcase-relay-event `
  FORCE_PAPER_MODE=true `
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
Fly is permanently paper-signal-only. Do **not** install
`BITFINEX_API_KEY` or `BITFINEX_API_SECRET` on Fly, and do not change
`FORCE_PAPER_MODE=true` or `BITFINEX_LIVE_ENABLED=false`. Private Bitfinex
credentials belong only to Railway's isolated subscriber executor. A Fly
deployment that can submit directly to Bitfinex violates the production
architecture and must fail closed.

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
Fly normally runs trading-only. The desktop analyzer reads the synchronized
volume mirror; a temporary analyzer warm-up response is not a trading outage.

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
`SHOWCASE_HOST=fly` documents the active topology. Production code also pins
the canonical Fly URL so a stale database value or environment variable cannot
silently repoint trading controls to a legacy host. There is no supported
home-PC strategy fallback while the canonical lock is frozen.

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

### 9. Test pause/resume from the website
1. Open https://doxxedcrypto.digital/agent-hub/conservative-btc (as admin).
2. The command center should identify Fly as the canonical owner.
3. **Pause trading** must call the canonical bot pause endpoint without stopping
   the Fly machine.
4. **Resume trading** must first re-prove readiness; it must never redeploy or
   revive a Railway/home bot as a fallback.

### 10. Verify the single-owner boundary

1. Confirm Fly `/ready` reports the expected source revision and strict market readiness.
2. Confirm the desktop has only `fly-dashboard-proxy.py` and the analyzer—no
   `bot.py`, `btc_conservative_agent.py`, or `cloudflared` strategy path.
3. Confirm website controls resolve to `https://doxed-btc-bot.fly.dev`.
4. Keep copy trading disarmed until the exchange is flat, fidelity is at least
   98%, NEXT_FRESH_ONLY is proven, and the soak is stable.

---

## Important notes / decisions

- **Region `sin`** — the current canonical app/volume region.
- **Size `shared-cpu-1x` / 1024MB** — current production allocation. Keep the
  heavy analyzer on the desktop mirror rather than reducing trading headroom.
  ```toml
  [[vm]]
    size = "shared-cpu-1x"
    memory = 1024
  ```
- **24/7 uptime** — `fly.toml` sets `auto_stop_machines = false`, `auto_start_machines = false`, `min_machines = 1`. The trading bot must never auto-sleep.
- **Port binding** — `bot.py` binds `0.0.0.0` via `DASHBOARD_BIND_HOST`; the
  Dockerfile pins the same address so Fly can reach internal port `7002`.
- **Analyzer `:9001`** — normally runs on the desktop against the incremental
  Fly data mirror. Fly's analyzer switch is an explicit recovery option, not
  the default topology.
- **Requirements** — `services/btc-conservative-agent/requirements.txt` exists and is used as-is: `flask`, `ccxt`, `websocket-client`, `numpy`, `pytz`, `requests`. If the bot imports anything else at runtime that isn't in there, `fly deploy` will still succeed but the bot will crash at boot — check `fly logs` and add the missing dep.
- **Deploys are revision-locked** — the GitHub workflow tests, deploys the exact
  source commit, and verifies `/ready` plus paper-safe flags before succeeding.

## What you must provide before `fly deploy` works
1. A Fly.io account + `flyctl auth login`.
2. `flyctl apps create doxed-btc-bot` (step 2).
3. The volume (step 3 — the script creates it).
4. The Fly AI/control/webhook secrets and permanent paper-mode flags from step
   4. Never put private Bitfinex API credentials on Fly.
5. A Fly API token + machine ID + `SHOWCASE_HOST=fly` on Railway (steps 3 + 7) for the website Start/Stop to control Fly.

## Troubleshooting
- **`fly deploy` fails on `pip install`** — a bot import isn't in `requirements.txt`. Check `fly logs` for `ModuleNotFoundError`, add to `requirements.txt`, redeploy.
- **Start button says the machine is not ready** — inspect strict `/ready`;
  WebSocket ticks, candles, indicators, and the stability window must all pass.
- **Stop button errors with "already stopped"** — handled idempotently; treated as success.
- **`/api/trading-agents/showcase-host` reports anything except Fly** — treat it
  as architecture drift and redeploy the current API revision; do not start a
  local bot as a workaround.
- **Bot OOMs / restarts** — bump `memory` to 1024 in `fly.toml`.
