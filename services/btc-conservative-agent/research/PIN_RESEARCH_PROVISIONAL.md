# PROVISIONAL 5b implementation — pre-freeze, non-evidential

Live OFF. Offline, synthetic/in-repo fixtures only. No Stage-D, B2/window data,
laptop, trading keys, network, deploy, live arming, freeze or acceptance run.
No runtime wiring is changed; all files in this change are additions.

## Bindings

- Pin 7 r2: `3b5dba29fdfffe916fef9d209c6095db5ca45fc1a998a9682ea6acc383584fc1`.
- P8 r5 (content-bound despite stale r4 title): `ca77dbaeed9cf55ac1f14d6e4ecd525c21ccacc4987c64c112c7e1fb5f5d581a`.
- c_T table: `37603987e6dabdc42ce89aec9482a3366d881f14c5d7c53356307e890c7595f0`.
- c_T compute artifact: `73f6a1b27987fa754189cc5f865003df06658f0a9112b39ac57d1e8860ac0f3d` (hashed data, never executed).
- Current rules: `4305f12a02d23f5cad689fd388387ffad3ecc50e02e614c883894d7ef3bf94f2`.
- RK provisional-only ruling: `72c250a0eb82`.
- HM manifest: `a023c98ca3811dd07953d7bd869ec18a58c41628b27acf3b87b90c18228507db`; HM receipt reference `7009bc1ebc68`.
- Field 9 model pack: `39a9e5efe92e3cea97658ab445673cfc9928f8adda8fb2ec80c5afeaa97f1bd2`.
- Existing unchanged fill_model.py: `3b6cfcac59ea46f0da073cb2ed883b46cc6d50d059b181545dba7f156769b3c7`.
- Branch base: master `08c2b3762cc166bbc9dd91f45a13edb3a0cfd876`.

All authored module headers repeat the source/rules pins. Vendored HM input
artifacts remain byte-identical, including historical bindings inside them;
they are inert test data, not modules imported by this implementation.
`pin_fixtures/SOURCE_PINS.json` records exact source paths and copied-file hashes.
The original entire HM manifest was checked at intake; the runner rechecks its
identity, all copied files, and their original HM manifest entries before replay.

## Entry points

- `pin7_classifier.candle_features` / `versioned_features`: complete 1441-close
  minute grid, 1h features V/E/z/B, receipt/version/content checks on forward
  inputs, explicit final-version historical limitation, no invented receipts.
- `calibrate` / `CalibrationSeal`: exactly 720 complete feature-only C0 pairs,
  literal linear quantiles, immutable interval/source/bindings; reusable seal,
  no per-job refit. `prior_calibration` / `role_mask` are synthetic mask units.
- `candidate` / `step` / `replay`: literal equalities and SHOCK priority/latch,
  >=2h/three closes, 12h dwell, rolling `(h-24h,h]` cap; missing closes reset
  pending only. Subhour/repeated/out-of-order callbacks cannot accept flips.
- `StateJournal`: explicit RUN_ROOT/state/pin7-state.jsonl append+fsync,
  canonical chained hashes, durable source sequence, checkpoint/binding/clock
  validation; corrupt/missing state never grants a fresh budget. Single writer
  is a caller precondition, not a deployed process-lock implementation.
- `ordered_close_intents`: safety first, one coincident safety/flip request for
  a position intent, never fabricates actual-flat or realised P&L.
- `pin_scoring.pin8_coverage`: terminal 28 UTC dates, distinct completed intents,
  each branch 30 trades/14 days AND parent 100/28; A only on independently
  supplied full-pass predicate, otherwise flat. Never grants live authority.
- `score_pin15` / `taker_entry_and_score`: own NO_FILL capture column, read-only
  shared taker fill API, adverse exit-bar extreme exactly at request+1s,
  holding-window fallback and strict integer `>5%` filled denominator flag.
- `dual_record`: same signal/time marks; (i) decides, (ii) only 50% comparator,
  measured slippage=(ii)-(i), no slippage gate. Confidence/risk predicates are
  mandatory caller inputs, not statistics computed here.
