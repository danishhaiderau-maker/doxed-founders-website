# Per-signal research table and walk-forward scorer (PR-D)

Status: offline research tool, PAPER / analysis only. Nothing here runs on Fly, imports `bot.py`, places orders or
touches the relay. It implements recommendation 3 of `SYSTEM-REVIEW-20261004.md`: one per-signal table with forward
price paths from the bot's own 1 s tape, so entry / exit variants are tested offline as queries instead of as live
tiles.

| Piece | Path | Output |
|---|---|---|
| Table builder | `services/btc-conservative-agent/research/signal_research_table.py` | `per_signal_research_table.csv.gz` + `.manifest.json` |
| Variant scorer | `services/btc-conservative-agent/research/walk_forward_scorer.py` | `walk_forward_scores.json`, `walk_forward_scores.md`, `walk_forward_trades.jsonl.gz` |
| Tests (synthetic fixture) | `services/btc-conservative-agent/test_signal_research_table.py` | run by the `Laptop tests` workflow |

## Running it

From `services/btc-conservative-agent` (needs only numpy; Python 3.12):

```bash
# 1. table: every AI call, cross-venue signal and tile entry candidate, with forward paths
python -m research.signal_research_table --data-dir <runtime copy> --out-dir out/
#    optional: --tape-dir <dir with market_microstructure_1s.jsonl*>  --since-utc 2026-10-04T01:40:00Z

# 2. scorer: entry x exit variants, REALISTIC_V1 fills, day-by-day walk-forward
python -m research.walk_forward_scorer --table out/per_signal_research_table.csv.gz \
    --data-dir <runtime copy> --out-dir out/score/
#    optional: --kinds AI_CALL XVENUE_SIGNAL TILE_ENTRY  --latency-sec 1.2  --since-utc ...
```

On the 4 Oct sample (9 tape rotations, 360k seconds, 9.9k signals) the table takes about 10 s and the full 192-variant
score about 25 s on the box.

Inputs (all optional except the tape; files are read from a copy of Fly `runtime/` or the laptop shadow tree):

| File | Used for |
|---|---|
| `market_microstructure_1s.jsonl*` (all rotations) | the tape: L1 bid/ask, top size, 1 s aggressor VWAPs (`market_microstructure_1s_v1`) |
| `decision_feature_snapshots.jsonl`, `adaptive_entry_decisions.jsonl`, `taker_signal_counterfactuals.jsonl`, `ai_input_log.jsonl` | AI calls (`scan-*`): time, raw decision/direction, scores, logged commit flag |
| `adaptive_entry_decisions.jsonl` (`xvl-*`, `xvp-*`, `xvs-*`), `xvs_shadow_signals.jsonl` (qualifying triggers) | cross-venue signals |
| `trades_3factor.csv`, `expired_orders_3factor.csv` | tile entry candidates (filled / expired) |
| `v3/ledgers/{opportunity,order_intent}.jsonl` | measured signal -> first submit latency (`fill_model.measure_decision_latency`; 6 s default if absent) |

## Table schema `per_signal_research_table_v1`

One row per signal. CSV, header = `table_columns()`; empty cell = null. Booleans are `true` / `false`.

### Conventions

* **bp** = basis points of the anchor price, 4 decimals.
* **Anchor** = the first fresh, valid quote at or after the signal second, within 5 s (the REALISTIC_V1 taker wait).
  `anchor_lag_sec` = anchor second - signal time.
* **Horizon sample** at `h` = forward-filled quote at `anchor_ts + h`. It is null when the last valid quote is older
  than 60 s at that second (tape hole, bot down) or the second is past the end of the tape.
* **MFE / MAE** include the anchor itself, so `mfe_* >= 0` and `mae_* <= 0`. They run over every second from the anchor
  to the horizon, ignoring seconds whose quote is older than 60 s.
* **Direction-signed** columns (`dir_*`) use the row's `direction`; they are null when `direction` is `NONE`.
* Horizons: `1s 5s 15s 30s 1m 3m 5m 15m 30m 60m 90m 120m 240m` (= 1, 5, 15, 30, 60, 180, 300, 900, 1800, 3600, 5400,
  7200, 14400 s).

### Identity and decision fields

