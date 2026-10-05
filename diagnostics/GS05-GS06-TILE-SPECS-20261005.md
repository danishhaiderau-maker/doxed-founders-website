# Paper-only tile build request: GS-05 + GS-06 (FREEZE21B follow-on)

- **From:** Health Monitor + Grok Strategist (aligned 5 Oct 2026 ~20:12 Melbourne)
- **To:** Boss — please implement as new paper research tiles (no live arming, no Bitfinex)
- **Danish ask:** 5 Oct ~20:15 Melbourne — design two tiles in detail and send to Boss for making
- **Evidence:** `/workspace/grokbot/audit/PROFIT-PATTERN-FREEZE21B-20261005.md` and `/workspace/grok-strategist/reports/WINNER-PATTERN-FREEZE21B-20261005.md`
- **Constraint:** paper-only; do not replace H-A / H-C / GS-01; keep them running as controls/shadows

## Why these two (one sentence each)

1. **GS-05** — same premium *entry* that already wins (H-C / GS-01), but **gate by regime** (skip QUIET where premium loses) and use an **active exit stack** (GS-01-style + partial TP) so H-C’s unmanaged give-back shrinks.
2. **GS-06** — same committed-AI *fade entry* that already beats CTRL (H-A), but import **GS-01’s asymmetric ATR TP / BE / thesis-cut** and **stand aside in VIOLENT** (where H-A’s edge flips).

Shared stance: both **fade a dislocation**. Different sensors: premium vs AI commitment. Do **not** merge into one tile.

Locked meta-rule (for regime gates — do not re-fit on live PnL):
- **QUIET** (ATR pct < 80 AND ADX < 25; same `REGIME_BARS_3M` as GS-01/B3): fades OK; **stand aside on premium / follow / CVD**
- **TREND** (not VIOLENT AND ADX ≥ 25): premium and/or committed-fade OK
- **VIOLENT** (ATR pct ≥ 80 OR spread ≥ 3 bp): premium quick ATR OK; fades **stand aside**
- **Never** NO_TRADE score-led follow

---

## Tile 1 — GS-05 `FAMILY_GS05_PREMIUM_REGIME_MANAGED`

**Display label:** GS-05 Premium regime-managed · H-C/GS-01 premium trigger, QUIET aside / TREND taker / VIOLENT quick ATR, ladder+BE+cut  
**id_prefix:** `gs5`  
**display_order:** next free (suggest 12)  
**lifecycle:** `PAPER_ONLY` · `paper_eligible=true` · `live_copy_eligible=false` · `platform_relay_eligible=false`  
**execution_scope:** `PAPER_ONLY`  
**margin_usd:** `0.25` · notional $25 × 100× (same as siblings) · `account_risk_pct: 0.5`  
**max_active_signals / max_open_positions:** `1`  
**Clone from:** `FAMILY_GS01_XV_PREMIUM_ATR_TP` + H-C premium thresholds; add regime_exec gates + ladder.

### Entry (clone H-C / GS-01 premium stream)

| Field | Value |
|---|---|
| `direction_source` | `CROSS_VENUE_PREMIUM` |
| `ai_decision_role` | `NONE` |
| `leader_venues` | `binance`, `bybit` |
| `premium_mean_window_sec` | `3600` |
| `premium_min_mean_samples` | `1200` |
| `premium_long_threshold_bps` | `1.75` |
| `premium_short_threshold_bps` | `-1.88` |
| `max_spread_bps` | `3.0` |
| `max_bbo_age_sec` / `max_venue_age_sec` | `2.0` |
| `signal_clock` | `PER_SECOND_CROSS_VENUE_EVALUATOR` (own instance, same as GS-01) |
| `allowed_sessions` | ASIA + EU + US |
| `min_submit_interval_sec` | `5` |
| `max_submissions_per_hour` | `60` |
| `taker_protection_bps` | `5.0` |
| `taker_ttl_sec` | `3` |
| `fill_model` | `REALISTIC_V1` |
| `regime_source` | `REGIME_BARS_3M_LAST_CLOSED_BAR_AT_SIGNAL` |
| `regime_classifier` | `{ violent_atr_pct_gte: 80, trend_adx_gte: 25, violent_spread_bp_gte: 3 }` |

**`regime_exec` (the GS-05 delta vs GS-01):**

