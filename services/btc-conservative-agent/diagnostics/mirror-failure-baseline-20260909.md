# Canonical mirror recovery baseline

Read-only snapshot of canonical `.fly-data-sync-loop.heartbeat.json` on2026-09-09.

- Receipt timestamp:2026-09-06T23:29:58.8916504+10:00
- ok:false; phase:bundle_failed; failureCode:PACKAGE_RETRY_EXHAUSTED
- Files:3148/36592; verifiedPayloadBytes:52667501
- newlyTransferredPayloadBytes:0 in this saved receipt, not a current measurement
- completionAuthority:NONE_TRANSFER_PROGRESS_ONLY; ackPending:true
- mirroredSourceRevision:1b23c945e315e1884c94d4865adfbe4a9c0c01d2
- revisionParity:MATCH applies to REQUEST_VS_MANIFEST_NOT_MIRROR_COMPLETION
- collectionEpochId:epoch-5a856f6f873c84fee7cefb2a

This old receipt is neither live process proof nor a completed/current mirror. It explains the actual reconciliation CLI failure MIRROR_SYNC_RECEIPT_FAILED. Preserve it until a newly verified terminal transfer supersedes it; do not manually flip ok/parity/ACK flags.

Current Fly inventory worker remains separate and live: same resume token,15395 files inspected,120 invocations,SCAN,no failure,no authoritative SHA. No download or restart initiated by this diagnostic.
