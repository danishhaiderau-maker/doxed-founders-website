# Tile 2 (SR_MICRO_TILE_V2_STATIC) Promotion Rules

**Frozen:** `sr_micro_static_long_adx40_no_london_v1` · exit `scenario_c_ladder_12_to_10_v1`

This document defines the ONLY conditions under which the Tile 2 lane may
be promoted out of PROBATION / PAPER_ONLY. The operator makes the final
call; nothing here is automated.

These rules are documentation only — **no promotion logic is implemented
in code.** Promotion is a human decision based on the fresh independent
holdout sample.

---

## Why this document exists

The 346-row historical sample was used to *tune* the Tile 2 policy (ADX
cap, London blacklist, SHORT disable, etc.). It is **in-sample training
data**. Promoting the lane on the same data it was tuned on is not a
valid test. The rules below exist to make sure promotion is justified by
fresh data the policy has never seen.

The historical sample is preserved as an archived training cohort. It is
NOT deleted, but it MUST NOT be used as evidence for promotion.

---

## Minimum requirements for promotion

Tile 2 MAY be promoted only when ALL of the following are true on the
fresh independent holdout cohort (started after `POST /api/tile2/reset_counters`):

1. **Sample size.** At least **75 independent reconciled filled closes**
   after the corrected process restarts. "Independent" means deduped by
   `sr_episode_id` — see the "No duplicate episode inflation" rule below.

2. **EV per fill.** Positive **EV per filled close** on the reconciled
   holdout sample (paper P&L ÷ filled closes > 0).

3. **EV per eligible opportunity.** Positive **EV per eligible LONG
   opportunity** (paper P&L ÷ eligible_long > 0). This is the harder bar:
   it rewards selectivity, not just fills.

4. **Fill rate.** **Fill rate ≥ 25%** (filled closes ÷ paper limits). A
   strategy that almost never fills is not a real strategy even if its
   shadow P&L looks good.

5. **More than one session and market regime.** The holdout must span
   multiple UTC session buckets (not just ASIA, not just OVERLAP) and
   more than one market regime. A strategy that only works in one
   session/regime is not robust.

6. **No duplicate episode inflation.** Independent S/R episodes (deduped
   by `sr_episode_id`, which is support level + pivot revision + UTC
   bucket) must be ≥ some sensible fraction of the raw events. If 75
   "independent" fills come from only 5 distinct price levels, that is
   not 75 independent data points. The promotion call must include a
   distinct-episode count.

7. **Same-window comparison against CONTINUOUS.** The holdout's EV per
   fill and EV per eligible must be compared against CONTINUOUS over
   the SAME time window, not against an arbitrary historical baseline.

8. **Replay/live exit parity passing.** The shared exit-decision helper
   `should_skip_fast_cut_for_mfe_protection()` (Section 5) must be in
   use, and there must be no unresolved discrepancy between LAB replay
   PnL and live position PnL for the same trade.

9. **Truthful exit classifications.** Outcomes must be classified by
   genuine strategy exits (Scenario C / thesis / stop / TIME_EXIT /
   TAKE_PROFIT / THESIS_FAST_CUT). Outcomes classified as
   `BUFFER_TRUNCATED` or `MARK_TO_MARKET` are excluded from the
   reconciled sample.

---

## What does NOT count toward promotion

- **Any historical row.** The 346-row archived training sample is
  explicitly excluded.
- **Outcomes from before the corrected process restart.** Only rows
  with `policy_id == sr_micro_static_long_adx40_no_london_v1` and a
  matching `entry_policy_hash` count.
- **Outcomes from the old buggy replay path.** Pre-fix rows where
  MFE protection could not fire (`unreal >= mfe_protect` bug) are
  explicitly excluded. See Section 5 of the static integrity repair.
- **Outcomes classified as MARK_TO_MARKET or BUFFER_TRUNCATED.** See
  rule 9 above.
- **SHORT outcomes.** SHORT is fully disabled by the frozen policy;
  any SHORT row in the holdout is a bug.
- **Outcomes from the LONDON blackout window (08:00–12:59 UTC).**
  Any LONDON row in the holdout is a bug.

---

## The 12→10 ladder is held frozen separately

The Scenario C 12→10 ladder has only **19 historical fills** in the
training sample. It is tagged with `exit_profile_id =
scenario_c_ladder_12_to_10_v1_provisional` and held frozen for the
holdout.

It is evaluated as a **separate exit-profile cohort**, NOT folded into
the main Tile 2 numbers. The promotion decision for the main 12→10
ladder and for the provisional 12→10 ladder are independent.

---

## When SHORT may be reconsidered

SHORT is fully disabled from Tile 2 execution and the dashboard.
SHORT-related code is **archived, not deleted**. SHORT may be
reconsidered only when:

- LONG alone has achieved 75 independent profitable fills (the main
  promotion gate), OR
- A genuinely new SHORT hypothesis emerges (not just "let's try SHORT
  again").

Until then, the most we add is a lightweight rejected-setup counter
(once per unique S/R episode, no order simulation, kept separate from
Tile 2 statistics).

---

## How to read the metrics

The dashboard's "Tile 2 funnel" row and the `GET /api/tile2/metrics`
endpoint return the full metric set:

- `bracket_evals` — every `evaluate_bracket()` invocation
- `eligible_long` — LONG leg qualified (post-midpoint, post-structure, post-ADX)
- `paper_limits` — local paper resting limits actually submitted
- `filled_closes` — filled positions that closed
- `ttl_expiries` — 30-minute resting limit expired unfilled
- `cancellations` — structural cancel (SR invalid, ADX blow, etc.)
- `independent_episodes` — distinct `sr_episode_id` count
- `fill_rate` — filled_closes ÷ paper_limits
- `paper_pnl_usd` — sum of closed LAB PnL for the lane
- `ev_per_eligible_opportunity` — paper_pnl_usd ÷ eligible_long
- `ev_per_filled_close` — paper_pnl_usd ÷ filled_closes
- `cohort_label` — the policy + exit-profile identifier

The operator must reconcile these against the raw
`shadow_lane_outcome.jsonl` rows before making the promotion call. The
counters are a convenience, not the source of truth — the JSONL rows
are.
