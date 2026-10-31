"""Retired V3 streams (RK PASS 25 Oct 2026, R17). Standalone: no project imports.

Shared by research_v3_store.open_read_only and #872. A stream listed here may keep a stale
identity pointer (it is never republished) WITHOUT failing the read-only store, but only
while its real ledger file still matches the fingerprint recorded at retirement. Any write
after retirement, or a file that can't be read, is a conflict (RED on the retired stream).

Fingerprint mismatch must NOT poison active ledgers (decision/execution/opportunity): the
pinned fingerprint is Fly-volume-specific, while the laptop analyzer reads a different host
copy. open_read_only soft-excludes the retired stream on fingerprint-family refusals and
keeps hard-raising only on identity (epoch/tile/field) mismatches.

Adding or changing an entry here REQUIRES an RK ruling. Do not edit without one.

R21 same-run rule (R0c wave plan sha eb14e8711d31): any R21 action that changes a
fingerprinted file MUST, in the same run, re-measure live rows/size/mtime/sha256 and land
the matching RETIREMENT_FINGERPRINTS update (or STOP / R34 RED on mismatch vs a declared
plan pin — never land a surprise measured value). See docs/LEDGER_STAMP_MONITOR_CHECKLIST.md.
"""
from __future__ import annotations

import os
from pathlib import Path

RETIRED_STREAM_ALLOWLIST: dict[str, str] = {
    "market_segment": "MARKET_SEGMENTS_RETIRED_R17",
}

# R0c follow-up (plan sha eb14e8711d31). Pin ONLY if Fly confirm-measure at merge gate
# matches all four: rows 18289 / size 37892450 / file sha
# 7ca7436d1ba4f4a92dd9150983d00b012c64c79e9e21198035e2b7e0f2319f57 / mtime 1792899429.
# On any mismatch → STOP / R34 RED / do not merge / escalate RK / never land a new measured
# value (HM b73586b2dc16). Prior pin (18322 / 37969967 / mtime 1792830018) kept in
# retired_streams.py.bak-2113a082dbad. R21 receipt 6c51ea64c3e1; HM CARRY 4d36b9db01f1.
RETIREMENT_FINGERPRINTS: dict[str, dict[str, int]] = {
    "market_segment": {"rows": 18289, "mtime": 1792899429, "size": 37892450},
}

# open_read_only: exclude the retired stream (serve no rows) instead of raising a store-wide
# identity conflict. Active authorities stay readable; tipMatch / v3_read_authority must not
# go INVALID solely because a Fly-pinned fingerprint disagrees with a laptop copy.
SOFT_RETIRED_EXEMPTION_REFUSALS: frozenset[str] = frozenset({
    "RETIRED_STREAM_WRITTEN_SINCE_RETIREMENT",
    "RETIRED_STREAM_HAS_ROTATIONS",
    "RETIRED_STREAM_UNREADABLE",
    "RETIRED_STREAM_FINGERPRINT_MISSING",
})


def retired_stream_unchanged(ledger_dir: str | Path, stream: str) -> tuple[bool, str]:
    """(True, '') only if ``stream`` is allowlisted and its file proves no writes since retirement."""
    if stream not in RETIRED_STREAM_ALLOWLIST:
        return False, "RETIRED_STREAM_NOT_ALLOWLISTED"
    fp = RETIREMENT_FINGERPRINTS.get(stream)
    if not fp:
        return False, "RETIRED_STREAM_FINGERPRINT_MISSING"
    ledger_dir = Path(ledger_dir)
    path = ledger_dir / f"{stream}.jsonl"
    try:
        if any(ledger_dir.glob(f"{stream}.jsonl.*")):
            return False, "RETIRED_STREAM_HAS_ROTATIONS"
        st = os.stat(path)
        if st.st_size != fp["size"] or int(st.st_mtime) != fp["mtime"]:
            return False, "RETIRED_STREAM_WRITTEN_SINCE_RETIREMENT"
        rows = 0
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                rows += chunk.count(b"\n")
        if os.stat(path).st_size != fp["size"]:
            return False, "RETIRED_STREAM_WRITTEN_SINCE_RETIREMENT"
    except OSError:
        return False, "RETIRED_STREAM_UNREADABLE"
    if rows != fp["rows"]:
        return False, "RETIRED_STREAM_WRITTEN_SINCE_RETIREMENT"
    return True, ""
