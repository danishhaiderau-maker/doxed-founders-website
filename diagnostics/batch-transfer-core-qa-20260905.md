# Verified batch-transfer core — source only

Purpose: replace per-receipt HTTP overhead with bounded transport packages while
retaining every original manifest row, hash, generation and acknowledgement.

Implemented core builds deterministic uncompressed TAR packages of at most 256
immutable members and 16 MiB payload. Only canonical market-segment paths and
COMMITTED emergency-idempotency receipts are eligible. Hot ledgers and SQLite
stay on their existing consistency-fenced transfer paths.

Validation precedes bounded reads. Source size/mtime/inode are checked before
and after reading; growth reads stop at expected size + one byte. Symlink and
Windows reparse paths are refused. Package publication is fsynced and atomic.
Extraction rejects noncanonical aliases, traversal, duplicate/unexpected members,
links, identity/hash/count mismatches and oversized packages. All members must
verify before a staged result is returned. No source or canonical mirror is deleted.

Independent implementation and root review added resource/race bounds,
COMMITTED receipt checks, normalized-path rejection and TAR record-padding
headroom. Executable verification: 24 passed, one real-symlink fixture skipped
because this Windows host lacks symlink creation privileges. A deterministic
Windows reparse-point test passed. Maximum-payload TAR roundtrip passed.

NOT ACTIVE: bounded resumable worker, authenticated package API, client download
and extraction integration, exact original-row ACK, deploy and runtime canary
are still required. The current downloader continues using per-file transfer;
this module alone is not a throughput or completion receipt.
