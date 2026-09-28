# Batch runtime-root repair

Deployed cb7745e resolves eligible runtime-relative inventory paths under the
volume parent. Actual evidence is under /app/data/runtime, not /app/data/v3.
Entrypoint does not provide a v3 alias. The coordinator now passes the runtime
root; derivative output remains volume/.data-sync-snapshots/transport-bundles.

The topology regression extracts the actual coordinator and relpath functions,
uses separate volume/runtime directories, and performs real package/extraction
roundtrips. Before repair: two failures (market segment and signal snapshot).
After repair: 83 passed, one existing Windows symlink limitation skipped across
topology, integration, coordinator, worker, runtime and transport tests.

This is source/test verification, not a production transfer receipt. No source
deletion, cleanup acknowledgement or live trading behavior is changed.

Runtime recovery job 33945673051 completed successfully. At 05:31:11 UTC,
production cb7745e reported bootstrap COMPLETE, paused false, system_ready true.
First authenticated manifest after recovery remained STALE_REVALIDATING with no
inventory SHA; authority is not yet established. Downloader stays parked.

Revision-only deployments currently restart the identity-bound receipt bootstrap.
Do not relabel source revisions to bypass that check. Investigate a bounded call
to the existing deployed worker with real generation/pressure/ownership checks
before causing another restart. No such production worker call is yet proven.
