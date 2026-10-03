"""Single-physical-copy helpers for the laptop mirror layers (NTFS hardlinks).

The segment shadow tree, the promotion view and the canonical store hold the
same bytes for every settled file. Settled files are hardlinked across layers
instead of copied; hot append-only streams stay private per layer because each
layer is a point-in-time snapshot that readers consume to EOF.

Rules every writer of a mirror layer must follow:

* never write a file in place while it has other links: call
  :func:`ensure_private` first (copy-on-write), or write a temp file and
  ``os.replace`` it over the name;
* only link files whose bytes were verified (sha256) against the source;
* if a hardlink cannot be created (other volume, FAT, link limit) fall back to
  a verified copy and record a ``LINK_FALLBACK_COPY`` alarm so the duplicate
  copy is visible to the self-aware duplicate-copy check.

Laptop-only module; the Fly runtime never imports it. Never prints secrets.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
import time
from pathlib import Path
from typing import Iterable, Optional

_CHUNK = 4 * 1024 * 1024
ALARM_SCHEMA = "storage_link_alarm_v1"
DEFAULT_ALARM_LOG = Path(r"C:\DoxxedCrypto\bot-data-retention\storage-link-alarms.jsonl")
# Files rewritten in place by their own engine (SQLite pages, WAL, shared
# memory) must never share an inode with another layer.
NEVER_LINK_SUFFIXES = (".db", ".sqlite", ".sqlite3", "-wal", "-shm", "-journal", ".tmp", ".lock")
# A file untouched this long is settled: sealed rotations, receipts, finished
# per-day partitions. Hot streams are appended every few seconds.
DEFAULT_SETTLE_SEC = 6 * 3600
LINK_ENV = "DOXXED_MIRROR_HARDLINKS"


def links_enabled() -> bool:
    return os.environ.get(LINK_ENV, "1").strip().lower() not in ("0", "false", "off", "no")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def identity(path: Path) -> Optional[tuple[int, int]]:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_dev, st.st_ino) if st.st_ino else None


def same_file(a: Path, b: Path) -> bool:
    ia, ib = identity(a), identity(b)
    return ia is not None and ia == ib


def link_count(path: Path) -> int:
    try:
        return int(os.stat(path).st_nlink)
    except OSError:
        return 0


def linkable(relpath: str) -> bool:
    name = relpath.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return not name.startswith(".") and not name.endswith(NEVER_LINK_SUFFIXES)


def settled(st: os.stat_result, now: float, settle_sec: float = DEFAULT_SETTLE_SEC) -> bool:
    return now - st.st_mtime >= settle_sec


def record_alarm(code: str, alarm_log: Optional[Path] = None, **detail) -> None:
    path = Path(alarm_log) if alarm_log else DEFAULT_ALARM_LOG
    row = {"schema": ALARM_SCHEMA, "code": code,
           "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **detail}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")
    except OSError:
        pass


def _make_writable(path: Path) -> None:
    try:
        mode = os.stat(path).st_mode
        if not mode & stat.S_IWRITE:
            os.chmod(path, mode | stat.S_IWRITE)
    except OSError:
        pass


def ensure_private(path: Path) -> bool:
    """Copy-on-write: give ``path`` its own inode before an in-place write.

    Returns True when a shared file was split. The private copy is verified
    byte-for-byte (size + sha256) before it replaces the shared name; other
    links keep the original bytes untouched.
    """
    path = Path(path)
    if link_count(path) <= 1:
        return False
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".cow", dir=path.parent)
    os.close(fd)
    candidate = Path(name)
    try:
        shutil.copy2(path, candidate)
        _make_writable(candidate)
        if candidate.stat().st_size != path.stat().st_size or sha256_file(candidate) != sha256_file(path):
            raise OSError(f"copy-on-write verification failed: {path}")
        os.replace(candidate, path)
    finally:
        candidate.unlink(missing_ok=True)
    return True


def link_or_copy(source: Path, target: Path, *, expected_sha256: Optional[str] = None,
                 alarm_log: Optional[Path] = None, allow_link: bool = True) -> str:
    """Make ``target`` hold ``source``'s bytes, preferring a hardlink.

    Returns ``ALREADY_LINKED``, ``LINKED`` or ``COPIED`` (``COPIED_FALLBACK``
    when a link was wanted but failed). The target is swapped atomically via
    ``os.replace`` and is never written in place.
    """
    source, target = Path(source), Path(target)
    if allow_link and same_file(source, target):
        return "ALREADY_LINKED"
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".link", dir=target.parent)
    os.close(fd)
    candidate = Path(name)
    outcome = "COPIED"
    try:
        candidate.unlink()
        linked = False
        if allow_link and links_enabled():
            try:
                os.link(source, candidate)
                linked = same_file(source, candidate)
            except OSError as exc:
                record_alarm("LINK_FALLBACK_COPY", alarm_log, source=str(source), target=str(target),
                             error=f"{type(exc).__name__}: {exc}")
                outcome = "COPIED_FALLBACK"
        if linked:
            outcome = "LINKED"
        else:
            candidate.unlink(missing_ok=True)
            shutil.copy2(source, candidate)
            _make_writable(candidate)
            if expected_sha256 and sha256_file(candidate) != expected_sha256:
                raise OSError(f"copied checksum mismatch: {target}")
        if candidate.stat().st_size != source.stat().st_size:
            raise OSError(f"size mismatch after {outcome}: {target}")
        try:
            os.replace(candidate, target)
        except PermissionError:
            # A reader holds the target open (Windows denies the swap). An
            # identical target is left as is and relinked on a later pass.
            if (target.is_file() and target.stat().st_size == source.stat().st_size
                    and sha256_file(target) == (expected_sha256 or sha256_file(source))):
                return "DEFERRED_IN_USE"
            raise
    finally:
        candidate.unlink(missing_ok=True)
    return outcome


def replace_with_link(keeper: Path, duplicate: Path, *, keeper_sha256: Optional[str] = None,
                      alarm_log: Optional[Path] = None) -> str:
    """Collapse an existing duplicate onto ``keeper`` after verifying identical bytes.

    Both files are hashed (the keeper hash may be supplied); nothing changes
    unless sizes and sha256 match.
    """
    keeper, duplicate = Path(keeper), Path(duplicate)
    if same_file(keeper, duplicate):
        return "ALREADY_LINKED"
    if keeper.stat().st_size != duplicate.stat().st_size:
        return "SKIP_SIZE_DIFFERS"
    digest = keeper_sha256 or sha256_file(keeper)
    if sha256_file(duplicate) != digest:
        return "SKIP_CONTENT_DIFFERS"
    outcome = link_or_copy(keeper, duplicate, expected_sha256=digest, alarm_log=alarm_log)
    if outcome == "LINKED" and sha256_file(duplicate) != digest:
        raise OSError(f"post-link verification failed: {duplicate}")
    return outcome


def unique_bytes(roots: Iterable[Path]) -> dict:
    """Logical vs physical bytes across ``roots`` (hardlinks counted once)."""
    seen: set[tuple[int, int]] = set()
    logical = physical = files = shared = 0
    for root in roots:
        root = Path(root)
        if not root.is_dir():
            continue
        for directory, _dirs, names in os.walk(root):
            for name in names:
                try:
                    st = os.stat(os.path.join(directory, name))
                except OSError:
                    continue
                files += 1
                logical += st.st_size
                key = (st.st_dev, st.st_ino)
                if st.st_ino and key in seen:
                    shared += 1
                    continue
                if st.st_ino:
                    seen.add(key)
                physical += st.st_size
    return {"files": files, "logical_bytes": logical, "physical_bytes": physical,
            "shared_links": shared, "saved_bytes": logical - physical}


# ------------------------------------------------------------ WOF compaction
# Settled text files (sealed rotations, digest grids, receipts) compress 10-25x
# with Windows Overlay Filter LZX. Reads are transparent and the bytes are
# unchanged, so digest references and sha256 receipts stay valid. A later
# in-place write makes Windows decompress the file first; nothing breaks.
NEVER_COMPRESS_SUFFIXES = NEVER_LINK_SUFFIXES + (".gz", ".zst", ".zip", ".parquet", ".xz", ".bz2", ".7z", ".png")
COMPRESS_MIN_BYTES = 1024 * 1024
# The emergency evidence WAL proves its reserve is physically allocated
# (EMERGENCY_WAL_RESERVE_NOT_PHYSICALLY_ALLOCATED); a compressed reserve makes
# every V3 evidence store refuse to open.
NEVER_COMPRESS_DIRS = frozenset({"emergency_evidence_wal_v2"})
COMPRESS_ALGORITHM = "lzx"
COMPRESS_ENV = "DOXXED_MIRROR_WOF"


def compression_enabled() -> bool:
    return os.name == "nt" and os.environ.get(COMPRESS_ENV, "1").strip().lower() not in ("0", "false", "off", "no")


def allocated_bytes(path) -> Optional[int]:
    """On-disk bytes after NTFS/WOF compression (``None`` off Windows)."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes
    func = ctypes.windll.kernel32.GetCompressedFileSizeW
    func.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    func.restype = wintypes.DWORD
    high = wintypes.DWORD(0)
    low = func(str(path), ctypes.byref(high))
    if low == 0xFFFFFFFF and ctypes.GetLastError():
        return None
    return (high.value << 32) + low


