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
- A crashed monitor run (`monitor_error: monitor crashed`) observes nothing,
  so it never counts as a clean check, and a run that started from a reset
  state (cache miss) never closes the issue. Closing needs two consecutive
  clean runs on restored state. Each run summary shows `restored` and `crashed`.
- Severity: **critical** alerts fail the run (red) and comment on the issue.
  **warning** alerts are listed in the issue (and comment on it) but the run
  stays green.

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
| `laptop_silent` | `LAPTOP_CHAIN_HEARTBEAT` repo variable older than 2h, unparseable, or unset/empty | 12h |
| `laptop_health_silent` | Fly `/api/system-health` `age_sec` > 1800 or `stale=true` (laptop watcher stopped pushing), 2 runs / 30 min | 12h |
| `monitor_schedule_gap` (warning) | > 45 min since the previous monitor run (heartbeat variable, cached state, or Actions runs list) | 12h |
| `xvl_evaluator_stale` | `/ready` `xvl_evaluator_health` `STALE` or `tick_age_s` > 60s (not `DISABLED`/`STARTING`), 2 runs / 15 min | 6h |
| `cross_venue_stale` | `/ready` `cross_venue_health` `DOWN`/`STALE` or `collector_age_s` > 120s; or `DEGRADED` (some venues stale) continuously > 30 min; 2 runs / 30 min | 6h |
| `cross_venue_reconnects` (warning) | `/api/status` cross-venue venue reconnects grew >= 30 since the previous run, 2 runs / 30 min | 6h |
| `market_context_stale` | `/ready` `market_context_health` `COLLECTOR_DOWN`/`DEGRADED`/`UNAVAILABLE` (lists `stale_feeds`), 2 runs / 30 min | 6h |
| `ai_input_dead` | `/ready` `ai_input_health.status == DEAD_INPUT` while unpaused, 2 runs / 30 min | 6h |
| `bbo_refresh_stale` | `/ready` `bbo_refresh` last success > 300s, in flight > 120s, or >= 10 consecutive failures, while unpaused; 2 runs / 15 min | 6h |
| `lifecycle_stalled` | `/api/status` `lifecycle_pipeline` not running, `last_success_age_sec` > 3600, or `emergency=true`; 2 runs / 30 min | 6h |
| `lifecycle_wal` | `lifecycle_pipeline.emergency_wal.status` `ALARM`/`INVALID`/`STALE`, 2 runs / 15 min | 6h |
| `lifecycle_blocked` (warning) | any `lifecycle_pipeline.blocker_counts` code >= 10 lifecycles, 2 runs / 60 min | 12h |
| `collector_v3_reconcile_stalled` | `/health` `research_collection.multiverse.v3_reconcile_worker`: phase != `IDLE` > 600s, 0 runs after 15 min, or not alive (`COLLECTOR_V3_RECONCILE_STALLED`); 2 runs / 15 min | 6h |
| `relay_stale_owner_pending` (warning; critical while `live_armed`) | `/api/relay-execution-state` `state_integrity.relay_push.delivery_scheduler.counts.stale_owner_pending` > 0 continuously > 30 min (pre-epoch retired events are excluded, see below) | 6h |
| `entries_blocked` (warning) | `/ready` `scheduled_ai_cycle.last_poll_entry_eligible=false` continuously > 2h while unpaused | 6h |
| `contract_field_missing` (warning) | a required field path (`scripts/fly_monitor_subsystems.py` `REQUIRED_FIELDS`) is absent from an endpoint that answered, 2 runs / 15 min | 12h |
| `transfer_lag` | segment shipper stale/erroring/> 36 segments un-ACKed, or legacy ACK > 3h | informational until `FLY_MONITOR_SEGMENTS_LIVE=1` |
| `not_ready`, `revision_drift`, `registry_drift`, `monitor_error` | unchanged from PR #185 | 6-12h |

