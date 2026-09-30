# Research segments — Phase 2 shadow runbook

Implements Phase 2a/2b of `TRANSFER-ARCHITECTURE-PROPOSAL-20260929.md` in
**shadow mode only**. The legacy inventory/bundle/ACK pipeline stays
authoritative. Pruning is not implemented (`PRUNING_ENABLED = False`); when it
lands, the laptop ACK is the only prune authority (no time-based fallback).

## Components

| Where | File | Role |
|---|---|---|
| Fly | `research_segment_shipper.py` | Separate niced process (`SCHED_IDLE` when available). No bot import, no trade lock, no HTTP. Started by `fly-entrypoint.sh` only when `RESEARCH_SEGMENTS_ENABLED=1`. |
| shared | `research_segment_format.py` | Deterministic tar.gz (mtime 0, fixed metadata), canonical manifest, sha256 chain, ACK schema. |
| shared | `research_segment_store.py` | Stdlib SigV4 S3 client. Only create-if-absent (`If-None-Match: *`), GET, HEAD, List. No delete. |
| laptop | `research_segment_puller.py` + `scripts/research-segment-pull.ps1` | In-order pull, chain/hash verification, create-new raw archive, idempotent apply into the shadow tree, cumulative ACK. |
| laptop | `research_segment_parity.py` | Read-only shadow-vs-legacy comparison. |

Object layout (prefix `v1`): `v1/seg/<seq>.tar.gz`, `v1/man/<seq>.json`,
`v1/acks/laptop/<seq>.json`. There is no mutable `head.json`: the laptop
probes `man/<applied+1>` so the Fly key never needs overwrite rights.

Fly state lives on the volume root, outside the inventory roots:
`/app/data/segment-shipper/{state.json,status.json,intent/}` and
`/app/data/segment-shipper.log`.

## Volume sink (active; no Fly login or Tigris bucket needed)

`RESEARCH_SEGMENTS_SINK=volume` (set with `RESEARCH_SEGMENTS_ENABLED=1` in
`fly.toml [env]`) makes the shipper write the same keys write-once and fsynced
to `/app/data/segment-store/v1/{seg,man}/`. The Tigris sink is unchanged and is
selected again by unsetting `RESEARCH_SEGMENTS_SINK` (default `tigris`).

| Where | File | Role |
|---|---|---|
| Fly | `research_segment_store.VolumeStore` | Durable create-if-absent files; no delete/overwrite. |
| Fly | `research_segment_server.py` | WSGI dispatcher mounted *in front of* Flask by `bot.py` (`_mount_research_segment_server`). No Flask hooks, bot state or trade lock; its own reserved worker class (2). |
| Fly | `research_segment_prune.py` | Plan-only prune hook, OFF (see below). |
| laptop | `HttpSegmentSource` + `research-segment-pull.ps1 -Source Http` | Pulls over HTTPS with `BOT_ADMIN_TOKEN` from `vault\home-bot.env`. |
| laptop | `research-segment-pull-loop.ps1` | One instance, kept alive by `DoxxedLaptopChainSupervisor`: pull every 2 min (40-segment batches, back-to-back while a backlog remains), parity every 30 min. Opt out with `C:\DoxxedCrypto\laptop-chain\segment-pull.disabled`. |

Endpoints (`X-Bot-Admin-Token` required; 503 when the token is unset):
`GET /api/research-segments/v1/head`, `GET .../man/<seq>`, `GET .../seg/<seq>`
(streamed in 64 KiB chunks, `ETag` = manifest `segment_sha256`, 304 on
`If-None-Match`), `GET .../ack/<seq>`, `POST .../ack` (canonical laptop ACK
JSON, at most 4 KiB). A segment is published only once its manifest exists.

ACK rules: `through_seq` must be published and `manifest_sha256` must equal the
sha256 of manifest `through_seq` (the chain head the laptop verified). Stored
write-once at `v1/acks/laptop/<seq>.json` with a receipt at
`v1/acks/laptop-receipts/<seq>.json` (`received_at`). Lower seq -> 409
`ACK_REGRESSION`; same seq and hash -> 200 `ALREADY_RECORDED`; hash mismatch ->
409 `ACK_HEAD_MISMATCH`. The laptop logs receipts to
`C:\DoxxedCrypto\fly-mirror-segments\.puller\ack-receipts.jsonl`.

