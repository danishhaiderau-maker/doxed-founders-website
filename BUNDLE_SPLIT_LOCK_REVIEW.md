# Bundle producer/read exclusion repair

Candidate base: b1ad6d3. No production operations performed.

## Protocol

- Production retains exclusive `.bundle-worker.lease` across its bounded slice.
- DownloadProtection derives `.bundle-readers.lease` from the existing worker
  lease argument. Pins, fences, expiry and all bounded reads use that lease.
- Derivative retirement takes worker, then readers, nonblocking, and retains
  both through validation and deletion. Failure to get readers releases worker.
- No changes to original manifest ACK, retention authority, pin TTL, permanent
  fences, source deletion, file hash validation, authentication or retry budgets.
- Packages/descriptors are immutable; worker state is atomically replaced only
  after package and descriptor publication. Old complete indexes remain valid.
- Both lease files are explicitly allowlisted and accounted as 4096 bytes each;
  enumeration bounds grow by exactly one entry, not an unbounded directory walk.

## Mandatory migration boundary

Do not mix old and new lock protocols. Park all old bundle workers, HTTP owners
and maintenance owners through the existing controlled deployment boundary,
then start the consistent new image. Preserve pin/fence files and transport
packages; do not wipe/recreate them. Do not run an old retirement utility after
the migration. New readers still honor all existing persisted pins/fences.

## Callback audit

`maintain_capacity` supplies a frozen reservation-bound protection set; it
does not recursively acquire either lease. `scripts/fly_bundle_retire.py`
requires the old generation HTTP endpoint to reject authority before pin/read;
an admitted/busy response fails closed. Retirement's callback contract now
explicitly forbids recursive acquisition/admitted bundle reads.

## Verification

`test_bundle_split_lock.py` uses spawn-process barriers to prove existing index,
descriptor and chunk reads while producer owns its OS lease; a real paused
build preserves the earlier published index and advances to the new index after
release. It verifies both retirement exclusions and release on failed second
lock acquisition. Pin expiry cannot defeat an in-flight read; durable fences
still deny readers afterward. Existing protection/security suites remain in
the acceptance command. These are source tests, not deployed recovery proof.

Acceptance XML: services/btc-conservative-agent/split-lock-acceptance.xml.
Production acceptance must additionally show advancing package production and
download bytes on one generation, then full original-manifest ACK and current
analyzer publication. No live-trading readiness claim follows from this repair.
