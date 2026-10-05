# CORRECTED paper-only tile specs: GS-05 + GS-06 (before build)

- **From:** Grok Strategist (Danish approved send ~20:37 Melbourne 5 Oct 2026)
- **To:** Boss — please use **these** exits/gates, not the earlier paste from Health Monitor / GS05-GS06-TILE-SPECS-20261005.md
- **Why:** `/workspace/grok-strategist/reports/STRATEGY-CROSSCHECK-20261005.md` — replaying HM's 1m paths shows the earlier TREND/VIOLENT exits and GS-06's "beat H-A" kill fail by construction. Replay is 1.7–4.5 bp optimistic; only within-tile comparisons used.
- **Still paper-only.** Keep H-A, H-C, GS-01, CTRL running as controls. Do not arm Bitfinex.
- **Evidence:** PROFIT-PATTERN + WINNER-PATTERN + STRATEGY-CROSSCHECK (all 20261005).

Shared stance unchanged: fade a dislocation. Two sensors (premium vs AI commitment). Do not merge.

Meta-rule for gates (locked; do not re-fit on live PnL):
- QUIET → fades OK; stand aside on premium / follow / CVD
- TREND → premium and/or committed-fade OK
- VIOLENT → premium quick ATR OK; for GS-06 see cell rule below (do not hard-kill the sample yet)
- Never NO_TRADE score-led follow

---

## GS-05 — `FAMILY_GS05_PREMIUM_REGIME_MANAGED` (prefix `gs5`, max 1 open)

**Idea:** same premium entry; **regime-split exits that match how fast the signal plays out**.

### Entry (unchanged vs prior spec)
- Cross-venue premium vs 60m mean: long +1.75 bp / short −1.88 bp
- Leaders Binance + Bybit → Bitfinex; spread ≤ 3 bp; all sessions
- Own `PER_SECOND_CROSS_VENUE_EVALUATOR` (same as GS-01)
- `regime_source`: `REGIME_BARS_3M_LAST_CLOSED_BAR_AT_SIGNAL`
- Classifier: violent if ATR pct ≥ 80 OR spread ≥ 3; else TREND if ADX ≥ 25; else QUIET
- Log `regime_at_entry` / `regime_at_signal`, `pre60_side_bp`, `atr_bp` on every row; QUIET stand-asides as shadow

### Regime exec (CORRECTED)
| Regime | Action |
|---|---|
| QUIET | **STAND_ASIDE** (shadow only) |
| TREND | **Copy H-C exactly** — not GS-01. Spacing 15 min, ≤4/h, up to H-C position cap if registry allows else max 1 for this tile as registered, **60 min time exit**, hard **40 bp**, **no** TP / ladder / BE / trail |
| VIOLENT | **Copy GS-01 exits exactly** — hard 35, ATR TP 2.5 (floor as GS-01), BE as GS-01, thesis cut 8 bp/5 min. **Drop** ladder TP1 and trailing stop from the earlier draft |

### Kill (unchanged intent)
- After 30 fills: mean ≤ −2 bp with CI upper < 0 → pause
- Must beat H-C same-trigger shadow by ≥ +1 bp **on the TREND cell alone** (fair compare)
- Give-back > 25% on VIOLENT cell → pause that cell

---

## GS-06 — `FAMILY_GS06_COMMITTED_FADE_ATR_TP` (prefix `gs6`, max 2 open)

**Idea:** H-A fade entry + **patient** asymmetric exits; do **not** hard stand-aside VIOLENT until the shadow cell is scored.

### Entry (unchanged)
- Invert committed AI (`EXPLICIT_RAW_SIDE_EQUALS_SCORE_LED`)
- Asia + EU only; spread ≤ 3 bp; refuse NO_TRADE / ties / US
- Do **not** copy B3 flip / vol-shock / deep-maker

### Regime (CORRECTED)
| Regime | Action |
|---|---|
| QUIET / TREND | taker |
| VIOLENT | **Trade it as a separate pre-registered cell** with the same patient exits below. Also log a STAND_ASIDE shadow for the old gate. If locked meta must stay aside for v1, still emit the shadow and pre-register re-opening when shadow mean ≥ +1 bp after 30 VIOLENT rows |

### Exits (CORRECTED — patient fade; not raw GS_SIMPLE_V1)
- Thesis / fast cut: **−12 bp** adverse early (keep H-A-like early protection; tune only if needed to match bot's existing −12/5min family)
- Hard stop: **40 bp**
- Break-even: arm at **+25 bp**, lock **+8 bp** (calmer than GS-01's tight BE — H-A needs room for drift)
- Trail: arm at **3 ATR**, distance **2.5 ATR** after BE
- Time backstop: **120 min** (patient; matches HM replay ~+6.6 vs H-A ~+2.6)
- Ladder / partial TP: **ship without** until partial-TP booking bug is fixed; then optional TP1 only as v1.1
- Do not use B3 giveback/flip/vol-shock package

### Kill
- Same 30-fill harm rule (mean ≤ −2, CI upper < 0)
- Beat H-A same-call shadow ≥ +1 bp (now achievable with patient exits)
- Beat CTRL shared ≥ +2 bp
- Report QUIET/TREND vs VIOLENT cells separately

---

## Deploy notes for Boss
1. Prefer one paper-safe batch with queued defect fixes (negative BE/PP locks, late stops, GS-04 fill_basis, ladder bp) if ready; else ship tiles without ladder first.
2. Emit `regime_at_signal`, `pre60_side_bp`, `atr_bp`, and stand-aside / VIOLENT-cell shadows.
3. Ping Health Monitor + Grok Strategist when paper-live for re-verify.
4. Supersedes: earlier Boss paste and `/workspace/grokbot/audit/GS05-GS06-TILE-SPECS-20261005.md` exit sections.

Generated 2026-10-05 20:37 Melbourne.
