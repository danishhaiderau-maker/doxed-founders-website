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
| Self-diagnosis, AI scorecard, edges, digest | `http://127.0.0.1:9021/` and `/api/selfaware/*`, `health_client.self_aware()`, `scripts/self_aware_client.py`; alarms appear here as `selfaware.*`. See [SELF_AWARE_RUNBOOK.md](SELF_AWARE_RUNBOOK.md) |

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

## Fly monitor heartbeat

The scheduled GitHub monitor (`fly-bot-monitor.yml`) writes the
`FLY_MONITOR_HEARTBEAT` repository variable at the end of every run once the
`FLY_MONITOR_VARIABLES_TOKEN` secret exists. A laptop check can read it with
`gh variable get FLY_MONITOR_HEARTBEAT` (JSON; parse `at`, stale after 45
minutes) or, without the secret, with
`gh run list --workflow fly-bot-monitor.yml --limit 1 --json createdAt,conclusion`.
The monitor in turn watches this watcher through Fly `/api/system-health`
(`laptop_health_silent` when `age_sec` > 1800 or `stale`). Details:
`docs/BOT_ALERTING_RUNBOOK.md`, "Monitor heartbeat".

## CI and [skip ci]

`.github/workflows/laptop-tests.yml` runs the watcher, incident, supervisor
(PowerShell, on `windows-latest`) and segment-puller tests on every pull
request that touches them, and on master pushes. It is read-only: no secrets,
no deploy steps.

GitHub skips `pull_request` workflows when the PR **head commit** message
contains `[skip ci]`, so a PR whose commits carry it merges untested.

- Never put `[skip ci]` in commits pushed to a PR branch.
- Put `[skip ci]` only in the **squash-merge subject**. The resulting master
  push then does not trigger `fly-bot-deploy.yml`, and the v2c laptop-only
  follow path accepts it.

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
signature of a prompt or parse regression or of dead inputs. Below 20 responses the
check is SKIP (not rated), never GREEN.
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
- AMBER "toggle state unavailable" when neither `/api/state` nor the runtime snapshot
  carries `research_lane_enabled` (missing toggles are not "all OFF").

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
120 seconds or when not progressing. `ws_progressing` missing from both Fly
`strategy_progress` and the runtime snapshot is AMBER (never assumed true).
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

