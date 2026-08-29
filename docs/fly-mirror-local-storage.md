# Canonical Fly research-data storage

## Hard 30 GiB raw-mirror boundary

The desktop mirror has two coordinated safeguards. The downloader refuses any
sync whose projected local growth would exceed 30 GiB (`MaxLocalMirrorGiB`).
The analyzer retention pass records fingerprints in both the daily JSON
manifest and `storage_retention_receipt.md`, then may remove only closed numeric
JSONL/CSV/log/text rotations recorded by that receipt. Active ledgers, databases, and files
that have not been acknowledged in the receipt are never cap-deletion
candidates. If those protected files alone exceed the cap, status becomes
`FAIL_SAFE_CAP_EXCEEDED` and downloads remain blocked for operator review.

The analyzer dashboard reports bytes, percentage, configured GiB, and cap
status. `ANALYZER_RAW_MIRROR_CAP_GIB` can change the analyzer boundary; the
downloader parameter must be changed to the same reviewed value.

Raw Fly runtime evidence is synchronized into one repository-contained canonical store:

`services\btc-conservative-agent\canonical-research-data`

The sync loop, one-shot sync, collector-side archive tooling, and desktop analyzer resolve this
same location. `DOXXED_FLY_MIRROR_DIR` is retained only as a compatibility input and is rejected
unless it resolves to that exact canonical directory. Fly `/app/data` remains the durable runtime
authority; this local store is its verified analyzer mirror. Analyzer reports remain under
`services/btc-conservative-agent` so readable results and exports are separate from raw data.

## Safe migration from a legacy mirror

`%LOCALAPPDATA%\DoxxedCrypto\fly-data-mirror` and the former repository
`services\btc-conservative-agent\fly-data-mirror` directory are legacy migration/cleanup sources
only. No active collector, sync, archive, or analyzer path may use either location.

1. Stop desktop tools so the sync loop is not writing the source snapshot.
2. Run:

   `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\migrate-fly-mirror-to-local.ps1`

3. The command copies every file, verifies SHA-256 for every source/destination pair, and
   reports `verified_copy_complete`. It never deletes the source.
4. Restart desktop tools and confirm the sync heartbeat, local-size panel, and a fresh analyzer
   report all reference the repository-contained canonical target.
5. Only after that operational proof may an operator archive or remove the legacy source.

Do not redirect `DOXXED_FLY_MIRROR_DIR`; the resolver fails closed when it is not the canonical
path. Acknowledged Fly rotations can later be pruned, so the canonical mirror may contain unique
historical evidence and must remain under the separate retention/cap policy. Readable analyzer
reports and handoff exports are the portable artifacts to preserve before any eligible raw
rotation is deleted.
