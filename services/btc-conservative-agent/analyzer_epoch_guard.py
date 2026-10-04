"""Analyzer epoch guard: a current-cohort result never mixes data versions.

Loaders call :meth:`EpochGuard.admit` on every row they read (or
:meth:`EpochGuard.filter_frame` on a DataFrame); the guard keeps only rows of the
declared clean epoch (stamped ``data_epoch_id``, or unstampable/epoch-independent
rows timestamped at or after the epoch start) and counts what it rejected. The
generation receipt must carry :meth:`EpochGuard.receipt_block` under
``data_epoch``; the self-aware check ``data.compat_epoch_purity`` is RED when
that block is missing, names another epoch or reports
``pre_epoch_rows_admitted > 0``.

No epoch declared means no filtering (the block reports ``declared: false``).

Retained pre-epoch rows stay on disk on purpose, so purity is proven against
what readers actually opened: :class:`StreamReadMonitor` records every
read-open of an epoch-scoped stream file under the watched roots. Opens inside
:meth:`StreamReadMonitor.guarded` belong to a loader that filters through the
guard; any other open of a file holding pre-epoch rows counts all of those rows
as admitted.
"""

from __future__ import annotations

import contextlib
import csv
import os
import re
import sys
import threading
from pathlib import Path
from typing import Any, Iterable, Iterator

import data_epoch as de

ADMITTED_CLASSES = de.COMPATIBLE_CLASSES
_STREAM_RE = re.compile(r"^(?P<base>.+\.(?:jsonl|csv))(?:\.\d+)?$")
_SKIP_SUFFIXES = (".malformed_rows.jsonl",)


class PreEpochRowError(RuntimeError):
    """A pre-epoch or foreign-epoch row reached a current-cohort result."""


class EpochGuard:
    def __init__(self, manifest: dict | None) -> None:
        self.manifest = manifest
        self.admitted: dict[str, int] = {}
        self.rejected: dict[str, dict[str, int]] = {}

    @classmethod
    def from_data_dir(cls, data_dir: str | os.PathLike) -> "EpochGuard":
        return cls(de.load_manifest(Path(data_dir) / de.MANIFEST_NAME))

    @property
    def declared(self) -> bool:
        return self.manifest is not None

    def admit(self, relpath: str, row: dict, ts_fields: Iterable[str] = de.TS_KEYS) -> bool:
        if not self.manifest:
            return True
        cls = de.classify_row(relpath, row, self.manifest, ts_fields)
        if cls in ADMITTED_CLASSES:
            self.admitted[relpath] = self.admitted.get(relpath, 0) + 1
            return True
        bucket = self.rejected.setdefault(relpath, {})
        bucket[cls] = bucket.get(cls, 0) + 1
        return False

    def admit_line(self, relpath: str, line: str | bytes) -> bool:
        """:meth:`admit` for one raw JSONL line (same classification as the purity audit)."""
        if not self.manifest:
            return True
        raw = line.encode("utf-8", "replace") if isinstance(line, str) else line
        if not raw.strip():
            return True
        cls = de.classify(relpath, stamp_value=de.line_stamp(raw), ts=de.line_ts(raw), manifest=self.manifest)
        if cls in ADMITTED_CLASSES:
            self.admitted[relpath] = self.admitted.get(relpath, 0) + 1
            return True
        bucket = self.rejected.setdefault(relpath, {})
        bucket[cls] = bucket.get(cls, 0) + 1
        return False

    def filter_rows(self, relpath: str, rows: Iterable[dict], ts_fields: Iterable[str] = de.TS_KEYS) -> list[dict]:
        fields = tuple(ts_fields)
        return [row for row in rows if self.admit(relpath, row, fields)]

    def filter_frame(self, relpath: str, frame: Any, ts_fields: Iterable[str] = de.TS_KEYS):
        """:meth:`filter_rows` for a pandas DataFrame (returns a filtered copy)."""
        if not self.manifest or frame is None or len(frame) == 0:
            return frame
        fields = tuple(ts_fields)
        mask = [self.admit(relpath, row, fields) for row in frame.to_dict("records")]
        return frame[mask].copy()

    def assert_pure(self, relpath: str, rows: Iterable[dict], ts_fields: Iterable[str] = de.TS_KEYS) -> None:
        """Raise if any row of an already-built result is not clean-epoch."""
        if not self.manifest:
            return
        fields = tuple(ts_fields)
        for row in rows:
            cls = de.classify_row(relpath, row, self.manifest, fields)
            if cls not in ADMITTED_CLASSES:
                raise PreEpochRowError(f"{relpath}: {cls} row in a {self.manifest['epoch_id']} result")

    def receipt_block(self, *, pre_epoch_rows_admitted: int = 0) -> dict:
        rejected = sum(n for bucket in self.rejected.values() for n in bucket.values())
        manifest = self.manifest or {}
        return {"declared": self.declared, "epoch_id": manifest.get("epoch_id"),
                "started_at_utc": manifest.get("started_at_utc"),
                "rows_admitted": sum(self.admitted.values()), "pre_epoch_rows_rejected": rejected,
                "pre_epoch_rows_admitted": int(pre_epoch_rows_admitted),
                "rejected_by_stream": {k: dict(v) for k, v in sorted(self.rejected.items())}}

    # ------------------------------------------------------------ guarded loaders

    def read_jsonl(self, path: str | os.PathLike, relpath: str | None = None) -> list[dict]:
        """Clean-epoch rows of one JSONL file (malformed lines skipped)."""
        import json

        rel = relpath or Path(path).name
        rows = []
        with guarded_read(rel), open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and self.admit(rel, row):
                    rows.append(row)
        return rows

    def read_csv_rows(self, path: str | os.PathLike, relpath: str | None = None) -> list[dict]:
        """Clean-epoch rows of one CSV file as dicts."""
        rel = relpath or Path(path).name
        with guarded_read(rel), open(path, "r", encoding="utf-8", errors="replace", newline="") as handle:
            return [row for row in csv.DictReader(handle) if self.admit(rel, row)]


