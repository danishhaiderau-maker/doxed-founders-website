# Fly transfer reconciliation — 2026-09-19

## Pinned identity

- Fly revision: `f84fff81e2905fcf38d5f02f348bd8d9ee655816`
- Inventory generation/SHA: `fb7bdfca19f0a25f5736f67f13862f1e5648d98b78f1b18358211999fcf1e6d8`
- Collection epoch: `epoch-7161f943f3209b89cc66d95b`
- Manifest: `CURRENT`, authoritative, ACK-eligible; 66 pages; 16,237 files.
- Live manifest acknowledgement count: `16,237/16,237`.

## Local mirror reconciliation

- Canonical mirror: `services/btc-conservative-agent/canonical-research-data`
- Manifest paths present locally: `16,237/16,237`; missing: `0`.
- Local-only transport artifacts found: `.fly-sync-state.json`, empty
  `.fly-mirror-generation.lease`, and two zero-byte `.download` files. The two
  zero-byte temporary files were removed after verifying no transfer owner was
  running; source evidence was not removed.
- Two strict JSON size differences are authorized hot-file replacements, not
  missing tails. The transfer log records atomic-snapshot fallback for both
  paths, and local sync state records the complete replacement identities:
  `paper_lifecycle_v1.json` (274,526 bytes) and
  `v3/receipts/future-path-last-attempt.json` (381 bytes). Both parse as valid
  JSON.
- `v3/qualification_horizon_index.sqlite3` is 114,741,248 bytes locally;
  read-only `PRAGMA integrity_check` returned `ok`. The pinned initial row was
  a different snapshot size; the transfer log records successful SQLite lease
  attempts before the final ACK.

## ACK interpretation

- The client reached all ACK page calls and `acknowledgement_finalize` with
  HTTP success. The server writes its compact ACK before returning that final
  response, and the live manifest reports all 16,237 files acknowledged.
- The client then failed its terminal comparison because PowerShell treated the
  returned ISO timestamp as a locale-formatted `DateTime`; timestamp
  normalization is now patched and the focused sync contract suite reports
  `18 passed`.
- Exact `ack_session_id` and persisted served-identity files are not exported by
  the authenticated read API. Therefore this is **server-ACK evidence plus
  client transfer evidence**, not a fabricated client terminal receipt.

## Gate status

`LOCAL_MIRROR_RECONCILIATION = PARTIAL_CERTIFIED`

Do not delete the Fly source, publish analyzer rankings, resume paper, or arm
Bitfinex until the ACK/session identity is either recovered from a durable
receipt or a fresh bounded transfer produces a complete client terminal
receipt. No Bitfinex/live action was performed.
