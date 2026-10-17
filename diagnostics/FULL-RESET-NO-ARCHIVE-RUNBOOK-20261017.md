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
  `bfx_trades`, `bfx_deriv_status_1s`, `venue_bbo_1m`, cross venue, context, liquidations, and every other top-level
  `.jsonl` / `.csv` row stream with all rotated `.N` / `.N.gz` copies.
* Unclassified files are RETAINED and listed in the plan and receipt (never deleted silently).
* The V3 ledgers, collector v22 seals and research payloads are reset by the existing in-process guarded reset
  (`POST /api/wipe_fly_only`), which the job runs first with the freeze override. `/api/wipe_fly_only` alone keeps
  accounting and the tape, which is why this mode exists.

## Safety gates (each aborts BEFORE any deletion)
confirm token `DELETE-FULL-RESET:<plan sha256[:12]>`; plan sha; written reason (>= 10 chars); volume snapshot within
6 h (recoverability only, NOT an archive); freeze override `BREAK_21_DAY_RESEARCH_FREEZE` + reason (this reset only);
bot paused by DEPLOY_MAINTENANCE, force_paper, live off, disarmed; no open or pending paper position (close them first
through the existing path); exchange flat; relay PAUSED, disarmed and flat from durable state; `source_git_rev` equals
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
