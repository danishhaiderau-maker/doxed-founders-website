# results_gates_v1

Read-only Stage C results consumer for synthetic fixture evaluation. This package does not run research, does not approve a strategy, and does not connect to a dashboard.

Card identity: `c0b239a5b6f8b076d1d4e36535c4a4055d21d9838da18a7cafcb5b1059f3b81b` (9566 bytes). Stage A `ecb42d094510f7dd507b4ebc5fb2797a2614951614745d1fcfbaa79e44bfb122` and Stage B `025082ee2c98c9d8e8637e6ea1d23f446546b2fc935ecf8119533ccbef2e9f22` are design-spec hashes only. They are not implementation pins. Producer `CoverageReport` is not defined here.

`evaluation_status` is `BLOCKED_NO_APPROVED_REAL_PROFILE`. A synthetic branch status is software behavior on the versioned example profile, not an evaluation PASS, a confidence claim, or research DONE. Live is OFF.

## Run

From the repository root, fixture-only:

```bash
python -m research.results_gates_v1.fixture_cli --fixture-only
```

There is no real-input mode. Paths outside `fixtures/` are rejected before they are opened. Acceptance tests:

```bash
python -m unittest research.results_gates_v1.tests.test_acceptance
```

Bounds are the original six: 2 CPU workers inclusive, 512 MiB for the whole parent+child tree, 120 seconds per command, 600 seconds per suite, 8 MiB receipts, 256 fixture strategies. The parent is the only receipt writer. It forks one child and reads that child's stdout through a bounded pipe. Each process installs a VAS ceiling of half of 512 MiB before the child starts, so the sum of the ceilings is 512 MiB. VAS is a conservative upper bound, not RSS. The limit is set after the interpreter has already started, and a kill is not instantaneous; this does not claim zero overshoot. There is no CPU-time cap, no 2048-event cap, and no extra aggregate byte quota. A cap hit emits `INCOMPLETE_STOP` and the reason. A nonzero exit alone is not that status. Results are not truncated and no ETA is emitted.

Only the canonical fixture path is admitted, and that check happens before the file is opened. The child reads it once, hashes those bytes against the lock, and only then decodes that same buffer. Unpinned paths are refused before open. Nonzero missed, rejected, or unfilled nets are rejected and are not rewritten as zero. Zero non-fills stay in the denominator. Inverted ledger epochs and unmarked zero-width epochs are incomplete and unknown. A zero-width epoch is admitted only when it is explicitly marked EMPTY and carries provenance. That width rule is an RK interpretation, not an original positive-width requirement. Real exposure stays unknown and untouched validation stays blocked.

## What the fixture proves

The consumer checks a strict `research_results_v1` document: full posted-N Benjamini-Hochberg with non-evaluated cells at p=1 as bookkeeping, a White reality check on the whole comparable family using the fixture's fixed resampling draws, coverage reconciliation, nested-fold integrity, exposure-ledger overlap, cost disposition, draft packages, and dashboard provenance.

Synthetic profile `synthetic-branch-profile-v1` is the only profile. Its example thresholds (BH q 0.05, White p ≤ 0.05, 3 of 4 folds, 60 qualifying OOF trades, day share ≤ 0.40, week share ≤ 0.50, median hold ≥ 300 seconds, ≤ 10 packaged leads, gross ≥ 2 × the synthetic roundtrip, stress net > 0 at 1.5 × slippage only) are branch examples. Real N, real dates, and real cost thresholds are null.

## Dispositions that stay unresolved

- Stage A, Stage B, and producer CoverageReport compatibility: blocked.
- Fee verification evidence: null. The Danish account fee 0 figure is an account assertion used as a test, not venue verification.
- Measured real execution friction: null and blocked.
- Funding charges: excluded and not modelled. That is not a measured zero.
- Queue and delay: synthetic.
- Deflated Sharpe, effective n, and cluster intervals: requirements retained, not tested.
- Romano-Wolf, PBO, diversity dedup, and bootstrap B=2000 / seed 20261104 / block 1 day: proposed, not tested. No favorable default is filled in.
- Nested walk-forward dated definitions: not approved. No iid fold p-value is emitted.
- White reality-check real inference: blocked until benchmark, centering, dependence-preserving bootstrap, RNG, repetitions, block length, and missingness are preregistered.
- Exposure of Study1 train/test, RUN-A/A2, 07d, and spent holdouts: real dates are unknown. Unknown or incomplete ledgers block untouched-validation acceptance. Overlap of a half-open `[start, end)` claim with an exposed interval rejects undisturbed, untouched, and unseen claims. 07d is audit only. Inverted and unmarked zero-width required epochs stay unknown. A declared EMPTY epoch needs provenance. Real untouched acceptance stays blocked.
- Dashboard UI and API mapping remains separately owned. This projection keeps `source`, `as_of`, fixture status, and `NOT_CONNECTED`. A missing mapping blocks dashboard compatibility.
- REALISTIC_V1 / R7 reconciliation remains open. R53 future-live criteria and Danish's arm are outside this package.

Old N, dates, fold definitions, and any prior claim that A2 was unseen are not inherited. Old fee and funding thresholds are not revived.