- `pin_research_contracts`: grammar-only minimum numeric U/D/Q/A IDs, frozen-ID
  comparison, pure pinned c_T floor-row lookup and metadata-only B3 guards.
- `pin_k3`: original-order completed-batch intent accounting, partial/finality
  and checkpoint units, exact four looks, monotone prior kill, never PASS;
  rational iid false-kill DP and explicitly single-level MTM unit probes.
- `pin_fixture_runner`: independent implementation dispatch. No HM oracle
  Python is imported/executed, and expected outputs never enter a SUT function.

## Reproduction (from services/btc-conservative-agent)

```sh
python3 -B -m research.pin_fixture_runner > /tmp/pin-research-results.json
python3 -B -m pytest test_pin_research_provisional.py test_fill_model.py -q -p no:cacheprovider -rs
python3 -B -m pytest test_ci_test_inventory.py -q -p no:cacheprovider
```

The tape harness uses recursive asserted dictionary subsets, exact ordered
lists/types, numeric rel=1e-9/abs=1e-10 for assertions ONLY. Decision thresholds
use literal comparisons. Gross backtest arithmetic has no cost allowance.
Field 9 runs are unchanged-model regressions/identity adapter checks, not
historical never-cheaper acceptance, which is MOOT under R61. The C7-15 negative
control demonstrates the historical -3bp failure; it is not a backtest gate.

## OPEN / not accepted

1. Rebind before any HM/RK gate review to the approved next Pin 7 and 15:00
   Sydney freeze hashes. The original coding card names r3; latest R59(6)
   effectiveness additionally awaits Pin 7 r4/HM hash binding. N=836 is a
   provisional LOWER BOUND (V_other=0); 1672 sensitivity is informational,
   fragile status must be pre-registered and cannot drop/swap a pick. No
   production effective-N claim is made from old hypothetical-N tape vectors.
2. HM tapes/index/conventions bind P8 r4, while implementation binds r5. Three
   clarification families are skipped pending GS re-taping: 08B's two
   alternative refusal contracts; the lower-bound latch vector; three missing
   suffix-log vectors. Six physical vectors have exact reason
   `pending GS spec clarification`; they are NOT acceptance passes.
3. Pin15(c) absent entry basis versus (a) NO_FILL/filled denominator remains
   unresolved. Named `unpriced_without_entry_basis` retains NO_FILL in capture,
   does not invent a fill or P&L, and discloses the holding-window worst exit
   reference in a separate UNPRICED_WORST missing-price layer. It is excluded
   from filled denominator and never a win. HM (c) tapes condition on a known
   filled basis and do not resolve this precedence.
4. CI inventory/allowlist must be updated in a separately authorised serial
   change. `test_pin_research_provisional.py` is intentionally not added to the
   existing shared `ci/unit_test_allowlist.txt` under R55/new-files-only scope.
   The inventory guard consequently fails, and PR CI cannot be claimed green.
5. No executable runtime/Windows/IPC, historical coverage, statistical
   confidence/bootstrap, L2 execution, frozen numeric production thresholds,
   real 832-tuple catalogue, chain launch or fixture acceptance is established.
   All metadata guard predicates and L1 probes are scoped unit contracts only.

## Standard request card

Action: preserve ONE draft PR for provisional review only after future rebind;
no undraft, merge or deploy. Scope: these new `research/pin*` modules, synthetic
`research/pin_fixtures/**`, this document, and new top-level test file. No
existing file, fill pricer, dashboard or PR #1000/#1001/#1002/#1003 file changes.
R0/R3/R4: shared fill API unchanged, no candle-as-fill claim. R10: no keys.
R12: no laptop. R19/R24/R59/R61: gross research, N disclosure, forward decides.
R22/R23/R31/R56: no DONE/acceptance verdict, future exact-tip gates mandatory.
R47/R55/R58: provisional ruling/GO, disjoint additions, consolidated handoff.
Rollback: no deployed surface exists; revert this additive commit through the
normal reviewed process if needed. Dry-run: not applicable (no destructive or
runtime action). Self-check: provisional synthetic tests only; acceptance and
CI remain BLOCKED, not PASS.
