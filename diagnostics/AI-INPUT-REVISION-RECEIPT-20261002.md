# AI input revision r2 receipt, 2026-10-02

The shared direction prompt id stays `shared_direction_conflict_abstain_v4_1_20261001`.
The model, the call cadence (180 s) and the prompt wording are unchanged.

What the model is shown changed, so every call now carries a separate
`prompt_input_revision`. The revisions are listed in
`AI_PROMPT_INPUT_REVISION_HISTORY` in `combo_pathway_config.py`:

| Revision | Meaning |
|---|---|
| `shared_direction_inputs_r1` | Inputs before this change. Rows logged before the field existed are r1. |
| `shared_direction_inputs_r2_20261002` | The three input fixes below. |

## Input fixes (r1 to r2)

1. **`ret_1m` / `ret_5m` were always 0.** `build_pure_ai_context` read two
   deques that nothing ever appended to. They now come from the 1 s Bitfinex
   tape ring (`_ai_shadow_tape_features`), the same source the shadow
   challengers already use. The dead deques were removed.
2. **`lower_high_detected` was always False, and `higher_low` was never set.**
   `compute_market_structure` never set these fields, and the "last two labels"
   check usually saw only low labels. `last_swing_pair_labels` now compares the
   last two swing highs and the last two swing lows separately. It feeds both
   `compute_market_structure` and `build_micro_sr_levels`.
   - `hh_hl_sequence_active` / `lh_ll_sequence_active` now require both sides.
   - The trend-hierarchy and weak-countertrend gates read these flags. They
     only change the shared decision tier. Tile 1 and Tile 2 use the raw
     scores and raw direction.
3. **`volume_ratio` scale.** It was the last trade's size over the mean trade
   size; a live row showed `trend_health.volume_ratio = 0.0031`. It is now the
   last closed 15 m bar's volume over the mean of the 20 closed bars before it.
   The forming bar is excluded, so a normal bar is about 1.0.

Tests: `test_ai_input_revision_r2.py`.

## Logged on every call

`ai_input_log.jsonl` rows, `ai_shadow_challengers.jsonl` CALL rows and
`decision_feature_snapshots.jsonl` now carry these fields:

- `prompt_id` and `prompt_input_revision`;
- `deepseek_model` (requested) and `deepseek_served_model`;
- `raw_direction`, `long_score` and `short_score`;
- the commit/abstain flags from `ai_commit_flags`: `ai_committed`,
  `explicit_abstain`, `score_direction_mismatch`, `score_tie`, `score_gap` and
  `commit_rule`.

A call commits only when the raw direction is LONG or SHORT, it equals the
score-led side, and the score gap is at least 30.

## Duplicate trend labels (documented, prompt unchanged)

Here is one live r1 payload, the newest mirrored `ai_input_log` row at
2026-10-02T06:41Z. The EMA-regime label appears **seven times**:

- `regime`
- `market_context.regime_label`
- `trend_health.trend_state`
- `trend_health.trend_health`
- `trend_health.base_state`
- `trend_health_state` (twice)

Each one reads `BULL`. The structure evidence that disagreed appeared once or twice each:

- `market_structure.structure_bias = BEARISH_STRUCTURE`
- `trend_health.structure_score = -3`
- `bear_score 7 > bull_score 6`
- `trend_score 0`
- `micro_structure.structure_bias = LEAN_BEAR`

`trend_health_detail.interpretation` still said "Bullish and intact". The
prompt tells the model not to anchor on the regime label, but the payload
repeats that label far more than anything else.

De-duplicating the labels would change the prompt text, which needs a new
prompt id and a new cohort. It is deliberately left for a separate
pre-registered change. The H9 logistic challenger uses the de-duplicated
numeric fields instead.

## Compact shadow side rule

Across 425 compact-prompt rows mirrored to 2026-10-02, `p_long_success` was 0.42
in about 95% of rows, so only `p_short_success` moved. The relative-gap rule
`relative_gap_0.04_v2` still produces both sides (3 of 9 LONG under 0820b3a41),
but in practice `p_short` alone decides the side.

`test_ai_shadow_challengers.py` pins the rule on the observed live pairs and on
abstain/invalid rows. The compact prompt is unchanged, because changing it would
need a new prompt id.

## Tile 1 analyzer cohort boundary (policy-identity receipt)

- Tile 1 `FAMILY_TREND_FADE_60` keeps policy signature
  `a0a04faefaba977b203ad0a84117612ca7d487b0ce22f9d55504f3554b927d65`, epoch
  `v31-dynamic-adaptive-ladder-paper-v4` and raw policy
  `INVERT_SCORE_LED_SIDE_SPREADLE1.68BP_TAKER_CAP5BPS|TIME_3600_HARD40BP`. Its
  entry, exit, size and risk are unchanged, and the registry contract test pins
  the signature.
- Its signal is the shared AI call, and r2 changes what that call sees. The
  boundary is therefore per call, not per deploy time:
  - `tile_paired_comparison_report.json` now has `input_revision_cohorts`. It
    splits every AI-fed tile's fills by the revision of their
    `shared_ai_call_id`, joined from the challenger CALL rows. Fills whose call
    is missing from the journal are counted as `UNJOINED`.
  - `ai_challenger_report.json` keys its cohorts as
    `prompt_id@prompt_input_revision`.
- Evidence must not be pooled across r1 and r2.

## Order-book depth (item 6): skipped

Top-10 depth for Bitfinex and the leaders is not collected
(`book_depth_collected = false` in every snapshot). Measured before this change:

- **Machine:** Fly performance-1x, 1 vCPU, 2048 MB.
- **Cross-venue collector:** `cpu_pct_1m` 1.64% with 0 reconnects on three venues
  (`/ready`, 2026-10-02T07:18Z).
- **Open AMBER:** `trading.lifecycle` reports 4 expired-and-filled
  contradictions in 24 h, which the watcher attributes to fill-thread latency.

Streaming L2 deltas from four venues means tens of messages per second parsed
under the same GIL as the fill thread. That would add load exactly where the
open AMBER is. Fly CPU metrics also could not be read from the laptop (no
token), so the cost cannot be measured safely before deploying.

A follow-up option is REST top-10 snapshots taken only at each decision: 4
requests per 180 s, about 0 CPU. It should be a separate measured change. The
snapshot schema already has the flag.
