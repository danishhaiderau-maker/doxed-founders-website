# System health runbook

One verdict for the whole BTC V3.1 stack, built only from APIs and files
(never screenshots). Every check tests **positive progress**: a live process
or an HTTP 200 is not health (AGENTS.md, "Repair-first monitoring").

Alarms and diagnostics only. Nothing here pauses, resumes, deploys, arms,
closes or prunes. Repairs go through the normal guarded workflow (WALL slot,
`fly-bot-deploy.yml`), never from the watcher.

## Use it

| What | How |
|--|--|
| Human CLI (live) | `python scripts/system_health.py` (exit 0 GREEN, 1 AMBER, 2 RED) |
| Full JSON (live) | `python scripts/system_health.py --json` |
| Last published verdict | `python scripts/system_health.py --latest` |
| Agent helper | `import health_client; snap = health_client.snapshot(); health_client.failing(snap)` |
| Local endpoint | `GET http://127.0.0.1:9011/api/system-health` (`?live=1` for a fresh read-only evaluation) |
| Alarm events (raw) | `GET http://127.0.0.1:9011/api/system-health/alarms?limit=50` |
| Alert history (one entry per alert) | `GET http://127.0.0.1:9011/api/system-health/alerts`, `health_client.alerts()` |
| Alerts page, analyzer | `http://127.0.0.1:9001/alerts` (JSON: `/api/system-health/alerts`) |
| Alerts page, Fly | `https://doxed-btc-bot.fly.dev/alerts`, plus the Alerts section on the main dashboard |
| Agent feed | `insights_client.snapshot()["active_alerts"]` and `["components"]["alerts"]`, or `GET 127.0.0.1:9001/api/insights` |
| Fly endpoint (after the batched deploy) | `GET https://doxed-btc-bot.fly.dev/api/system-health` |