| Regime | Action |
|---|---|
| `QUIET` | **`STAND_ASIDE`** (do not submit; log shadow would-have for audit) |
| `TREND` | `TAKER` at signal (same as GS-01 taker fields) |
| `VIOLENT` | `TAKER` at signal with **VIOLENT exit profile** below (quick harvest). Optional v1.1: maker `DEEP` offset `0.75 ATR`, TTL 20 min, no chase — only if taker VIOLENT underperforms after ≥30 fills |

Refuse / skip same stale-BBO and spread gates as GS-01. Log `regime_at_entry` on every row.

### Exit (`REGIME_ADAPTIVE_FIRST_TRIGGER_WINS`)

ATR source: `REGIME_BARS_3M_ATR14_BP_AT_SIGNAL_DEFAULT_4BP` (same as GS-01).

**Shared stack base = GS_SIMPLE_V1 + ladder TP1:**

| Rule | Setting |
|---|---|
| Hard stop | `35` bp |
| Thesis cut | `8` bp adverse within `300` s → close |
| Break-even | arm at `max(6 bp, 1.5 ATR)`; lock `+2` bp |
| Ladder TP1 | `50%` at `max(6 bp, 1.0 ATR)`; then lock remainder at `+2` bp |
| Final ATR TP | `max(10 bp, 2.5 ATR)` maker limit |
| Trail (after BE) | `1.5 ATR` |
| Time backstop | see profiles |
| Exit order | `HARD_STOP → LADDER_TP1 → BREAKEVEN_LOCK → THESIS_CUT → ATR_TAKE_PROFIT → ATR_TRAIL → TIME_BACKSTOP` |

**`regime_profiles`:**

| Regime | Profile tweaks |
|---|---|
| QUIET | N/A (no entries) |
| TREND | time `3600` s; full ladder+TP stack above |
| VIOLENT | time `2700` s (45 min); same stack; prefer banking TP1/TP fast (no trail arm delay beyond BE) |

`stop_fill`: side-correct BBO tick that fired the rule (GS-01 style).  
`take_profit_fill`: maker limit at target filled when side-correct mark trades through.

### Kill / pass (pre-register before enable)

- Harm: after **30 fills**, mean ≤ **−2 bp** and CI95 upper < 0 → pause.
- Futility at epoch end: `n_eff ≥ 30` and mean < **+1 bp**, or Bonferroni lower bound ≤ 0 → retire.
- Must beat **H-C same-trigger shadow** by ≥ **+1 bp** mean (exit/regime upgrade value).
- Give-back: MFE ≥ 8 bp then closed ≤ 0 in **>25%** after 30 fills → pause and tighten.
- TREND-aside counterfactual: if QUIET would-have shadow is strongly **positive** after 30, the QUIET gate is wrong → reopen QUIET as taker in a v1.1 patch (do not silently flip).
- Signal→submit p50 > 5 s → pause (clock defect).

### Tests / modules (suggested)

- `paper_policy_family_gs05_premium_regime_managed.py`
- `test_paper_policy_family_gs05_premium_regime_managed.py`
- Assert QUIET produces zero live submits + shadow rows; TREND/VIOLENT produce taker submits; ladder partial closes book correctly on `/api/monitor/lanes`.

---

## Tile 2 — GS-06 `FAMILY_GS06_COMMITTED_FADE_ATR_TP`

**Display label:** GS-06 Committed fade + ATR TP · H-A invert entry, Asia+EU, QUIET/TREND taker, VIOLENT aside, GS_SIMPLE exits  
**id_prefix:** `gs6`  
**display_order:** suggest 13  
**lifecycle / risk:** same paper-only envelope as GS-05  
**max_active_signals / max_open_positions:** `2` (H-A-like capacity; not B3’s twitchy cap-1 mom stack)  
**Clone from:** `FAMILY_COMMITTED_FADE_TAKER_90` entry + `FAMILY_GS01_XV_PREMIUM_ATR_TP` exit package.

### Entry (clone H-A)

