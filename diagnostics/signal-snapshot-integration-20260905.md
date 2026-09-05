# Immutable first-capture signal evidence integration

## Proven defect and change

Delayed accepted/rejected collector maturation rebuilt pre-signal features from
the newest candle/state cache. The repair stores the first available context in
a bounded content-addressed snapshot and preserves its reference through pending,
restart, promotion and terminal paths. Existing historical records are not edited.
Capture is explicitly FIRST_COLLECTOR_CAPTURE; availability at signal is false,
not inferred from the snapshot's existence. This does not supply missing forward
candles or prove long-lookback historical completeness.

The actual bot and mirrored engine use one canonical runtime root for snapshots,
provisional recovery and final collector writes. Rebuilt pending feature fields
come from the restored record, not a newer pre-build variable. Epoch serialization
remains on both collector callers; conflicting references fail closed.

The V3 bridge validates before ledger writes and preserves event-specific refs
plus signal time on lifecycle rows, including shared-opportunity cases. Transfer
and qualification bundles include distinct verified snapshot dependencies.
Fast transport adds only the strict content-addressed snapshot path. No cleanup
authority or live trading behavior is added.

## Verification

Root combined suite: 225 passed in 18.43s, including actual extracted bot callers,
collector integrity/provisional recovery, snapshot parsing, bridge propagation,
bundle materialization/ACK/dependencies and snapshot batch transport.
Receipt: diagnostics/qa-signal-snapshot-integration-20260905.xml.

Staged bot/engine normalized source hash: b6eeb756529a. Unrelated dirty runtime,
inventory, cleanup and PnL changes are deliberately excluded from staging.
No deployment is claimed. Current Fly recovery continues on cb7745e827e1.

## Remaining cross-layer acceptance

Normal analyzer per-event snapshot projection is implemented in the same
integration: generation-pinned active/rotated lifecycle rows and snapshot objects
are joined without overwriting shared-opportunity fields. The normal report
exposes per-event evidence and coverage, retaining first-capture provenance.
Root verification: 82 tests passed across the snapshot projection and baseline
context/unit integration suites. These overlap agent test totals; do not add them.
Do not claim observed-at-signal or fill-time ATR authority.
Current-generation publication and visual QA remain blocked on completed sync.
All source checks must be followed by installed collection/transfer receipts.
