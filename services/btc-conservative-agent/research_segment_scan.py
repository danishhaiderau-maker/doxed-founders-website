"""Incremental file-universe scanning for the research segment shipper.

The shipper previously walked and stat()'d the *entire* runtime tree every
cycle, so its per-cycle CPU grew with the total number of tracked files. This
module replaces that full walk with a delta-aware scan:

* On Linux (the Fly production runtime) it seeds recursive ``inotify`` watches
  while it performs the *first* full walk, then each subsequent cycle reads the
  pending events and re-stats only the changed paths: ``O(changed)`` instead of
  ``O(total)``.
* On every other platform — and on any watcher failure, queue overflow, or an
  explicit env kill-switch — it falls back to the full walk.

Correctness contract: this is a pure optimization. The scan result is a
``dict[relpath, (Path, stat)]`` identical in every case to what a full walk
would produce, because:

1. Watches are added *before* a directory's contents are scanned, closing the
   seed race (a file created after the watch is reported by an event; a file
   created before is found by the scan).
2. Any inotify queue overflow (``IN_Q_OVERFLOW``) or read error forces a full
   re-walk, so a missed event can never yield a stale universe — only a slower
   cycle.
3. A changed path is re-applied against the shipper's own validation rules
   (excluded dirs/names/globs, symlink skipping, relpath validation), so the
   incremental result cannot diverge from the full walk.

Only stdlib + ``ctypes`` (Linux inotify) are used; this module never imports
``bot`` or Flask.
"""

from __future__ import annotations

import ctypes
import os
import struct
import sys
from pathlib import Path

IS_LINUX = sys.platform.startswith("linux")

# linux/inotify.h
IN_NONBLOCK = 0o4000
IN_CLOEXEC = 0o2000000

IN_MODIFY = 0x00000002
IN_ATTRIB = 0x00000004
IN_CLOSE_WRITE = 0x00000008
IN_MOVED_FROM = 0x00000040
IN_MOVED_TO = 0x00000080
IN_CREATE = 0x00000100
IN_DELETE = 0x00000200
IN_DELETE_SELF = 0x00000400
IN_MOVE_SELF = 0x00000800
IN_Q_OVERFLOW = 0x00004000
IN_IGNORED = 0x00008000
IN_ISDIR = 0x40000000

# Events that mean "the contents of this exact path changed".
_FILE_CHANGE = (IN_MODIFY | IN_CLOSE_WRITE | IN_ATTRIB | IN_CREATE
                | IN_DELETE | IN_MOVED_FROM | IN_MOVED_TO)
_DIR_APPEAR = IN_CREATE | IN_MOVED_TO
_DIR_VANISH = IN_DELETE | IN_MOVED_FROM

WATCH_MASK = (IN_CREATE | IN_DELETE | IN_MOVED_FROM | IN_MOVED_TO
              | IN_MODIFY | IN_ATTRIB | IN_CLOSE_WRITE
              | IN_DELETE_SELF | IN_MOVE_SELF)

# Sentinel returned by InotifyWatcher.poll() when the queue overflowed: the
# caller must re-walk the whole tree (never trust a partial delta).
OVERFLOW = "overflow"


def watcher_available() -> bool:
    """True only where a kernel watcher can actually run (Linux)."""
    return IS_LINUX


def _libc():
    libc = ctypes.CDLL(None, use_errno=True)
    libc.inotify_init1.argtypes = [ctypes.c_int]
    libc.inotify_init1.restype = ctypes.c_int
    libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
    libc.inotify_add_watch.restype = ctypes.c_int
    libc.close.argtypes = [ctypes.c_int]
    libc.close.restype = ctypes.c_int
    return libc


def parse_inotify_buffer(data: bytes, dir_of: dict) -> tuple[set, list, list, bool]:
    """Parse one read()'d inotify buffer into a change set.

    Returns ``(changed, dirs_appeared, dirs_vanished, overflow)``:

    * ``changed``: set of relpaths (files and directories) that changed;
    * ``dirs_appeared``: list of ``(rel_dir, name)`` directories that were
      created/moved-in and must have watches added recursively;
    * ``dirs_vanished``: list of ``rel_dir`` directories deleted/moved-out that
      must have their watches dropped;
    * ``overflow``: True when the kernel dropped events (IN_Q_OVERFLOW).

    Pure and platform-independent so it can be unit-tested with synthetic bytes.
    """
    changed: set = set()
    appeared: list = []
    vanished: list = []
    overflow = False
    offset = 0
    while offset < len(data):
        wd, mask, _cookie, length = struct.unpack_from("iIII", data, offset)
        raw_name = data[offset + 16: offset + 16 + length]
        name = raw_name.split(b"\0", 1)[0].decode("utf-8", "replace")
        offset += 16 + length
        if wd == -1:  # IN_Q_OVERFLOW
            overflow = True
            continue
        rel_dir = dir_of.get(wd)
        if rel_dir is None:
            continue
        relpath = f"{rel_dir}/{name}" if rel_dir else name
        is_dir = bool(mask & IN_ISDIR)
        if mask & _DIR_APPEAR:
            if is_dir:
                appeared.append((rel_dir, name))
            changed.add(relpath)
        elif mask & _DIR_VANISH:
            if is_dir:
                vanished.append(rel_dir)
            changed.add(relpath)
        elif mask & _FILE_CHANGE:
            changed.add(relpath)
        elif mask & (IN_DELETE_SELF | IN_MOVE_SELF):
            changed.add(rel_dir)
        # IN_IGNORED: kernel removed the watch (dir gone); nothing to re-stat.
    return changed, appeared, vanished, overflow


