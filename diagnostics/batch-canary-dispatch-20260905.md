# Batch canary deployment dispatch

- Candidate: 194f40bddb9340242d02c11dbb87cee1fae5f7a2, pushed to
  codex/v31-analyzer-cohort-truth.
- Guarded Actions run: 33940188490; observed headSha matches the full candidate.
- Inputs: mode=deploy, transport_bundles=true. This enables bounded backend
  preparation only after the guarded deploy succeeds. Laptop owner remains
  parked, scheduled task Disabled; no second downloader has been started.
- Full-backlog local receipt: 34,433 files, 276 packages, 553 requests,
  every_member_bytes_equal=true, original_ack_rows_equal=true. Temporary fixture
  cleanup COMPLETE, exit 0. Not production throughput or immutable Fly ACK.
- Clean exported candidate: full signal parity PASS; 325 regression tests passed,
  1 host-dependent skip. Additional Docker import smoke added for shipped bundle
  modules. Source tests are not deployed readiness.
- Pre-canary Fly baseline: 2026-09-05T02:47:39-42Z, df45887e1526, paper-only,
  unpaused, Bitfinex disabled/disarmed, pending 0/open 0, healthy endpoints.
- Disposable index-export QA directory remains at
  C:\DoxxedCrypto\btc-v31-current\.qa-batch-candidate-289e77585a4b.
  A proposed read-only ownership check followed by deletion was rejected by
  execution policy before it ran. No deletion claimed. Regenerable source copy,
  not research evidence; defer cleanup rather than bypass policy. Measured C:
  free space before that attempt: 311,543,283,712 bytes.

Next proof: terminal guarded run, exact production revision and paper safety,
current signed inventory with complete package index, a single flagged client
owner, measured throughput/endpoint health, full manifest ACK, atomic analyzer.

## Run 33940188490 terminal failure and incumbent recovery

The run failed before deployment in test_manual_pause_entry_gate.py: raced
order cancellation/removal failed (54 pass, 2 fail). This was a real handoff
regression: touch detection now retains PENDING until durable OPEN, while the
pause branch only published expiry. Local repair uses the existing confirmed
cancellation route, preserves uncertain/private-fill cases, and always releases
handoff tracking. Updated realistic fixture preserves the original assertions:
clean candidate 58 passed/0 failed; 8 focused fault/race tests passed. Clean
candidate lifecycle restart/outbox/handoff suite: 55 passed.

The workflow's unconditional failure handler DID pause the unchanged incumbent
despite failure before maintenance/deploy. Root verified authenticated status
and /ready on df45887e1526 then used the normal /api/resume. Postcondition:
paper-only, unpaused, manual pause false, live disabled/disarmed, pending 0/open 0.
The failure handler now requires an actually attempted maintenance/deploy step;
all 16 step-outcome combinations are tested. No real-exchange action occurred.

Actual manifest wire inspection found inventory_ack_eligible, not internal
ack_eligible. Laptop adapter now strictly admits canonical schema + CURRENT +
inventory_authoritative=true + inventory_ack_eligible=true, rejects conflicting
aliases, and binds all original identity fields. It waits on the SAME generation
for bounded package preparation (600s, 5-30s cadence), with two-pressure-failure
circuit and total1800s budget. INDEX_WAITING is not copied-file progress or ACK.
Adapter/client suite:59 passed. Real PowerShell parent rejects foreign/late wait
receipts and accepts valid waits without checkpointing them.

Remaining CI audit found exactly three missing characterization entries for the
already-committed transition->file->trade lock order. Only those entries added;
named graph acyclic, threaded saver/transition and outbox crash-recovery checks
passed (53 tests). Workflow now invokes pytest for that existing concurrency
test, which its prior script-form invocation did not execute.

Final clean exported repair candidate: full signal parity PASS (d4a2d6fdc45f),
manual-pause script58 passed/0failed, and129 focused integration/PowerShell/
canonical-manifest/wait/handoff/cleanup/concurrency/workflow tests passed in
57.11 seconds. Unrelated dirty bot/engine edits remain excluded from the index.
