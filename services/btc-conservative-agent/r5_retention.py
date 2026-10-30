"""R5 retention for the v3 evidence store (DISK-CAP, E4b/E4c, v3/.locks).

R5: caps and discards are never custody- or ack-gated. When a cap removes
something that was not yet acknowledged / reconciled, a RED disk alert is
raised and one small receipt line is written; the deletion itself never waits.

* ``cap_market_segments`` - PR2 never-delete: at/over the 5 GiB byte cap,
  stop local writes + 70/85 alarms; never unlink (R25/R34). Real deletes are
  R21-only. PR4 must not reintroduce deletes here.
* ``cap_lifecycle_bundles`` - PR4 never-delete: measure vs byte/age policy;
  stop-write + 70/85; never rmtree.
* ``purge_retired_transfer_bundles`` - PR4 never-delete: if retired transfer
  tree reappears → RED + stop-write; never rmtree.
* ``wipe_stale_locks`` - removes ``v3/.locks/*.lock`` only when older than the
  current process boot AND older than one hour (lock files only; out of PR4
  never-delete scope).
* ``count_market_segments`` - reset post-condition (must be 0 after a reset).
* ``seal_drain_discard_v22`` - PR4 never-delete: at size threshold refuse
  seal/discard rotate; 70/85 + stop-write; keep sealed/active until R21.

PRIME RULE: neutral. None of these files feed paper_fill, exits or tile
decisions (fills read bfx_book_10l_1s.jsonl only, which is never touched here).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

import disk_alert
import retention_stop_write
import rotation_cap

SEGMENT_CAP_BYTES = 5 * 1024 ** 3
BUNDLE_CAP_BYTES = 256 * 1024 ** 2
BUNDLE_MAX_AGE_SEC = 7 * 86400
LOCK_MIN_AGE_SEC = 3600.0
MAX_BUNDLE_REMOVALS_PER_CALL = 50
MAX_LOCK_UNLINKS_PER_CALL = 5000
V22_DRAIN_MAX_SEC = 30 * 60
V22_SEAL_MIN_BYTES = 512 * 1024 ** 2
V22_RECEIPT_MAX_BYTES = 64 * 1024
V22_RECEIPT_FILE = "v22_discard_receipts.jsonl"
_SEGMENT_RE = re.compile(r"^[0-9a-f]{64}\.json$")
MARKET_SEGMENTS_FAMILY = "market_segments"
FAMILY_LIFECYCLE_BUNDLES = "lifecycle_bundles"
FAMILY_TRANSFER_BUNDLES = "lifecycle_transfer_bundles"
FAMILY_V22 = "research_events_v22"


def _receipt(root: Path, row: Mapping[str, Any]) -> bool:
    return rotation_cap.write_cap_receipt(str(root), dict(row))


def _iter_segments(root: Path) -> Iterable[tuple[float, int, Path]]:
    base = root / "v3" / "market_segments"  # R3-NON-INPUT: R5 byte cap/reset count via stat() metadata only, never content
    if not base.is_dir():
        return
    with os.scandir(base) as buckets:
        for bucket in buckets:
            if not (bucket.is_dir(follow_symlinks=False) and len(bucket.name) == 2):
                continue
            with os.scandir(bucket.path) as entries:
                for entry in entries:
                    if not (_SEGMENT_RE.match(entry.name) and entry.name[:2] == bucket.name):
                        continue
                    try:
                        st = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    yield float(st.st_mtime), int(st.st_size), Path(entry.path)


def count_market_segments(root: str | os.PathLike) -> int:
    return sum(1 for _ in _iter_segments(Path(root)))


def cap_market_segments(root: str | os.PathLike, *, max_bytes: int = SEGMENT_CAP_BYTES,
                        now: float | None = None) -> dict[str, Any]:
    """Never-delete market-segment byte-cap check (PR2).

    At/over ``max_bytes`` (default 5 GiB) or free-disk RED: latch stop-write +
    70/85 alarms. Never unlinks any segment (R22: no max_unlinks / referenced
    delete-compat params).
    """
    root = Path(root)
    now = time.time() if now is None else now
    rows = list(_iter_segments(root))
    total = sum(size for _, size, _ in rows)
    rows.sort(key=lambda r: (r[0], r[2].name))  # oldest first (telemetry only)
    would_drop: list[str] = []
    probe_total = total
    for _, size, path in rows:
        if probe_total <= max_bytes:
            break
        would_drop.append(path.name)
        probe_total -= size
    over_cap = total > max_bytes
    decision = retention_stop_write.evaluate_and_latch(
        MARKET_SEGMENTS_FAMILY,
        used=total,
        cap=int(max_bytes),
        reason="MARKET_SEGMENT_CAP_STOP_WRITE" if over_cap else "MARKET_SEGMENT_CAP_PRESSURE",
        receipt_dir=root,
        detail={
            "would_have_dropped": would_drop[:200],
            "rule": "R25_NEVER_DELETE",
            "kind": "MARKET_SEGMENT_CAP_R5",
        },
        now=now,
    )
    if over_cap:
        decision["stop_write"] = True
        if decision.get("alarm_level") != "RED":
            decision["alarm_level"] = "RED"
            retention_stop_write.latch(
                MARKET_SEGMENTS_FAMILY, reason="MARKET_SEGMENT_CAP_STOP_WRITE",
                used=total, cap=int(max_bytes), now=now,
                detail={"would_have_dropped": would_drop[:200]},
            )
    result = {
        "deleted": 0, "freed_bytes": 0, "remaining_bytes": total,
        "over_cap": over_cap,
        "cap_bytes": int(max_bytes),
        "would_have_dropped": would_drop,
        "stop_write": bool(decision.get("stop_write")),
        "alarm_level": decision.get("alarm_level") or "GREEN",
        "used_ratio": decision.get("used_ratio"),
        "amber_at": retention_stop_write.AMBER_RATIO,
        "red_at": retention_stop_write.RED_RATIO,
    }
    if result["stop_write"] or result["alarm_level"] != "GREEN":
        _receipt(root, {"ts": round(now, 3), "kind": "MARKET_SEGMENT_CAP_R5",
                        "rule": "R25_NEVER_DELETE", **result})
    return result


def market_segments_stop_write_latched() -> bool:
    """True when local v3/market_segments writes must stop (PR2)."""
    return retention_stop_write.is_stopped(MARKET_SEGMENTS_FAMILY)


def _dir_bytes(path: Path) -> int:
    total = 0
    for directory, _, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(directory, name)).st_size
            except OSError:
                pass
    return total


def _bundle_dirs(root: Path) -> list[tuple[float, int, Path, str]]:
    out = []
    for parent_name, prefix in (("lifecycle_bundles", "lifecycle-"),):
        parent = root / "v3" / parent_name
        if not parent.is_dir():
            continue
        for bucket in parent.iterdir():
            if not bucket.is_dir() or bucket.is_symlink():
                continue
            for item in bucket.iterdir():
                if item.is_dir() and not item.is_symlink() and item.name.startswith(prefix):
                    try:
                        mtime = item.stat().st_mtime
                    except OSError:
                        continue
                    out.append((float(mtime), _dir_bytes(item), item, item.name[len(prefix):]))
    return out


BUNDLE_RECEIPT_MAX_IDS = 200


BUNDLE_FRESH_FLOOR_SEC = 600.0  # HM C7: never cap a bundle younger than 10 min (may be mid-materialisation)


def cap_lifecycle_bundles(root: str | os.PathLike, *, ack_root: str | os.PathLike | None = None,
                          max_bytes: int = BUNDLE_CAP_BYTES, max_age_sec: float = BUNDLE_MAX_AGE_SEC,
                          max_removals: int = MAX_BUNDLE_REMOVALS_PER_CALL,
                          fresh_floor_sec: float = BUNDLE_FRESH_FLOOR_SEC,
                          now: float | None = None) -> dict[str, Any]:
    """PR4 never-delete: measure lifecycle bundles; stop-write + 70/85; never rmtree.

    ``max_removals`` retained for call-site compatibility only. Ack/age counts are
    telemetry. At/over byte cap (or free-disk RED) latches stop-write for
    materialisation.
    """
    del max_removals  # never-delete
    root = Path(root)
    now = time.time() if now is None else now
    acks = Path(ack_root) if ack_root is not None else root / "v3" / "lifecycle_cleanup_acks"
    rows = sorted(_bundle_dirs(root), key=lambda r: (r[0], str(r[2])))
    total = sum(r[1] for r in rows)
    aged = [path.name for mtime, _size, path, _suffix in rows if now - mtime > max_age_sec]
    fresh = [path.name for mtime, _size, path, _suffix in rows if now - mtime < fresh_floor_sec]
    unacked = [
        path.name for _mtime, _size, path, suffix in rows
        if not (acks / f"lifecycle-{suffix}.json").exists()
    ]
    would_cap = []
    probe = total
    for mtime, size, path, _suffix in rows:
        if probe <= max_bytes or now - mtime < fresh_floor_sec:
            continue
        would_cap.append(path.name)
        probe -= size
    over_cap = total > max_bytes
    decision = retention_stop_write.evaluate_and_latch(
        FAMILY_LIFECYCLE_BUNDLES,
        used=total,
        cap=int(max_bytes),
        reason="LIFECYCLE_BUNDLE_CAP_STOP_WRITE" if over_cap else "LIFECYCLE_BUNDLE_CAP_PRESSURE",
        receipt_dir=root,
        detail={
            "would_have_cap_dropped": would_cap[:BUNDLE_RECEIPT_MAX_IDS],
            "aged_ids": aged[:BUNDLE_RECEIPT_MAX_IDS],
            "unacked_names": unacked[:20],
            "rule": "R25_NEVER_DELETE",
            "kind": "LIFECYCLE_BUNDLE_CAP_PRESSURE_R5",
        },
        now=now,
    )
    if over_cap:
        decision["stop_write"] = True
        if decision.get("alarm_level") != "RED":
            decision["alarm_level"] = "RED"
            retention_stop_write.latch(
                FAMILY_LIFECYCLE_BUNDLES, reason="LIFECYCLE_BUNDLE_CAP_STOP_WRITE",
                used=total, cap=int(max_bytes), now=now,
                detail={"would_have_cap_dropped": would_cap[:BUNDLE_RECEIPT_MAX_IDS]},
            )
    result = {
        "removed": 0, "freed_bytes": 0, "remaining_bytes": total, "used_bytes": total,
        "unacked_removed": 0, "cap_removed": 0, "age_removed": 0,
        "over_cap": over_cap, "cap_bytes": int(max_bytes),
        "max_age_sec": max_age_sec, "fresh_floor_sec": fresh_floor_sec,
        "aged_count": len(aged), "fresh_count": len(fresh), "unacked_count": len(unacked),
        "would_have_cap_dropped": would_cap,
        "stop_write": bool(decision.get("stop_write")),
        # F3 locked: UNKNOWN/missing → receipt reports RED (not ambiguous UNKNOWN / soft-GREEN).
        "alarm_level": retention_stop_write.receipt_alarm_level(decision.get("alarm_level")),
        "used_ratio": decision.get("used_ratio"),
        "amber_at": retention_stop_write.AMBER_RATIO,
        "red_at": retention_stop_write.RED_RATIO,
        "reason": decision.get("reason") or (
            "LIFECYCLE_BUNDLE_CAP_STOP_WRITE" if over_cap else "LIFECYCLE_BUNDLE_CAP_PRESSURE"
        ),
    }
    if result["stop_write"] or result["alarm_level"] != "GREEN":
        _receipt(root, {"ts": round(now, 3), "kind": "LIFECYCLE_BUNDLE_CAP_PRESSURE_R5",
                        "rule": "R25_NEVER_DELETE", **result})
    return result


def lifecycle_bundles_stop_write_latched() -> bool:
    """True when local lifecycle bundle materialisation must stop (PR4)."""
    return retention_stop_write.is_stopped(FAMILY_LIFECYCLE_BUNDLES)


RETIRED_TRANSFER_BUNDLE_DIR = "lifecycle_transfer_bundles"


def purge_retired_transfer_bundles(root: str | os.PathLike, *, now: float | None = None) -> dict[str, Any]:
    """PR4 never-delete: inspect retired transfer tree; never rmtree.

    If the tree reappears, latch stop-write + RED. Bytes stay for a later R21.
    """
    root = Path(root)
    now = time.time() if now is None else now
    tree = root / "v3" / RETIRED_TRANSFER_BUNDLE_DIR
    if not tree.exists() or tree.is_symlink():
        retention_stop_write.clear_latches(FAMILY_TRANSFER_BUNDLES)
        return {"removed": False, "present": False, "entries": 0, "freed_bytes": 0,
                "stop_write": False, "alarm_level": "GREEN",
                "reason": "RETIRED_TRANSFER_BUNDLES_ABSENT"}
    entries = sum(1 for bucket in tree.iterdir() if bucket.is_dir() for _ in bucket.iterdir())
    size = _dir_bytes(tree)
    decision = retention_stop_write.evaluate_and_latch(
        FAMILY_TRANSFER_BUNDLES,
        used=max(size, 1),
        cap=1,  # any present bytes → hard over
        reason="RETIRED_TRANSFER_BUNDLES_PRESENT",
        receipt_dir=root,
        detail={"present": True, "entries": entries, "rule": "R25_NEVER_DELETE",
                "kind": "RETIRED_TRANSFER_BUNDLES_PRESENT_R5"},
        now=now,
    )
    result = {
        "removed": False, "present": True, "entries": entries, "freed_bytes": 0,
        "used_bytes": size,
        "stop_write": True,
        "alarm_level": decision.get("alarm_level") or "RED",
        "reason": "RETIRED_TRANSFER_BUNDLES_PRESENT",
        "used_ratio": decision.get("used_ratio"),
    }
    _receipt(root, {"ts": round(now, 3), "kind": "RETIRED_TRANSFER_BUNDLES_PRESENT_R5",
                    "rule": "R25_NEVER_DELETE", **result})
    return result


def wipe_stale_locks(root: str | os.PathLike, *, boot_ts: float, now: float | None = None,
                     min_age_sec: float = LOCK_MIN_AGE_SEC,
                     max_unlinks: int = MAX_LOCK_UNLINKS_PER_CALL) -> dict[str, Any]:
    """Unlink v3/.locks/*.lock older than this boot AND older than ``min_age_sec``.

    Pin (PR4): only ``*.lock`` under ``v3/.locks``; never touches market_segments,
    lifecycle_bundles, transfer trees, v22, or book10.
    """
    now = time.time() if now is None else now
    cutoff = min(float(boot_ts), now - float(min_age_sec))
    lock_dir = Path(root) / "v3" / ".locks"
    deleted, kept_recent = 0, 0
    if lock_dir.is_dir():
        with os.scandir(lock_dir) as entries:
            for entry in entries:
                if deleted >= max_unlinks:
                    break
                if not entry.name.endswith(".lock") or not entry.is_file(follow_symlinks=False):
                    continue
                try:
                    mtime = entry.stat(follow_symlinks=False).st_mtime
                except OSError:
                    continue
                if mtime >= cutoff:
                    kept_recent += 1
                    continue
                try:
                    os.unlink(entry.path)
                    deleted += 1
                except FileNotFoundError:
                    pass
                except OSError:
                    continue
    return {"deleted": deleted, "kept_recent": kept_recent, "cutoff": cutoff,
            "min_age_sec": float(min_age_sec), "lock_dir": str(lock_dir)}


def _write_v22_receipt(root: Path, row: dict[str, Any]) -> bool:
    """One <=64 KB receipt line; event-id lists are truncated to fit."""
    try:
        ids = list(row.get("pending_event_ids") or [])
        while True:
            row["pending_event_ids"] = ids
            line = json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
            if len(line.encode()) <= V22_RECEIPT_MAX_BYTES or not ids:
                break
            ids = ids[: max(0, len(ids) // 2)]
            row["pending_event_ids_truncated"] = True
        path = root / V22_RECEIPT_FILE
        if path.exists() and path.stat().st_size >= V22_RECEIPT_MAX_BYTES:
            os.replace(path, str(path) + ".1")
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line)
        return True
    except Exception:
        return False


def seal_drain_discard_v22(root: str | os.PathLike, *, drain: Callable[[], Iterable[str]] | None = None,
                           max_drain_sec: float | None = None, min_bytes: int = V22_SEAL_MIN_BYTES,
                           poll_sec: float | None = None, sleep: Callable[[float], None] | None = None,
                           clock: Callable[[], float] = time.time) -> dict[str, Any]:
    """PR4 never-delete: at threshold latch stop-write + 70/85; never rotate/unlink.

    ``drain`` / ``max_drain_sec`` / ``poll_sec`` / ``sleep`` accepted for call-site
    compatibility only and ignored. Active generation kept until a later R21.
    """
    del drain, max_drain_sec, poll_sec, sleep
    import collector_v22

    root = Path(root)
    now = float(clock())
    active = root / collector_v22.RESEARCH_EVENTS_FILE
    active_bytes = active.stat().st_size if active.is_file() else 0
    cap = max(int(min_bytes), 1)
    if active_bytes < int(min_bytes):
        decision = retention_stop_write.evaluate_and_latch(
            FAMILY_V22, used=active_bytes, cap=cap,
            reason="V22_BELOW_SEAL_THRESHOLD", receipt_dir=None,
            detail={"active_bytes": active_bytes}, now=now,
        )
        return {"status": "BELOW_THRESHOLD", "active_bytes": active_bytes,
                "stop_write": False, "alarm_level": decision.get("alarm_level") or "GREEN",
                "used_ratio": decision.get("used_ratio"), "cap_bytes": cap}
    decision = retention_stop_write.evaluate_and_latch(
        FAMILY_V22, used=active_bytes, cap=cap,
        reason="V22_SEAL_DISCARD_STOP_WRITE", receipt_dir=root,
        detail={"active_bytes": active_bytes, "rule": "R25_NEVER_DELETE",
                "kind": "V22_SEAL_DISCARD_STOP_WRITE_R5"},
        now=now,
    )
    row = {
        "ts": round(now, 3), "kind": "V22_SEAL_DISCARD_STOP_WRITE_R5",
        "status": "STOP_WRITE_RETAINED", "active_bytes": active_bytes,
        "cap_bytes": cap, "used_ratio": decision.get("used_ratio"),
        "alarm_level": decision.get("alarm_level") or "RED",
        "stop_write": True, "reason": "V22_SEAL_DISCARD_STOP_WRITE",
        "pending_event_ids": [], "pending_count": 0,
        "rule": "R25_NEVER_DELETE",
    }
    receipt_ok = _write_v22_receipt(root, dict(row))
    return {**{k: v for k, v in row.items() if k != "pending_event_ids"},
            "receipt_written": receipt_ok}


def v22_stop_write_latched() -> bool:
    """True when research_events_v22 appends must stop (PR4)."""
    return retention_stop_write.is_stopped(FAMILY_V22)



def file_sha256(path: str | os.PathLike) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
