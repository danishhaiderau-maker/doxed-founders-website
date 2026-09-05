# Reviewed lifecycle copy/ACK client — 5 September 2026

The parked downloader sources these helpers. Their previously dirty changes
were reviewed independently before activation; no running downloader was edited.

## Repair

- Qualification acknowledgements now bind explicit maturity and the evidence
  collection receipt required by the backend.
- Require genuine JSON booleans, empty collection blockers, exact identity and
  provenance; recompute collection SHA with backend-compatible Python JSON.
- Transfer-ready acknowledgements remain qualification/profitability-ineligible.
- Accept only a complete `ACKNOWLEDGED_SOURCE_RETAINED` response; the client
  acknowledgement itself does not delete or authorize source cleanup.
- Vault import supports the existing laptop attestation credentials. This is
  authority-relevant, not merely formatting. Explicit process values win.
  Controlled activation must pin `LIFECYCLE_CLEANUP_ENABLED=0`; this does not
  override Fly's independently configured retention worker.
- HMAC hexadecimal formatting is compatible with older PowerShell runtimes.

## Verification

- Independent parse check: all three helper/test files have zero errors.
- Root executed modern `pwsh -NoProfile -File scripts/test-fly-lifecycle-bundle-copy.ps1`:
  PASS qualification and transfer canonical/archive/index ACK producer.
- Includes 16 new negative cases for malformed booleans, blockers, receipt hash,
  identity/provenance and unsafe ACK truth values, plus existing corruption and
  idempotency coverage.
- Independent synthetic vault test passed without reading real secrets;
  explicit cleanup flag `0` remains `0`.
- Source-only acceptance; no live download/ACK/cleanup receipt is inferred.

At review, `DoxxedFlyMirrorSync` is disabled and no process consumes either sync
script. Existing downloads and checkpoints are retained. Activation follows the
advancing existing bootstrap continuation; no new deploy/restart is required for
these laptop-only helpers.