Applied comes from `.puller\status.json`, then `segment-pull.status.json`, then
`.puller\state.json` (a lock refusal rewrites the first two without seqs). Applied
unknown in all three is AMBER, RED after 30 minutes; it is never GREEN.
`segment-pull.status.json` `exitCode != 0` is AMBER, and RED once it has failed for
more than one consecutive pull (by `iteration`) and 10 minutes. A lock refusal
("another puller run holds the shadow-root lock") while applied keeps advancing (the
analyzer cycle's own puller) stays AMBER.

Fix: check `segment-pull.status.json`, `fly-mirror-segments\.puller\status.json` and
`logs\segment-pull-loop-*.log`. The supervisor restarts a dead loop.

<a id="laptop-supervisor"></a>
### laptop.supervisor
Verified from the process table, not the log: one cached (60 s) PowerShell probe reads
`Get-ScheduledTask`/`Get-ScheduledTaskInfo DoxxedLaptopChainSupervisor` and the
`Win32_Process` command lines.
- RED: task missing or disabled, last run older than 15 minutes, or no
  `research-segment-pull-loop.ps1` process for 10 minutes (AMBER before that).
- AMBER: a last task result other than 0 / running, or the probe itself failed (then the
  log TICK is shown; a stale TICK is still RED).

Fix: `Get-ScheduledTask DoxxedLaptopChainSupervisor`, then
`Start-ScheduledTask DoxxedLaptopChainSupervisor`. Check that the laptop is not asleep.

<a id="laptop-legacy_ack_watcher"></a>
### laptop.legacy_ack_watcher
The data-sync ACK watcher was retired (segments replaced it), but
`laptop-chain\laptop-ack-watcher.status.json` stayed behind, frozen since 2026-09-30.
A status file whose `lastPollAt` is older than 1 h is AMBER orphan state. Fix: archive the
file, or create `laptop-chain\laptop-ack-watcher.RETIRED` to record the retirement (GREEN).

<a id="selfaware-engine"></a>
### selfaware.engine
The self-aware keeper's real progress from `:9021/api/selfaware/health`, not a ping.
- RED: unreachable for 15 minutes (AMBER before), `generated_at` or the `diagnose` job's
  `last_ok` older than 20 minutes.
- AMBER: any `engine.jobs.<job>.last_ok` older than 3x its `engine.cadence_sec`, a job
  that never succeeded after 3 cadences of uptime, or no jobs reported.

Fix: `Get-ScheduledTask DoxxedSelfAware`, the keeper log, then `docs/SELF_AWARE_RUNBOOK.md`.

<a id="watcher-sources"></a>
### watcher.sources
Every input the watcher fetches (Fly `/api/status`, `/health`, `/api/state`, :9001
`/api/health`, `/api/status`, `/api/streams/health`, :9021, Railway). A source failing
(HTTP_429, timeout, token missing) for more than 15 minutes is AMBER, and any check that
SKIPped because of it (for example `trading.orders` on `/api/state` HTTP_429) is turned
AMBER with the fetch error. A SKIP is never a silent pass for longer than that.
Fix: the Fly API is rate-limited at 60/min per IP shared by all laptop jobs; check for a
polling loop, the admin token in the vault, or the endpoint itself.

<a id="analyzer-generation"></a>
### analyzer.generation
Age of the last completed analyzer generation (`analyzer-run.status.json`). AMBER at
45 minutes, RED at 90 minutes.
Fix: read `logs\analyzer-once-*.err.log` and `segment-analyzer-cycle.status.json`
(phase). A failed cycle is retried on the next supervisor tick.

<a id="analyzer-api"></a>
### analyzer.api
The :9001 `/api/health` and `/api/status` must both answer `ok` (health ok with status
not ok is AMBER). Down is AMBER for up to 20 minutes while a cycle replaces the
dashboard. Otherwise it is RED after 2 ticks. Also AMBER (values in `observed_fields`):
- `source_revision_parity` / `generation_freshness.revision_parity` not `MATCH`
  (strings are parsed; missing is not a match);
- `generation_freshness.generation_revision` (`generation_rev`) not a prefix match of Fly
  `/health.source_git_rev` (`fly_rev`): the generation predates the deploy;
- `upstream_sync_id` or `analyzer_sync_id` different from Fly `analyzer_sync_id`
  (`sync_id_match=false`): stale upstream identity;
- the mirror sync receipt was more than 30 minutes old when the analysis run started, or
  is more than 60 minutes old on the wall clock (`receipt_age_sec`). The dashboard's own
  `mirror_sync_receipt_freshness` turns STALE after 10 minutes, so it is not used.

Fix: the supervisor restarts a down dashboard (`run-analyzer-once.ps1 -EnsureDashboardOnly`).

<a id="analyzer-reports"></a>
### analyzer.reports
:9001 `/api/status.required_reports_ok` and `ok`. False is AMBER on first sight and RED
once it lasts 45 minutes or is seen on a second generation (`generated_at`). The observed
text lists `required_report_failures` with each `generation_error` (2026-10-02:
`best_policy_research_report.json`, `safe_policy_genome_v3_report.json` on
`POLICY_ID_SPEC_COLLISION`). A missing field or unreachable `/api/status` is AMBER.
Fix: read the analyzer-once log for the failing study; the analyzer pass exiting 0 does
not mean its reports were produced.

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
collection does not block trading. Nothing reported at all is AMBER ("coverage
unknown"), never GREEN.

<a id="streams-analysed_freshness"></a>
### streams.analysed_freshness
:9001 `/api/streams/health`. AMBER when a stream the analyzer uses (`analyzer_usage` not
`HEALTH_ONLY`) has `content_lag_sec` (content end vs its export) over 60 minutes, or is
`STALE` while `continuous` (2026-10-02: `research_events_v22.jsonl`), or when
`not_fully_analysed` is non-empty. An unreachable endpoint is AMBER.
Fix: check the Fly collector for that stream and the laptop mirror; stale content must
not be reported as analysed evidence.

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
verify on Railway immediately. Arming is the user's decision only. A relay status with
`reconciliation: null` is AMBER (reconciliation unverified).

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
violation is RED. An unreported exchange quantity is GREEN only while everything is
explicitly disarmed (Fly flags above and the relay PAUSED/DISARMED), and the observed
text then says "not probed (disarmed ...)"; otherwise it is AMBER.
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
than 15 minutes; it is RED and makes the served verdict RED. `laptop_chain_incident.py`
raises the matching `system_health_stale` incident (also for a missing report). The
watcher itself is not ticking: check the supervisor or interim task and
`logs\system-health-*.log`. The interim `DoxxedSystemHealthWatcher` task defers only
while the supervisor task ran and a verdict was published within 15 minutes; otherwise
it ticks itself (`TAKEOVER` in its log).

`GET :9011/api/system-health?live=1` returns the newest cached verdict at once with
`age_sec`, `generated_at` and `refresh: started|running`, and starts at most one
background re-evaluation. Only when the cache is older than 120 s does it wait (up to
10 s) for that refresh (`refresh: completed`).

<a id="analyzer-parity_checker"></a>
### analyzer.parity_checker
`C:\DoxxedCrypto\fly-mirror-segments\parity-latest.json`, written by
`research_segment_fly_parity.py` after a parity pull. RED when the verdict is not GREEN
(missing, sealed mismatch or corrupt SQLite) or the report is older than 8 h; AMBER after
3 h, or while a running scan has held the puller lock for more than 15 minutes.
`timing` shows `lock_held_sec`, `hashed` and `hash_cache_hits`: snapshot digests are
cached by size and mtime, so a slow scan with few cache hits means the cache was reset or
the tree churned.

<a id="laptop-puller_lock"></a>
### laptop.puller_lock
Whether the shadow-root lock is starving the segment pull. Only counted while the
puller's own status shows `LOCK_BUSY` naming the same holder (a crashed holder's sidecar
file is ignored). AMBER after 20 minutes, RED after 45. Also shows the last OK run's
`run_seconds` against `max_run_seconds`. Do not kill a parity scan by hand without
preserving its log; it releases the lock when it exits.

<a id="laptop-chain_monitor"></a>
### laptop.chain_monitor
Active alerts from `laptop-chain-monitor.state.json` (previously toast/event-log only):
critical is RED, warning is AMBER, and a monitor that has not checked for 30 minutes is
AMBER. Fix the underlying alert code; see `alerts\alerts-*.jsonl` for its history.

<a id="laptop-incident_relay"></a>
### laptop.incident_relay
`laptop-chain-incident.state.json`: AMBER when the incident watchdog heartbeat is older
than 30 minutes; RED when deploy-maintenance suppression has lasted more than 90 minutes
(it must cap out, otherwise real incidents are muted).

<a id="watcher-interim"></a>
### watcher.interim
`health\interim-tick.status.json`, written by every `system-health-tick.ps1 -Interim`
run with its `DEFERRED`/`TAKEOVER` decision. AMBER when older than 20 minutes: the
`DoxxedSystemHealthWatcher` task stopped and nobody watches the watcher.

<a id="watcher-delivery"></a>
### watcher.delivery
Result of the previous tick's Fly banner push. AMBER after 2 consecutive failures, RED
after 12 (about an hour): the Fly dashboard banner and alarm history go stale.

<a id="watcher-fly_copy"></a>
### watcher.fly_copy
Lag between the Fly-published copy (`GET /api/system-health`) and the previous local
verdict. AMBER above 15 minutes, RED above 1 hour.

<a id="watcher-flapping"></a>
### watcher.flapping
Checks with 4 or more status changes in the last 12 ticks (each flagged `flapping`).
Tune the threshold or fix the intermittent source before people learn to ignore it.

<a id="coordination-wall"></a>
### coordination.wall
`diagnostics\WALL-STATUS-FLY.md`: AMBER when any of the last 40 entries is not
`timestamp | owner | msg | STATE`, or the newest entry is older than 12 h.

<a id="laptop-adhoc_processes"></a>
### laptop.adhoc_processes
Visibility only (owner: Danish): listeners on :7002/:9097 and running `watch_queue.ps1`
copies. Always GREEN; it never changes the verdict.

### Acknowledging an AMBER
Write `C:\DoxxedCrypto\laptop-chain\health\acks.json`:
`{"acks": [{"check": "<id>", "until": "<ISO time>", "by": "<name>", "reason": "<why>"}]}`.
An unexpired ack removes that AMBER from the verdict and `failing` and lists it under
`acked`. RED is never acknowledged (the check gets `ack_ignored`). Duplicate check ids are
merged, keeping the worst status.
