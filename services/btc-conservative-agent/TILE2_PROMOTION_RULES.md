# Tile 2 (SR_MICRO_TILE_V2_STATIC) Promotion Rules

**Frozen:** `sr_micro_static_dual_leg_normalized_adx_vol_v2_20260720` · exit `scenario_c_ladder_12_to_10_v1`

This document defines the ONLY conditions under which the Tile 2 lane may
be promoted out of PROBATION / PAPER_ONLY. The operator makes the final
call; nothing here is automated.

These rules are documentation only — **no promotion logic is implemented
in code.** Promotion is a human decision based on the fresh independent
holdout sample.

---

## Why this document exists

The 346-row historical sample was used to *tune* the earlier LONG-only
Tile 2 policy (ADX cap, London blacklist, SHORT disable, etc.). It is
**in-sample training data**. The first normalized-indicator cohort starts
under a new policy ID so those rows cannot be mixed with the holdout.
Promoting the lane on the same data it was tuned on is not a valid test.
The rules below exist to make sure promotion is justified by fresh data
the policy has never seen.

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

3. **EV per eligible opportunity.** Positive **EV per eligible direction
   opportunity** (paper P&L ÷ eligible_total > 0). This is the harder bar:
   it rewards selectivity, not just fills.

4. **Directional integrity.** LONG and SHORT results must be reported
   separately. Neither side may hide a materially negative EV behind the
   other side's gains.

5. **Fill rate.** **Fill rate ≥ 25%** (entry fills ÷ paper limits). A
   strategy that almost never fills is not a real strategy even if its
   shadow P&L looks good.

6. **More than one session and market regime.** The holdout must span
   multiple UTC session buckets (not just ASIA, not just OVERLAP) and
   more than one market regime. A strategy that only works in one
   session/regime is not robust.

7. **No duplicate direction-slot inflation.** Independent direction slots
   are deduped by `sr_episode_id + direction`. If 75
   "independent" fills come from only 5 distinct price levels, that is
   not 75 independent data points. The promotion call must include a
   distinct-episode count.

8. **Same-window comparison against CONTINUOUS.** The holdout's EV per
   fill and EV per eligible must be compared against CONTINUOUS over
   the SAME time window, not against an arbitrary historical baseline.

9. **Replay/live exit parity passing.** The shared exit-decision helper
   `should_skip_fast_cut_for_mfe_protection()` (Section 5) must be in
   use, and there must be no unresolved discrepancy between LAB replay
   PnL and live position PnL for the same trade.

10. **Truthful exit classifications.** Outcomes must be classified by
   genuine strategy exits (Scenario C / thesis / stop / TIME_EXIT /
   TAKE_PROFIT / THESIS_FAST_CUT). Outcomes classified as
   `BUFFER_TRUNCATED` or `MARK_TO_MARKET` are excluded from the
   reconciled sample.

---

## What does NOT count toward promotion

- **Any historical row.** The 346-row archived training sample is
  explicitly excluded.
- **Outcomes from before the normalized-indicator process restart.**
  Only rows with
  `policy_id == sr_micro_static_dual_leg_normalized_adx_vol_v2_20260720` and a
  matching `entry_policy_hash` count. Rows from
  `sr_micro_static_long_adx40_no_london_v1` remain archived as the
  historical baseline and cannot promote this cohort.
- **Outcomes from the old buggy replay path.** Pre-fix rows where
  MFE protection could not fire (`unreal >= mfe_protect` bug) are
  explicitly excluded. See Section 5 of the static integrity repair.
- **Outcomes classified as MARK_TO_MARKET or BUFFER_TRUNCATED.** See
  rule 10 above.
- **Rows from the old LONG-only policy.** Those remain a separate archived
  cohort and cannot be pooled into the dual-leg result.
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

## Dual-leg operating contract

When the tile is ON, the showcase paper book may maintain one $20 LONG
resting at support and one $20 SHORT resting at resistance. A pending
order or open position blocks only a replacement on the same direction.
Turning the tile OFF removes order eligibility but preserves LAB/shadow
collection and historical statistics.

Bitfinex has one merged BTC-PERP position. The platform relay must remain
fail-closed against simultaneous opposing copied exposure; exact dual-leg
exchange replication requires separate subaccounts or a venue-supported
atomic entry structure proven safe for this use case.

---

## How to read the metrics

The dashboard's "Tile 2 funnel" row and the `GET /api/tile2/metrics`
endpoint return the full metric set:

- `bracket_evals` — every `evaluate_bracket()` invocation
- `eligible_long` — LONG leg qualified (post-midpoint, post-structure, post-ADX)
- `eligible_short` — SHORT leg qualified in the same independent cohort
- `eligible_total` — eligible LONG + eligible SHORT direction slots
- `paper_limits` — local paper resting limits actually submitted
- `filled_closes` — filled positions that closed
- `ttl_expiries` — 30-minute resting limit expired unfilled
- `cancellations` — structural cancel (SR invalid, ADX blow, etc.)
- `independent_episodes` — distinct `sr_episode_id` count
- `fill_rate` — entry_fills ÷ paper_limits
- `paper_pnl_usd` — sum of closed LAB PnL for the lane
- `ev_per_eligible_opportunity` — paper_pnl_usd ÷ eligible_total
- `ev_per_filled_close` — paper_pnl_usd ÷ filled_closes
- `cohort_label` — the policy + exit-profile identifier

The operator must reconcile these against the raw
`shadow_lane_outcome.jsonl` rows before making the promotion call. The
counters are a convenience, not the source of truth — the JSONL rows
are.