| Field | Value |
|---|---|
| `direction_source` | `INVERTED_SCORE_LED_SIDE` |
| `commit_rule` | `EXPLICIT_RAW_SIDE_EQUALS_SCORE_LED` |
| `ai_decision_role` | `FEATURE_ONLY` |
| `admission_treatment` | `INVERTED_COMMITTED_SCORE_LED_SIDE_TAKER_V1` (same as H-A) |
| `allowed_sessions` / fade sessions | `ASIA`, `EU` only (US refuse) |
| `max_spread_bps` | `3.0` |
| `max_bbo_age_sec` | `5.0` |
| `mode` | regime adaptive below |
| `taker_protection_bps` | `5.0` |
| `taker_ttl_sec` | `3` |
| `trades_raw_ai_no_trade` | `false` |
| `refuse_on` | same as H-A: `SCORE_TIE`, `INVALID_SCORES`, `AI_ERROR`, `RAW_AI_NO_TRADE`, `SCORE_DIRECTION_MISMATCH`, `BBO_STALE`, `SPREAD_ABOVE_MAX`, `SESSION_GATED` |
| `regime_source` | `REGIME_BARS_3M_LAST_CLOSED_BAR_AT_SIGNAL` |
| `regime_classifier` | same thresholds as GS-05 |

**`regime_exec`:**

| Regime | Action |
|---|---|
| `QUIET` | `TAKER` at signal |
| `TREND` | `TAKER` at signal |
| `VIOLENT` | **`STAND_ASIDE`** (+ shadow would-have) |

Do **not** copy B3’s maker/touch/deep/flip/vol-shock package — those exits waste the fade’s forward drift.

### Exit (import GS_SIMPLE_V1; calmer horizon than B3)

| Rule | Setting |
|---|---|
| Family | `REGIME_ADAPTIVE_FIRST_TRIGGER_WINS` with single profile `ALL` for QUIET+TREND |
| Hard stop | `35` bp |
| ATR TP | `2.5 ATR` floor `8` bp (maker) |
| BE | arm `2.0 ATR` floor `6` bp → lock `+1` bp |
| Thesis cut | `8` bp / `5` min |
| Optional ladder TP1 | `50%` @ `1.0 ATR` floor `6` bp (include in v1 if lanes partial-TP booking is fixed; else ship without ladder first) |
| Time backstop | **`5400` s (90 min)** — keep H-A’s calmer horizon (do not use B3’s short mom exits) |
| Exit order | `HARD_STOP → BREAKEVEN_LOCK → THESIS_CUT → ATR_TAKE_PROFIT → TIME_BACKSTOP` |
| No | `INDICATOR_FLIP`, `VOL_SHOCK`, `MFE_GIVEBACK` in v1 |

### Kill / pass

- Harm: ≥30 fills, mean ≤ −2 bp, CI95 upper < 0 → pause.
- Must beat **H-A same-call shadow** by ≥ **+1 bp** (exit upgrade).
- Must beat **CTRL shared committed calls** by ≥ **+2 bp**.
- Give-back >25% after 30 → pause.
- If still all-SHORT after ≥30 fills: require two-sided n≥10 before any PASS language (note the sample, don’t auto-retire solely for side balance).
- Day-21 / Bonferroni: follow GS-01 style research-candidate rules unless you prefer H-A’s day-21 text — pick one and pre-register before enable.

### Tests / modules (suggested)

- `paper_policy_family_gs06_committed_fade_atr_tp.py`
- `test_paper_policy_family_gs06_committed_fade_atr_tp.py`
- Assert VIOLENT → zero submits + shadow; QUIET/TREND taker; paired vs H-A/CTRL on identical committed calls.

---

## Build / deploy notes for Boss

1. **Paper-safe batched deploy only** — extend freeze allowlist; **do not arm live / Bitfinex**.
2. Prefer shipping **after** the already-queued defect fixes Danish asked for (negative BE/PP locks, late stops, GS-04 fill_basis, ladder bp on `/tiles/trades`), or include BE/PP lock fix in the same batch so new tiles don’t inherit the tiny negative-lock bug.
3. Register both in tile registry + relay allowlist (paper) + analyzer cohort strings + dashboard display order.
4. Emit `regime_at_entry` and shadow would-have rows for STAND_ASIDE regimes (Health Monitor needs this for the next conformance/profit check).
5. Keep H-A, H-C, GS-01, CTRL running unchanged as live shadows/controls.
6. Tell Health Monitor when each tile is live on paper so we re-check specs + first fills.

## Out of scope this request

- Do **not** add a NO_TRADE-follow tile.
- Do **not** fold CVD into the meta-rule yet (B1/B2 still the proof track).
- Optional later: exit-only fix for **B3 / GS-03** (strong drift, bad exits) — separate ticket if you want; not these two tiles.

