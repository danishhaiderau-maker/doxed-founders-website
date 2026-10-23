# Full reset, NO archive: runbook (17 Oct 2026)

Decision (Danish, 17 Oct 2026 18:09): delete everything collected since the 13 Oct fresh start on Fly and start
clean from now. No archive, no export. Live stays OFF, Tiles 6 and 7 stay OFF. Bitfinex has no trading fees:
none is applied or shown anywhere.

## What it does
`clean_epoch_wipe.py --full-reset-no-archive` (module `full_reset_no_archive.py`, dispatch-only workflow job
`full-reset-no-archive`). Existing wipe modes, KEEP rules and workflows are untouched.

* KEEP is an explicit allowlist: live-off flags, arming state, force_paper, settings, spread gate, tile ON/OFF and
  the tile 6/7 switches, boot config (`config-7002.json`, `state.json`, `policy.json`, `spread-gate.json`,
  `pathway_lane_specs.json`, `execution_settings_history.jsonl`, `data_epoch.json`, `fresh_start_epoch.json`,
  `research_session.json`, paper lifecycle / open position state, relay evidence), the V3 store, WAL, SQLite,
  collector v22 files, code, secrets and every wipe receipt.
* DELETE is an explicit set of data classes: paper rows (`trades_3factor.csv`, `expired_orders_3factor.csv`, lane
  ledgers; the PAPER_FILL_FORCED deploy-flatten rows are rows of these files), `paper_ioc_no_fill_shadow.jsonl`,
  DISLOC ledgers, shadow / counterfactual streams, tape (`market_microstructure_1s`), `bfx_book_10l_1s`,
  `bfx_trades`, `bfx_deriv_status_1s`, `bfx_ws_events`, `venue_bbo_1m`, cross venue, context, liquidations, and every other top-level
  `.jsonl` / `.csv` row stream with all rotated `.N` / `.N.gz` copies.
* Unclassified files are RETAINED and listed in the plan and receipt (never deleted silently).
* The V3 ledgers, collector v22 seals and research payloads are reset by the existing in-process guarded reset
  (`POST /api/wipe_fly_only`), which the job runs first with the freeze override. `/api/wipe_fly_only` alone keeps
  accounting and the tape, which is why this mode exists.

## Safety gates (each aborts BEFORE any deletion)
confirm token `DELETE-FULL-RESET:<plan sha256[:12]>`; plan sha (a hash of the delete CLASSES and RULES, not of exact paths, so rotation, collectors appending or the in-process reset between plan and execute cannot cause `PLAN_SHA256_MISMATCH`); written reason (>= 10 chars); volume snapshot within
6 h (recoverability only, NOT an archive); freeze override `BREAK_21_DAY_RESEARCH_FREEZE` + reason (this reset only);
bot paused by DEPLOY_MAINTENANCE, force_paper, live off, disarmed; no open or pending paper position (close them first
through the existing path); exchange flat (fresh private exposure audit, authoritative + flat + all counts a real 0, proven at the boundary and again right before the file wipe; /health carries no exchange facts and a missing key counts as NOT flat); `config-7002.json` readable; `open_positions.json` a real empty list; `orders.json` / `positions.json` / `pending_orders.json` / `paper_state.json` (kept, never written by the bot) empty if present; no unclassified `.csv/.jsonl/.gz` file (the execute aborts, classify then re-plan); relay PAUSED, disarmed and flat from durable state; `source_git_rev` equals
the one guarded deploy; `RESEARCH_SEGMENTS_PREFIX_PER_WIPE=1`.

Receipts: `runtime/full-reset-receipts/<plan sha12>/receipt-intent.json` is fsynced BEFORE the first unlink, then
`receipt-complete.json` + `deleted.jsonl.gz`. They are allowlisted, so they survive. Re-running a completed plan is a
no-op. The tool is never imported by the bot or the entrypoint, so it cannot run at boot.

