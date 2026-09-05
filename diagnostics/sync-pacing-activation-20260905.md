# Authorized download pacing handoff

Danish explicitly authorized stopping the download to repair transfer overhead.
Pre-stop receipt: `sync-authorized-pacing-handoff-20260905.json` records PID 16576,
checkpoint hash, consumed script hashes, and manifest progress 2447/34433.

The root agent disabled Scheduled Task DoxxedFlyMirrorSync, requested one task
stop, then verified PID 16576 was absent before editing its consumed client.
No research source files, checkpoint, or promoted mirror were deleted. This
interruption is not a terminal sync receipt or an acknowledgement.

The active-client change imports the previously tested pure pacing helper.
Healthy small receipts share a 500 ms request-start budget; large, slow, or
pressure-state reads retain protective delay. Chunk validation, atomic file
replacement, checkpoint reuse, and acknowledgement paths are unchanged.

Many tiny objects are emergency-record idempotency receipts, not individual
strategy permutations: each binds ledger, record ID, row hash, offset, length,
generation, identity and PREPARED/COMMITTED state. Keep their recovery semantics.
Future batch transport should pack verified members without discarding these
identities; it is not implemented by this pacing change.

Executable loop progress and lifecycle qualification/transfer ACK-isolation
contracts passed. The combined pacing/client/relay suite returned 166 passes and
four failures in existing dirty server inventory code. Re-running those exact
four tests with only their BOT source bound to committed HEAD returned four
passes (150 deselected). No dirty server changes are included in this activation.
The client pacing tests include actual PowerShell execution and source parsing.
Post-restart progress must be recorded separately; no measured speed or
completion claim is made here.

After commit 08b0255, root enabled and started the same Scheduled Task once.
Exactly one new loop owner was observed: PID 10172, started
2026-09-05T01:08:30Z. Three startup quiet probes passed in 747, 274, and 271 ms.
The last inspected file-progress heartbeat still belonged to the interrupted
owner (01:05:09Z, index 2544). Therefore new-owner file-transfer throughput and
terminal parity are NOT YET VERIFIED. Repeated focused pacing tests: 5 passed.

Verified subsequent progress: PID10172 published new chunk receipts at
01:11:49Z (research.db 4,194,304/17,846,272 bytes) and 01:12:49Z
(12,582,912/17,846,272 bytes). File index is 3/34433 in the re-enumerated manifest;
this proves current transfer, not discarded checkpoints or tiny-file throughput.
An authenticated Fly /ready probe at 01:12:51Z responded in 857ms with
system_ready=true, pending/open paper 0/0, scheduled AI completed_ts
1788570620.8171742 and stage IDLE. Mirror parity still MISMATCH; no terminal ACK.