Cadence rules are skipped while paper is paused (covered by `paper_paused`)
and during the first 20 minutes after boot; the `/ready` subsystem rules also
skip the first 20 minutes, and the AI-input, BBO and entries rules skip while
paused. Transitional rules are suppressed only while an **image deploy** is in
progress (a push run, or a dispatch whose `test-and-deploy` job is not skipped)
and for at most 45 minutes after it started, or while `DEPLOY_MAINTENANCE`
owns the pause (also capped at 45 minutes). Inspect, snapshot, repair and
restart dispatches never suppress anything. `safety`
(`force_paper_mode`/`live_armed`/`bitfinex_live_enabled`), disk, `deploy_stuck`,
`laptop_silent`, `laptop_health_silent`, `monitor_schedule_gap` and
`relay_stale_owner_pending` are never suppressed.

A missing optional field still skips its rule, but the fields the deployed
revision is known to emit are a contract: their absence raises
`contract_field_missing` instead of silently disabling the rule. Update
`REQUIRED_FIELDS` in the same change that removes or renames such a field.

### Relay outbox: retired pre-epoch stale-owner events

The bot's relay delivery guard retires events that can never be delivered and say
nothing about current delivery. An event is retired only when all of these hold:

- it is sticky-quarantined as `STALE_OWNER` or `MISSING_OWNER` in `relay_outbox_quarantine.jsonl`;
- its owner (`bot_instance_id`) differs from the verified current dashboard owner;
- it was created before the active clean data epoch started (`data_epoch.json` `started_at_ts`);
- relay delivery is **disarmed** (`live_armed` and `bitfinex_live_enabled` both false).

Retirement happens automatically on the next delivery-scheduler pass and is idempotent
across restarts. Each event is appended once to `relay_outbox_retired.jsonl`
(`relay_outbox_retirement_v1`: event identity, `quarantine_reason`,
`retired_reason=PRE_EPOCH_STALE_OWNER`, `data_epoch_id`, `data_epoch_started_at`,
`retired_at_unix`). Nothing is deleted or rewritten: the outbox record in
`paper_lifecycle_v1.json` and the quarantine row stay byte-identical. A retired event
is never deliverable, armed or not, and still blocks its trade's successors.

Retired events count as `retired_pre_epoch_pending`, not as `pending_total`,
`stale_owner_pending` or the `RELAY_OUTBOX_STALE_OWNER_PENDING` alarm. `/health.relay_outbox`
shows `retired_pre_epoch_total`, `retirement_ledger`, `last_retired_ts` and
`retirement_write_failures`. A stale-owner event created **inside** the current epoch
is not retired and still alarms after 30 minutes; treat that one as real. If
`retirement_write_failures` > 0, the events stay counted and the alarm stays on, which
fails safe. Fix the volume and the next pass retires them.

## Monitor heartbeat (missed schedules)

GitHub delivers `*/15` schedules best-effort (66% delivery and a 63-minute gap
were measured in BLINDSPOT-AUDIT-3). Every run compares now with the newest
evidence of the previous run and raises `monitor_schedule_gap` above 45 minutes.
Evidence sources, newest wins:

1. repository variable `FLY_MONITOR_HEARTBEAT`, written at the end of every run
   (success or failure) as compact JSON
   `{"at":"<UTC ISO>","run_id":"...","attempt":"...","crashed":false,"restored":true}`;
2. `last_run.ts` in the cached incident state;
3. the previous run of `fly-bot-monitor.yml` from the Actions runs API.