## Order (one guarded deploy, then the dispatch job)
1. Merge, then the ONE guarded deploy (this PR sets `RESEARCH_SEGMENTS_PREFIX_PER_WIPE = "1"` in fly.toml).
2. Dispatch `full-reset-no-archive-plan`; read `confirm_token` and the plan.
3. `snapshot-volume`, then dispatch `full-reset-no-archive-execute` with `clean_epoch_confirm` = the token and
   `full_reset_reason` = the written reason. The job pauses, proves flat, snapshots the live-off facts, runs the
   in-process reset, stamps `fresh_start_epoch.json` (the D8 data version `fse-<epoch_ts>` is carried by new book,
   deriv, trades, bbo rows as `dv` and by new closed trades as `data_version`), deletes the files, restarts the
   machine (memory and collector file handles are clean), verifies (live-eligible switch ON 0, DISARMED, force_paper
   true, flags unchanged; 0 rows on tiles/trades, reconcile, fill-provenance, lanes) and waits for the book stream.
   Paper is NOT resumed by the job. Health Monitor checks the first 10 minutes; Boss then resumes paper.
4. Laptop (NOT run by the job; each step needs Danish's approval prompt): the new per-wipe prefix is
   `v3-w<epoch_ts>` (see `research_segment_epoch.resolve_prefix`). Point the puller at it so it starts at seq 0:
   `scripts/research-segment-pull.ps1 -Prefix v3-w<epoch_ts>` (state is keyed by prefix, a new prefix has
   `applied_seq` 0), pass the wipe receipt `plan_sha256` as `--reset-ack-plan-sha256`, and confirm a beacon received
   after the wipe shows the new prefix / `fresh_start_epoch_id`. The old stuck seq 7073 state stays untouched until
   the old mirror is deleted (separate approved step). The `DoxxedCrypto-R2-Backup` task stays DISABLED.

## Heads-up for the first 10 minutes
Empty-file edge cases (covered by tests), a segment publisher seq reset, AMBER/RED states from the deploy
(exit-queue / fill-outside-BBO warnings reset, not fixed), and `REJECT_NO_BOOK` until the book stream has rows.

## Failure and rollback path
There is NO archive and no undo of a completed delete: the only recovery source is the pre-wipe volume snapshot
(recoverability only), restored by hand with Danish's approval. Before any delete everything aborts cleanly, so a red
job at a gate means nothing was deleted.

| Where it failed | State | What to do |
|---|---|---|
| a gate before the in-process reset (snapshot, boundary, exchange proof, relay flat, live facts) | nothing deleted, bot PAUSED | fix the cause (close a paper position through the existing path, wait for the exchange to be flat), re-dispatch execute. If you stop here, resume paper with the normal guarded resume. |
| the in-process reset (`/api/wipe_fly_only`) refused or failed | file wipe NOT started; the in-process reset may be partly applied, its own operation receipt says how far | the job's restart step still runs (clean file handles) and keeps the bot paused. Re-dispatch execute: the plan sha is rules-based so it is unchanged. |
| the file wipe failed or was partial | intent receipt exists under `runtime/full-reset-receipts/<sha12>/`, some files deleted, bot PAUSED | the restart step runs anyway (`if: always()`), then the post-restart read-only plan lists what is left. Re-dispatching execute finishes the job (already-absent files are skipped). A `LIVE_FLAGS_CHANGED` receipt (`completed=false`) means a live flag differs: STOP, do not resume, tell HM. |
| verify, dv or leftovers check red | wipe done, bot PAUSED | read the failing line. Do NOT resume paper. HM decides: fix forward (re-dispatch verify after repair) or restore the snapshot. |

Rollback to the pre-wipe state (only on Danish's explicit order): keep the bot paused, restore the pre-wipe volume
snapshot with a fresh volume (the volume id is pinned in the workflow), redeploy the same revision, verify live off and
DISARMED, then re-point the laptop puller at the OLD prefix. The new per-wipe prefix `v3-w<epoch_ts>` is then abandoned
(never re-used). Paper stays paused through every branch above; the job never resumes it.

The DEPLOY_MAINTENANCE pause is durable: `/api/pause` calls `save_persistent_config()` (`manual_admin_pause`,
`pause_intent`) and the boot loader restores `execution_paused` from it (pinned by a test), so the restart cannot let
paper run, and the restart step fails the job if the bot does not come back paused by DEPLOY_MAINTENANCE. On SIGTERM the
bot writes only `open_positions.json`, `paper_lifecycle_v1.json`, the config and replay dumps; it never rewrites
`lane_pnl_ledger.json` or the trade CSV from memory (pinned by an AST test). The post-restart plan allows only small
delete-class leftovers (default max 200 files and 64 MiB; a collector or replay dump may recreate tiny files).

## Notes added after HM review (17 Oct 20:16 AEDT)
* **`plan_sha256` binds to RULES, not files.** It is a hash of the delete classes, the KEEP allowlist, the deny-list and
  `RULES_VERSION` (`full-reset-rules-v3-denylist`). Rotation, collectors appending or the in-process reset between plan and
  execute never change it; a new file only changes what the execute deletes (and the plan file lists it). Changing any
  rule or the deny-list changes the sha, so a stale confirm token cannot be used against different rules.
* **`exchange_delta_btc` is just 0.0 when there are no exchange positions.** The private exposure audit returns counts
  (`open_position_count`, `open_order_count`), not a delta, so the proof sets the delta to 0.0 only when the position count
  is a real 0 (otherwise null, which fails). It is not an independent measurement. Freshness uses the audit's own
  `checked_ts` (never the runner clock) and requires `error` to be present and null.
* **Deny-list.** A file whose NAME contains `live`, `arm`, `flag`, `switch`, `tile` or `settings` is never deleted by any
  rule (explicit, dataset or the top-level catch-all). If it is not on the KEEP allowlist it is UNCLASSIFIED, and a
  `.csv/.jsonl/.gz` of that kind makes execute abort with `UNCLASSIFIED_DATA_FILES` until HM classifies it. The plan file
  (`plan --out`, and the plan printed by the job) lists `top_level_delete` (name, files, bytes with rotations folded in)
  and `top_level_denied_or_unclassified`, so it can be sent to HM as is.
* **Failure safety net.** The last job step runs on `failure()`: it POSTs `/api/pause` (owner DEPLOY_MAINTENANCE) and, if
  `/health` still does not show paused by DEPLOY_MAINTENANCE with live off, runs `flyctl machine stop`. It never resumes
  paper. After a safety-net stop the machine is started by hand only after HM clears it.

## Inventory limits (RESET_INVENTORY_INCOMPLETE fix)

The first execute stopped at the in-process reset with `RESET_INVENTORY_INCOMPLETE`
(`ENTRY_BUDGET_EXCEEDED`): the read-only inventory scans every entry under the
runtime root, and the live volume has ~394k entries (~323k are retained
`v3/receipts/emergency_record_idempotency_v1` receipts that are scanned but never
deleted). Nothing was deleted; the bot stayed paused.

Fix (hard caps, not unlimited):
- `RESET_MAX_ENTRIES = 1_000_000` (`research_reset_inventory.py`) is the default scan
  budget for `plan_research_reset`, `execute_research_reset` and target-stability sampling
  (2.5x the observed count; equals the validation ceiling).
- `MAX_RETAINED_ROWS = 1_000_000` (`research_exact_deletion.py`) matches it (retained rows
  stream to the `.retained.jsonl` sidecar). A second, hidden 200k cap was found by the test.
- `max_files` (targets) raised 100,000 -> `MAX_RESET_FILES = 600_000` (hard cap in the deleter, executor admission and progress). Live replay of the file listing gives 27,064 targets and 323,129 retained, so the cap has >20x headroom. Exceeding it still refuses (`RESET_TARGET_BUDGET_EXCEEDED`) before any delete.
- Timing: ~16 s per scan at live size; the whole in-process reset is well inside the
  workflow request timeout, which is raised 900 s -> 1800 s. The reset stays a blocking call
  (not a background job); fail-closed semantics unchanged.
- Tests: `test_research_reset_large_layout.py` (410k-entry layout: old budget refuses,
  new default plans/executes, mid-run abort + resume, re-run idempotency).

## Background reset job (replaces the single blocking `/api/wipe_fly_only` call)

Why: the 18 Oct 02:38 AEDT execute held one HTTP request for 7.5 min (hashing 18.7 GB of 27,230 targets in ALL_SCOPES_PREFLIGHT), then returned 502 and the bot froze. Nothing was deleted (no ACTIVE_RESET, no operation.json).

How it works now:
- `POST /api/wipe_fly_only {"background": true, "freeze_override": {...}}` refuses if LIVE is armed or a job is running, otherwise starts a job thread and returns `202 {operation_id}` immediately. The existing guards still apply inside the job (paused, flat, disarmed, paper-only, exclusive barriers, freeze override).
- The heavy bot-independent calls (`execute_research_reset` preflight/payload/physical-scope stages and target sampling) run in a SEPARATE process (`full_reset_job.py worker`, nice 19, ionice idle). The job thread only waits on it. Nothing in the pinned deleter changed and no pass was removed: each is a safety gate or the deleter's own pre-unlink revalidation.
- Heartbeat guard: the bot publishes `runtime/full-reset-receipts/_job/heartbeat.json` every second; the worker reads it every 2048 scanned entries and every target. If the bot heartbeat age exceeds 5 s the worker sleeps (throttles); if it stays high for 30 s the worker aborts fail-closed (`HeartbeatAbort`, resumable: rerun the workflow). The reset holds the exclusive research/epoch barriers for its whole duration, so a persistently stalled bot loop will abort the job rather than delay it indefinitely.
- Status: `GET /api/monitor/full-reset` (admin/monitor auth, file-backed, never blocks on the job) returns state, stage, outcome, blockers, started/updated, `progress` (phase, completed/total targets, fingerprinted bytes, scanned entries, throttled seconds, heartbeat age) and `alert` (RED on failed/interrupted job or when running while LIVE ARMED). A RUNNING status not updated for 10 min is reported as `INTERRUPTED`.
- Resume/idempotent: a killed worker or bot restart leaves the intent/operation receipts and no completion; re-running the workflow resumes via `ACTIVE_RESET.json`/`operation.json` (existing `_fresh_research_reset_resume`) or starts clean. The job never resumes the DEPLOY_MAINTENANCE pause.
- Workflow: the in-process step starts the job and polls every 30 s (hard cap 130 min, step cap 140 min); retryable pre-mutation codes restart the job up to 7 times; any failure ends the job red ("the file wipe was NOT started"), the always-run restart + safety net keep the bot paused.
- Tests: `test_full_reset_job.py` (410k-entry isolated scan keeps the in-process ticker unstarved, guard throttle/abort, mid-run kill + rerun, isolated results equal direct, status view, wiring, workflow poll step).

### HM review additions (#698)
- Root cause found by code reading: `heartbeat_loop` called `_maybe_record_heartbeat_cycle_3m` -> `_record_cycle_3m_universe` -> `_safe_append_jsonl`, which blocks on `_research_write_gate`; the reset holds that gate exclusively, so `last_heartbeat` (which `/health` reads, 15 s limit) froze. `heartbeat_loop` now skips that research write while `_fresh_collection_lock` is held or a background job is active (barrier hold unchanged). `/health` reads only `state["last_heartbeat"]` with no locks; `heartbeat_loop` is the dedicated feeder and no longer reaches any gate. `state_monitor_loop` can still wait on gates (reconcile paths) but it is not the only feeder.
- The status now carries `deletion_started` (true from stage PAYLOAD_DELETION on). The workflow failure message says whether research-reset deletion MAY HAVE STARTED (partial reset), had not started, or is UNKNOWN; the later file wipe is always reported as not started.
- The worker dies with the bot (PR_SET_PDEATHSIG) so no unattended deletion continues; rerun resumes.
- Limitation: the pinned deleter hashes one file in a single call (e.g. the ~7 GB signal_replay file), so the worker cannot tick the guard inside a single large file; ticks happen per target and per 2,048 scanned entries.