Files under `C:\DoxxedCrypto\laptop-chain\health\`:

- `system-health-latest.json`: the last full report (verdict, per-check detail, open alarms).
- `alarms.jsonl`: append-only alarm log (`OPEN`, `STILL_RED`, `RECOVERED`, `AMBER`, `AMBER_CLEAR`).
- `verdicts-YYYYMM.jsonl`: one line per tick.
- `health-state.json`: progress memory (last success times, alarm state, Fly alarm-sync cursor).
- `neon.env`: optional, never committed: `NEON_API_KEY=` and `NEON_PROJECT_ID=` for `neon.usage`.

## Watcher and alarms

- **Single watcher.** `DoxxedLaptopChainSupervisor` runs `system_health.py --tick`
  every 5 minutes once its checkout (`C:\DoxxedCrypto\v2c`) carries this code.
  Until then, the interim `DoxxedSystemHealthWatcher` task runs the same tick
  from a pinned checkout and defers automatically when the supervisor has it.
  A lock file plus a 4-minute minimum interval guarantee one evaluation per
  window, whoever calls it. To remove the interim task, run
  `scripts\register-system-health-task.ps1 -Unregister`.
- **Edge-triggered.** A RED check opens one alarm, after 2 consecutive bad ticks
  for flap-prone checks (`fly.process`, `analyzer.api`, `railway.api`,
  `trading.orphans`). It stays quiet while open, re-notifies every 6h
  (`STILL_RED`), and sends `RECOVERED` when the check is no longer RED. AMBER
  transitions are logged only.
- **Channels.** The dashboards' **Alerts** section is the primary channel:
  Danish reviews it whenever he checks. There are no Telegram, Discord or
  webhook channels, by owner decision.
  - **Alerts section** on the analyzer dashboard (`:9001/alerts`, linked first in
    its nav) and on the Fly dashboard (a section on `/` plus the full `/alerts`
    page). Each entry shows when it started (AEST, UTC+10, with UTC), its
    severity (RED / AMBER, or RECOVERED once cleared), the check in plain words,
    what the watcher saw, the likely cause with a runbook link, and when it
    cleared and how long it lasted. Active alerts are listed first.
  - Windows toast on the laptop for RED alarms (open, still red, recovered).
  - Append-only `alarms.jsonl`, the source of the Alerts section.
  - Red/amber banner on both dashboards while anything is not GREEN.
  - A GitHub issue with the `laptop-chain-incident` label while any RED alarm is open.
- **How Fly gets the history.** The analyzer reads `alarms.jsonl` directly. Fly
  has no access to the laptop, so every watcher tick posts the banner summary
  plus the alarm events Fly does not hold yet to `POST /api/system-health/report`
  (oldest first, at most 120 per tick, cursor `fly_alarm_sync` in
  `health-state.json`). Fly keeps them in memory, bounded to the last 30 days or
  2,000 events, and refills from the laptop within a tick or two after a restart.
- **Neon slot.** Set `NEON_API_KEY` and `NEON_PROJECT_ID` (read-only key) in the
  watcher's environment or in `C:\DoxxedCrypto\laptop-chain\health\neon.env`
  (outside git) to enable `neon.usage`. The optional
  `NEON_EGRESS_BUDGET_BYTES_PER_HOUR` defaults to 200 MiB/h; optional `NEON_ORG_ID`
  skips the org lookup.

## Checks and fixes

Each check reports status, observed value, threshold, `last_good_at`, a hint,
and the anchor below.

<a id="fly-process"></a>
### fly.process
Fly `/api/status` and `/health` answer and report `process_alive`. RED after 2 unreachable ticks.
Fix: check `flyctl status -a doxed-btc-bot` and the Fly logs. If the machine is wedged,
use the guarded `fly-bot-deploy.yml` restart mode. Never use ad-hoc `flyctl deploy`.

<a id="fly-paused"></a>
### fly.paused
Paper must be running. A `DEPLOY_MAINTENANCE` pause is AMBER for up to 45 minutes
(guarded deploy). Any other owner, or a longer pause, is RED.
Fix: identify the owner from `/api/status`. For a stuck deploy boundary, check the
workflow run (`gh run list --workflow fly-bot-deploy.yml`); its failure path resumes paper.
For `THREAD_CRASH` or `SAFETY`, read `runtime_incident_history` before resuming through
the authenticated `/api/resume`.

<a id="fly-revision"></a>
### fly.revision
Fly `git_rev` compared with `origin/master`. A Fly revision behind master is normal
while deploys queue (see WALL). It turns AMBER when the last guarded deploy did not
conclude `success`.
Fix: read the failed run and requeue it in `diagnostics/WALL-STATUS-FLY.md`.

<a id="ai-success"></a>
### ai.success
Age of the last SUCCESSFUL model response. Attempts do not count: on 2026-10-01
`ai_age` looked normal while every DeepSeek call timed out from 18:56Z. AMBER at 6
minutes, RED at 12 minutes while paper runs.
Sources, newest wins:
- Fly `ai_provider_health.last_success_ts` (once c609373e ships);
- `/api/state.ai_history` entries with `ai_error=false`;
- the mirror's `decisions_3factor.csv` AI rows.

Fix: read the `ai_errors_3factor.csv` tail and run the read-only DeepSeek egress probe
(`scripts/fly-deepseek-egress-probe.py` via the inspect mode). Check the key, quota and
provider status.

<a id="ai-failures"></a>
### ai.failures
Consecutive failed AI calls (`ai_history` trailing errors, or provider
`consecutive_failures`). RED at 3 or more. Fix: same as `ai.success`.

<a id="ai-decision_mix"></a>
### ai.decision_mix
Over the last 6 hours, with at least 20 successful responses, the decisions must not be
100% NO_TRADE or neutral. AMBER only. A quiet market can do this, but it is also the
signature of a prompt or parse regression or of dead inputs.
Fix: check the `ai_input_health` dead fields and the raw `comment` in `ai_history`.

<a id="ai-served_model"></a>
### ai.served_model
The response's `model` field (`last_model_echo`) must equal the configured model, and must
not have changed within the last 6 hours. AMBER only. On 2026-10-01 DeepSeek retired
`deepseek-v4-flash` and silently served those requests as `deepseek-flash`
(DeepSeek-V4.1-Flash). The observed value includes the response `system_fingerprint`.
Fix: compare `GET https://api.deepseek.com/models` against `DEEPSEEK_DEFAULT_MODEL` in `bot.py`,
pin the served id, and confirm the analyzer splits the cohort at the switchover annotation.
Never start a new epoch or change a tile policy signature for a model change.

<a id="deepseek-balance"></a>
### deepseek.balance
Read-only `GET https://api.deepseek.com/user/balance` (USD total). AMBER under $5, RED under
$1 or when `is_available=false`. Fly exposes the same value as
`ai_provider_health.deepseek_balance` (preferred when fresh); otherwise the watcher queries
it with `DEEPSEEK_API_KEY` from the environment or vault. The key is only sent as a request
header and is never stored, logged or printed. At $0 every AI call fails with HTTP 402.
Fix: top up the DeepSeek account.

