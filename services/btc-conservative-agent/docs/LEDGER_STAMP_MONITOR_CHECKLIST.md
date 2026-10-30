# Ledger stamp monitor checklist (v3_ledger_unstamped_since)

Route: `GET /api/monitor/ledger-unstamped?since=<unix_ts>`. Module: `ledger_stamp_monitor.py`.

## Status rules (HM R2, 25 Oct 2026)
- Boundary key is only `write_stamp.written_at_ts` (M1). Every rotated `{ledger}.jsonl.N` is scanned (M2).
- Unstamped rows newer than any stamped row: **RED** (fail closed, never skipped).
- Rows below the oldest stamped row: reported in `legacy_unstamped_before_first_stamp`, not RED (M3).
- Scan budget exhausted (`DEFAULT_MAX_BYTES` = 256 MiB per call): **AMBER** with `unclassified_at_cap`; never GREEN.
- Retired streams are an explicit allowlist `RETIRED_STREAM_ALLOWLIST = {market_segment: MARKET_SEGMENTS_RETIRED_R17}`.
  IDLE needs the allowlist entry AND every writer flag True:
  `research_v3_bridge.MARKET_SEGMENT_WRITES_RETIRED`, `research_v3_future_paths.SEGMENT_WRITES_RETIRED_R17`,
  `recover_archived_future_paths.SEGMENT_WRITES_RETIRED_R17`, `qualification_horizon_index.SEGMENT_WRITES_RETIRED_R17`.
- Any write since the cutoff to a retired stream (stamped or not): **RED** `WRITE_TO_RETIRED_STREAM`.
- A non-allowlisted stream with no write since the cutoff (or no file): **RED** `NO_WRITE_SINCE_NOT_ALLOWLISTED`.
- **R21 same-run fingerprint (R0c / plan `eb14e8711d31`):** when an R21 dry→execute changes any file covered by
  `RETIREMENT_FINGERPRINTS` in `retired_streams.py`, the same run must re-measure live rows/size/mtime/sha256 and
  land the matching fingerprint update in that run’s code PR (or a paired PR citing the R21 receipt). On mismatch
  vs a declared plan pin → STOP / R34 RED / do not merge / escalate RK / never land a surprise measured value.
  Keep the prior module as a sha-named `.bak-<sha12>`. No hand edits to the data file.
- mtime boundary (`FILE_MTIME_BEFORE_SINCE`) is cross-checked against the generation's last stamp, because a
  timestamp-preserving restore/copy can make mtime lie: no stamp -> warning `MTIME_BOUNDARY_UNCONFIRMED_BY_STAMP`;
  stamp >= since -> warning `MTIME_BOUNDARY_CONTRADICTED_BY_STAMP` + unverified (not GREEN).

## Epoch rollover
1. The epoch is pinned once: `research_freeze.FREEZE_DATA_EPOCH_ID` (today `ce-20261004-v31-freeze21b`);
   `ledger_write_stamp.EXPECTED_DATA_EPOCH_ID` and `research/tile_realism_table.py` read it. No other literal.
2. Fly env `DATA_EPOCH_ID` must equal the pin; any other value makes `configured_epoch_id()` None and every
   stamp is refused (rows become UNSTAMPED -> RED). Change the pin and the env together, in one reviewed PR + deploy.
3. After rollover, run the monitor with `since` = the new epoch's first write (deploy boot time); expect RED only
   if rows were written unstamped. Update `RETIRED_STREAM_ALLOWLIST` only via a reviewed PR with all writer flags.
4. Record the new epoch id, deploy release and boot `since` in SHARED-TRACKER.md (R1) before the switch.

## Reference: 25 Oct 2026 one-off scan (read-only, Fly)
- Cutoff `written_at_ts >= 1792851605` (25 Oct 2026 01:20:05 AEDT); run at 1792852401 (01:33:21 AEDT).
- Full read of every generation, **no size cap** (order_intent.jsonl = 2,525,648,818 bytes, 8,822 rows, 1 generation).
- order_intent: 23 stamped since cutoff; **8,776** unstamped rows all below the first stamped row (legacy), 0 after.
  HM's earlier 1,016 came from the monitor's 256 MiB cap and is only a lower bound.
