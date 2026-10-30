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
"""
from __future__ import annotations

import os
from pathlib import Path

RETIRED_STREAM_ALLOWLIST: dict[str, str] = {
    "market_segment": "MARKET_SEGMENTS_RETIRED_R17",
}

# Recorded from the live file (doxed-btc-bot, read-only, 25 Oct 2026 03:38 AEDT).
# mtime 1792830018 = 24 Oct 2026 19:20:18 AEDT (last write before MARKET_SEGMENT_WRITES_RETIRED).
RETIREMENT_FINGERPRINTS: dict[str, dict[str, int]] = {
    "market_segment": {"rows": 18322, "mtime": 1792830018, "size": 37969967},
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
