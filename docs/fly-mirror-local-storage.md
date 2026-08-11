# Fly mirror local storage

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

Raw Fly runtime evidence is stored outside the repository and outside OneDrive by default:

`%LOCALAPPDATA%\DoxxedCrypto\fly-data-mirror`

The sync loop, one-shot sync, and desktop analyzer resolve the same location. Set
`DOXXED_FLY_MIRROR_DIR` for a different machine-local disk. Analyzer reports remain under
`services/btc-conservative-agent` so readable results and exports are separate from raw data.

## Safe migration from the legacy OneDrive mirror

1. Stop desktop tools so the sync loop is not writing the source snapshot.
2. Run:

   `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\migrate-fly-mirror-to-local.ps1`

3. The command copies every file, verifies SHA-256 for every source/destination pair, and
   reports `verified_copy_complete`. It never deletes the source.
4. Restart desktop tools and confirm the sync heartbeat, local-size panel, and a fresh analyzer
   report all reference the machine-local target.
5. Only after that operational proof may an operator archive or remove the legacy source.

Do not place `DOXXED_FLY_MIRROR_DIR` under OneDrive. Acknowledged Fly rotations can later be
pruned, so the local raw mirror may contain unique historical evidence and must remain under the
separate retention/cap policy. Readable analyzer reports and handoff exports are the portable
artifacts to preserve before any eligible raw rotation is deleted.