def compressible(relpath: str) -> bool:
    name = relpath.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return not name.startswith(".") and not name.endswith(NEVER_COMPRESS_SUFFIXES)


def _compact(path: str, runner=None) -> bool:
    import subprocess
    run = runner or subprocess.run
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = run(["compact.exe", "/c", f"/exe:{COMPRESS_ALGORITHM}", "/q", path],
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
    return getattr(proc, "returncode", 1) == 0


def compress_settled(roots: Iterable[Path], *, now: Optional[float] = None,
                     settle_sec: float = DEFAULT_SETTLE_SEC, min_bytes: int = COMPRESS_MIN_BYTES,
                     max_bytes: int = 4 * 1024 ** 3, dry_run: bool = False, runner=None,
                     verify: bool = True) -> dict:
    """WOF-compress settled, uncompressed files under ``roots``; bytes verified by sha256.

    Reparse points (junctions) are never followed and every inode is handled
    once, so hardlinked layer copies are compressed a single time.
    """
    now = time.time() if now is None else now
    out = {"enabled": compression_enabled(), "dry_run": dry_run, "candidates": 0, "compressed": 0,
           "failed": 0, "verify_failed": 0, "input_bytes": 0, "saved_bytes": 0, "already_compressed": 0,
           "budget_exhausted": False}
    if not out["enabled"]:
        return out
    seen: set = set()
    budget = int(max_bytes)
    for root in roots:
        root = Path(root)
        if not root.is_dir():
            continue
        for directory, dirs, names in os.walk(root):
            dirs[:] = [d for d in dirs if not d.startswith(".")
                       and d.lower() not in NEVER_COMPRESS_DIRS
                       and not os.path.islink(os.path.join(directory, d))
                       and not _is_reparse(os.path.join(directory, d))]
            for leaf in names:
                full = os.path.join(directory, leaf)
                if not compressible(leaf):
                    continue
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                key = (st.st_dev, st.st_ino)
                if st.st_size < min_bytes or not settled(st, now, settle_sec) or (st.st_ino and key in seen):
                    continue
                seen.add(key)
                alloc = allocated_bytes(full)
                if alloc is not None and alloc < st.st_size * 0.9:
                    out["already_compressed"] += 1
                    continue
                out["candidates"] += 1
                if budget <= 0:
                    out["budget_exhausted"] = True
                    continue
                if dry_run:
                    budget -= st.st_size
                    out["input_bytes"] += st.st_size
                    continue
                before = sha256_file(Path(full)) if verify else None
                if not _compact(full, runner):
                    out["failed"] += 1
                    continue
                budget -= st.st_size
                if verify and sha256_file(Path(full)) != before:
                    out["verify_failed"] += 1
                    record_alarm("WOF_VERIFY_FAILED", path=full)
                    continue
                after = allocated_bytes(full)
                out["compressed"] += 1
                out["input_bytes"] += st.st_size
                if after is not None:
                    out["saved_bytes"] += max(0, st.st_size - after)
    return out


def _is_reparse(path: str) -> bool:
    try:
        attrs = getattr(os.lstat(path), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attrs & 0x400)
