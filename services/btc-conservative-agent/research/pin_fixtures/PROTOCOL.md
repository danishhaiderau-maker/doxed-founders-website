# Language-neutral replay contract

Current `tapes/index.json` names six ordinary UTF8 JSON arrays. Each row has `id`, `operation`, `input`, `expected`, `family`, `evidence_status`, `notes`. `TAPES.schema.json` describes the envelope. There are no pickles, Python objects, callable expressions or external service dependencies. A Coder adapter can dispatch `operation` to ANY implementation and compare its output with `expected` without running HM Python.

`expected` is an independently authored ASSERTED SUBSET: recursively require its dictionary keys and values; require list order/length exactly; permit additional dictionary fields. Strings/booleans/null exact; numbers tolerance rel1e-9/abs1e-10 in comparison only. Production decision thresholds use literal inequalities; 5% flag vectors use integer counts and strict>.

Units: bp for P&L, relative seconds for classifier/minute-end labels, milliseconds only in explicit L1 probes; UTC day=floor(relative seconds/86400). Synthetic display offsets are declared and never replace UTC day. No real market timestamps/receive evidence is claimed.

Hash payloads are explicit UTF8 STRINGS. Hash their exact UTF8 bytes, including newline/spacing, NOT a reserialization of numeric JSON. `content_payload_utf8` is checked against `content_sha256` and its parsed OHLC fields; table bytes are `table_utf8`. This removes cross-language float-format/JSON-whitespace dependence. Frozen runtime cT expected SHA is37603987e6dabdc42ce89aec9482a3366d881f14c5d7c53356307e890c7595f0; changing both bytes and claimed SHA still STOPs.

Principal operation contracts:
- pin15: conditioned fill/basis + quote/fallback bars => category/fill/neverwin/exit/bp.
- pin15_counts: strategy/fold categories => distinct capture/filled, a/b/c shares/strictflag.
- dual: i/ii/backtest/N/confidence/risk predicates =>ii−i, ratio, decisive(i) admission. Synthetic p-values/DD predicates are not bootstrap/risk calibration.
- k3: event/intent ids, original ordinal, partial/final/known flags and paper(i) MFE/net =>exact-prefix kills/report/restart persistence; no PASS.
- features/calibration/candidate/fsm/observe/cap: explicit synthetic candles/features/state =>math/elapsed3-close priority/persistence/rolling gates. Observe queries never create a close.
- prior_c0/role_mask: explicit prior source hours/feature pairs/job/role masks =>720-only prior calibration reuse, end ownership, no37d refit, common OOS masks/HOLD missing bridge/data. Complete-minute flag is a unit precondition, not proof of market coverage.
- grammar_select: synthetic grammar-only tuples =>fixed min-numeric U/D/Q/A, reject performance columns. No real832 catalogue claim.
- coverage/branch_ledger/utc_days/safety/runtime_gate: strict countability/dates/idempotence/safety/gate admission units, not real adapters.
- executable_l1_mtm: qty-fitting single-level BID-long/ASK-short mark/fault probes, not P12L2 fill realism.
- ct_lookup: frozen hash verified BEFORE parsing; floor elapsed days<28 NO_DECISION;28..364 largest rowT<=d;>364 PRIMARY c365=22.958. ROW_ONLY_NOT_PASS is not a strategy decision.

`results.json` deliberately distinguishes actual_HM_oracle from actual_P12_reference. Do not copy HM oracle outputs into real-implementation actuals. Coder's independent adapter/results/implementation hash must be separately pinned; no real reference execution occurred here.

P8 exact authority is f72c1b0e7367bf3f3d7592d3925260d4cfc5385f40c5d80d235738690700df8d, matching backup; classifier remains r2 3b5dba29fdff. `compute_code_utf8` and expected_code_sha256 additionally pin the cT generation artifact before lookup. They are data, never imported/executed.

Additional operations: versioned_features selects forward V1 received by the decision and emits the explicit historical-final-version limitation with null receipts; frozen_ids refuses post-freeze substitution; grammar_policy is conditional08B interpretation testing ONLY. b3_guard consumes synthetic proposed-read metadata/rows/logs and persistent latch, never opens any actual data file. It checks every selected row against the suffix, not just claimed range endpoints. Outcome artefact metadata STOPs+alarm.

Cases marked AMBIGUOUS_PENDING_GS are conditional self-checks, not a selected implementation contract; Coder must keep08B unresolved until GS rules. For f72 lower-bound latch and incomplete-log cases compare only asserted keys; do not infer unasserted runtime behavior from the HM model.
