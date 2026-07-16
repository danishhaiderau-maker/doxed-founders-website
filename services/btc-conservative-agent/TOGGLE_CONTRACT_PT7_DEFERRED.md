# Toggle Contract -- Pt 7 Deferred Items

This file documents the Pt 7 integrity work that is **deferred** from the
toggle-contract branch. Each item is described with enough detail that a
future session can pick it up without re-discovering the scope.

Branch: `codex/toggle-contract`
PR: see GitHub

## Status of Pt 7

| Item | Status | Notes |
|---|---|---|
| 7a Display Tile 1 peak ≥3% → breakeven protection | **DONE** | Commit `pending`. Chip added to TYPE_B Hunter filter_chips. |
| 7b Fix Type B trade-count reconciliation | DEFERRED | No existing reconciliation logic to fix; needs new code. |
| 7c Unify replay and live exit decisions (peak MFE) | DEFERRED | Need to find both code paths and unify. |
| 7d Version the exit profile separately | DEFERRED | `exit_profile_id` exists; add a version bump on changes. |
| 7e Normalize numeric EMA slope / nested regime inputs | DEFERRED | Needs careful review of feature_snapshot pipeline. |
| 7f Separate session / policy-cohort / paper / exchange metrics | DEFERRED | Currently co-mingled in session_stats. |
| 7g Label promotion/kill rules as evaluation criteria | DEFERRED | UI wording only; not auto-enforced. |
| 7h Restore bot/analyzer signal parity | DEFERRED | Needs to compare bot.py vs analyzer output formats. |

## What 7b (Type B trade-count reconciliation) needs

The dashboard reports trade counts per lane. For TYPE_B_HUNTER_V1, these
counts can drift because:
- Paper trades in PAPER mode are counted in `session_stats`
- Shadow outcomes in LAB_SHADOW mode are counted in `shadow_lane_outcome.jsonl`
- Bitfinex fills in LIVE mode would be counted via reconciliation

The reconciliation should:
1. Read `session_stats[TYPE_B_HUNTER_V1]` (paper count)
2. Read `shadow_lane_outcome.jsonl` filtered by `research_lane == TYPE_B_HUNTER_V1`
3. Read `open_positions` filtered by lane (live count)
4. Surface mismatches in `/api/state` so the dashboard shows consistent totals

Implementation hint: add a `_reconcile_type_b_trade_count()` helper next to
`_load_lane_metrics_from_disk()` in bot.py.

## What 7c (replay/live exit parity) needs

Two code paths decide exits:
- `process_positions()` -- live exit decisions per tick
- `_load_post_exit_replays()` + replay processing -- restores in-flight buffers on restart

Both must use the SAME peak MFE calculation. Search for `peak_mfe` and
`compute_trend_health` to find divergent logic.

## What 7e (EMA slope normalize) needs

The `feature_snapshot` pipeline computes EMA slope as a raw numeric value.
Different magnitudes depending on timeframe. Normalize to a [-1, +1] band
or z-score before feeding to scoring functions.

## What 7h (bot/analyzer signal parity) needs

The analyzer service (port 9001) processes signals independently. Its
output format may have drifted from bot.py's `signal_ref` dict. Compare:
- `services/btc-conservative-agent/bot.py` signal_ref schema
- analyzer's signal output schema

Add a parity test that exercises both with the same input features.
