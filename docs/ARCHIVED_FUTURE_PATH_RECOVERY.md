# Archived future-path recovery

`services/btc-conservative-agent/recover_archived_future_paths.py` is an
offline evidence-recovery tool. It does not run in the collector and archived
files are never implicit runtime sources.

The command defaults to a read-only dry run. It requires the canonical root,
epoch, archived tape, independently recorded byte size, and SHA-256:

```text
python recover_archived_future_paths.py \
  --canonical-root <canonical-research-data> \
  --archive-tape <archive/sync-retired/.../market_microstructure_1s.jsonl.N> \
  --epoch-id <epoch> --expected-size <bytes> --expected-sha256 <sha256>
```

Add `--apply` only after reviewing the dry-run JSON. Apply mode verifies that:

- every resolved path stays inside the canonical store;
- the tape is beneath `archive/sync-retired/` and is not a symlink;
- its cleanup receipt says `recoverable: true`, identifies the same original
  and archived relative paths, and records retirement because the file was
  absent from the authenticated Fly manifest;
- the independently supplied size and SHA-256 match the archived bytes; and
- the tape fits within the same 24 MiB evidence-read ceiling.

Apply mode never edits or deletes an earlier ledger row. It appends a
deterministic superseding `COMPLETE` or `UNKNOWN` record, writes any complete
path as a content-addressed object, and publishes one immutable status receipt
under `v3/receipts/`. Reapplying an already receipted source is a no-op.

`COMPLETE` means only that both requested time bounds are present. Coverage
still records maximum gaps, BBO/depth counts, and
`conservative_bbo_depth_eligible`; a bound-complete path with gaps remains
separately ineligible for conservative execution claims.
