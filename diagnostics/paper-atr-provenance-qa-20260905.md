# Paper ATR provenance repair — 5 September 2026

## Defect and bounded repair

Paper evidence previously labeled any selected positive ATR field as
`FILL_TIME_3M_ATR14`, including signal/cycle fallbacks. Candidate projection
repeated that inference. A numeric field name does not prove observation time.

The bridge and candidate loader now retain numeric source precedence but label
unproven timing `UNVERIFIED_TIMING_FALLBACK`. An explicit at-fill observation
receipt is checked for hash, event, value, 3-minute/14-period identity, causal
availability and recent closed-candle time before receiving the stronger label.
Non-finite values and booleans are rejected. Existing raw records are not rewritten.

This validates receipt consistency, not the independent authenticity of an exchange
measurement. No current production writer for this new observation receipt was
found. Genuine collection and pinned market-source verification remain required;
the repair does not manufacture historical fill-time ATR or qualify a strategy.

## Verification and scope

- Root read the complete four-file diff and focused negative tests.
- Root combined analyzer/provenance integration: **295 passed, 2 subtests passed**
  in 24.52 seconds; this includes `test_paper_atr_provenance.py` and
  `test_research_v3_bridge.py`. Overlapping earlier test runs are not additive.
- `git diff --check` passed for these files (line-ending normalization notices only).
- Numeric precedence, stale/future/value-mismatched receipts, hash tampering,
  malformed numeric values, projection and no raw-history rewrite are tested.
- Source-only repair. Fly remains `cb7745e827e1` during existing bootstrap
  continuation; this does not authorize a deployment, restart or live arming.