| Column | Meaning |
|---|---|
| `schema` | `per_signal_research_table_v1` |
| `signal_id` | `scan-*` (AI call), `xvl-*` / `xvp-*` / `xvs-*` (cross-venue), `trade:<trade_id>` / `expired:<trade_id>` (tile candidate) |
| `signal_kind` | `AI_CALL`, `XVENUE_SIGNAL`, `TILE_ENTRY` |
| `signal_ts`, `signal_utc`, `utc_day`, `utc_hour` | signal time (epoch s, ISO UTC, UTC day used for walk-forward, hour) |
| `session` | `ASIA` (UTC 0-8), `EU` (8-16), `US` (16-24), as `combo_pathway_config.HYPOTHESIS_SESSION_HOURS_UTC` |
| `direction` | AI raw direction (`AI_CALL`), trigger side (`XVENUE_SIGNAL`), recorded tile side (`TILE_ENTRY`); `NONE` if no side |
| `score_led_side`, `long_score`, `short_score`, `score_gap` | AI scores; `score_led_side` is `NONE` on a tie |
| `ai_raw_decision`, `ai_raw_direction` | AI output as logged |
| `ai_committed` | `bot.ai_commit_flags` rule: explicit LONG/SHORT = score-led side and gap >= 30 (logged value preferred) |
| `ai_explicit_aligned` | explicit LONG/SHORT = score-led side, any gap (the Danish / committed-fade tile definition) |
| `explicit_abstain`, `score_direction_mismatch`, `score_tie` | AI commit facts |
| `counterfactual_direction` | side used by `taker_signal_counterfactuals` for that call |
| `xv_trigger_source` | `CROSS_VENUE_LEAD`, `CROSS_VENUE_PREMIUM`, `PREMIUM`, `LEAD` ... |
| `research_lane`, `parent_signal_id`, `tile_trade_id`, `tile_filled`, `tile_entry_type`, `tile_limit_price`, `tile_recorded_pnl_bp` | tile candidate fields; `tile_recorded_pnl_bp` = ledger `pnl` (margin % = bp at 100x) |
| `epoch_id` | data epoch as logged (filter pre-epoch rows with it or `--since-utc`) |
| `sources` | `|`-joined input files that mentioned the signal |

### Market state at the anchor

| Column | Meaning |
|---|---|
| `tape_status` | `OK`, `OUTSIDE_TAPE`, `NO_FRESH_ANCHOR` (paths are null unless `OK`) |
| `anchor_ts`, `anchor_lag_sec`, `anchor_bid`, `anchor_ask`, `anchor_mid`, `anchor_last` | anchor quote |
| `spread_bp` | (ask - bid) / bid |
| `l1_imbalance` | (bid_qty - ask_qty) / (bid_qty + ask_qty) at the anchor |
| `ret_prior_1m_bp`, `ret_prior_15m_bp`, `ret_prior_60m_bp` | mid return into the anchor (regime / trend label) |
| `range_prior_15m_bp` | high-low mid range over the previous 15 min (volatility label) |
| `path_complete_sec` | last forward second (from the anchor, capped at 14400) before the first quote older than 60 s |

### Forward path, per horizon `h`

| Column | Meaning |
|---|---|
| `mid_{h}_bp` | mid(h) vs anchor mid (+ = price up) |
| `bid_{h}`, `ask_{h}` | executable quotes at h |
| `mfe_up_{h}_bp`, `mae_dn_{h}_bp` | highest / lowest mid up to h vs anchor mid |
| `long_taker_{h}_bp` | buy the anchor ask, sell bid(h): taker round trip, no latency |
| `short_taker_{h}_bp` | sell the anchor bid, buy back ask(h) |
| `dir_{h}_bp`, `dir_mfe_{h}_bp`, `dir_mae_{h}_bp` | mid path, MFE and MAE signed by `direction` |

The manifest (`per_signal_research_table_manifest_v1`) carries the row counts per kind, the horizons, tape coverage
(first/last UTC, rows, valid-quote share, fingerprint), the thresholds above and the REALISTIC_V1 fill-model
declaration.

## Scorer `walk_forward_variant_scores_v1`

A variant is `SIDE_RULE|ENTRY|EXIT`. Each (signal, variant) is simulated on the 1 s tape with the repo's REALISTIC_V1
functions from `research/fill_model.py`, so results follow the same fill rules as the paper ledgers:

* **Taker entry** `TAKER`: `fill_model.taker_fill_rows` - opposite BBO of the first fresh quote at signal + measured
  latency (p50 of signal -> initial submit), with the no-L2 size walk.
* **Maker entries** `MAKER_TOUCH_5M` (limit at the touch at arrival, 5 min TTL) and `MAKER_10BP_30M` (10 bp better than
  the last price, never past the touch, rounded passively, 30 min TTL): `fill_model.maker_fill_rows` - filled only
  on a trade-through or at-limit aggressor volume beyond the estimated queue; a touch is not a fill. Unfilled = no
  trade (counted in `fill_rate`).