Disk guard: the store duplicates source data until pruning exists, so the
volume sink defaults to a 10 GiB store cap (`RESEARCH_SEGMENTS_VOLUME_MAX_BYTES`,
status `STORE_CAP_REACHED`) and a 4 GiB free-space floor
(`RESEARCH_SEGMENTS_MIN_FREE_BYTES`, status `LOW_DISK_SKIPPED`). Both fail closed
by pausing the shipper only. `/health` `volume.transfer` shows `sink`,
`store_bytes` and `max_store_bytes`.

Prune hook (OFF): `plan_prune` lists only rotated `x.jsonl.N` files that were
shipped whole at `shipped_seq <= laptop-ACKed seq` and are unchanged on disk.
Execution requires `PRUNE_ENABLED` in code **and**
`RESEARCH_SEGMENTS_PRUNE_ENABLED=1` **and** two advancing ACK receipts **and** a
`fly_volume_snapshot_receipt_v1` newer than the covering ACK. The module has no
delete capability; enabling execution is a separate reviewed change.

Hot snapshots: a snapshot copy needs to be stable only while it is read (up to
3 attempts). A file that still changes on every attempt is backed off (60 s,
doubling, capped at 1 h) so it cannot stall other streams; `/head` lists it in
`racing_paths` and `last_error` reads `PLAN_RACE: ...`.

SQLite files (`research.db` ~182 MB, qualification/lifecycle indexes) are never
copied raw: the shipper takes an online backup (`sqlite3` backup API, 1024-page
steps so the bot's rollback-journal writers are not starved, 180 s deadline,
`PRAGMA integrity_check`) into `segment-shipper/sqlite-snapshots/`, streams that
backup alone into its own segment (`consistency=sqlite_online_backup_v1`), and
deletes the scratch copy. A backup that cannot finish is a `PLAN_RACE` with
backoff; nothing is shipped. SQLite members may be up to 512 MiB
(`RESEARCH_SEGMENTS_MAX_SQLITE_BYTES`); snapshots above the 64 MiB regular cap
are re-shipped at most every 6 h (`RESEARCH_SEGMENTS_HUGE_SNAPSHOT_INTERVAL_SECONDS`).
`oversized_paths` is computed during planning so it no longer disappears when a
cycle's byte budget is exhausted first.

Analyzer on the segment shadow: the analyzer reads only a promoted canonical
store (`canonical_dataset_current.json`). `research_segment_promotion.py --view
<empty dir>` copies the shadow tree (holding the puller lock) and writes the
`.fly-sync-state.json` and `.segment-promotion.heartbeat.json` that the
unchanged `scripts/migrate_canonical_research_store.py --source <view>
--heartbeat <view>\.segment-promotion.heartbeat.json --destination
<checkout>\services\btc-conservative-agent\canonical-research-data` accepts.
It refuses unless the laptop has applied every published seq, Fly reports
`unshipped_bytes == 0` with no oversized/racing paths or shipper error, the
shipped revision matches `/health`, and `research_session.json` is present.

## Scoped credentials

Create two access keys in the Tigris dashboard (`fly storage dashboard <bucket>`).
Never print them; paste them straight into the destinations below.

Fly key (write, no delete):

```json
{"Version": "2012-10-17", "Statement": [
  {"Effect": "Allow", "Action": ["s3:PutObject"],
   "Resource": ["arn:aws:s3:::doxed-btc-research/v1/seg/*", "arn:aws:s3:::doxed-btc-research/v1/man/*"]},
  {"Effect": "Allow", "Action": ["s3:GetObject"],
   "Resource": ["arn:aws:s3:::doxed-btc-research/v1/seg/*", "arn:aws:s3:::doxed-btc-research/v1/man/*",
                "arn:aws:s3:::doxed-btc-research/v1/acks/*"]},
  {"Effect": "Allow", "Action": ["s3:ListBucket"], "Resource": ["arn:aws:s3:::doxed-btc-research"],
   "Condition": {"StringLike": {"s3:prefix": ["v1/acks/laptop/*"]}}}
]}
```

Laptop key (read segments, write ACKs only):

```json
{"Version": "2012-10-17", "Statement": [
  {"Effect": "Allow", "Action": ["s3:GetObject"],
   "Resource": ["arn:aws:s3:::doxed-btc-research/v1/seg/*", "arn:aws:s3:::doxed-btc-research/v1/man/*",
                "arn:aws:s3:::doxed-btc-research/v1/acks/laptop/*"]},
  {"Effect": "Allow", "Action": ["s3:PutObject"],
   "Resource": ["arn:aws:s3:::doxed-btc-research/v1/acks/laptop/*"]}
]}
```

