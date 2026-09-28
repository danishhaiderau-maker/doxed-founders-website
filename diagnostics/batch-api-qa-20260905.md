# Prebuilt package API — source only

Explicit Flask registration helper, not yet registered in bot.py. Injects strict
authorization and retained-generation lookup. Read-only routes expose a bounded
package index, descriptor and <=1 MiB checksum-labeled chunks. No HTTP-path
package building, full-TAR hashing, inventory trigger, source scan, ACK or purge.

The worker publishes path-free descriptors so their canonical hashes match the
descriptor returned to the laptop. Server-local paths are not exposed. Wrong
generation/epoch, missing or ineligible retained inventory, duplicate/malformed
index entries, invalid ranges and descriptor tampering fail closed.

Tests: API + resumable worker + transport core = 51 passed, one host-permission
symlink fixture skipped. Deterministic reparse rejection, bounded archive reads,
descriptor hash equality, strict authorization and generation-change tests pass.

Open before activation: bind retained generation metadata to the published
inventory identity; integrate managed, pressure-aware, timed worker scheduling;
register authenticated routes; implement client package extraction and original
member ACK; test the full large-small-file fixture; guarded deployment and an
authorized single-owner client handoff; measured trading latency and sync speed.

Integration issue to resolve: current worker forbids output inside source_root,
but production's excluded derivative area is /app/data/.data-sync-snapshots.
Permit only that verified excluded namespace (not arbitrary runtime subfolders),
with containment/reparse checks and tests, before wiring the production driver.
