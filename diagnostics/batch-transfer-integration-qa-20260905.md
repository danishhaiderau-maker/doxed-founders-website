# Batch transfer integration receipt — source only

The prior serial download is parked by user authorization. Task remains Disabled;
PID10172 is absent. No production deployment, restart, ACK or cleanup occurred.

## Implemented path

Validated inventory publication captures its admission revision/epoch/config.
An opt-in singleton coordinator launches bounded child slices under pressure,
free-space, per-generation and total derivative admission. Prebuilt package GETs
require an actual admin credential even on loopback. Each descriptor, chunk,
package and extracted member is checked against the original manifest. The
PowerShell client promotes verified members through existing atomic replacement,
checkpoints them, then runs the original full-manifest loop and unchanged ACK.
Bundled rows skip that loop's per-file requests and sleep.

Activation flags remain OFF by default:

- Fly DATA_SYNC_TRANSPORT_BUNDLES_ENABLED=1 enables package preparation.
- Laptop FLY_SYNC_TRANSPORT_BUNDLES=1 opts into verified package consumption.

Neither is enabled in production by this source change. A BUILDING/unavailable
bundle index is a classified failure, not silent serial fallback. No source
purge, registry change, trading gate change or Bitfinex operation is included.

## Executable evidence

-146 passed,1 existing host-dependent symlink skip across package core, worker,
 API, pure client, runtime, backend integration, total derivative admission,
 Python HTTP adapter and real PowerShell subprocess/promotion fixtures.
- Backend integration tests in that run read the selectively staged bot.py,
 excluding unrelated dirty inventory TTL/bootstrap/polling and PnL changes.
-384 small objects became3 packages retrieved through7 HTTP requests, with all
 file bytes verified and original path/size/mtime ACK rows exactly equal.
 This is fixture efficiency, NOT measured Fly production throughput.
- Real PowerShell child verified promotion/checkpoint and rejected a bad member
 hash without publishing it. Python selection bug with multiple PATH entries
 found by this test was repaired by selecting exactly one executable.
- PowerShell client and helper parse clean. Python modules compile.

Legacy regression check:158 passed,5 failed on the mixed worktree. Four failures
are caused by unrelated dirty inventory admission/tuning functions; all four
passed when rerun against the selectively staged bot source. The fifth was an
older pacing-patch reverse-apply context check disturbed by the new helper import;
moving that import outside the historical patch context restored all5 pacing
tests. No trading/inventory gate was weakened to make those tests pass. The
mixed worktree is still NOT a deployment candidate.

## Independent findings addressed

Strict token auth, link/reparse path checks, package replacement/growth fences,
descriptor/index count parity, short Windows temporary names, captured rather
than relabeled generation identity, hard subprocess timeouts and two-failure
circuit. The original128MiB conservative derivative estimate could not cover
34,433 tiny receipts;256MiB per-generation cap plus512MiB free reserve and
512MiB total derivative admission with max4 retained generations now prevent
that specific dead end and prevent unrestricted accumulation.

Successful laptop package scratch files are removed only after hash-verified
canonical promotion and checkpointing. Failed nonempty scratch is retained;
only an empty unique directory may be removed without recursive deletion.

## Still required before activation / completion

- Integrated source committed as 91582de; clean-candidate regression and guarded
  deployment remain separate receipts.
- Production pressure/latency canary on guarded exact deploy.
- Recurring derivative retention/reclamation, including diagnosis of interrupted
 orphan packages and failed laptop staging. Admission bounds are not GC receipts.
- Verified complete mirror/ACK, current atomic analyzer, new-generation UI QA,
 model-context wiring, strategy qualification and all Bitfinex readiness gates.

Passing these source tests does not establish profitable strategies, current
production data, sustained faster sync, or permission to delete Fly evidence.

## Full-backlog terminal fixture receipt

The real subprocess worker, authenticated API test client, and verified download
client completed all 34,433 synthetic small receipt files: 24,195,289 payload
bytes, 138 inventory pages, 276 packages, 553 HTTP requests, 276 worker slices.
Every member's bytes and every original ACK row matched exactly. Maximum child
slice was 1.531 seconds; total local fixture time was 681.547 seconds. Conservative
derivative admission estimate was 174,370,084 bytes. Temporary fixture cleanup
completed; process exited 0. This is not Fly network throughput or a source ACK.

Deployment preflight also found older canonical-to-engine drift, including the
durable relay outbox. The reviewed index mirrors only committed canonical bot
and outbox bytes; unrelated dirty engine edits remain 20 insertions/6 deletions.
The clean exported candidate passed full signal parity, combo fixtures and
research/showcase signal-flag equality. Explicit normal-deploy batching opt-in
and portable package tests are now added to the workflow; default remains OFF.
Clean exported candidate regression: 325 passed, 1 host-dependent skip in
180.09 seconds across package integration, legacy sync/backoff/pacing, active
registry/execution graph and workflow contracts. This resolves the mixed-tree
test ambiguity above for the reviewed candidate only, not the remaining edits.

Read-only pre-canary production sample at 2026-09-05T02:47:39-42Z: df45887e1526;
/health 200/1129ms, /ready 200/344ms, /api/status 200/393ms; paper-only, unpaused,
Bitfinex disabled, relay disarmed, pending 0/open 0. One started machine. Batching
flag absent. This single snapshot is not proof of sustained cycle advancement.
