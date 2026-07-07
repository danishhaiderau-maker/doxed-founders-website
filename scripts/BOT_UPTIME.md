# Showcase Bot Uptime Contract

This document defines the **strict uptime semantics** for the showcase bot
(`services/btc-conservative-agent/bot.py` on `:7002`, exposed publicly at
`bot.doxxedcrypto.digital`). It is the canonical reference for everyone
editing `scripts/`.

> *"It is very important for the bot to keep running, and have a good uptime.
> Only a manual stop by pressing the stop button can stop it from restarting
> other a code update, and any crash or anything should start the bot in
> auto mode."*

## The three scenarios

| # | Scenario                            | What happens                                       | Detection latency |
|---|-------------------------------------|----------------------------------------------------|-------------------|
| A | Bot crashes (`python.exe` non-zero) | Auto-restarted with exponential backoff            | < 5 seconds       |
| B | Windows reboot / power failure      | Task Scheduler relaunches the stack at next logon  | 30 s + boot time  |
| C | User clicks **Stop** in dashboard   | Bot process keeps running for everyone else        | n/a (no restart)  |

## Scenario A — crash / process death

Two layers of defense, both running 24/7:

### Layer 1: `bot-auto-restart.ps1` (sub-second PID watcher)

- Started by `start-home-bot.ps1 -NoWait` as a detached hidden loop.
- Watches the bot PID with a 2-second `WaitForExit(2000)` poll.
- On non-zero exit:
  1. Writes `logs/last_crash.json` and appends `logs/crash_history.jsonl`.
  2. Fires optional Discord/Slack webhook (`CRASH_NOTIFY_WEBHOOK`).
  3. Pops a Windows toast (`msg *`).
  4. Waits `BaseCooldownSec * 2^(crashes-1)` seconds (5 s → 10 s → 20 s → 40 s → 60 s cap).
  5. Re-launches the bot hidden, exactly like `start-home-bot.ps1 -NoWait`.
- **Rate cap:** `MaxRestartsPerHour=10`. After 10 restarts in a rolling 60-min
  window, the monitor HALTS and emits a toast. Prevents an infinite loop on a
  fatal misconfig (bad vault env, broken Python install).
- **Single instance:** `.home-bot-auto-restart.lock` ensures only one monitor
  ever runs. Stale locks (dead PID) are reclaimed.
- **Heartbeat:** `.home-bot-auto-restart.heartbeat` written every ~2 min. The
  supervisor (Layer 2) respawns the monitor if this file goes >5 min stale —
  this closes the gap that left the bot unwatched for hours on 2026-07-08.

### Layer 2: `home-stack-supervisor.ps1` (60 s HTTP health probe)

- Started by `home-stack-start-everything.ps1` as a hidden loop.
- Polls `http://127.0.0.1:7002/api/ping` every 60 s.
- After `BotFailThreshold=2` consecutive fails (~2 min), calls
  `Restart-BotComponent` which force-kills stale port listeners and launches
  a fresh `start-home-bot.ps1` console.
- **Crash-loop circuit breaker:** if the bot is recovered
  `MaxBotRestartsInWindow=5` times in `BotRestartWindowMin=5` minutes, recovery
  is HALTED and a toast + log entry is emitted. Manual intervention required.
- **Respawns Layer 1:** every tick, if `Test-AutoRestartMonitorAlive` returns
  false (heartbeat stale/missing) and the bot is healthy and the user has not
  stopped the stack, the supervisor re-spawns `bot-auto-restart.ps1`.

### Per-component cooldowns (avoid hammering)

| Component | Cooldown |
|-----------|----------|
| Bot       | 300 s    |
| Analyzer  | 600 s    |
| Tunnel    | 900 s    |
| Bridge    | 300 s    |

## Scenario B — reboot / power failure / Windows Update restart

Handled by **Task Scheduler**, not PowerShell loops (a loop dies with its
session; a scheduled task survives logoff).

### `DcfShowcaseBotAutostart` (installed by `register-bot-autostart.ps1`)

| Property                  | Value                                              |
|---------------------------|----------------------------------------------------|
| Trigger 1                 | At logon, +30 s delay (network/DNS warm-up)        |
| Trigger 2                 | Daily 04:00 local (safety net for overnight death) |
| Action                    | `cmd.exe /c scripts\start-showcase-bot.cmd`        |
| `start-showcase-bot.cmd`  | launches `home-stack-start-everything.ps1 -NoWait` |
| RunLevel                  | Highest (elevation needed for cloudflared)         |
| User                      | Current user (Interactive logon)                   |
| `AllowStartIfOnBatteries` | true                                               |
| `DontStopIfGoingOnBatteries` | true                                            |
| `StartWhenAvailable`      | true (catches missed triggers after sleep/resume)  |
| `RestartCount` / `RestartInterval` | 3 / 5 min (retries if the launch itself fails) |
| `MultipleInstances`       | IgnoreNew (no duplicate stacks)                    |

