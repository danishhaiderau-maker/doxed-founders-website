# Clean data epoch runbook (2026-10-03, owner CLEAN-EPOCH)

Decision (Danish): the first post-freeze batch deploy (~2026-10-04T11:10Z or later) starts ONE clean
data version. All older, untrusted data is **hard-deleted** (no cold storage, no disk for it) — but only
after the new epoch is certified. Bitfinex stays DISARMED throughout; nothing here arms the relay.

Epoch id: `ce-20261004-v31-clean` (`DATA_EPOCH_ID` in `services/btc-conservative-agent/fly.toml`; the
date is a label — the real start is the boot time recorded in `data_epoch.json`).

## Pieces

| Piece | Where | What it does |
|---|---|---|
| `data_epoch.py` | service | manifest (`data_epoch.json`), `data_epoch_id` stamp, row classification, schema fingerprints, certification doc |
| bot hooks (POST-FREEZE PR) | `bot.py` | `_open_data_epoch()` at boot (stable across restarts), stamps every `_safe_append_jsonl` row, `/api/status.data_epoch` |
| segment prefix v3 (POST-FREEZE PR) | `fly.toml`, laptop scripts | genesis BASELINE: only clean-epoch bytes ship; v1/v2 stay readable/ACKable as archived prefixes |
| compatibility monitor | `:9021/api/selfaware/data/compatibility`, `/data` | per stream: versions + row counts, epoch classes, schema drift, dead-in-version fields, segregated partitions, delete command |
| analyzer epoch guard | `analyzer_epoch_guard.py` | loader admits only clean-epoch rows; generation receipt `data_epoch` block; `data.compat_epoch_purity` RED otherwise |
| certification gate | `scripts/clean_epoch_certify.py` | 2 h window, contracts/sections/field checks GREEN, no RED in window, stamped rows present → `C:\DoxxedCrypto\clean-epoch\certification.json` + confirm token |
| wipe | `clean_epoch_wipe.py` / `scripts\clean-epoch-wipe.cmd` | plan (dry-run, default) → execute with epoch + confirm token + plan sha; receipts |

Row classes: `CURRENT` (stamped this epoch), `CURRENT_UNSTAMPED` (sealed/CSV rows after start),
`UNSTAMPED_POST_EPOCH` (after start, writer not stamping yet — admitted, AMBER until the writer
stamps), `EPOCH_INDEPENDENT` (1s tape, cross-venue/market-context minutes, liquidations), and the
incompatible `PRE_EPOCH`, `FOREIGN_EPOCH`, `UNDATED`. Before an epoch is declared every row is
`LEGACY` and rows whose version differs from the current release are segregated as `LEGACY_VERSION`.

## Hard KEEP guard (never deleted, tested)

Code, secrets/config, relay/Bitfinex evidence (`relay`, `bitfinex`, `live_copy`, `exchange_`,
`platform-relay`, `rearm`), the 1s market tape (`market_microstructure_1s.jsonl*`,
`bitfinex_l1_tape_1s*` — TapeMinuteBarStore/ADX rebuild needs 14+ days), epoch-independent market
data. On Fly additionally: SQLite, restart-recovery state, the whole `v3/` store, top-level runtime
JSON, every live head file. On the laptop: chain state (`.puller`, locks, leases, acks, status).
Execute re-checks the guard per file and refuses if size/mtime changed since the plan.

## Order (do not reorder)

### 0. Preconditions
- Freeze over; master CI green; relay/Bitfinex DISARMED (`/api/status`: `live_armed=false`,
  `bitfinex_live_enabled=false`).
- Laptop branch `feat/clean-epoch-20261003` merged (monitor, guard, certify, wipe tool).
- ANALYZER-FIDELITY has wired `analyzer_epoch_guard` into the loader and emits `data_epoch` in
  `analyzer_generation_receipt.json`, and `_v2_data_start_ts` reads the `data_epoch.json` start
  (otherwise `data.compat_epoch_purity` stays RED and certification fails closed — by design).

### 1. Deploy (starts the epoch)
1. Merge the POST-FREEZE PR as part of the batch deploy (normal `deploy` mode, paper boundary).
2. Verify on Fly (read-only): `/api/status` → `data_epoch.declared=true`, `epoch_id=ce-20261004-v31-clean`,
   `started_at_utc` ≈ boot; `/api/research-segments/v3/files` lists `data_epoch.json` and the v3
   genesis BASELINE; v2 still answers (archived prefix).

