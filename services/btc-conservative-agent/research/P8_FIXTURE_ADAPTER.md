# P8 research fixture adapter (provisional; no acceptance authority)

Entry: `research.p8_fixture_adapter.evaluate(operation, input) -> dict`.
Exact 27-operation map from the frozen seven arrays (233 total: A15/B218).
The adapter calls the functions below, with shape-only argument conversion;
all decisions remain in the real research modules/unchanged fill_model.
No HM oracle/driver, case-ID dispatch, envelope `expected`, engine, supervisor,
network or real restart is used. The `frozen_ids` input's own `expected` key
is the declared frozen ID binding, not a fixture golden envelope.

## Verified bindings

- Card: `1dc08d9a36bc5feb433bd50e6e222157df1cf0cc0c7ffafec99859c9a24571dd`.
- Manifest: `fbdfd1ca2b6c69cf977b176f11ad1b1e9fad2f7782aaa01c30a69f159ec04c3e`.
- Pin7 r7: `dc0cc45f24f5afee9a69718fcc120fc27450f80b6e35de1058f0ed0e68eedf79`.
- P8 r10: `28315aeb4423e88d4e3dadcf3e42005b29ddbba1f2e054467aeab7827135641e`.
- Rules: `4305f12a02d23f5cad689fd388387ffad3ecc50e02e614c883894d7ef3bf94f2`.

## Operation map

Aliases: c=pin7_classifier; s=pin_scoring; contracts=pin_research_contracts;
k3=pin_k3; fills=pin_fill_operations. All are under `research`.

| operation | real function |
|---|---|
| `features` | `c.candle_features` |
| `versioned_features` | `c.versioned_features` |
| `calibration` | `c.calibrate` |
| `candidate` | `c.candidate` |
| `fsm` | `c.replay` |
| `cap` | `c.cap_allows` |
| `observe` | `c.observe` |
| `safety` | `c.safety_admission` |
| `utc_days` | `s.utc_days` |
| `coverage` | `s.pin8_coverage` |
| `branch_ledger` | `s.branch_ledger` |
| `pin15` | `s.score_pin15` |
| `pin15_counts` | `s.pin15_counts` |
| `dual` | `s.dual_from_pvalue` |
| `grammar_select` | `contracts.select_ids` |
| `frozen_ids` | `contracts.frozen_ids` |
| `prior_c0` | `contracts.prior_calibration` |
| `role_mask` | `contracts.role_mask` |
| `runtime_gate` | `contracts.runtime_gate` |
| `eligibility` | `contracts.selection_eligibility` |
| `ct_lookup` | `contracts.ct_lookup` |
| `b3_guard` | `contracts.b3_guard` |
| `k3` | `k3.score_k3` |
| `falsekill` | `k3.falsekill_probability` |
| `executable_l1_mtm` | `k3.executable_l1_mtm` |
| `field9_fill_model` | `fills.evaluate_fill` |
| `field9_historical_control` | `fills.historical_control` |

## Guard/persistence and JSON

Finite JSON is checked on input AND output with `allow_nan=False`; no NaN/Inf
replacement or post-output threshold/rounding rescue. Checkpoint ops need an
existing absolute environment `RUNROOT`; each call creates and cleans a private
temp directory beneath it. Pin7 uses its real StateJournal; K3 and the guard
serialize/reload synthetic checkpoints there. No actual process restart occurs.
B3 metadata paths are never followed/opened. A missing/unreadable/mismatched
log discards warmup, latches terminally, alarms and forbids B3 scoring. Later
valid logs, restart/score requests and unreviewed CLEAR flags cannot resume it.
08B rejects the entire polluted input including null/nested/auxiliary columns,
with STOP/alarm/IDs=null; no salvage. The lower bound also STOPs+latches.

## Disclosed wire-contract HOLDs (do not alter the approved packet)

1. Ten `candidate` cases tape a scalar string (e.g. `"SHOCK"`), while the card
   and requested adapter require a JSON object. This adapter returns
   `{"candidate":"SHOCK"}`. It does NOT silently unwrap for the formal judge.
   Private tests separately check the real scalar primitive and this object
   shape; that is NOT ten adapter/golden acceptance passes.
2. Twenty other cases tape continuous fields as JSON integer literals, while
   the card's strict integer/type comparison rejects natural floating output,
   e.g. P15 pnl_bp=-200 vs -200.00000000000017, or V=0 vs 0.0.
   P16 slippage, F274 MFE/final, E and calendar/OOS durations also occur.
   Own strict tests retain these failures; no tolerance/type rule was loosened.
   HM must resolve with a newly bound packet/schema, not runtime hand editing.

Private pytest (Pin tests plus unchanged fill_model): **330 passed, 20 failed**,
zero skips/xfails. All formerly pending semantic cases are implemented and
pass their own strict assertions. The twenty failures are listed below.
No HM driver/card/Part B acceptance run was executed. Formal adapter use would
also encounter the ten scalar/object mismatches: no full PASS is claimed.

- `test_hm_tape[P15-B-LONG-1S]`
- `test_hm_tape[P15-B-SHORT-1S]`
- `test_hm_tape[P15-C-LONG-HOLDING]`
- `test_hm_tape[P15-C-SHORT-HOLDING]`
- `test_hm_tape[P15-PRICED-POSITIVE]`
- `test_hm_tape[P16-POS-COST]`
- `test_hm_tape[P16-ZERO-COST]`
- `test_hm_tape[P16-NEG-COST]`
- `test_hm_tape[P16-II-CANNOT-RESCUE-LOSS]`
- `test_hm_tape[F274-L1-EXECUTABLE-LONG]`
- `test_hm_tape[F274-L1-EXECUTABLE-SHORT]`
- `test_hm_tape[C02-GOLDEN-FLAT]`
- `test_hm_tape[C02-GOLDEN-EXP-HOUR24]`
- `test_hm_tape[C01-FUTURE-MUTATION-PREFIX]`
- `test_hm_tape[R2-02-T6-PRIOR-C0-FIRST-JOB]`
- `test_hm_tape[R2-02-T6-SECOND-JOB-NO-REFIT]`
- `test_hm_tape[R2-03-T6-FUTURE-C0-MUTATION]`
- `test_hm_tape[R2-01-HISTORICAL-FINAL-VERSION-LIMITATION]`
- `test_hm_tape[P8R4-06A-NO-FABRICATED-RECEIPT]`
- `test_hm_tape[P8R4-06B-LATE-V2-PREFIX-IMMUTABLE]`
