# Numeric bars follow-up (gates any `strategies/*.yaml`)

**Status:** REQUIRED FOLLOW-UP — not implemented in #985.  
**Gate:** `strategy_package_loader.NUMERIC_BARS_FOLLOWUP_REQUIRED = True` refuses any
registered `strategies/*.yaml` until this follow-up lands with its own R22 + RK PASS.

No strategy package may be added under `strategies/` until the bars below are
implemented (or explicitly waived by RK citing this file).

## HOLDS bars (22m OOS label)

Before a package may claim `evidence.oos_22m: HOLDS` (or legacy
`oos_22m_summary: HOLDS`) with `OOS_PASS` / `FWD_PASS`:

| Bar | Requirement |
|-----|-------------|
| Profit factor | PF ≥ 1.10 |
| Trade count | ≥ 200 trades |
| Quarters | ≥ 60% of quarters positive |
| Slippage stress | net > 0 at 1.5× slippage |

## R53(c) bars (FWD_PASS feed only)

| Bar | Requirement |
|-----|-------------|
| OOS trades | ≥ 200 |
| Monthly folds | ≥ 70% of monthly folds positive |

Loader already refuses non-`FWD_PASS` into R53(c) and requires HOLDS +
`leak_suspect: false` for `OOS_PASS`/`FWD_PASS`.

## Per-tile kill rule

Each package YAML must declare (or inherit via registry activation PR) a
per-tile kill rule before activation. Exact shape is owned by the activation
PR (own R22 + RK PASS) — not by the empty schema PR.

## Paper-fill vs `REALISTIC_V1` drift

`costs: REALISTIC_V1` is schema-required. A paper-fill vs `REALISTIC_V1` drift
check must run before any package is activated; until that checker exists,
this follow-up gate stays closed.

## Flip procedure

1. Implement the bars + checker + tests (own PR).  
2. RK PASS on that PR.  
3. Set `NUMERIC_BARS_FOLLOWUP_REQUIRED = False` in the same or a follow-on tip.  
4. Only then may `strategies/<id>.yaml` files be added (still each needs own R22 + RK PASS for activation).