`s3:PutObject` alone can overwrite when a client omits the conditional header.
Both clients always send `If-None-Match: *`, and the laptop's chain check plus
its create-new archive detect any substituted object. If Tigris supports the
`s3:if-none-match` condition key, add
`"Condition": {"Null": {"s3:if-none-match": "false"}}` to both PutObject
statements.

## Enable shadow mode (after the deploy freeze lifts)

1. **Bucket.** From a directory with **no** `fly.toml`, and **without** `-a`
   (attaching to the app sets secrets on it, which restarts the trading machine):
   `fly storage create --name doxed-btc-research -o <org> -y > <vault>\tigris-admin.txt`.
   Keep the admin credential in the vault only; it is never deployed.
2. **Keys.** Create the two scoped keys above.
3. **Laptop vault.** Create
   `C:\DoxxedCrypto\doxedcryptofounder-secrets\vault\research-segments-laptop.env`:
   `RESEARCH_SEGMENTS_BUCKET`, `RESEARCH_SEGMENTS_ACCESS_KEY_ID`,
   `RESEARCH_SEGMENTS_SECRET_ACCESS_KEY` (optionally `RESEARCH_SEGMENTS_ENDPOINT`,
   default `https://fly.storage.tigris.dev`).
4. **Fly secrets, staged (no restart):**
   `fly secrets set --stage -a doxed-btc-bot RESEARCH_SEGMENTS_BUCKET=... RESEARCH_SEGMENTS_ACCESS_KEY_ID=... RESEARCH_SEGMENTS_SECRET_ACCESS_KEY=... RESEARCH_SEGMENTS_ENABLED=1`
   Enter the values interactively or from the vault, never on a logged command line.
5. **Deploy** through the normal guarded `fly-bot-deploy.yml` boundary. The
   staged secrets take effect with that restart.
6. **Prove progress** (two advancing cycles, per repair-first monitoring):
   `fly ssh console -a doxed-btc-bot -C "cat /app/data/segment-shipper/status.json"`.
   `shipped_seq` must advance, `last_error` must be null and `pruning_enabled`
   must be false. The genesis backlog ships in 8 MiB segments about every 5 s.
7. **Laptop pull** (idempotent; schedule every 2 min with Task Scheduler):
   `powershell -NoProfile -ExecutionPolicy Bypass -File C:\DoxxedCrypto\btc-v31-current\scripts\research-segment-pull.ps1`
   Run parity separately, e.g. every 30 min and outside legacy publish windows:
   `... research-segment-pull.ps1 -Parity`. The report is written to
   `C:\DoxxedCrypto\fly-mirror-segments\parity-latest.json`.

Optional tuning (Fly env): `RESEARCH_SEGMENTS_INTERVAL_SECONDS` (300),
`RESEARCH_SEGMENTS_MAX_SEGMENT_BYTES` (8 MiB), `RESEARCH_SEGMENTS_MAX_MEMBER_BYTES`
(64 MiB), `RESEARCH_SEGMENTS_LARGE_SNAPSHOT_BYTES` (1 MiB),
`RESEARCH_SEGMENTS_LARGE_SNAPSHOT_INTERVAL_SECONDS` (3600),
`RESEARCH_SEGMENTS_ACK_POLL_SECONDS` (1800), `RESEARCH_SEGMENTS_MIN_FREE_BYTES`
(200 MiB), `RESEARCH_SEGMENTS_PREFIX` (`v1`).

**Rollback:** `fly secrets unset --stage -a doxed-btc-bot RESEARCH_SEGMENTS_ENABLED`,
applied at the next guarded boundary. Objects already in Tigris are harmless.

## Rotation finding (do not shrink rotation yet)

`rotate_log` renames `x.jsonl` to `x.jsonl.N` at 20 MB. Only these readers
include numeric rotations: `_signal_replay_paths`, `_one_second_tape_by_bucket`,
`research/opportunity_backfill.py`, `research/platform_relay_evidence.py`
(newest 128) and `research/source_market_evidence.load_market_evidence_index`
(capped at 128, but `sorted(..., reverse=True)[-128:]` keeps the **oldest** 128).
The generic analyzer `_load_jsonl_rows` (about 40 call sites, including trade
lifecycle, counterfactual, AI input log, signal snapshot, fill quality and
source-order market evidence) reads only the active file. Smaller rotation would silently drop rows from those
loaders. Make the loaders rotation-aware first.

## Fresh-start epoch (v2, 2026-09-30)