def stream_relpath(root: str | os.PathLike, path: str | os.PathLike) -> str | None:
    """Epoch-scoped stream base (``a.jsonl``, ``v3/ledgers/b.jsonl``) of ``path`` under ``root``."""
    try:
        rel = os.path.relpath(os.path.abspath(path), os.path.abspath(root))
    except ValueError:
        return None
    rel = rel.replace("\\", "/")
    if rel.startswith("../") or rel == "..":
        return None
    m = _STREAM_RE.match(rel)
    if not m:
        return None
    base = m.group("base")
    if base.endswith(_SKIP_SUFFIXES) or base.startswith(de.EPOCH_AUDIT_SKIP_DIRS) or de.epoch_independent(base):
        return None
    return base


def file_pre_epoch_rows(path: str | os.PathLike, relpath: str, manifest: dict | None) -> int:
    """Rows of one stream file that the guard would not admit."""
    if not manifest:
        return 0
    bad = 0
    if relpath.endswith(".csv"):
        with guarded_read(relpath, "inventory"):
            handle = open(path, "r", encoding="utf-8", errors="replace", newline="")
        with handle:
            for row in csv.DictReader(handle):
                if de.classify_row(relpath, row, manifest) not in ADMITTED_CLASSES:
                    bad += 1
        return bad
    with guarded_read(relpath, "inventory"):
        handle = open(path, "rb")
    with handle:
        for line in handle:
            if not line.strip():
                continue
            cls = de.classify(relpath, stamp_value=de.line_stamp(line), ts=de.line_ts(line), manifest=manifest)
            if cls not in ADMITTED_CLASSES:
                bad += 1
    return bad


_LOCAL = threading.local()
_ACTIVE_MONITOR: "StreamReadMonitor | None" = None
_HOOK_INSTALLED = False
_LIBRARY_DIR = os.path.normcase(os.path.dirname(os.__file__))
_THIS_FILE = os.path.normcase(os.path.abspath(__file__))


@contextlib.contextmanager
def guarded_read(relpath: str, reason: str = "epoch_filtered") -> Iterator[None]:
    """Opens inside this block belong to a loader that filters ``relpath`` through the guard.

    ``reason="inventory"`` marks byte/row-count inventory (retention, health
    sizes) that never feeds a cohort result; it is listed separately.
    """
    stack = getattr(_LOCAL, "stack", None)
    if stack is None:
        stack = _LOCAL.stack = []
    stack.append(reason)
    try:
        yield
    finally:
        stack.pop()


def stream_name(path: str | os.PathLike) -> str:
    """Stream base of a data-root file: ``v3/ledgers/x.jsonl`` for ledgers, else the rotation-free basename."""
    parts = Path(path).parts
    name = re.sub(r"\.\d+$", "", parts[-1]) if parts else str(path)
    if len(parts) >= 3 and parts[-3] == "v3" and parts[-2] == "ledgers":
        return f"v3/ledgers/{name}"
    return name


def guarded_open(path: str | os.PathLike, *args: Any, relpath: str | None = None, **kwargs: Any):
    """``open`` for a loader that admits every row it reads through the guard."""
    with guarded_read(relpath or stream_name(path)):
        return open(path, *args, **kwargs)


_PROCESS_GUARD: "EpochGuard | None" = None


def set_process_guard(guard: "EpochGuard | None") -> None:
    global _PROCESS_GUARD
    _PROCESS_GUARD = guard


def process_guard() -> "EpochGuard":
    """The analyzer's declared-epoch guard; admits everything outside an analyzer run."""
    return _PROCESS_GUARD if _PROCESS_GUARD is not None else EpochGuard(None)


def epoch_lines(path: str | os.PathLike, mode: str = "r", *, relpath: str | None = None,
                **kwargs: Any) -> Iterator[Any]:
    """Clean-epoch lines of one JSONL file (text or bytes per ``mode``)."""
    rel = relpath or stream_name(path)
    guard = process_guard()
    with guarded_open(path, mode, relpath=rel, **kwargs) as handle:
        for line in handle:
            if guard.admit_line(rel, line):
                yield line


