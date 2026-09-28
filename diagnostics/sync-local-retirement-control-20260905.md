# Laptop sync retirement is explicitly opt-in

The previous handoff mentioned a nonexistent local-cleanup disable flag.
Inspection of ab110c5 showed archive-first local raw-file retirement always ran
before transfer. The downloader was parked (Scheduled Task disabled and no
sync/client processes) before editing the consumed script.

`FLY_SYNC_LOCAL_RETIREMENT_ENABLED=1` now explicitly enables the existing
archive/hash/source-stability verification and removal path. Missing or any
other value retains local raw files, does not enumerate/hash retirement
candidates, and reports DISABLED_SOURCE_RETAINED with zero retired bytes/files.
Keep this flag unset or 0 for the initial recovery download.

Root tests: `python -m pytest scripts/test_fly_sync_local_retirement.py services/btc-conservative-agent/test_fly_sync_bundle_adapter.py scripts/test_fly_transport_bundle_workflow.py -q`

61 passed in 23.36 seconds. Nine retirement tests execute the real PowerShell
block, including default retention, explicit archive-first success, corrupted
archive/source preservation and whole-script parsing. These are not live-sync
receipts. No download, cleanup or production changes were executed.

This does not modify loop cleanup of dead-owner scratch files, successful batch
scratch removal, Fly lifecycle/raw cleanup, or network resume. The current batch
client still re-fetches packages on retry and transfers unbundled files by chunks.
Full transfer, immutable ACK and canonical promotion remain pending.
