# Laptop analyzer export (`analyzer_export_v1`)

Written by the laptop analyzer (`analyzer_research_engine_v62.py`, port 9001) at the end of every
completed generation (~every 30 min). It is the machine-readable source of truth for strategy research.
Do not edit files here; nothing in this folder is deleted automatically.

```
analyzer-exports/
  latest/                      current generation (summary.json is replaced last)
  history/<UTC>-<rev12>-<gen12>/   immutable copy of every generation
  latest_export_id.txt         name of the newest history folder
  analyzer_client.py           loader (this README's companion)
  insights_client.py           one-call agent feed (health + Fly + transfer + deploy queue + this export)
```

## Load from Python

```python
import sys; sys.path.insert(0, r"C:\DoxxedCrypto\analyzer-exports")
from analyzer_client import load_latest, StaleExportError

exp = load_latest()                 # raises StaleExportError unless provably current
exp["hypotheses"]                   # pandas DataFrame
exp.summary["generation"]           # revision, epoch, dataset checksum, generation id
exp.checks                          # which freshness / parity checks passed
```

`load_latest()` refuses an export older than 45 min, with any table hash mismatch, whose
generation differs from the analyzer's current `report_manifest.json`, or (when :9001 is up) whose
generation/revision differs from the dashboard. `require_revision="a76a52a"` pins a revision.

## Long-horizon archive and compact Tier A data

```python
from analyzer_client import load_archive, load_tier_a

arc = load_archive(since="2026-09-01")   # C:\DoxxedCrypto\analysis-archive (never auto-deleted)
arc["snapshots"]                          # one row per verified generation snapshot
arc["daily"], arc["weekly"]               # per epoch x tile/family/exit/regime: n, wins, net, mean
arc["compat"]                             # COMPATIBLE / INCOMPATIBLE / TAMPERED per document
tape = load_tier_a("bitfinex_l1_tape_1s", since="2026-09-30", parse=True)   # zstd Parquet per UTC day
```

Raw Tier B blobs (signal/post-exit replay, order multiverse) are deleted on the laptop once Fly
ACK + hash parity + analyzer consumption + a verified snapshot cover them, so the archive and the
generation's `long_horizon_report.json` are the source for anything older than the live window.
Documents with an unsupported `schema_version` are excluded and listed, never silently mixed in.

## HTTP (local, read-only, 127.0.0.1:9001)

- `GET /api/archive` - archive freshness, schema compat, 30-day tile rollups, retention state, long-horizon report
- `GET /history` - History & retention page (`/data-health` also shows archive schema compat)

- `GET /api/export/latest` — summary.json (`?table=hypotheses` returns that table as JSON rows)
- `GET /api/hypotheses` — hypothesis verdicts + exploratory family tests
- `GET /api/streams/health` — mirror stream inventory, freshness and analyzer usage
- `GET /api/insights` — the `insights_client.snapshot()` payload below, uncached

## Agent insights feed (`agent_insights_v1`)

```python
import sys; sys.path.insert(0, r"C:\DoxxedCrypto\analyzer-exports")
import insights_client
snap = insights_client.snapshot()   # ~2-8 s, read-only; same as GET http://127.0.0.1:9001/api/insights
snap["status"]                      # COMPLETE when every component is fresh, else PARTIAL
snap["refused"]                     # [{component, status, reason}] for each STALE / UNAVAILABLE component
snap["system_verdict"], snap["failing_checks"]
snap["components"]["fly_bot"]["data"]["tiles"]   # lane, accepting, Win %, closed trades, corrected verdict
```

| component | source | refused when |
|--|--|--|
| `system_health` | :9011 `/api/system-health`, else `laptop-chain\health\system-health-latest.json` | older than 15 min or unreadable |
| `fly_bot` | live `https://doxed-btc-bot.fly.dev/api/status` (rev, paused, open/pending, AI last success, tile roster) + Win % per tile from `tile_stats` | live call fails and the laptop Fly snapshot is older than 5 min |
| `transfer` | `segment-pull.status.json` + `fly_segment_head_snapshot_v1.json` (published / applied / ACK seq) + the health checks `shipper.progress`, `laptop.pull_ack` | last pull older than 15 min |
| `deploy_queue` | `btc-v31-current\diagnostics\WALL-STATUS-FLY.md` parsed live: Fly slot holder, state (FREE / HELD / DEPLOYING), queue in order, recent DONE | file unreadable |
| `analyzer_export` | `load_latest()` of this folder: per-tile stats with corrected verdicts, hypothesis verdicts, main-ranking family summaries, stream health, exit regret at 1 h, taker EV at 1 s, fill markouts, quarantine | any `load_latest()` refusal (age > 45 min, hash, manifest or live-dashboard mismatch) |

Every component has `status` (OK / STALE / UNAVAILABLE), `reason`, `as_of`, `age_sec`, `max_age_sec`
and `data`. A refused component always has `data: null`: old data is never returned as current.
`fly_bot.data.ai_success_stale` flags an AI last success older than 15 min (the component itself is live).

## Tables

