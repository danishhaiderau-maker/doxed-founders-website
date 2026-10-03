# Data retention runbook (Fly ≤ a few GB, laptop ≤ 120 GB)

Retention never deletes anything that has not been proven safe by custody. Historical evidence stays
immutable: the analysis archive is never auto-deleted, and relay/Bitfinex evidence, current-epoch live
state, ledgers and restart-recovery state are `PROTECTED` by `data_retention_policy.py` on both sides.

## Custody chain (what must be true before any byte is deleted)

1. Fly ships a segment; the laptop puller applies it and **ACKs** it.
2. Laptop hash **parity** is GREEN for that segment prefix.
3. The laptop analyzer has **consumed** it: the generation froze the promotion heartbeat at start, so the
   archive snapshot's `segment_seq_through` never overclaims.
4. A **verified archive snapshot** (`C:\DoxxedCrypto\analysis-archive\generations\…`, receipt hashes match)
   covers the window.
5. The laptop issues a `research_segment_custody_receipt_v1` with
   `through_seq = min(acked, parity_seq, analyzer_consumed_seq)` plus `verified_files` (laptop sha256 ==
   Fly checkpoint sha256) and POSTs it to Fly `/api/research-segments/<prefix>/custody` (write-once).

Fly prunes only `seq ≤ min(custody.through_seq, latest ACK)` and only after two advancing ACK cycles.

## Fly (`research_segment_prune.py`, run by the shipper between cycles)

| What | Rule |
|--|--|
| Segment bytes | seq ≤ bound and ≥ 6 h old; manifests kept; reads return `410 PRUNED` |
| Runtime rotations | sealed snapshot shipped at seq ≤ bound, in `verified_files` with equal sha, unchanged since shipped, not baseline; Tier B ≥ 12 h (keep newest 2), others ≥ 24 h (keep newest 3); the 1 s tape (market_microstructure_1s) ≥ 15 days because a restarted bot rebuilds its 14-day minute-bar store from those rotations |
| Never | `PROTECTED` paths, live files, `chase_offset_touch_grid` / `order_multiverse_entry_grid` rotations |
| Per pass | ≤ 4 GiB |
| Ledger | `runtime/retention/prune_ledger.jsonl` (append-only, ships to the laptop) |

Mode: env `RESEARCH_SEGMENTS_PRUNE_ENABLED=1` is the master switch; `<state_dir>/prune-mode.json`
selects `off | dry_run | enforce` (default `RESEARCH_SEGMENTS_PRUNE_DEFAULT_MODE=dry_run`). Inspect and
switch with the admin token (never echo it):

```powershell
$h = @{ "X-Bot-Admin-Token" = $env:BOT_ADMIN_TOKEN }
Invoke-RestMethod https://doxed-btc-bot.fly.dev/api/research-segments/v2/prune-mode -Headers $h   # mode + latest plan
Invoke-RestMethod https://doxed-btc-bot.fly.dev/api/research-segments/v2/prune-mode -Headers $h -Method Post `
  -ContentType application/json -Body '{"mode":"enforce"}'
```

The head (`/api/research-segments/v2/head`) reports `pruning_enabled`, `prune_mode`,
`pruned_through_seq`, `custody_through_seq` and `prune_deleted_bytes_total`; the 48 h unattended proof
fails if `pruned_through_seq` ever exceeds custody or the ACK.

## Laptop (`bot_data_retention.py`, run by `run-segment-analyzer-cycle.ps1` after a successful analyzer pass)

- **Tier A** (L1 tape, cross-venue/market context, liquidations, AI call logs…): compacted to
  `C:\DoxxedCrypto\bot-data-compact\tierA\<dataset>\v<schema_version>\date=YYYY-MM-DD\part-*.parquet`
  (zstd-9, columns `ts` float64 + raw `row`), each with a manifest sha256. Raw rotations are deleted only
  under cap pressure and only once compaction covers them.
- **Tier B** (signal/post-exit replay, order multiverse): every copy (shadow tree, promotion view,
  canonical) of a rotation is deleted once custody gates pass and it is ≥ 24 h old. Each copy's size and
  sha must match or nothing is deleted.
- **Segment archive** (`C:\DoxxedCrypto\fly-segments`): segment bytes ≤ custody bound and ≥ 7 days old.
- **Cap**: 120 GB over the managed roots (`--cap-gb` overrides). AMBER at 80 %, RED at 90 %. Under
  pressure the order is legacy → Tier B (any age) → segment archive (any age) → Tier A raw covered by
  compaction → Tier A partitions with a final daily rollup. The analysis archive is never touched.
  ≤ 20 GB per run.
- **Protected floor**: cap pressure never deletes legacy, Tier A raw or Tier A compact data newer
  than `min(now − 14 days, current collection epoch start)` (epoch from `research_session.json`;
  unknown epoch = nothing older is deletable), and never the ledger datasets (`v3_*`, closed trades,
  trade outcomes/lifecycle, fills, expired orders, decisions, quarantine receipts). If the cap can only
  be met by crossing the floor the run refuses, `cap.status` = `CAP_EXCEEDED_PROTECTED_FLOOR`, `level`
  = RED with an `alarm`, and self-aware `data.capacity` goes RED.
- Gates fail closed: parity GREEN for the same prefix, parity seq ≤ ACK, verified snapshot, analyzer
  consumed seq present, Fly `/files` checkpoint fetched. `deny_reasons` lists every closed gate.

```powershell
python services\btc-conservative-agent\bot_data_retention.py --data-root <canonical-research-data> --mode dry-run
python services\btc-conservative-agent\bot_data_retention.py --data-root <canonical-research-data> --set-mode enforce
```

State lives in `C:\DoxxedCrypto\bot-data-retention\`: `mode.json`, `dry-run-latest.json` (every
candidate and every kept file with its reason), `status.json`, `last-run.json`, `prune-ledger.jsonl`.

## Schema versions

Every Tier A dataset and archive document carries `schema_version`. A supported old version is upgraded
by a registered converter (`analysis_archive.CONVERTERS`, mirrored in `strategy_lab/client.py`); anything
else is `INCOMPATIBLE`, excluded from analysis, moved to `legacy/`, and deleted first under cap pressure.
Status is on :9001 `/data-health`, `/history`, `/api/archive`, and the insights `analysis_archive` component.

## Using the archive

`analyzer_client.load_archive(since=)` / `load_tier_a(dataset, since=)` (see `EXPORT_README.md`). Every
generation writes `long_horizon_report.json`: archive rollups + current raw trades, deduplicated per
(day, epoch), with per-tile/family trends and suggestions.

## Alarms

`scripts/system_health.py` checks `storage.retention` (last run fresh, level not RED, no persistent deny)
and `archive.freshness` (latest snapshot ≤ 3 h AMBER / 12 h RED). See `SYSTEM_HEALTH_RUNBOOK.md`.

## Rollback

- Fly: POST `{"mode":"off"}` to `prune-mode` (or unset `RESEARCH_SEGMENTS_PRUNE_ENABLED`). Pruned segment
  bytes are gone from Fly but remain on the laptop (`fly-segments`) for ≥ 7 days and in the shadow tree.
- Laptop: `--set-mode dry-run`. Deleted Tier B rotations are reconstructible only from their archive
  snapshot results; there is no undelete, so never bypass the gates.