### 1b. Boundary reset — wipe pre-epoch V3 rows (within 60 min of `started_at_utc`)
Relay/Bitfinex stay DISARMED throughout; the job never arms anything.
1. `mode=snapshot-volume` (execute refuses without a snapshot created within 6 h).
2. Dry run: `mode=clean-epoch-reset-plan` — runs, read-only,
   `python /app/clean_epoch_reset_plan.py plan --runtime-root /app/data/runtime` and fails on any
   protected candidate or incomplete inventory. Review `would_delete` (expect `RETIRED_EPOCH_V3_LEDGER`,
   research payloads/rotations, derived indexes, genome/accumulator), `protected_present` (must list
   `paper_lifecycle_v1.json` and `market_microstructure_1s.jsonl`), `relay_evidence.pending` (22 today)
   and `v3_generation_pointers` (expect `[]`).
3. Execute: `mode=clean-epoch-reset-execute`,
   `clean_epoch_confirm=RESET-AT-BOUNDARY:ce-20261004-v31-clean:<deployed rev12>`. In order:
   1. token + snapshot ≤ 6 h;
   2. deployed revision == token rev, running epoch == `fly.toml` `DATA_EPOCH_ID`, epoch age ≤ 60 min;
   3. `/api/pause` (`DEPLOY_MAINTENANCE`) and wait ≤ 180 s for paused + `live_armed=false` +
      `bitfinex_live_enabled=false` + force-paper + `pending_orders=0` + `open_positions=0`; otherwise
      it fails with **no deletion** and resumes paper;
   4. re-plan on the paused book and pin `protected_present` + `relay_evidence.pending_ids_sha256`;
   5. `POST /api/wipe_fly_only` (the bot re-checks its own admission);
   6. verify: `clean_epoch_reset_plan.py verify --epoch <epoch> --expect-present=… --expect-relay-sha256=…`
      — every V3 head absent/empty/current-epoch, no sealed V3 generation, no `ACTIVE.json` pointer,
      every protected file still present, relay pending event ids unchanged; then `df -h /app/data`;
   7. resume paper (`fly_failure_paper_resume.py`, always) and `fly_postdeploy_active_gate.py`.
4. Afterwards (read-only): `/api/status` `data_epoch.epoch_id` unchanged, `/api/relay-state` stale-owner
   pending count unchanged, two advancing paper cycles. Receipts:
   `/app/data/runtime/research_reset_receipts/<reset_id>/{binding,operation,deletion}.json`.
5. Then do the laptop cutover (1c), so the fresh mirror tree never pulls pre-epoch V3 rows.