**Lock screen / no user logged in:** the AtLogon trigger fires only after a
user logs in. The daily 04:00 trigger fires regardless of login state, so
overnight reboots from Windows Update are still caught within 24 h. For true
"no one is logged in, ever" scenarios, the task can be switched to
`-LogonType S4U` (runs as SYSTEM without a saved password) — see
`register-bot-autostart.ps1` and re-run as admin if needed.

### `DoxedSupervisorWatchdog` (optional, recommended)

- A separate Task Scheduler entry that runs `home-stack-supervisor-watchdog.ps1`
  every 5 min and relaunches the supervisor if its PID file points at a dead
  process. Belt-and-suspenders for the supervisor itself.

### `DcfTidyOrphanShells` (housekeeping)

- Every 6 h, kills orphan `cmd.exe` / hidden `powershell.exe` shells while
  preserving `python.exe`, `cloudflared.exe`, and the supervisor/watchdog
  loops. Prevents the slow shell leak that previously degraded the host over
  weeks of unattended runs.

## Scenario C — user clicks **Stop** in the dashboard

> **CRITICAL CONTRACT:** The dashboard Stop button is a **per-user pause** for
> the relay, NOT a kill switch for the showcase bot.

There are two distinct "Stop" surfaces:

1. **Dashboard Stop button (web UI)** — sets `TradingAgentInstance.status =
   PAUSED` in the database. This stops the **relay on Railway** from copying
   trades for that one user. It does **NOT** stop the showcase bot's
   `python.exe` process — other live-copy users (or future ones) still get
   trades.

2. **Agent Hub Stop button (`home-stack-cmd-worker.ps1 stop-bot`)** — this is
   the user's explicit "kill the showcase bot" button on the local control
   panel. It writes `.home-stack-user-stopped` (via `Set-HomeStackUserStopped`)
   and force-kills `python.exe`.

### How we honor the stop

Every auto-restart path checks `Test-HomeStackUserStopped` (existence of
`.home-stack-user-stopped`) BEFORE relaunching:

| Path                              | Honors stop flag? |
|-----------------------------------|-------------------|
| `bot-auto-restart.ps1`            | Yes (after cooldown, before relaunch) |
| `home-stack-supervisor.ps1` bot   | Yes (`RECOVER bot skipped - user stopped stack`) |
| `home-stack-supervisor.ps1` analyzer | Yes                              |
| `home-stack-supervisor.ps1` tunnel   | Yes                              |
| `bridge-watchdog.ps1`             | Yes (bridge + tunnel respawn both gated) |
| `home-stack-supervisor.ps1` monitor respawn | Yes (no point watching a stopped bot) |

### We NEVER read the dashboard API to decide bot liveness

The supervisor and watchdogs poll **`http://127.0.0.1:7002/api/ping`**
(local liveness only) and the public tunnel URL. They never query
`doxxedcrypto.digital/api/...`, Railway, or Neon to decide whether to keep
the bot running. The dashboard DB status is the relay's concern, not the
showcase bot's.

The `/api/pause` endpoint on the bot itself (used by the dashboard Pause
button via `home-stack-cmd-worker.ps1 pause-trading`) only pauses **trade
execution inside the bot** — it does not kill `python.exe` and does not
affect other users.

## One-time install (run on the home PC, as admin)

```powershell
cd C:\Users\user\Desktop\Final Bots\doxedcryptofounder
powershell -ExecutionPolicy Bypass -File scripts\register-bot-autostart.ps1
powershell -ExecutionPolicy Bypass -File scripts\register-bridge-watchdog.ps1
powershell -ExecutionPolicy Bypass -File scripts\register-tidy-orphan-shells.ps1
# Optional belt-and-suspenders (supervisor respawn):
# powershell -ExecutionPolicy Bypass -File scripts\register-supervisor-watchdog.ps1
```

Verify:

```powershell
Get-ScheduledTask -TaskName 'DcfShowcaseBotAutostart','DcfTidyOrphanShells','DoxxedBridgeWatch' |
  Select-Object TaskName, State
```

## Operational status checks

```powershell
# Is the bot alive?
Get-NetTCPConnection -LocalPort 7002 -State Listen

# Is the auto-restart monitor alive?
Get-Content .home-bot-auto-restart.heartbeat   # ISO timestamp, < 5 min old = healthy

# Is the supervisor alive?
Get-Content .home-stack-supervisor.pid          # then Get-Process -Id <pid>

# Has the user stopped the stack?
Test-Path .home-stack-user-stopped              # True = Stop button active

# Recent restart history
Get-Content logs\bot_restarts.log -Tail 20
Get-Content .home-stack-supervisor.log -Tail 20
```

## Removing autostart (e.g. before a clean wipe)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\register-bot-autostart.ps1 -Uninstall
powershell -ExecutionPolicy Bypass -File scripts\register-bridge-watchdog.ps1 -Uninstall
Unregister-ScheduledTask -TaskName DcfTidyOrphanShells -Confirm:$false
```
