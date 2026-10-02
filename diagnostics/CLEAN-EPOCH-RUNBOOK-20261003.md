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
3. Laptop cutover, between analyzer cycles (status file `phase` DONE):
   - stop the puller/cycle scheduled tasks;
   - `git pull` the merged master into `C:\DoxxedCrypto\btc-v31-current` (do not reset/clean);
   - instant rename (no copy, no disk): move `C:\DoxxedCrypto\fly-mirror-segments` (tree, `.puller`,
     quarantine, tombstones), `segment-promotion-view`, `segment-analyzer-view` and
     `analyzer-exports\latest` into `C:\DoxxedCrypto\pre-clean-epoch\`;
   - start the tasks; the cycle now pulls prefix `v3` into a fresh tree.
4. Confirm `:9021/api/selfaware/data/compatibility`: `epoch.declared=true`, streams show `CURRENT`
   rows, `data.compat_mixed` GREEN (the fresh tree holds no pre-epoch rows).

### 2. Certify (≥ 2 h after `started_at_utc`)
```
python scripts\clean_epoch_certify.py --epoch ce-20261004-v31-clean
```
- `PENDING` (< 2 h) or `REJECTED` (lists failing checks) → fix, re-run; never wipe.
- `CERTIFIED` prints `confirm token: DELETE-PRE-EPOCH:ce-20261004-v31-clean:<cert8>`.

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

## Dry-run numbers (2026-10-02/03, nothing deleted)
See the PR description for the final measured totals (laptop dry-run walk and Fly checkpoint preview).

## Follow-ups (not in this change)
- V3 ledger generation rotation and `research_events_v22.jsonl` (1.77 GB live head) are kept: live
  heads are never deleted. They need writer-side rotation to drop pre-epoch rows.
- Writers outside `_safe_append_jsonl` (execution_funnel, order_multiverse, opportunity_capture,
  ai_call_logger, genome store, collectors) are classified by timestamp; stamp them next.
