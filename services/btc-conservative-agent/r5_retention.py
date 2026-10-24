"""R5 retention for the v3 evidence store (DISK-CAP, E4b/E4c, v3/.locks).

R5: caps and discards are never custody- or ack-gated. When a cap removes
something that was not yet acknowledged / reconciled, a RED disk alert is
raised and one small receipt line is written; the deletion itself never waits.

* ``cap_market_segments`` - byte cap on ``v3/market_segments`` (oldest first).
  Segments referenced by an open lifecycle are deleted last, but are still
  bounded by the same cap (no uncapped keep-set).
* ``cap_lifecycle_bundles`` - byte/age cap on ``v3/lifecycle_transfer_bundles``
  and ``v3/lifecycle_bundles`` regardless of the laptop cleanup ack.
* ``wipe_stale_locks`` - removes ``v3/.locks/*.lock`` only when older than the
  current process boot AND older than one hour (a held lock is never removed).
* ``count_market_segments`` - reset post-condition (must be 0 after a reset).
* ``seal_drain_discard_v22`` - seal the active research_events_v22 generation,
  let the Fly-local v3 reconcile drain for a bounded time, then delete the
  sealed generation unconditionally; ``index.sqlite3`` is kept.

PRIME RULE: neutral. None of these files feed paper_fill, exits or tile
decisions (fills read bfx_book_10l_1s.jsonl only, which is never touched here).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

import disk_alert
import rotation_cap

SEGMENT_CAP_BYTES = 5 * 1024 ** 3
BUNDLE_CAP_BYTES = 256 * 1024 ** 2
BUNDLE_MAX_AGE_SEC = 7 * 86400
LOCK_MIN_AGE_SEC = 3600.0
MAX_SEGMENT_UNLINKS_PER_CALL = 500
MAX_BUNDLE_REMOVALS_PER_CALL = 50
MAX_LOCK_UNLINKS_PER_CALL = 5000
V22_DRAIN_MAX_SEC = 30 * 60
V22_SEAL_MIN_BYTES = 512 * 1024 ** 2
V22_RECEIPT_MAX_BYTES = 64 * 1024
V22_RECEIPT_FILE = "v22_discard_receipts.jsonl"
_SEGMENT_RE = re.compile(r"^[0-9a-f]{64}\.json$")


def _receipt(root: Path, row: Mapping[str, Any]) -> bool:
    return rotation_cap.write_cap_receipt(str(root), dict(row))


def _iter_segments(root: Path) -> Iterable[tuple[float, int, Path]]:
    base = root / "v3" / "market_segments"  # R3-NON-INPUT: R5 byte cap / reset count
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
                        referenced: Iterable[str] = (), max_unlinks: int = MAX_SEGMENT_UNLINKS_PER_CALL,
                        now: float | None = None) -> dict[str, Any]:
    """Delete the oldest segments until the store is under ``max_bytes``.

    ``referenced`` (sha names) are ordered last but are still deleted when the
    cap requires it; that raises RED and writes a receipt (R5)."""
    root = Path(root)
    now = time.time() if now is None else now
    refs = {str(r).removesuffix(".json") for r in referenced}
    rows = list(_iter_segments(root))
    total = sum(size for _, size, _ in rows)
    rows.sort(key=lambda r: (r[2].stem in refs, r[0], r[2].name))
    deleted, freed, referenced_deleted = 0, 0, 0
    for _, size, path in rows:
        if total <= max_bytes or deleted >= max_unlinks:
            break
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            continue
        deleted += 1
        freed += size
        total -= size
        if path.stem in refs:
            referenced_deleted += 1
    result = {"deleted": deleted, "freed_bytes": freed, "remaining_bytes": total,
              "over_cap": total > max_bytes, "referenced_deleted": referenced_deleted,
              "cap_bytes": int(max_bytes)}
    if deleted:
        _receipt(root, {"ts": round(now, 3), "kind": "MARKET_SEGMENT_CAP_R5", **result})
    if referenced_deleted:
        disk_alert.raise_red("SEGMENT_CAP_DELETED_REFERENCED", {"count": referenced_deleted}, now=now)
    return result


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
    for parent_name, prefix in (("lifecycle_transfer_bundles", "transfer-"), ("lifecycle_bundles", "lifecycle-")):
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


def _transfer_pointer_root(root: Path) -> Path:
    return root / "v3" / "lifecycle_transfer_bundles" / "index"


def _drop_transfer_pointers(root: Path, removed_names: set[str]) -> int:
    """HM C8: remove the transfer pointer(s) of removed transfer bundles.

    lifecycle_bundles fails closed on a pointer whose bundle is gone, so the
    pointer goes with the bundle. A pointer that already dangles (bundle gone
    and no ``.staging`` copy to recover from) is swept too; a pointer whose
    staged bundle still exists is the crash-recovery path and is left alone.
    """
    pointer_root = _transfer_pointer_root(root)
    bundle_root = root / "v3" / "lifecycle_transfer_bundles"
    if not pointer_root.is_dir():
        return 0
    dropped = 0
    with os.scandir(pointer_root) as entries:
        for entry in entries:
            if not (entry.name.endswith(".json") and entry.is_file(follow_symlinks=False)):
                continue
            try:
                bundle_id = str(json.loads(Path(entry.path).read_text("utf-8")).get("bundle_id") or "")
            except (OSError, ValueError, AttributeError):
                continue
            if not bundle_id.startswith("transfer-") or len(bundle_id) != len("transfer-") + 64:
                continue
            target = bundle_root / bundle_id[-64:-62] / bundle_id
            # HM C8 (re-review): staging FIRST, then target. lifecycle_bundles writes the
            # pointer, then os.replace(staging -> target); checking target first could
            # see neither if the replace lands between the two checks and drop the
            # pointer of a just-published bundle.
            dangling = (bundle_id not in removed_names
                        and not (bundle_root / ".staging" / bundle_id).exists()
                        and not target.exists())
            if bundle_id in removed_names or dangling:
                try:
                    os.unlink(entry.path)
                    dropped += 1
                except FileNotFoundError:
                    pass
                except OSError:
                    continue
    return dropped


def cap_lifecycle_bundles(root: str | os.PathLike, *, ack_root: str | os.PathLike | None = None,
                          max_bytes: int = BUNDLE_CAP_BYTES, max_age_sec: float = BUNDLE_MAX_AGE_SEC,
                          max_removals: int = MAX_BUNDLE_REMOVALS_PER_CALL,
                          keep: Iterable[str] = (),
                          fresh_floor_sec: float = BUNDLE_FRESH_FLOOR_SEC,
                          now: float | None = None) -> dict[str, Any]:
    """Cap transfer/completion bundles; never gated on the laptop ack (R5, RK 18:10).

    * Cap pass FIRST (HM C8), with its own ``max_removals`` budget: at the cap,
      oldest first regardless of ship state or ``keep`` until the store is
      under the cap. Bundles younger than ``fresh_floor_sec`` (10 min) are
      never capped (HM C7: they may still be materialising). Every dropped id
      is logged in the capped receipt and RED is raised.
    * Age pass second, with its own budget: below the cap, an aged bundle is
      removed unless it is still unshipped (no laptop cleanup ack) AND an open
      lifecycle or a pre-registered test needs it (its id is in ``keep``, see
      r5_keep.open_lifecycles). Fresh bundles stay.
    * A removed transfer bundle takes its transfer pointer with it (HM C8).
    """
    root = Path(root)
    now = time.time() if now is None else now
    acks = Path(ack_root) if ack_root is not None else root / "v3" / "lifecycle_cleanup_acks"
    keep_ids = {str(k).removeprefix("transfer-").removeprefix("lifecycle-") for k in keep}
    rows = sorted(_bundle_dirs(root), key=lambda r: (r[0], str(r[2])))
    total = sum(r[1] for r in rows)
    freed, unacked, kept = 0, [], 0
    cap_dropped: list[str] = []
    age_dropped: list[str] = []
    removed_transfer: set[str] = set()

    def _remove(path: Path) -> bool:
        shutil.rmtree(path, ignore_errors=True)
        gone = not path.exists()
        if gone and path.name.startswith("transfer-"):
            removed_transfer.add(path.name)
        return gone

    def _acked(suffix: str) -> bool:
        return (acks / f"lifecycle-{suffix}.json").exists()

    survivors = []
    # Pass 1 (cap): oldest first regardless of ship state or keep; own budget.
    for mtime, size, path, suffix in rows:
        if (total <= max_bytes or len(cap_dropped) >= max_removals
                or now - mtime < fresh_floor_sec or not _remove(path)):
            survivors.append((mtime, size, path, suffix))
            continue
        freed += size
        total -= size
        cap_dropped.append(path.name)
        if not _acked(suffix):
            unacked.append(path.name)
    # Pass 2 (age): aged bundles except needed unshipped ones; own budget.
    for mtime, size, path, suffix in survivors:
        if now - mtime <= max_age_sec:
            continue
        acked = _acked(suffix)
        if not acked and suffix in keep_ids:
            kept += 1
            continue
        if len(age_dropped) >= max_removals:
            continue
        if not _remove(path):
            continue
        freed += size
        total -= size
        age_dropped.append(path.name)
        if not acked:
            unacked.append(path.name)
    removed = len(cap_dropped) + len(age_dropped)
    pointers = 0
    if removed_transfer:
        try:
            pointers = _drop_transfer_pointers(root, removed_transfer)
        except OSError:
            pointers = 0
    result = {"removed": removed, "freed_bytes": freed, "remaining_bytes": total,
              "unacked_removed": len(unacked), "kept_needed_unshipped": kept,
              "cap_removed": len(cap_dropped), "age_removed": len(age_dropped),
              "over_cap": total > max_bytes, "transfer_pointers_removed": pointers,
              "cap_bytes": int(max_bytes), "max_age_sec": max_age_sec, "fresh_floor_sec": fresh_floor_sec}
    if removed:
        _receipt(root, {"ts": round(now, 3), "kind": "LIFECYCLE_BUNDLE_CAP_R5", **result,
                        "cap_dropped_ids": cap_dropped[:BUNDLE_RECEIPT_MAX_IDS],
                        "age_dropped_ids": age_dropped[:BUNDLE_RECEIPT_MAX_IDS],
                        "unacked_names": unacked[:20]})
    if cap_dropped:
        disk_alert.raise_red("BUNDLE_CAP_REACHED", {"count": len(cap_dropped)}, now=now)
    if unacked:
        disk_alert.raise_red("BUNDLE_CAP_REMOVED_UNACKED", {"count": len(unacked)}, now=now)
    return result


def wipe_stale_locks(root: str | os.PathLike, *, boot_ts: float, now: float | None = None,
                     min_age_sec: float = LOCK_MIN_AGE_SEC,
                     max_unlinks: int = MAX_LOCK_UNLINKS_PER_CALL) -> dict[str, Any]:
    """Unlink v3/.locks/*.lock older than this boot AND older than ``min_age_sec``."""
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
    return {"deleted": deleted, "kept_recent": kept_recent, "cutoff": cutoff}


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


def seal_drain_discard_v22(root: str | os.PathLike, *, drain: Callable[[], Iterable[str]],
                           max_drain_sec: float = V22_DRAIN_MAX_SEC, min_bytes: int = V22_SEAL_MIN_BYTES,
                           poll_sec: float = 30.0, sleep: Callable[[float], None] = time.sleep,
                           clock: Callable[[], float] = time.time) -> dict[str, Any]:
    """Seal the active v22 head, drain for at most ``max_drain_sec``, delete it unconditionally.

    ``drain()`` runs one Fly-local v3 reconcile pass and returns the event ids
    still pending. Pending rows never block the delete (R5): they are listed
    in a <=64 KB receipt and RED is raised. ``index.sqlite3`` is kept; the
    orphan seal receipt is quarantined so collector reads stay valid.
    """
    import collector_v22

    root = Path(root)
    active = root / collector_v22.RESEARCH_EVENTS_FILE
    if not active.is_file() or active.stat().st_size < int(min_bytes):
        return {"status": "BELOW_THRESHOLD",
                "active_bytes": active.stat().st_size if active.is_file() else 0}
    seal = collector_v22.rotate_research_events(data_dir=str(root))
    sealed_rel = str(seal.get("relative_path") or "")
    sealed = root / sealed_rel
    if not sealed_rel or not sealed.is_file():
        return {"status": "SEAL_MISSING", "seal": seal}
    started = clock()
    pending: list[str] = []
    while True:
        try:
            pending = sorted({str(x) for x in (drain() or [])})
        except Exception as exc:  # a broken drain never blocks the discard (R5)
            pending = [f"DRAIN_ERROR:{type(exc).__name__}"]
        if not pending or clock() - started >= max_drain_sec:
            break
        sleep(poll_sec)
    size = sealed.stat().st_size
    sha = str(seal.get("sha256") or "")
    sealed.unlink()
    quarantine = None
    try:
        plan = collector_v22.plan_orphan_event_seals(str(root))
        quarantine = collector_v22.quarantine_orphan_event_seals(
            str(root), expected_plan_sha256=plan["plan_sha256"]).get("status")
    except Exception as exc:
        quarantine = f"FAILED:{type(exc).__name__}"
    now = clock()
    row = {"ts": round(now, 3), "kind": "V22_SEALED_GENERATION_DISCARDED_R5",
           "generation": seal.get("generation"), "relative_path": sealed_rel, "bytes": size,
           "sha256": sha, "row_count": seal.get("row_count"), "pending_count": len(pending),
           "pending_event_ids": pending, "drain_sec": round(now - started, 1),
           "seal_quarantine": quarantine}
    receipt_ok = _write_v22_receipt(root, dict(row))
    if pending:
        disk_alert.raise_red("V22_DISCARDED_WITH_PENDING", {"count": len(pending), "sha256": sha}, now=now)
    return {"status": "DISCARDED", **{k: v for k, v in row.items() if k != "pending_event_ids"},
            "receipt_written": receipt_ok}


def file_sha256(path: str | os.PathLike) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