### 1c. Laptop cutover
1. Laptop cutover, between analyzer cycles (status file `phase` DONE):
   - stop the puller/cycle scheduled tasks;
   - `git pull` the merged master into `C:\DoxxedCrypto\btc-v31-current` (do not reset/clean);
   - instant rename (no copy, no disk): move `C:\DoxxedCrypto\fly-mirror-segments` (tree, `.puller`,
     quarantine, tombstones), `segment-promotion-view`, `segment-analyzer-view` and
     `analyzer-exports\latest` into `C:\DoxxedCrypto\pre-clean-epoch\`;
   - start the tasks; the cycle now pulls prefix `v3` into a fresh tree.
2. Confirm `:9021/api/selfaware/data/compatibility`: `epoch.declared=true`, streams show `CURRENT`
   rows, `data.compat_mixed` GREEN (the fresh tree holds no pre-epoch rows).

### 2. Certify (≥ 2 h after `started_at_utc`)
```
python scripts\clean_epoch_certify.py --epoch ce-20261004-v31-clean
```
- `PENDING` (< 2 h) or `REJECTED` (lists failing checks) → fix, re-run; never wipe.
- `CERTIFIED` prints `confirm token: DELETE-PRE-EPOCH:ce-20261004-v31-clean:<cert8>`.

#### Fresh certification window on the same epoch
Planned laptop-chain downtime during a reset (self-aware/analyzer down, findings RED while they
restart) must not permanently poison an epoch. After the cause is fixed and the chain is proven to
advance, declare a fresh window forward in time — no new epoch, no deploy:
```
python scripts\clean_epoch_certify.py --declare-window now --reason "<why>"            # plan only, prints ISO start + token
python scripts\clean_epoch_certify.py --declare-window <ISO start> --reason "<why>" --confirm CERT-WINDOW:<epoch>:<YYYYmmddTHHMMSSZ>
```
- Refused if the start precedes the epoch start, is > 5 min in the past (a window can never be
  declared over already-observed REDs), or is > 24 h ahead; `--reason` is mandatory.
- Confirm appends the declaration to `C:\DoxxedCrypto\clean-epoch\certification-windows.jsonl` and a
  `| CERT-WINDOW | ... |` line to `diagnostics\WALL-STATUS-FLY.md` with the window start.
- Later certify runs use the latest declaration for the epoch: the 2 h age and
  `required.no_red_in_window` count from the window start (a required finding already RED when the
  window opened counts as RED in the window; a truncated findings history fails closed). Every other gate is unchanged: required
  findings GREEN now (AMBER fails), no RED now, same epoch, stamped CURRENT rows, fresh self-aware.

### 3. Wipe — dry-run diff first, then execute
Laptop (between analyzer cycles; execute refuses while a cycle runs):
```
scripts\clean-epoch-wipe.cmd plan --scope laptop --epoch ce-20261004-v31-clean --out C:\DoxxedCrypto\clean-epoch\plan-laptop.json
scripts\clean-epoch-wipe.cmd execute --scope laptop --epoch ce-20261004-v31-clean --confirm <token> --expect-plan-sha256 <plan_sha256>
```
Fly (GitHub Actions `Deploy Fly BTC bot`):
1. `mode=snapshot-volume` (execute refuses without a snapshot created within 6 h).
2. `mode=clean-epoch-wipe-plan` — review the printed plan (`delete_by_reason`, `kept_by_reason`, largest candidates).
3. `mode=clean-epoch-wipe-execute`, `clean_epoch_confirm=<token>` — enters the paper maintenance
   boundary, re-plans, executes the pinned plan, resumes paper, proves paper active.
Receipts: laptop `C:\DoxxedCrypto\clean-epoch\<epoch>\receipt-laptop-*.json` + gzipped deleted list;
Fly `/app/data/runtime/clean-epoch-receipts/<epoch>/`.

### 4. Verify the analyzer reads only the new epoch
- `analyzer_generation_receipt.json` → `data_epoch.epoch_id == ce-20261004-v31-clean`,
  `pre_epoch_rows_admitted == 0`.
- `:9021` findings `data.compat_mixed`, `data.compat_epoch_purity`, `contract.*` GREEN.
- `:9021/data` "Data compatibility" panel: segregated 0 B.

### 5. Shrink the Fly volume
Fly volumes cannot shrink in place. After step 4, with used space ≈ the kept set:
create a 10 GB volume in `sin`, restore/copy the kept tree during a paper maintenance boundary,
swap the machine mount, prove two advancing cycles, keep the old 52.8 GB volume 7 days, then
destroy it. (Separate dispatch; not automated here.)

## Dry-run numbers (measured 2026-10-02 ~15:30Z, `--simulate-now`, nothing deleted)

| Scope | Delete | Keep | Notes |
|---|---|---|---|
| Laptop | **85.19 GB** (655,454 files) | **1.12 GB** | perf caches 59.31, canonical-research-data 9.35, promotion view 5.56, mirror tree 5.55, archive 2.82, fly-segments 2.24, Tier A 0.19, analyzer view/exports/analysis archive 0.17. Kept: 1s tape 0.92, chain state 0.16, epoch-independent market data 0.04, relay/Bitfinex evidence 0.002, code/config |
| Fly runtime | **3.50 GB** (166 sealed pre-epoch rotations + quarantine) | **5.54 GB** | signal_replay 1.62, post_exit_replay 0.71, order_multiverse 0.66, entry grid 0.32, source_order_market_evidence 0.10, other 0.09. Kept: V3 store 2.79, live heads 2.12 (research_events_v22 1.77), SQLite 0.51, tape 0.11 |
| Fly outside runtime | up to **3.18 GB** | — | archived v2/v1 segment store + shipper state; candidate only after the laptop ACKs the final v2 seq |

Fly volume 52.8 GB, 12.22 GB used → ≈ 5.6 GB after the wipe (fits a 10 GB volume with the
15-day tape growth). Compatibility now (no epoch declared): 66 streams, 0 RED / 30 AMBER (data
versions mixed in one input: v3 ledgers mix 6-8 versions, trades_3factor.csv 13) / 36 GREEN;
1.70 GB in 32 streams segregated as `LEGACY_VERSION`; 9 streams declare no version at all.

## Epoch boundary (Fly, #336)
- Every writer stamps `data_epoch_id`: `_safe_append_jsonl`, execution_funnel, order_multiverse,
  opportunity_capture, ai_call_logger, genome store, the 1 s tape, and the cross-venue / market-context
  collectors (separate processes; they adopt the bot's manifest via `DATA_EPOCH_ID`). V3 rows carry it
  inside the hashed material (also from the lifecycle worker). CSVs cannot take a column; the
  compatibility monitor treats them as declared once every post-epoch row is epoch-dated.
- `data_epoch.json` carries a `fingerprint` (bot / research-stack / fill model / collector / feature
  schema). A restart under the same id with a different fingerprint is appended to
  `fingerprint_changes`; certification must reject a mixed epoch.
- On boot of a new epoch a background thread writes `data_epoch_boundary/<epoch>.<stream>.json`:
  - `research_events_v22`: when the head's first row predates the epoch start it is sealed into the
    next numbered generation (`rotate_research_events`, under the writer lock, no deletion). Its
    pre-epoch mtime makes it a `PRE_EPOCH_SEALED_ROTATION` wipe candidate; seals, indexes and the
    provisional store stay.
  - V3 ledgers: assessed only at boot. Generation pointers are bound to the deployed revision, so
    adopting the legacy generation 0 on boot would invalidate appends after the next deploy. Pre-epoch
    V3 rows are removed by step 1b (boundary reset); until then the analyzer epoch guard excludes them.
    The `v3` boundary receipt keeps saying `PRE_EPOCH_HEAD / NOT_ROTATED` (it records the boot state);
    `clean_epoch_reset_plan.py verify` is the post-reset truth.

## Boundary reset — evaluation (CLEAN-EPOCH, 2026-10-03)
The existing guarded Fresh Collection reset (`POST /api/wipe_fly_only` →
`perform_fresh_collection_reset(send_local_signal=False)`) is the step that wipes pre-epoch V3 rows:
- **Admission (bot-enforced):** `execution_paused`, `live_armed=false`, force-paper, zero pending
  orders and open positions (`fresh_collection_requires_paused_disarmed_flat_boundary`), empty WAL,
  clear recovery audit, orphan/auxiliary audits. Never force-closes; refuses otherwise. Leaves
  `execution_paused=true` (the workflow resumes paper explicitly).
- **Relay/Bitfinex evidence:** not in V3. A scan of the mirrored V3 `execution` / `order_intent` /
  `lifecycle` ledgers found no exchange/Bitfinex order id, relay receipt, relay event id or live fill;
  only `relay_eligible` research metadata. The relay outbox (incl. the 22 stale-owner pending events)
  lives in `paper_lifecycle_v1.json` (+ `relay_lifecycle_evidence_v1.json`), both inventory-ESSENTIAL,
  so no move is needed. The workflow pins the pending event-id digest before and verifies it after.
- **Restart-recovery state kept:** positions/orders/state/session/ledgers, recovery/owner/emergency
  paths, locks, `ledger_generations_v1`, `append_heads`, `data_epoch.json`, `data_epoch_boundary/`.
- **V3 generation pointers:** production has none; the reset deletes the V3 ledgers then
  `retire_empty_epoch_authority`, leaving no `ACTIVE.json`. Absent pointer = generation 0 for any
  revision, so appends stay valid after later deploys (verified by `verify`).
- **Fix shipped in #336:** the reset inventory now keeps epoch-independent market data
  (`market_microstructure_1s.jsonl*`, cross-venue/context 1m, liquidations) with reason
  `RETAINED_EPOCH_INDEPENDENT_MARKET_DATA`; before, it would have deleted the 1 s tape.
- **Caveat:** the reset is whole-file (it also removes current-epoch research heads, genome and
  accumulator rows). It therefore runs only at the epoch-opening boundary; the workflow refuses when the
  epoch opened > 60 min ago.