class InotifyWatcher:
    """Recursive inotify watcher over one or more roots (Linux only).

    Relpaths are relative to each root's ``prefix`` (``""`` for the runtime
    root, ``"research"`` etc. for the linked research directories), matching the
    shipper's universe keys exactly.
    """

    def __init__(self, *, excluded_dirs: frozenset | set = frozenset()):
        if not IS_LINUX:
            raise OSError("inotify is only available on Linux")
        self._libc = _libc()
        self.fd = -1
        self.excluded_dirs = {name.lower() for name in excluded_dirs}
        self.watch_of: dict[str, int] = {}   # rel_dir -> wd
        self.dir_of: dict[int, str] = {}     # wd -> rel_dir
        self._roots: list[tuple[str, str]] = []  # (abs_dir, prefix)
        self.fd = self._libc.inotify_init1(IN_NONBLOCK | IN_CLOEXEC)
        if self.fd < 0:
            raise OSError(ctypes.get_errno(), "inotify_init1 failed")

    def _resolve(self, relpath: str) -> str | None:
        for abs_dir, prefix in self._roots:
            if relpath == prefix or relpath.startswith(prefix + "/"):
                rel = relpath[len(prefix):].lstrip("/")
                return os.path.join(abs_dir, rel)
        return None

    def set_roots(self, roots: list[tuple[str, str]]) -> None:
        """Record the root -> prefix mapping used to resolve relpaths."""
        self._roots = list(roots)

    def add_dir_watch(self, abs_dir: str, rel_dir: str) -> None:
        """Watch one directory (no recursion; the walker drives recursion)."""
        try:
            wd = self._libc.inotify_add_watch(self.fd, os.fsencode(abs_dir), WATCH_MASK)
        except OSError:
            return
        if wd < 0:
            return
        self.watch_of[rel_dir] = wd
        self.dir_of[wd] = rel_dir

    def _watch_subtree(self, abs_dir: str, rel_dir: str) -> None:
        """Add a watch and recurse into its subdirectories (for a new dir)."""
        self.add_dir_watch(abs_dir, rel_dir)
        try:
            with os.scandir(abs_dir) as iterator:
                entries = list(iterator)
        except OSError:
            return
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False) and entry.name.lower() not in self.excluded_dirs:
                    child_rel = f"{rel_dir}/{entry.name}" if rel_dir else entry.name
                    self._watch_subtree(entry.path, child_rel)
            except OSError:
                continue

    def _drop_subtree(self, rel_dir: str) -> None:
        prefix = rel_dir + "/" if rel_dir else ""
        for wd, rd in list(self.dir_of.items()):
            if rd == rel_dir or rd.startswith(prefix):
                self.watch_of.pop(rd, None)
                del self.dir_of[wd]

    def poll(self) -> str | set:
        """Drain pending events; return OVERFLOW or the set of changed relpaths."""
        if self.fd < 0:
            return OVERFLOW
        changed: set = set()
        while True:
            try:
                data = os.read(self.fd, 65536)
            except BlockingIOError:
                break
            except OSError:
                return OVERFLOW
            if not data:
                break
            paths, appeared, vanished, overflow = parse_inotify_buffer(data, self.dir_of)
            changed |= paths
            for rel_dir, name in appeared:
                rel_dir_path = self._resolve(rel_dir) if rel_dir else self._roots[0][0]
                child_rel = f"{rel_dir}/{name}" if rel_dir else name
                child_abs = os.path.join(rel_dir_path, name) if rel_dir_path else None
                if child_abs is not None:
                    self._watch_subtree(child_abs, child_rel)
            for rel_dir in vanished:
                self._drop_subtree(rel_dir)
            if overflow:
                return OVERFLOW
        return changed

    def close(self) -> None:
        if self.fd >= 0:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = -1
            self.watch_of.clear()
            self.dir_of.clear()

    def __del__(self):  # pragma: no cover - best effort
        try:
            self.close()
        except Exception:
            pass
