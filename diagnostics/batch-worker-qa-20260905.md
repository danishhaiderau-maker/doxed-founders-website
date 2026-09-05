# Resumable batch worker — source-only verification

The worker consumes published disk_pages_v2 index/pages with hash and count
validation. It builds one bounded package per invocation, persists an atomic
cursor/index, skips ineligible hot files to the existing per-file path, and
uses one OS-held lease. It performs no raw-volume walk, HTTP request, mirror
promotion, ACK, production change or source deletion.

Root took ownership after the implementation agent was pending initialization,
reviewed the draft and added bounded metadata reads, strict integer counts,
bounded index lines, descriptor hashes for serving, zero-total handling, and
explicit cooperative time-budget semantics. Hard wall-clock isolation still
requires a timed subprocess driver; filesystem IO/fsync cannot be interrupted
by a Python deadline check.

Verification: worker + transport core, 39 passed and one host-permission symlink
fixture skipped. The deterministic reparse fixture passes. Tests cover resume,
index/page/source drift, singleton lease, budgets, source preservation, ineligible
fallback, descriptor hashes and exact generation totals.

Remaining integration: managed background driver and pressure/backoff cadence;
authenticated immutable package API; laptop client verified extraction and
original per-member ACK; guarded deployment and measured canary. Not active.

Storage audit correction: production /api/data_size currently hard-codes
source_cleanup_authorized=false and source_reclaimed_bytes=0. These are default
projections, not transaction totals. At 01:29 UTC the current filesystem was
68.2% used with 1,084.2 MiB free; the displayed 2,088.88 MiB runtime figure was
STALE inventory. Verify actual cleanup receipts before reporting reclaimed bytes.