Danish approved abandoning the v1 backlog. v2 starts at a genesis `BASELINE`
segment (`RESEARCH_SEGMENTS_BASELINE_GENESIS=1`, new state dir
`/app/data/segment-shipper-v2`): each live append stream is recorded by its
complete-record offset, sha256 of the unshipped prefix and anchors; CSV
streams carry their header line. Top-level runtime state and SQLite DBs
(online backup) ship normally; rotations, per-record directories and other
history are tracked only and ship when they change. The laptop starts a fresh
shadow tree; the puller maps Fly offsets through each stream's baseline.

- Parity: `scripts/research_segment_fly_parity.py --prefix v2` compares the
  tree with the shipper checkpoint (`/api/research-segments/v2/files`) at the
  same seq. GREEN = 0 sealed mismatches, 0 missing shipped files.
- v1 stays readable/ACKable via `RESEARCH_SEGMENTS_ARCHIVE_PREFIXES` so the
  laptop can finish it; only then may the wipe remove it.
- Wipe: dispatch `Deploy Fly BTC bot` mode `fresh-start-wipe-plan` (read-only)
  and review it, then `fresh-start-wipe-execute` (pauses, deletes the
  same-boundary plan pinned by sha256, resumes with every tile ON). It deletes
  only closed rotations beyond the newest two and older than 6 h, closed
  rotations whose last byte predates the v2 genesis by at least 2.5 h, the v1
  epoch once the laptop ACK of its final published seq is on the volume, and
  the retired whole-generation transfer state under `.data-sync-snapshots`
  (bundles, inventory generations, strict/SQLite snapshots, maintenance
  receipts). Sealed `runtime/v3` ledger generations are never candidates. Take
  `snapshot-volume` first.
- The legacy `/api/data-sync/*` transfer routes answer 410
  `LEGACY_DATA_SYNC_RETIRED`; only the inbound `platform-relay-evidence` and
  `analyzer-report` uploads remain. The laptop runs one long-lived loop (the
  segment pull loop); the 30-min supervisor step runs
  `run-segment-analyzer-cycle.ps1` (pull, promote, migrate, analyze).
- Every deploy/restart/maintenance boundary ends with paper resumed and every
  registry tile ON (`fly_postdeploy_active_gate.py`, `--tiles-only` for
  restart/repair jobs); relay/Bitfinex are never armed.

## Backlog mode (2026-10-01)

The shipper runs SCHED_IDLE/nice 19 on a single dedicated core that the bot keeps
~100% busy (load ~3). Measured before the fix: shipper ran 0.009 s per 5 s wall,
every cycle rescanned ~38k files, so one <=8 MiB segment took 100-980 s and the
backlog grew past the laptop promotion gate (32 MiB).

- Above `RESEARCH_SEGMENTS_BACKLOG_BOOST_BYTES` (8 MiB) unshipped, the next cycle
  ships up to `RESEARCH_SEGMENTS_BOOST_SEGMENT_BYTES` (64 MiB raw), back-to-back,
  at `RESEARCH_SEGMENTS_BOOST_NICE` (10, SCHED_OTHER). At or below it the worker
  returns to 8 MiB segments and SCHED_IDLE. A boosted segment is never started if
  it could cross the 10 GiB store cap; the 4 GiB free floor still skips cycles.
- A snapshot that changes mid-copy drops only its own stream from the build; the
  other streams ship in the same cycle (the racing stream backs off as before).
- The scan uses `os.scandir` (one stat per file).
- `/api/research-segments/v2/head` exposes `backlog_mode`,
  `segment_budget_bytes`, `shipper_priority`, `shipper_priority_error`,
  `shipper_worker_state` (`CYCLING`/`SLEEPING`) and `shipper_next_cycle_at`.
  Mode and priority are re-evaluated right after each cycle, so a sleeping
  worker reports the mode its next cycle will run in.
- A file deleted between scan and read is a race for its stream only.
  `v3/lifecycle_worker/pipeline-request-*.json` (transient IPC handoffs) is
  excluded from the universe.
- The laptop pull loop polls every 15 s while the head is ahead of the applied
  seq or reports >8 MiB unshipped, otherwise every 120 s.
- Diagnose with `gh workflow run "Deploy Fly BTC bot" -f mode=inspect-runtime`:
  `SEGMENT_SHIPPER_THROUGHPUT` reports machine busy %, shipper run vs run-queue
  wait, log tail, and a timed dry-run scan/plan with per-stream pending bytes.
- The 32 MiB promotion gate is unchanged: it is ~10 min of peak growth and a
  caught-up shipper holds a few MiB between cycles.
