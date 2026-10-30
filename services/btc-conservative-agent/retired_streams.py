"""Retired V3 streams (RK PASS 25 Oct 2026, R17). Standalone: no project imports.

Shared by research_v3_store.open_read_only and #872. A stream listed here may keep a stale
identity pointer (it is never republished) WITHOUT failing the read-only store, but only
while its real ledger file still matches the fingerprint recorded at retirement. Any write
after retirement, or a file that can't be read, is a conflict (RED on the retired stream).

A copied file can have a different mtime from the Fly pin and still be the same bytes.
``retired_stream_unchanged`` therefore judges content (size and row count), not mtime.
A real write, a rotation, a missing file, or a missing fingerprint is a conflict: the
read-only store hard-raises. There is no soft-exclude set.

The one identity exception is the exact #924 GS-06 retire pair
(``GS06_RETIRE_TILE_SIGNATURE_PAIRS``): the frozen retired pointer may still carry the
pre-retire registry signature while the active authority carries the post-retire
signature. Any other ``tile_config_signature`` mismatch hard-fails. This does not
weaken ``research_freeze`` roster signature checks.

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

# Fail closed. #917/#929 put fingerprint and tile-config refusals here so a mismatch
# became GREEN. That set is empty: every refusal hard-raises. The GS-06 pair below is
# not a member of this set; it is an exact signature match checked before a refusal.
SOFT_RETIRED_EXEMPTION_REFUSALS: frozenset[str] = frozenset()

# research_freeze.MID_EPOCH_REGISTRY_SIGNATURES immediately before and after #924
# (GS-06 retire + GS-FADE3-LOOSE add). score_led then hypothesis.
# Retired pointer signature, adopted (active) signature.
GS06_RETIRE_TILE_SIGNATURE_PAIRS: frozenset[tuple[str, str]] = frozenset({
    (
        "b7da543be509a0b4b51ee5481ab06023c2cf560b23ee3ccc30242fe858d13cc3",
        "f241b3d0f7eb3cb4d1364c4f22cf3de804aaa2dabaadb4b3f3f23146e236c01c",
    ),
    (
        "563c0a6f3567a446d734f6f633ccc40c0fcc61a082e9f0c8f2553ebf95a4dc69",
        "45535a186adf7185e9e215fde1154a7248e86016c435585b3d6a0ec008cbe748",
    ),
})


def gs06_retire_signature_pair(retired_sig, adopted_sig) -> bool:
    """True only for the exact pre/post #924 registry signature pair."""
    return (str(retired_sig or ""), str(adopted_sig or "")) in GS06_RETIRE_TILE_SIGNATURE_PAIRS


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
        # Content identity only. mtime differs on a byte-identical laptop copy of the
        # Fly file and is not evidence of a write (the #917 false contamination).
        if st.st_size != fp["size"]:
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
