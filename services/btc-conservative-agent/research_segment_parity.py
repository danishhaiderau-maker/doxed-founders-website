"""Compare the segment SHADOW mirror against the legacy mirror (read-only).

Rules (from the transfer proposal, Phase 2b):
* Active append streams (``.jsonl``/``.csv``/``.log``): the shorter copy must
  be a byte prefix of the longer one. Either side may be ahead in time.
* Sealed rotations (``x.jsonl.N``): exact sha256; a mismatch is a hard failure.
* Other files (JSON state, receipts, SQLite): exact sha256 is reported; a
  difference is ``content_differs`` (expected timing drift for mutable
  snapshots), not a hard failure.
* Files present only on one side are reported, not failed: the legacy mirror
  holds history from before segment genesis and the shadow may be ahead.

Verdict GREEN requires zero prefix mismatches and zero sealed mismatches.
Neither tree is modified.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import sys
from pathlib import Path

APPEND_SUFFIXES = (".jsonl", ".csv", ".log")
LEGACY_OPERATIONAL_MARKERS = (".download", ".bak", ".tmp", ".part", ".validation.json.tmp")
EXAMPLE_LIMIT = 50


def _is_rotation(name: str) -> bool:
    base, _, suffix = name.rpartition(".")
    return bool(base) and suffix.isdigit() and not suffix.startswith("0") \
        and base.lower().endswith(APPEND_SUFFIXES)


def classify(relpath: str) -> str:
    name = relpath.rsplit("/", 1)[-1]
    if _is_rotation(name):
        return "sealed"
    if name.lower().endswith(APPEND_SUFFIXES):
        return "append"
    return "snapshot"


def open_shared_read(path: Path):
    """Open read-only without blocking concurrent rename-away or delete on Windows.

    A plain ``open()`` omits FILE_SHARE_DELETE. Windows still refuses a
    replace *onto* a file another process holds open, so schedule parity runs
    outside legacy publish windows.
    """
    if os.name != "nt":
        return path.open("rb")
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                     wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                                     wintypes.HANDLE)
    generic_read, share_all, open_existing, normal = 0x80000000, 0x7, 3, 0x80
    handle = kernel32.CreateFileW(str(path), generic_read, share_all, None, open_existing,
                                  normal, None)
    if handle in (None, wintypes.HANDLE(-1).value):
        raise ctypes.WinError(ctypes.get_last_error())
    return os.fdopen(msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY), "rb")


def _sha256(path: Path, limit: int | None = None) -> str:
    digest = hashlib.sha256()
    remaining = limit
    with open_shared_read(path) as handle:
        while remaining is None or remaining > 0:
            chunk = handle.read(1 << 20 if remaining is None else min(1 << 20, remaining))
            if not chunk:
                break
            digest.update(chunk)
            if remaining is not None:
                remaining -= len(chunk)
    return digest.hexdigest()


def _walk(root: Path) -> dict[str, Path]:
    files = {}
    for directory, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if not name.startswith(".")]
        for name in filenames:
            if name.startswith(".") or any(marker in name for marker in LEGACY_OPERATIONAL_MARKERS):
                continue
            path = Path(directory) / name
            files[path.relative_to(root).as_posix()] = path
    return files


def compare(shadow_tree: Path, legacy_tree: Path) -> dict:
    shadow, legacy = _walk(shadow_tree), _walk(legacy_tree)
    counts = {key: 0 for key in (
        "exact", "prefix_match", "prefix_mismatch", "sealed_mismatch", "content_differs",
        "missing_in_legacy", "missing_in_shadow",
    )}
    examples = {key: [] for key in counts}

    def record(key: str, relpath: str, **detail) -> None:
        counts[key] += 1
        if len(examples[key]) < EXAMPLE_LIMIT:
            examples[key].append({"path": relpath, **detail})

    for relpath, shadow_path in sorted(shadow.items()):
        legacy_path = legacy.get(relpath)
        if legacy_path is None:
            record("missing_in_legacy", relpath)
            continue
        kind = classify(relpath)
        shadow_size, legacy_size = shadow_path.stat().st_size, legacy_path.stat().st_size
        if kind == "append":
            common = min(shadow_size, legacy_size)
            if _sha256(shadow_path, common) != _sha256(legacy_path, common):
                record("prefix_mismatch", relpath, shadow_size=shadow_size, legacy_size=legacy_size)
            elif shadow_size == legacy_size:
                record("exact", relpath)
            else:
                record("prefix_match", relpath, shadow_size=shadow_size, legacy_size=legacy_size)
            continue
        same = shadow_size == legacy_size and _sha256(shadow_path) == _sha256(legacy_path)
        if same:
            record("exact", relpath)
        elif kind == "sealed":
            record("sealed_mismatch", relpath, shadow_size=shadow_size, legacy_size=legacy_size)
        else:
            record("content_differs", relpath, shadow_size=shadow_size, legacy_size=legacy_size)
    for relpath in sorted(set(legacy) - set(shadow)):
        record("missing_in_shadow", relpath)
    verdict = "GREEN" if counts["prefix_mismatch"] == 0 and counts["sealed_mismatch"] == 0 else "RED"
    return {
        "schema": "research_segment_parity_v1",
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "shadow_tree": str(shadow_tree), "legacy_tree": str(legacy_tree),
        "verdict": verdict, "counts": counts, "examples": examples,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Segment shadow vs legacy mirror parity")
    parser.add_argument("--shadow-tree", default=r"C:\DoxxedCrypto\fly-mirror-segments\tree")
    parser.add_argument(
        "--legacy-tree",
        default=r"C:\DoxxedCrypto\btc-v31-current\services\btc-conservative-agent\canonical-research-data",
    )
    parser.add_argument("--report", default=r"C:\DoxxedCrypto\fly-mirror-segments\parity-latest.json")
    args = parser.parse_args(argv)
    for raw in (args.shadow_tree, args.legacy_tree, args.report):
        if "\\onedrive\\" in os.path.abspath(raw).lower():
            print(json.dumps({"ok": False, "error": f"refusing OneDrive path {raw}"}))
            return 2
    report = compare(Path(args.shadow_tree), Path(args.legacy_tree))
    target = Path(args.report)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, target)
    print(json.dumps({"verdict": report["verdict"], "counts": report["counts"]}, sort_keys=True))
    return 0 if report["verdict"] == "GREEN" else 1


if __name__ == "__main__":
    sys.exit(main())