* **Exits**, first trigger wins in the runtime order HARD_STOP -> BREAKEVEN_LOCK -> ATR_TRAIL -> EARLY_CUT ->
  TIME_EXIT, evaluated each second on the executable-side margin path (bid for LONG, ask for SHORT) and booked with
  `fill_model.realistic_exit_margin` (the worse of the trigger mark and the mark one exit-latency later):
  `TIME_15M`, `TIME_60M`, `TIME_90M`, `STOP40_TIME90`, `STOP40_BE20TO5_TIME90`, `STOP40_BE_EARLYCUT_TIME90` (cut at
  -12 bp within 5 min while MFE <= +2 bp), `STOP40_TRAIL15_10_TIME90` (10 bp trail armed at +15 bp; a bp trail, not
  ATR), `STOP20_TIME30`. A trade whose time exit falls past the tape end is `CENSORED_TAPE_END` and not scored.
* **Fees**: `fill_model.fee_fields` maker/taker rates on entry and a taker exit (BITFINEX_ZERO today, applied
  explicitly so a fee change flows through).
* **Side rules**: `AI_COMMITTED_FADE`, `AI_COMMITTED_FOLLOW`, `AI_COMMITTED_GAP30_FADE`, `NOTRADE_SCORE_FOLLOW`,
  `ALL_SCORE_LED_FOLLOW`, `AI_RANDOM_SIDE` (hash-random side: the AI ablation baseline from review section 5.4),
  `XVENUE_FOLLOW`, `TILE_AS_RECORDED`. Definitions are in `SIDE_RULES` and echoed in the output `variant_grid`.

Output JSON keys:

| Key | Meaning |
|---|---|
| `variants.<id>` | `signals`, `fills`, `closed`, `fill_rate`, `mean_bp`, `median_bp`, `std_bp`, `ci95_1h_cluster`, `ci95_day_cluster` (>= 3 days), `hit_rate`, `sum_bp`, `worst_bp`, `best_bp`, `median_hold_sec`, `maker_share`, `exit_reasons`, `per_day.{day}.{n,mean_bp,sum_bp}`, `days`, `days_positive`, `daily_mean_std_bp`, `worst_day_mean_bp`, `max_day_share_of_profit` |
| `walk_forward.<group>` | expanding-window day walk-forward. For each test day from the second UTC day on, pick the variant in the group with the best mean over the earlier days alone (>= 30 closed trades), score it on the test day. `steps[]` = `test_day`, `chosen`, `train_mean_bp`, `train_n`, `test_n`, `test_mean_bp`; `oos` = pooled out-of-sample `closed`, `mean_bp`, `median_bp`, `ci95_1h_cluster`, `hit_rate`, `per_day`. Groups: one per side rule plus `ALL_NON_RANDOM_VARIANTS` |
| `latency`, `fill_model`, `notional_usd`, `variant_grid`, `days`, `min_train_trades`, `bootstrap_resamples` | run declaration |

`walk_forward_trades.jsonl.gz` (`walk_forward_variant_trade_v1`) has one record per (signal, variant): entry status,
liquidity, fill basis, fill time and price, fill delay, exit reason, hold, gross / fee / net bp, MFE / MAE.

## How Grok Strategist should read it

1. Judge a hypothesis on the `walk_forward` rows and on pre-registered variants only. The in-sample variant ranking
   covers about 190 variants; its top rows are expected to look good by chance (multiple testing).
2. Signals are scored independently with no concurrency cap. AI calls every 3 minutes with 60-90 minute holds
   overlap heavily, so trades within an hour are correlated. Use the 1 h-cluster CI, never a per-trade CI.
3. Compare every AI rule with `AI_RANDOM_SIDE` over the same calls and exits. An AI rule that does not beat the random
   side out of sample is not an AI edge.
4. Check `fill_rate` and `maker_share` beside the bp. A maker variant that fills only when the market runs through its
   limit is adversely selected, and its bp already includes that.
5. Filter the epoch with `--since-utc` (or `epoch_id`) for clean-epoch claims. The tape and ledgers on Fly still hold
   pre-epoch rows (#420).
6. The tape has rare one-second quote glitches (for example a 38 bp spike that reverts the next second on 2 Oct
   04:20 UTC). REALISTIC_V1 consumes the same tape, so the table keeps them; a stop can trigger on one.
7. The 30 Sep to 4 Oct sample spans high-volatility, trending days. Results from it are hints, not evidence.