`GITHUB_TOKEN` cannot write Actions variables (there is no `variables`
workflow permission). The write therefore uses the optional secret
`FLY_MONITOR_VARIABLES_TOKEN`: a fine-grained PAT for this repository with
**Variables: read and write** only. Until it is configured the monitor logs
"heartbeat variable not written" and gap detection relies on sources 2 and 3,
which need only the existing `actions: read`. (`LAPTOP_CHAIN_HEARTBEAT` is
written by the laptop with the owner's own `gh` login, not from Actions.)

The laptop watcher can read it with
`gh variable get FLY_MONITOR_HEARTBEAT --repo danishhaiderau-maker/doxed-founders-website`
(parse `at`; stale > 45 min means the monitor itself is not running), or,
without the secret, with
`gh run list --workflow fly-bot-monitor.yml --limit 1 --json createdAt,conclusion,databaseId`.

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

## Clean-epoch purity on the laptop (#420)

`data.compat_epoch_purity` is GREEN only when the latest analyzer generation receipt reports
`data_epoch.pre_epoch_rows_admitted == 0`. `clean_epoch_certify` also needs every `data.compat_*`
finding GREEN. When purity is RED after a boundary reset, read `pre_epoch_rows_admitted_by_stream` in
`canonical-research-data/analyzer/analyzer_generation_receipt.json`:

- **Files Fly deleted at the reset** (the shipper sent a TOMBSTONE): the puller keeps them in
  `fly-mirror-segments/tree` as custody copies and records them in `.puller/state.json` under
  `tombstoned`. Promotion skips them (receipt `files_retired_custody`, heartbeat
  `retiredCustodyPaths`). The migration then moves the canonical copy to
  `canonical-research-data/migration/retired/<UTC stamp>/` and ledgers it in
  `migration/retired_ledger.jsonl` (receipt `files_retired`). Nothing is deleted. Files Fly removed
  through custody-gated pruning (`retention/prune_ledger.jsonl`) stay analyzer input. A stream Fly
  writes again in the new epoch drops out of `tombstoned` on its own.
- **Read-guarded streams** (`data_epoch.READ_GUARDED_BASES`): the reset keeps them on purpose. Every
  analyzer reader filters through the epoch guard, so they appear under
  `pre_epoch_rows_read_guarded_by_stream` (data.compat AMBER), never as admitted.
- **Ops ledgers** (`data_epoch.NON_EVIDENCE_BASES`) are not audited.
- **Anything else** is a real leak: a new reader or a retained evidence stream. Fix the reader and add
  it to `test_read_guarded_streams_have_only_guarded_readers`.

If promotion shows `files_retired_custody == 0` while the receipt still names a retired file, check
that `.puller/state.json` has `tombstoned` and `tombstoned_backfill`. The first pull after upgrading
backfills the map from the archived manifests, or from the tombstone markers if a manifest is missing.

## 48h unattended proof

`python scripts/unattended_proof.py --start` records T0 and a baseline
(Fly revision, registry signature, tile toggles) and opens
`diagnostics\unattended-proof-<T0>.jsonl` in the canonical workspace. Each
`DoxxedLaptopChainSupervisor` tick then runs `--check` after the monitor and
appends a row at most every 30 minutes from the tick's own snapshots
(`fly_runtime_snapshot_v1.json`, `fly_segment_head_snapshot_v1.json`,
`relay_status_snapshot_v1.json`, `analyzer-run.status.json`,
`alerts\active-alerts.json`). A row passes only if paper is running (a
`DEPLOY_MAINTENANCE` pause counts as a guarded deploy boundary), every
runtime tile is ON, the AI cycle advanced since the last row, WS ticks are
under 60s old, published-minus-acked segments are within 30 with pruning
off and any lag is bounded (see below), the analyzer generation is under 45 min old, no critical alarm is
open, live/Bitfinex is disarmed and no manual intervention was seen.
Missing or stale evidence fails the row. At T0+48h the verdict is appended
and written to `unattended-proof-<T0>.verdict.json`: PASS needs no FAIL rows,
no gap over 45 min and every boundary resumed within 60 min.

Fly reads the laptop ACK between shipper cycles, so its `laptop_acked_seq`
can trail the laptop for a few minutes. A non-zero ACK lag passes only when
the laptop's own `segment-pull.status.json` (finished within 10 min, no
error) shows everything published as acked, or the newest published segment
is at most 10 min old. A lag that was already present at the previous row
with no ACK progress since always fails, whatever the laptop reports.

Any operator action during the window must be journalled as one JSON line
(`{"at": "<UTC ISO>", "action": "..."}`) in
`C:\DoxxedCrypto\laptop-chain\manual-interventions.jsonl`; it fails the
proof honestly.

`--start --force --reason "<why>"` replaces a running window only after
appending a `SUPERSEDED` verdict (reason, row and FAIL counts) to the old
receipt and its `.verdict.json`; the old window is never silently dropped.

## Proving the channel

- Fly: run **Monitor Fly BTC bot** via *Run workflow* with `test_alert`
  checked. The run fails red and opens/comments the `fly-monitor-incident`
  issue; the next two normal runs close it.
- Laptop: `python scripts/laptop_chain_incident.py --test-alert` opens a
  `laptop-chain-incident` issue; the next two supervisor ticks close it
  (unless a real laptop incident is also active).
- `python scripts/laptop_chain_incident.py --dry-run` evaluates without
  touching GitHub or local state.
