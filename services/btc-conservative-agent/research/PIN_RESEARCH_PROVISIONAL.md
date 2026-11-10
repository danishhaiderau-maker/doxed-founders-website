# PROVISIONAL 5b implementation — pre-freeze, non-evidential

Live OFF. Offline, synthetic/in-repo fixtures only. No Stage-D, B2/window data,
laptop, trading keys, network, deploy, live arming, freeze or acceptance run.
No runtime wiring is changed; all files in this change are additions.

## Bindings

- Pin 7 r7: `dc0cc45f24f5afee9a69718fcc120fc27450f80b6e35de1058f0ed0e68eedf79`.
- P8 r10: `28315aeb4423e88d4e3dadcf3e42005b29ddbba1f2e054467aeab7827135641e`.
- c_T table: `37603987e6dabdc42ce89aec9482a3366d881f14c5d7c53356307e890c7595f0`.
- c_T compute artifact: `73f6a1b27987fa754189cc5f865003df06658f0a9112b39ac57d1e8860ac0f3d` (hashed data, never executed).
- Current rules: `4305f12a02d23f5cad689fd388387ffad3ecc50e02e614c883894d7ef3bf94f2`.
- RK provisional-only ruling: `72c250a0eb82`.
- HM 5b manifest: `fbdfd1ca2b6c69cf977b176f11ad1b1e9fad2f7782aaa01c30a69f159ec04c3e`; HM receipt reference `7009bc1ebc68`.
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

Use a fresh unique directory on EVERY run; substitute the placeholder below.
After recording results, remove only that exact run directory. Never use `/tmp`
or a shared default pytest base.

```sh
python3 -B -m pytest test_pin_research_provisional.py test_fill_model.py -q -p no:cacheprovider --basetemp=/workspace/coder/pytest-tmp/5b-<fresh-unique-id>
```

Own pytest only, not HM's driver/card. Latest: **330 passed, 20 failed**, no
skips/xfails. Read `P8_FIXTURE_ADAPTER.md` for all 27 real-function mappings
and the unresolved **wire contract**, not GS semantic, blockers. The private
assertion harness now compares integer types/values exactly, floats with
rel=1e-9/abs=1e-10, exact list order and recursive dictionary asserted subsets.
No post-output tolerance/threshold/type rescue. Ten candidate scalar/object
mismatches are separately disclosed; private primitive checks are not adapter
acceptance. Results/logs live outside the branch under `/workspace/coder/`.
Field9 is explicit-input, read-only unchanged-fill regression, not a revived
historical never-cheaper strategy gate. CI is NOT green.

## OPEN / not accepted

1. Current Pin7 r7/P8 r10/rules bindings are verified above. The eventual
   freeze/execution card still needs exact head/adapter/approvals. N=836 is a
   LOWER BOUND (V_other=0); 1672 sensitivity is informational, never a swap/kill.
2. The three former GS semantic ambiguities are RESOLVED and implemented:
   whole-input 08B rejection, lower-bound latch, missing-log terminal latch.
   No pending/xfail exclusion remains. Separate wire-contract blockers are
   documented in `P8_FIXTURE_ADAPTER.md`: ten scalar/object candidates and
   twenty continuous-number integer literals versus natural float output.
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
no undraft, merge or deploy. Scope: these new `research/pin*`/`research/p8*` modules/docs, synthetic
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

## Resource/test limitations

The earlier 714-file broad suite used 25-file, 3GiB, 90s bounded shards. It
terminated before all 29 shards completed on shared-box inode exhaustion
(ENOSPC despite free bytes); there were also timeouts and baseline failures.
No broad-green claim. Only this task's generated Pin pytest temp directories
were cleaned manually to release inodes. Earlier pytest default shared-temp
retention was not instrumented. One subsequent private run used tmpfs before
the hygiene steering. The final run used a fresh `/workspace/coder/pytest-tmp/5b-*`
directory and removed only that exact directory afterwards. Shared CI inventory
still requires its own
serial authorized change and fails because this new test is not allowlisted.
