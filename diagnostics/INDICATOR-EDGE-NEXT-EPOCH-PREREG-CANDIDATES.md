# Indicator Edge: retune candidates for the NEXT epoch's pre-registration

Status: **parked**. The current pre-registration is unchanged: `IE-FS-18076a9143db`, feature set
`indicator_edge_v1`, sha `18076a9143db…`, frozen 2026-10-03T18:03:27Z. Nothing here changes the
running spec or restarts the forward clock. These items are inputs for whoever writes the next epoch's
pre-registration. They are not proposals to edit the frozen one.

Source: indicator coverage audit, 2026-10-04 (live `indicator_bars_v1` rows plus an 833-bar replay of the
Oct 1-3 tape). Owner decisions are dated 2026-10-04.

## 1. `CMO@S:REVERSION` (indicator #18, slow horizon)

- Frozen rule: CMO with n=50; REVERSION gives +1 when CMO < -50 and -1 when CMO > +50.
- Observation: it never fired in the live epoch, and it never fired once across the 833-bar replay, which
  included the 2.5-4% days of Oct 1-2. A 50-bar Chande momentum oscillator on 3-minute closes almost never
  reaches ±50, because that would need about 75% of 2.5 h of close-to-close movement in one direction. In
  practice the variant is structurally silent. It adds one trial to the FDR family and contributes no
  signals.
- Decision (Danish, 2026-10-04): **leave it as frozen** for this epoch.
- Next-epoch candidates (pick one and freeze it before any data is seen):
  - REVERSION threshold of ±30 on n=50, mirroring the fast-horizon TREND band. Alternatively, set the
    threshold to a rolling percentile of |CMO@S|, for example 95th pct, as ROC does.
  - Or drop `CMO@S:REVERSION` from the scored set and keep `CMO@S:TREND`. That lowers `trial_count()` by 1.

## 2. `PITCHFORK_12H` (indicator #43): watch item, no retune decided

- Frozen rule: needs 3 confirmed 0.8% zig-zag swings inside 240 bars (12 h).
- In quiet regimes it has no swings. The previous UTC day ranged about 0.71% high to low. Before engine
  `indicator_engine_v1_20261004b`, the engine stamped that case AVAILABLE with a null raw, which read as a
  DEAD field. Since that engine version (PR #456), the cell is **WARMING_UP** and the row carries
  `status_reasons = {"PITCHFORK_12H@F:STATE": "INSUFFICIENT_SWINGS"}`. The coverage audit reports
  WARMUP / INSUFFICIENT_SWINGS, not DEAD. The spec and sha are unchanged.
- Decision (Danish, 2026-10-04): keep the frozen spec.
- Next-epoch candidate, only if the label stays INSUFFICIENT_SWINGS most of the time: set `reversal_pct`
  from realised volatility, for example 2x ATR% instead of a fixed 0.8%. Alternatively, lower it to 0.5%.
  Base the choice on the share of bars that were INSUFFICIENT_SWINGS during this epoch. That share is in
  the `reason_counts` of the coverage block in `indicator_edge_export.json`.