def epoch_csv_rows(rows: Iterable[dict], path: str | os.PathLike, *, relpath: str | None = None) -> list[dict]:
    """Clean-epoch rows of a CSV already opened with :func:`guarded_open`."""
    rel = relpath or stream_name(path)
    return process_guard().filter_rows(rel, rows)


def _audit_hook(event: str, args: tuple) -> None:
    if event != "open" or _ACTIVE_MONITOR is None:
        return
    _ACTIVE_MONITOR._observe(args)


class StreamReadMonitor:
    """Process-wide record of which epoch-scoped stream files were opened, and how."""

    def __init__(self, roots: Iterable[str | os.PathLike]) -> None:
        self.roots = [os.path.abspath(str(r)) for r in roots if r and os.path.isdir(str(r))]
        self._norm_roots = [os.path.normcase(r) + os.sep for r in self.roots]
        self._lock = threading.Lock()
        self.files: dict[str, dict] = {}

    def install(self) -> "StreamReadMonitor":
        global _ACTIVE_MONITOR, _HOOK_INSTALLED
        _ACTIVE_MONITOR = self
        if not _HOOK_INSTALLED:
            sys.addaudithook(_audit_hook)
            _HOOK_INSTALLED = True
        return self

    @staticmethod
    def _is_read(mode: Any, flags: Any) -> bool:
        if isinstance(mode, str):
            return not any(c in mode for c in "wax+")
        if isinstance(flags, int):
            return not flags & (os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC)
        return True

    @staticmethod
    def _caller_sites(limit: int = 3) -> list[str]:
        sites = []
        frame = sys._getframe(1)
        while frame is not None and len(sites) < limit:
            filename = frame.f_code.co_filename
            norm = os.path.normcase(os.path.abspath(filename)) if not filename.startswith("<") else ""
            if norm and norm != _THIS_FILE and not norm.startswith(_LIBRARY_DIR):
                sites.append(f"{os.path.basename(filename)}:{frame.f_code.co_name}:{frame.f_lineno}")
            frame = frame.f_back
        return sites

    def _observe(self, args: tuple) -> None:
        path = args[0] if args else None
        if isinstance(path, int) or path is None:
            return
        try:
            path = os.fsdecode(os.fspath(path))
        except TypeError:
            return
        if not self._is_read(args[1] if len(args) > 1 else None, args[2] if len(args) > 2 else None):
            return
        absolute = os.path.abspath(path)
        norm = os.path.normcase(absolute)
        for root, norm_root in zip(self.roots, self._norm_roots):
            if norm.startswith(norm_root):
                relpath = stream_relpath(root, absolute)
                break
        else:
            return
        if relpath is None:
            return
        stack = getattr(_LOCAL, "stack", None)
        kind = stack[-1] if stack else "raw"
        with self._lock:
            entry = self.files.setdefault(absolute, {"stream": relpath, "epoch_filtered": 0, "inventory": 0,
                                                     "raw": 0, "raw_sites": []})
            entry[kind] = entry.get(kind, 0) + 1
            if kind == "raw" and len(entry["raw_sites"]) < 5:
                site = " <- ".join(self._caller_sites())
                if site not in entry["raw_sites"]:
                    entry["raw_sites"].append(site)

    def report(self, manifest: dict | None) -> dict:
        """Pre-epoch rows reachable through unguarded opens (all of a raw-opened file's bad rows)."""
        with self._lock:
            files = {k: dict(v, raw_sites=list(v["raw_sites"])) for k, v in self.files.items()}
        admitted_by_stream: dict[str, int] = {}
        unguarded: dict[str, dict] = {}
        inventory: dict[str, int] = {}
        filtered: dict[str, int] = {}
        with guarded_read("<monitor>", "inventory"):
            for path, entry in sorted(files.items()):
                stream = entry["stream"]
                if entry.get("epoch_filtered"):
                    filtered[stream] = filtered.get(stream, 0) + entry["epoch_filtered"]
                if entry.get("inventory"):
                    inventory[stream] = inventory.get(stream, 0) + entry["inventory"]
                if not entry.get("raw"):
                    continue
                try:
                    bad = file_pre_epoch_rows(path, stream, manifest)
                except OSError:
                    bad = 0
                item = unguarded.setdefault(stream, {"opens": 0, "pre_epoch_rows": 0, "files": 0, "sites": []})
                item["opens"] += entry["raw"]
                item["files"] += 1
                item["pre_epoch_rows"] += bad
                for site in entry["raw_sites"]:
                    if site not in item["sites"] and len(item["sites"]) < 5:
                        item["sites"].append(site)
                if bad:
                    admitted_by_stream[stream] = admitted_by_stream.get(stream, 0) + bad
        return {"active": True, "roots": self.roots,
                "pre_epoch_rows_admitted": sum(admitted_by_stream.values()),
                "pre_epoch_rows_admitted_by_stream": dict(sorted(admitted_by_stream.items())),
                "unguarded_stream_reads": dict(sorted(unguarded.items())),
                "epoch_filtered_stream_opens": dict(sorted(filtered.items())),
                "inventory_stream_opens": dict(sorted(inventory.items()))}


def active_monitor() -> "StreamReadMonitor | None":
    return _ACTIVE_MONITOR
