# BTC bot alerting runbook

The BTC stack alerts the owner once per incident through GitHub issues, so
nobody has to watch dashboards.

## How alerts reach Danish

- Every incident is one open GitHub issue in
  `danishhaiderau-maker/doxed-founders-website`.
- The repo owner watches the repository by default, so GitHub emails
  `danishhaiderau-maker` (and pushes to GitHub Mobile) on each new issue and
  comment. Keep **Watch -> All activity** (or at least Issues) on this repo
  and Email enabled under **Settings -> Notifications**.
- GitHub never notifies you about your own actions. Therefore every
  notifying write (open issue, alert comment, recovery comment) is posted by
  `github-actions[bot]`: the Fly monitor does so directly, and the laptop
  watchdog dispatches `.github/workflows/laptop-incident-relay.yml`. Only
  silent writes (body edits, close) use the owner's `gh` login.
- The issue body is edited in place. A comment is only added when a
  condition first alerts, re-alerts after its interval, or recovers. The
  issue closes automatically after two consecutive clean checks.

| Label | Source | Cadence |
|---|---|---|
| `fly-monitor-incident` | `.github/workflows/fly-bot-monitor.yml` -> `scripts/fly_monitor_run.py` | every 15 min |
| `laptop-chain-incident` | `DoxxedLaptopChainSupervisor` task -> `scripts/laptop_chain_incident.py` | every 5 min |

## Alert rules

Fly monitor (`scripts/fly_monitor_rules.py`, dedup in `scripts/fly_monitor_alerts.py`):

| Key | Fires when | Re-alert |
|---|---|---|
| `safety` | paper-only / disarmed contract broken | 1h |
| `unreachable`, `process_down` | bot down for 2 runs and 10 min | 6h |
| `paper_paused` | paper paused >= 2h (any owner) | 6h |
| `deploy_stuck` | pause owned by `DEPLOY_MAINTENANCE` > 60 min | 3h |
| `disk_warn` | Fly volume >= 70% used | 12h |
| `disk_critical` | Fly volume >= 85% used (URGENT) | 2h |
| `eval_stale` | no completed evaluation > 20 min while entries are eligible, or AI scheduler not polling > 10 min | 6h |
| `ai_stale` | no AI call > max(45 min, 3x bot threshold) while entries are eligible | 6h |
| `laptop_silent` | `LAPTOP_CHAIN_HEARTBEAT` repo variable older than 2h | 12h |
| `transfer_lag` | segment shipper stale/erroring/> 36 segments un-ACKed, or legacy ACK > 3h | informational until `FLY_MONITOR_SEGMENTS_LIVE=1` |
| `not_ready`, `revision_drift`, `registry_drift`, `monitor_error` | unchanged from PR #185 | 6-12h |

Cadence rules are skipped while paper is paused (covered by `paper_paused`)
and during the first 20 minutes after boot. Transitional rules are
suppressed for up to 90 minutes during a guarded deploy; `safety`, disk,
`deploy_stuck` and `laptop_silent` never are.

Laptop watchdog (`scripts/laptop_chain_incident.py`):

| Key | Fires when |
|---|---|
| `analyzer_stale` | analyzer has not COMPLETED (`lastSuccessAt`) for > 2h |
| `watcher_dead` | `laptop-chain-monitor.ps1` reports `WATCHER_DEAD` for 2 ticks and 10 min |
| `monitor_stale` | `alerts\active-alerts.json` not refreshed for > 30 min |

A dead supervisor cannot report itself; the watchdog refreshes the
`LAPTOP_CHAIN_HEARTBEAT` variable every 15 minutes and the Fly monitor raises
`laptop_silent` when it goes stale (this also fires when the laptop sleeps).

## Disk metrics

`GET https://doxed-btc-bot.fly.dev/health` returns a lock-free `volume`
block (cached 30s): `total_bytes`, `used_bytes`, `free_bytes`, `used_pct`,
`growth_bytes_per_hour` and `hours_to_full` (after 30 min of in-process
samples), plus `volume.transfer` with the segment-shipper status and the
legacy `sync_ack.json` age.

## Proving the channel

- Fly: run **Monitor Fly BTC bot** via *Run workflow* with `test_alert`
  checked. The run fails red and opens/comments the `fly-monitor-incident`
  issue; the next two normal runs close it.
- Laptop: `python scripts/laptop_chain_incident.py --test-alert` opens a
  `laptop-chain-incident` issue; the next two supervisor ticks close it
  (unless a real laptop incident is also active).
- `python scripts/laptop_chain_incident.py --dry-run` evaluates without
  touching GitHub or local state.