<a id="trading-orders"></a>
### trading.orders
Orders per ON tile from `tile_route_counts` deltas, live positions, orders and trades,
plus the mirror's `trades_3factor.csv` and `expired_orders_3factor.csv`.
- RED: no ON tile has placed an order for 3 hours while paper runs.
- AMBER: an individual ON tile has been quiet for 6 hours or more.

The observed value lists each tile's last order and its 48-hour count.
Fix: compare the `shared_ai_lane_counters[lane].reasons` admission rejections and check
`ai.success`. Check `new_entry_block_reason` and `trading_block_reason` in `/api/state`.

<a id="trading-orphans"></a>
### trading.orphans
`orphan_order_ids` and `orphan_position_ids` must be empty. RED after 2 ticks.
Fix: reconcile through the maintenance reconcile path. Never force-close real exposure.

<a id="trading-lifecycle"></a>
### trading.lifecycle
No trade id may be both EXPIRED and FILLED (the fill-vs-TTL race behind #253/#255).
- RED: a contradiction within the last 2 hours.
- AMBER: a contradiction within 24 hours, or any `unlinked_lifecycle_rows`.

Fix: quarantine the affected ids for the analyzer and investigate fill-thread latency.
Do not rewrite the evidence.

<a id="ws-ticks"></a>
### ws.ticks
Bitfinex trade-tick age (`ws_age`) and `ws_progressing`. AMBER at 60 seconds, RED at
120 seconds or when not progressing.
Fix: check `ws_last_disconnect_reason` and `ws_reconnect_count` in `/api/state`. A
persistent stall needs a guarded restart.

<a id="shipper-progress"></a>
### shipper.progress
Segment shipper on the Fly volume.
- RED: no new segment for 9 minutes while unshipped bytes or `last_error` are present
  (the cycle is 300 seconds).
- RED: the shipper status itself is older than 10 minutes.
- AMBER: no new segment for 6 minutes, any `last_error`, or a backlog of 256 MB or more.

The 2026-10-01 PLAN_RACE stall (no segment 18:03Z to 19:01Z) alarms at 18:15Z under
this rule. The legacy monitor only alarmed at 18:35Z.
Fix: read `/health` `volume.transfer.last_error` and `racing_paths`. The fix is code
(see #250, #251, #257), shipped through the guarded deploy.

<a id="laptop-pull_ack"></a>
### laptop.pull_ack
Fly published `shipped_seq`, compared with the laptop's applied seq and with Fly's
`laptop_acked_seq`. RED when any of these holds:
- no finished pull for 15 minutes;
- applied is more than 30 seqs behind published;
- applied has been behind published, without advancing, for 15 minutes;
- Fly's ACK is more than 10 seqs behind applied;
- Fly's ACK has trailed applied without advancing for 15 minutes.

Fly polls ACKs at most every `RESEARCH_SEGMENTS_ACK_POLL_SECONDS` (300s), between
idle-priority shipper cycles. While it ships every few minutes, `laptop_acked_seq`
normally trails applied by one or two seqs for up to roughly 10 minutes. That is not
a fault as long as it keeps advancing (false RED of 2026-10-02, replayed in
`test_replay_fly_ack_chasing_applied_20261002_is_not_red`). To tell a stuck Fly poll
from a failing laptop POST, compare `/api/research-segments/v2/head`
`laptop_acked.through_seq` with `/health` `volume.transfer.laptop_acked_seq`.

Fix: check `segment-pull.status.json`, `fly-mirror-segments\.puller\status.json` and
`logs\segment-pull-loop-*.log`. The supervisor restarts a dead loop.

<a id="laptop-supervisor"></a>
### laptop.supervisor
The last `DoxxedLaptopChainSupervisor` TICK must be within 15 minutes.
Fix: `Get-ScheduledTask DoxxedLaptopChainSupervisor`, then
`Start-ScheduledTask DoxxedLaptopChainSupervisor`. Check that the laptop is not asleep.

<a id="analyzer-generation"></a>
### analyzer.generation
Age of the last completed analyzer generation (`analyzer-run.status.json`). AMBER at
45 minutes, RED at 90 minutes.
Fix: read `logs\analyzer-once-*.err.log` and `segment-analyzer-cycle.status.json`
(phase). A failed cycle is retried on the next supervisor tick.

<a id="analyzer-api"></a>
### analyzer.api
The :9001 `/api/health` must answer with `ok`, sync match and revision parity. Down is
AMBER for up to 20 minutes while a cycle replaces the dashboard. Otherwise it is RED
after 2 ticks.
Fix: the supervisor restarts a down dashboard (`run-analyzer-once.ps1 -EnsureDashboardOnly`).

<a id="analyzer-cycle"></a>
### analyzer.cycle
- Running cycle duration: AMBER at 45 minutes, RED at 90 minutes.
- The last cycle's exit code.
- `v2c` HEAD must contain the Fly revision, which is the same ancestry rule as the cycle's
  `ANALYZER_REVISION_MISMATCH` guard. Laptop-only merges on top of the deployed revision
  are GREEN. `v2c-auto-ff` follows successful deploys.
- Exit 3 means promotion was refused. `FLY_UNSHIPPED_BYTES` > 32 MB right after a deploy
  restart is transient; the next scheduled cycle retries.

Fix: check `v2c-auto-ff.receipts.jsonl` (refusals) and the cycle phase log.

<a id="analyzer-studies"></a>
### analyzer.studies
Reads `analyzer_generation_receipt.json` and `analyzer_integrity_report.json` from the
published analyzer reports. An analyzer pass exits 0 even when a study throws, so this is
the only check that sees it.
- RED: a required study is ERROR/MISSING/INVALID (for example Best Policy Research), or
  integrity is `INVALID` or `UNCHECKED` (the policy cycle failed, so lifecycle and
  order-resolution integrity were never verified), or the protection replay kept fewer
  than half of the eligible events.
- AMBER: an optional study failed, an input is BLOCKED/DEGRADED, the protection replay is
  truncated, or there is no receipt for the current generation.
Fix: read the receipt `reasons` and the `analyzer-once-*.out.log` line for the study.

<a id="analyzer-data_health"></a>
### analyzer.data_health
`data_health_report.json` per-stream verdicts, judged against the mirror head (#293).
AMBER when the mirror is STALE or any stream is not OK/WARMUP. Reports without
`stream_status_basis` used wall-clock staleness (always STALE) and are SKIP.

<a id="storage-tier_a"></a>
### storage.tier_a
Per-dataset Tier A promotion health from `bot-data-retention\status.json` `tier_a`.
RED for rows dated outside 2020-2100 (the old 1970 partitions), unverified Parquet,
a closed day still unpromoted 24 h after settling, or undated rows remaining after
the one-shot backfill. AMBER while rows are undated or closed days are awaiting
promotion. Repair: `bot_data_retention.py --tier-a-backfill --dry-run`, then the
enforce run between analyzer cycles. It holds the cycle mutex and the shadow-root lock.

<a id="ledger-reconciliation"></a>
### ledger.reconciliation
One trade-count and PnL reconciliation per current tile across Fly `/api/state`
`trades`, the mirror ledger (`trades_3factor.csv`) and the analyzer cohort, bounded to
the analyzer's `source_data_through` watermark (`ledger_reconciliation.json`).
The analyzer side is RED when a current-epoch tile trade leaves the cohort without a
quarantine reason. AMBER for one Fly close missing from the mirror, one mirror
trade absent inside Fly's listed window, or any PnL difference above $0.01; RED
for two or more missing ids. Canonical Win % is wins (exact terminal-cost net PnL
> 0) over all closed trades. The CSV's cent-rounded `net_pnl_usd` turns sub-cent
winners into 0.00, so a Win % computed from it reads low (`cents_display_drift`).

<a id="exports-freshness"></a>
### exports.freshness
Freshness of `C:\DoxxedCrypto\analyzer-exports\latest`. When `analyzer_client` is
present, its status function is used (worker eed92197). The check is SKIP until the
directory exists, and AMBER above 90 minutes.

<a id="streams-coverage"></a>
### streams.coverage
Fly `collection.research_coverage.collection_health` (for example
`MULTIVERSE_TAPE_SOURCE_UNAVAILABLE` or `COLLECTOR_MATURATION_WORKER_STALLED`), the
cross-venue tape status, the XVL per-second evaluator (`collection.xvl_evaluator`:
`STALE` = ticks stopped, `DEGRADED` = shadow rows failing to write), AI dead
inputs, and per-stream age and coverage from
`data_streams` once worker d9f889db exposes it. AMBER only, because research
collection does not block trading.

<a id="dashboards-parity"></a>
### dashboards.parity
The following must agree:
- the Fly dashboard API `active_tiles`;
- the `research_lane_enabled` toggles;
- the registry roster (`combo_pathway_config.ACTIVE_TILE_ORDER` in the analyzer checkout);
- the analyzer epoch parity;
- per-lane open counts (positions compared with tile routes).

A truth label that contradicts evidence, for example "DeepSeek OK" while there has been
no success for 12 minutes, is RED immediately. Other contradictions are AMBER, then RED
after 30 minutes.

<a id="railway-relay"></a>
### railway.relay
The relay must be PAUSED or disarmed (`relayArmedAt` null), with a healthy executor
heartbeat and no reconciliation or position-mismatch alert. Armed or mismatched is RED:
verify on Railway immediately. Arming is the user's decision only.

<a id="railway-api"></a>
### railway.api
Railway API `/health`: api ok, database ok. A database error is RED, because it means
Neon is unreachable from Railway.

<a id="neon-usage"></a>
### neon.usage
Neon month-to-date usage from the usage-based consumption API
(`GET /api/v2/consumption_history/v2/projects`, cached 15 min; errors cached 5 min).
The project object's `data_transfer_bytes` / `compute_time_seconds` counters stay 0 on
Launch (`launch_v3`) plans, so they are not read. Completed days come from `daily`
buckets, today and the rate from `hourly` buckets. Reported: public egress (vs the 500 GB
Launch allowance), the egress of the latest complete hour, compute CU-hours, storage and
PITR GB-months, and an estimated cost at Launch list prices (compute $0.106/CU-h,
storage $0.35, PITR $0.20, snapshots $0.09 per GB-month, egress over 500 GB $0.10/GB;
extra branches not priced).

- GREEN: non-zero usage reported and the last complete hour's egress is within
  `NEON_EGRESS_BUDGET_BYTES_PER_HOUR` (default 200 MiB/h).
- AMBER: hourly egress over budget (look for a polling loop or unbounded query; see
  #261), no complete hour reported yet, no/all-zero consumption, or any API error.
  `HTTP_401` is a rejected key, `HTTP_403` means the plan has no consumption API, and
  `HTTP_404` means the key cannot see the org. Zeros never count as GREEN.
- SKIP until `NEON_API_KEY` and `NEON_PROJECT_ID` are set (environment or
  `laptop-chain\health\neon.env`); the observed text names the missing variable.
  `org_id` is required by the API. It is read from the project unless `NEON_ORG_ID` is set.

<a id="bitfinex-exposure"></a>
### bitfinex.exposure
These must hold: `live_armed=false`, `bitfinex_live_enabled=false`,
`force_paper_mode=true`, exchange position quantity 0 and 0 exchange orders. Any
violation is RED.
**Never force-close.** Escalate to Danish. Do not arm.

<a id="proof-latest"></a>
### proof.latest
The latest unattended-proof row (`unattended_proof.py`, every 30 minutes). AMBER on a
FAIL row, or when no row has appeared for 45 minutes. The matching health check carries
the cause.

<a id="disk-space"></a>
### disk.space
- Laptop free space: AMBER below 20 GB, RED below 5 GB.
- Fly volume free space: AMBER below 8 GB, RED below 4 GB (the shipper keeps a 4 GB floor).
- Segment store against its cap: AMBER at 80%, RED at 95%.

Pruning is custody-gated (approved by Danish 2026-10-02): check `storage.retention`
first. Never delete files by hand; see `docs/runbooks/DATA-RETENTION.md`.

<a id="storage-retention"></a>
### storage.retention
Laptop bot data against the 50 GB cap (`bot_data_retention.py`, run after every
analyzer cycle), from `C:\DoxxedCrypto\bot-data-retention\last-run.json`, plus the
Fly prune state from `/health` `volume.transfer`.
- AMBER at 80% of the cap, RED at 90%; AMBER when the last run is older than 3 h,
  RED after 12 h; AMBER at >= 80% while deletion is denied (`deny_reasons`).
- RED whenever Fly `pruned_through_seq` is beyond `custody_through_seq` (must never happen).

Fix the deny reason (parity not GREEN, no verified archive snapshot, Fly checkpoint
unreachable) rather than forcing deletion. Unmanaged scratch outside the managed roots
(perf snapshots, `tmp\`) is not counted and is never deleted automatically.

<a id="archive-freshness"></a>
### archive.freshness
Age of the newest immutable analysis-archive snapshot
(`C:\DoxxedCrypto\analysis-archive\index.jsonl`). One is written after every analyzer
generation. AMBER after 3 h, RED after 12 h. Without a fresh verified snapshot laptop
retention deletes nothing and Fly pruning stops advancing (fail closed).

<a id="watcher-stale"></a>
### watcher.stale
Synthesized by the endpoint and `health_client` when the published verdict is older
than 15 minutes. The watcher itself is not ticking: check the supervisor or interim
task and `logs\system-health-*.log`.