| table | contents |
|--|--|
| `tile_stats` | current-epoch registry tiles: n, after-cost USD PnL, win rate, hour-cluster CI, exit reasons |
| `hypotheses` | pre-registered hypotheses, controls, stress rows: full-epoch and post-registration (unseen) stats, Holm p, verdict |
| `hypothesis_trades` | every simulated trade behind `hypotheses` (CONSERVATIVE_BBO fills, cost decomposition) |
| `family_tests` | bounded exploratory configs: CI, BH q, family-wise max-t null p (`fwer_p`) |
| `walk_forward` | anchored walk-forward folds per exploratory family (embargo = max hold) |
| `correlation` | hourly PnL correlation of each primary hypothesis vs live tiles and vs each other |
| `sim_parity` | live tile trades re-simulated from their real fill; `diff_bp` = sim - live; `atr_basis` = `RECORDED:<basis>` (runtime fill-time ATR from the V3 execution ledger) or `TAPE_REBUILT` |
| `stream_health` | mirror streams: files, rotations, bytes, age, content lag, which analyzer path reads them |
| `quarantine` | trade rows excluded from the current tile cohort, with reason |
| `main_rankings` | every main-ranking row (tiles, top combinations, regime x lane cells, feature correlations, expanding-quintile feature buckets) with p, Holm p, BH q and `corrected_verdict` |
| `exit_regret` | per tile x horizon (5/15/30/60 min after exit): mean USD gained by holding longer, hour-cluster CI, verdict `EXITS_TOO_EARLY` / `EXITS_WELL_TIMED` / `NO_TIMING_EDGE` |
| `exit_regret_trades` | per closed trade: realised exit vs post-exit marks, drift, missed MFE, avoided MAE (% of margin) |
| `taker_counterfactual` | crossing the spread at each AI signal + latency: EV at the exit touch after 1/10/60/300 s, by ALL, AI decision and matched tile |
| `fill_markouts` | post-fill mid markout curves (1/10/60/300 s, bp, signed in trade direction) per tile x liquidity |
| `research_events` | current-epoch research_event_v2.2 rows by tile x outcome x observation status, replay eligibility, last signal |
| `stream_study_health` | the four stream studies: files, bytes, rows used, content last-at, parse seconds, error, plus `archive_files`, `mirror_files`, `tier_a_rows` |
| `event_study_hypotheses` | pre-registered event studies (H1-H5): spec hash, status, lockbox events counted, events/day, days to min sample |
| `data_health_streams` | `data_health_report.json` per stream: status, rows, 24 h coverage, staleness, lag vs mirror head |

`tile_stats` also carries `n_tested`, `p_holm`, `q_bh` and `corrected_verdict` from the `tiles`
family (strategy exits only; admin/deploy/forced closes are excluded from the test).

## Multi-day inputs and coverage

The strategy lab, stream studies and the pooled tile family read the whole current epoch, not just
the mirror's rolling window: frozen archive segments (`C:\DoxxedCrypto\archive\fly-mirror-segments-*\tree`),
compact Tier A partitions and the mirror, unioned per second (tape) or per natural key, with the
mirror winning on overlap, then the archive. Archive files and Tier A partitions are parsed once and
cached by size + mtime. Override with `STRATEGY_LAB_ARCHIVE_DIRS` / `STRATEGY_LAB_TIER_A_ROOT`
(`none` disables); it is off under pytest.

`summary.json["stream_coverage"][<stream>]` (also in `strategy_lab.stream_coverage` and `stream_health.csv`):

| field | meaning |
|--|--|
| `epoch_start` | current dataset epoch start (UTC ISO) |
| `first_available_ts`, `last_ts` | first / last unit present after the union |
| `horizon_hours` | `last_ts - first_available_ts` + one unit |
| `epoch_coverage_share` | present units x unit length / (now - epoch_start): the honest epoch-wide share |
| `in_window_present_share` | present share inside `[first_available_ts, last_ts]` only (the old `present_share`) |
| `sources` | `{tier_a_rows, archive_rows, mirror_rows}` rows read from each source (before dedupe) |

Units: tape 1 s, cross-venue 60 s, AI calls 15 min. Cross-venue-lead hypotheses and their controls are
restricted to the cross-venue span so the treatment and control windows stay identical.

`summary.json["main_rankings"]["tile_pool"]` and the `tiles_pooled` ranking family pool current-epoch
registry-tile trades with archive daily rollups (raw trades win per day x tile, never both). Retired
lanes and `NON_REGISTRY_LANE` are always excluded and listed in `excluded_archive_lanes`.

`summary.json["event_study"]` and `["data_health"]` summarise `event_study_report.json` and
`data_health_report.json`. Every analyzer generation's full report set is also kept under
`C:\DoxxedCrypto\analysis-archive\report-history` (`research/report_history.py`), with the append-only
`event_study_ledger.jsonl` and `data_health_ledger.jsonl` beside it.

## Reading the verdicts

- Primary hypotheses are judged on trades after `registered_at` (data the originating study never saw).
  `INSUFFICIENT` means fewer than 30 unseen trades — not evidence either way.
- `SUPPORTED` needs mean > 0 and Holm-adjusted p < 0.05; `HINT` is mean > 0 with p < 0.2.
- Exploratory families report `family_p` from a family-wise null (block-sign flips for XVL, circular
  shift of the AI direction for AI configs). `FAMILY_SIGNAL` also requires positive walk-forward OOS.
  A best config without these is selection noise.
- Costs: Bitfinex fee profile (`BITFINEX_ZERO`), spread through executable bid/ask, funding when a
  hold crosses 00/08/16 UTC; `S_*` stress rows add latency/slippage. 1 bp = $0.0025 at $0.25 x 100x.
- Main rankings: each ranking is one family. Row p-values come from an hour-cluster t-test on mean
  after-cost PnL (correlations use the Pearson t-transform); Holm controls the family-wise error at 5%
  and BH reports q (discovery at q <= 0.10). `corrected_verdict` is `POSITIVE_FWER` / `NEGATIVE_FWER`
  (Holm), `POSITIVE_FDR` / `NEGATIVE_FDR` (BH only), `NOT_SIGNIFICANT`, or `INSUFFICIENT_N` (< 10 trades,
  not tested). Feature buckets use expanding quintiles from strictly earlier trades (warm-up 30 trades,
  reported as `WARMUP`, never tested): no full-sample look-ahead.
- Research only. Nothing here toggles a tile or arms Bitfinex.
