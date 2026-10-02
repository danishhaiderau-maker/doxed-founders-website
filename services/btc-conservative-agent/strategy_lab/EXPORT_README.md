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

## HTTP (local, read-only, 127.0.0.1:9001)

- `GET /api/export/latest` — summary.json (`?table=hypotheses` returns that table as JSON rows)
- `GET /api/hypotheses` — hypothesis verdicts + exploratory family tests
- `GET /api/streams/health` — mirror stream inventory, freshness and analyzer usage

## Tables

| table | contents |
|--|--|
| `tile_stats` | current-epoch registry tiles: n, after-cost USD PnL, win rate, hour-cluster CI, exit reasons |
| `hypotheses` | pre-registered hypotheses, controls, stress rows: full-epoch and post-registration (unseen) stats, Holm p, verdict |
| `hypothesis_trades` | every simulated trade behind `hypotheses` (CONSERVATIVE_BBO fills, cost decomposition) |
| `family_tests` | bounded exploratory configs: CI, BH q, family-wise max-t null p (`fwer_p`) |
| `walk_forward` | anchored walk-forward folds per exploratory family (embargo = max hold) |
| `correlation` | hourly PnL correlation of each primary hypothesis vs live tiles and vs each other |
| `sim_parity` | live tile trades re-simulated from their real fill; `diff_bp` = sim - live |
| `stream_health` | mirror streams: files, rotations, bytes, age, content lag, which analyzer path reads them |
| `quarantine` | trade rows excluded from the current tile cohort, with reason |

## Reading the verdicts

- Primary hypotheses are judged on trades after `registered_at` (data the originating study never saw).
  `INSUFFICIENT` means fewer than 30 unseen trades — not evidence either way.
- `SUPPORTED` needs mean > 0 and Holm-adjusted p < 0.05; `HINT` is mean > 0 with p < 0.2.
- Exploratory families report `family_p` from a family-wise null (block-sign flips for XVL, circular
  shift of the AI direction for AI configs). `FAMILY_SIGNAL` also requires positive walk-forward OOS.
  A best config without these is selection noise.
- Costs: Bitfinex fee profile (`BITFINEX_ZERO`), spread through executable bid/ask, funding when a
  hold crosses 00/08/16 UTC; `S_*` stress rows add latency/slippage. 1 bp = $0.0025 at $0.25 x 100x.
- Research only. Nothing here toggles a tile or arms Bitfinex.
