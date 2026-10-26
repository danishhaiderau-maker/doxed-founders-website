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


NO_FRESH_FLOOR = "FRESH_FLOOR_MISSING"


class PreEpochRowError(RuntimeError):
    """A pre-epoch or foreign-epoch row reached a current-cohort result."""


class EpochGuard:
    def __init__(self, manifest: dict | None, floor_ts: float | None = None, require_floor: bool = False) -> None:
        self.manifest = manifest
        self.floor_ts = float(floor_ts) if floor_ts else None  # fresh_start_epoch.json epoch_ts (see data_epoch.classify)
        self.require_floor = bool(require_floor)
        # Fail closed: a declared epoch whose fresh-start floor could not be loaded admits NOTHING (a missing or
        # unreadable fresh_start_epoch.json must never silently turn the floor off).
        self.floor_missing = bool(manifest) and self.require_floor and self.floor_ts is None

        self.admitted: dict[str, int] = {}
        self.rejected: dict[str, dict[str, int]] = {}

    @property
    def floor_loaded(self) -> bool:
        return self.floor_ts is not None

    @classmethod
    def from_data_dir(cls, data_dir: str | os.PathLike) -> "EpochGuard":
        return cls(de.load_manifest(Path(data_dir) / de.MANIFEST_NAME), floor_ts=fresh_floor_ts(data_dir),
                   require_floor=True)

    @property
    def declared(self) -> bool:
        return self.manifest is not None

    def admit(self, relpath: str, row: dict, ts_fields: Iterable[str] = de.TS_KEYS) -> bool:
        if not self.manifest:
            return True
        if self.floor_missing:
            return self._reject_no_floor(relpath)
        cls = de.classify_row(relpath, row, self.manifest, ts_fields, floor_ts=self.floor_ts)
        if cls in ADMITTED_CLASSES:
            self.admitted[relpath] = self.admitted.get(relpath, 0) + 1
            return True
        bucket = self.rejected.setdefault(relpath, {})
        bucket[cls] = bucket.get(cls, 0) + 1
        return False

    def admit_v3_ledger_row(self, relpath: str, row: dict) -> bool:
        """HM 19 Oct (queue item 3): V3 ledger rows carry segment windows, not a row time. Under a declared epoch a row is
        admitted ONLY if its nested timestamps place it entirely at/after the fresh-start floor (class OK). PRE_EPOCH,
        STRADDLE (window crosses the floor) and UNSTAMPED rows are hard-rejected (counted per class in ``rejected``), and a
        declared epoch with no loadable floor admits nothing. No manifest (tests, tools outside an analyzer run): admit."""
        if not self.manifest:
            return True
        if self.floor_missing or self.floor_ts is None:
            return self._reject_no_floor(relpath)
        cls = nested_ts_class(row, self.floor_ts)
        if cls == NESTED_OK and write_stamp_rejects(row):
            cls = NESTED_NON_COUNTABLE
        if cls == NESTED_OK:
            self.admitted[relpath] = self.admitted.get(relpath, 0) + 1
            return True
        bucket = self.rejected.setdefault(relpath, {})
        bucket[cls] = bucket.get(cls, 0) + 1
        return False

    def _reject_no_floor(self, relpath: str) -> bool:
        bucket = self.rejected.setdefault(relpath, {})
        bucket[NO_FRESH_FLOOR] = bucket.get(NO_FRESH_FLOOR, 0) + 1
        return False

    def admit_line(self, relpath: str, line: str | bytes) -> bool:
        """:meth:`admit` for one raw JSONL line (same classification as the purity audit)."""
        if not self.manifest:
            return True
        raw = line.encode("utf-8", "replace") if isinstance(line, str) else line
        if not raw.strip():
            return True
        if self.floor_missing:
            return self._reject_no_floor(relpath)
        cls = de.classify(relpath, stamp_value=de.line_stamp(raw), ts=de.line_ts(raw), manifest=self.manifest,
                          floor_ts=self.floor_ts)
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
            if self.floor_missing:
                raise PreEpochRowError(f"{relpath}: {NO_FRESH_FLOOR}: declared epoch without fresh_start_epoch.json")
            cls = de.classify_row(relpath, row, self.manifest, fields, floor_ts=self.floor_ts)
            if cls not in ADMITTED_CLASSES:
                raise PreEpochRowError(f"{relpath}: {cls} row in a {self.manifest['epoch_id']} result")

    def receipt_block(self, *, pre_epoch_rows_admitted: int = 0) -> dict:
        rejected = sum(n for bucket in self.rejected.values() for n in bucket.values())
        manifest = self.manifest or {}
        return {"declared": self.declared, "epoch_id": manifest.get("epoch_id"),
                "floor_ts": self.floor_ts, "floor_loaded": self.floor_loaded,
                "floor_required": self.require_floor, "floor_missing_fail_closed": self.floor_missing,
                "started_at_utc": manifest.get("started_at_utc"),
                "rows_admitted": sum(self.admitted.values()), "pre_epoch_rows_rejected": rejected,
                "pre_epoch_rows_admitted": int(pre_epoch_rows_admitted),
                "rejected_by_stream": {k: dict(v) for k, v in sorted(self.rejected.items())},
                "admitted_by_stream": dict(sorted(self.admitted.items())),
                "fresh_rule": self.fresh_rule_status()}

    def fresh_rule_status(self) -> dict:
        """RED when a declared epoch has no loadable floor (the fresh rule then admits NOTHING, which must be loud, not
        a quiet empty report). Otherwise GREEN, with per-stream admitted/rejected-by-reason counts for the V3 readers."""
        v3 = [k for k in set(self.admitted) | set(self.rejected) if k.startswith("v3/ledgers/")]
        per = {k: {"admitted": int(self.admitted.get(k, 0)), "rejected_by_reason": dict(self.rejected.get(k, {}))}
               for k in sorted(v3)}
        if self.declared and (self.floor_missing or self.floor_ts is None):
            return {"status": "RED", "reason": NO_FRESH_FLOOR, "v3_readers": per}
        return {"status": "GREEN", "reason": None, "v3_readers": per}

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
    if base in de.PROVENANCE_JOURNAL_BASES:
        # Chain validation reads the whole journal from genesis; its rows are provenance, not evidence.
        return None
    return base


def fresh_floor_ts(data_dir: str | os.PathLike) -> float | None:
    """epoch_ts of fresh_start_epoch.json beside the manifest (volume root) or one level up; None if not stamped."""
    import json
    base = Path(data_dir)
    for directory in (base, base.parent):
        try:
            ts = float(json.loads((directory / "fresh_start_epoch.json").read_text(encoding="utf-8")).get("epoch_ts") or 0)
        except (OSError, ValueError, TypeError, AttributeError):
            continue
        if ts > 0:
            return ts
    return None


NESTED_PRE_EPOCH = "PRE_EPOCH"
NESTED_STRADDLE = "STRADDLE"
NESTED_UNSTAMPED = "UNSTAMPED"
NESTED_OK = "OK"
NESTED_NON_COUNTABLE = "WRITE_STAMP_NON_COUNTABLE"


def write_stamp_rejects(row: Any) -> bool:
    """A row whose ledger write stamp exists but is not countable (fail-closed writer) is never admitted. Rows with
    no write stamp keep the nested-ts rule (no nested ts = UNSTAMPED, always rejected)."""
    import ledger_write_stamp as lws
    return isinstance(row, dict) and isinstance(row.get(lws.STAMP_KEY), dict) and lws.row_non_countable_reason(row) is not None


def _nested_ts(value: Any, out: list, depth: int = 0, key: str = "") -> None:
    """Every plausible ``*_ts`` number nested anywhere in a row (segment_ref.start_ts, requested_start_ts, coverage.*)."""
    if depth > 8:
        return
    if isinstance(value, dict):
        for k, v in value.items():
            _nested_ts(v, out, depth + 1, str(k))
    elif isinstance(value, list):
        for v in value[:200]:
            _nested_ts(v, out, depth + 1, key)
    elif key.endswith("_ts") and not isinstance(value, bool):
        ts = de.parse_ts(value)
        if ts is not None:
            out.append(ts)


def nested_ts_class(row: Any, floor: float) -> str:
    """Epoch class of a row that carries no top-level timestamp, from its nested timestamps only (V3 ledger rows
    carry segment windows, not a row time). Entirely before the floor = PRE_EPOCH; entirely at/after = OK; a window
    crossing the floor = STRADDLE; none at all = UNSTAMPED. Never used to ADMIT a row: guard.admit / classify stay
    fail-closed (UNDATED); this only decides how the purity audit counts rows it cannot place."""
    stamps: list = []
    _nested_ts(row, stamps)
    if not stamps:
        return NESTED_UNSTAMPED
    if max(stamps) < floor:
        return NESTED_PRE_EPOCH
    return NESTED_OK if min(stamps) >= floor else NESTED_STRADDLE


def file_epoch_row_counts(path: str | os.PathLike, relpath: str, manifest: dict | None,
                          floor_ts: float | None = None) -> dict:
    """Rows of one stream file the guard would not admit, split into ``pre_epoch`` (provably before the epoch: RED),
    ``straddle`` and ``unstamped`` (cannot be placed: never countable, reported separately, not RED)."""
    counts = {"pre_epoch": 0, "straddle": 0, "unstamped": 0}
    if not manifest:
        return counts
    if relpath.endswith(".csv"):
        with guarded_read(relpath, "inventory"):
            handle = open(path, "r", encoding="utf-8", errors="replace", newline="")
        with handle:
            for row in csv.DictReader(handle):
                if de.classify_row(relpath, row, manifest, floor_ts=floor_ts) not in ADMITTED_CLASSES:
                    counts["pre_epoch"] += 1
        return counts
    import json
    floor = float(floor_ts or manifest.get("started_at_ts") or 0)
    with guarded_read(relpath, "inventory"):
        handle = open(path, "rb")
    with handle:
        for line in handle:
            if not line.strip():
                continue
            ts = de.line_ts(line)
            cls = de.classify(relpath, stamp_value=de.line_stamp(line), ts=ts, manifest=manifest, floor_ts=floor_ts)
            if cls in ADMITTED_CLASSES:
                continue
            if cls == de.UNDATED and ts is None and floor:
                try:
                    row = json.loads(line)
                except ValueError:
                    row = None
                kind = nested_ts_class(row, floor) if isinstance(row, dict) else NESTED_UNSTAMPED
                if kind == NESTED_OK and write_stamp_rejects(row):
                    kind = NESTED_UNSTAMPED  # fail-closed stamp: cannot be placed as countable
                if kind == NESTED_OK:
                    continue  # every nested window is at/after the floor: placed post-epoch (not admitted by classify)
                counts[{NESTED_PRE_EPOCH: "pre_epoch", NESTED_STRADDLE: "straddle"}.get(kind, "unstamped")] += 1
                continue
            counts["pre_epoch"] += 1
    return counts


UNPLACED_LIST_SCHEMA = "analyzer_epoch_unplaced_rows_v1"
_ROW_ID_KEYS = ("record_id", "event_id", "decision_id", "lifecycle_id", "segment_id", "trade_id", "id")


def file_unplaced_rows(path: str | os.PathLike, relpath: str, manifest: dict | None,
                       floor_ts: float | None = None) -> Iterator[dict]:
    """The exact straddle/unstamped rows of one JSONL stream file (same classification as
    :func:`file_epoch_row_counts`). Each item names the row by file, 1-based line number, byte offset, the
    sha256 of its raw bytes and its record id when it has one. These rows are EXCLUDED (never admitted, never
    guessed into the epoch); the list exists so they can be reviewed before any deletion. Read-only."""
    import hashlib
    import json
    if not manifest or relpath.endswith(".csv"):
        return
    floor = float(floor_ts or manifest.get("started_at_ts") or 0)
    if not floor:
        return
    with guarded_read(relpath, "inventory"):
        handle = open(path, "rb")
    with handle:
        offset = 0
        for line_no, line in enumerate(handle, 1):
            start, offset = offset, offset + len(line)
            if not line.strip():
                continue
            ts = de.line_ts(line)
            cls = de.classify(relpath, stamp_value=de.line_stamp(line), ts=ts, manifest=manifest, floor_ts=floor_ts)
            if cls in ADMITTED_CLASSES or not (cls == de.UNDATED and ts is None):
                continue
            try:
                row = json.loads(line)
            except ValueError:
                row = None
            kind = nested_ts_class(row, floor) if isinstance(row, dict) else NESTED_UNSTAMPED
            if kind == NESTED_OK and write_stamp_rejects(row):
                kind = NESTED_UNSTAMPED
            if kind not in (NESTED_STRADDLE, NESTED_UNSTAMPED):
                continue
            row_id = None
            if isinstance(row, dict):
                row_id = next((str(row[k]) for k in _ROW_ID_KEYS if row.get(k) not in (None, "")), None)
            yield {"stream": relpath, "file": os.path.basename(str(path)), "line_no": line_no, "byte_offset": start,
                   "kind": "straddle" if kind == NESTED_STRADDLE else "unstamped", "row_id": row_id,
                   "row_sha256": hashlib.sha256(line.rstrip(b"\r\n")).hexdigest()}


def unplaced_rows_document(data_root: str | os.PathLike, manifest: dict | None, floor_ts: float | None = None,
                           streams: Iterable[str] | None = None) -> dict:
    """Exclusion list of every straddle/unstamped row under ``data_root`` (default: the v3 ledgers), with a
    sha256 over the canonical JSONL of the list. Deterministic: same files, same digest."""
    import hashlib
    import json
    root = Path(data_root)
    if streams is None:
        ledgers = root / "v3" / "ledgers"
        streams = sorted(f"v3/ledgers/{p.name}" for p in ledgers.glob("*.jsonl")) if ledgers.is_dir() else []
    rows: list[dict] = []
    for rel in streams:
        base = root / rel
        files = sorted(base.parent.glob(base.name + ".*")) if base.parent.is_dir() else []
        files = [p for p in files if re.fullmatch(re.escape(base.name) + r"\.\d+", p.name)]
        for path in ([base] if base.is_file() else []) + files:
            rows.extend(file_unplaced_rows(path, rel, manifest, floor_ts))
    body = "".join(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in rows)
    by_stream: dict[str, dict[str, int]] = {}
    for r in rows:
        bucket = by_stream.setdefault(r["stream"], {})
        bucket[r["kind"]] = bucket.get(r["kind"], 0) + 1
    return {"schema": UNPLACED_LIST_SCHEMA, "epoch_id": (manifest or {}).get("epoch_id"), "floor_ts": floor_ts,
            "treatment": "EXCLUDED (never admitted, never guessed into the epoch, not deleted)",
            "count": len(rows), "by_stream": dict(sorted(by_stream.items())),
            "list_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "list_sha256_basis": "sha256 of the rows as sorted-key compact JSON lines, in file/line order",
            "rows": rows}


def file_pre_epoch_rows(path: str | os.PathLike, relpath: str, manifest: dict | None,
                        floor_ts: float | None = None) -> int:
    """Rows of one stream file that are provably pre-epoch (straddle/unstamped rows are counted separately by
    :func:`file_epoch_row_counts`)."""
    return file_epoch_row_counts(path, relpath, manifest, floor_ts)["pre_epoch"]


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


V3_LEDGER_DIRS = ("ledgers", "recovery_ledgers")


def v3_ledger_relpath(path: str | os.PathLike) -> str | None:
    """``v3/ledgers/<name>.jsonl`` (or ``v3/recovery_ledgers/...``) for a V3 ledger file, rotation suffix dropped;
    None for any other file (non-ledger readers keep their own guard)."""
    parts = Path(path).parts
    if len(parts) >= 3 and parts[-3] == "v3" and parts[-2] in V3_LEDGER_DIRS:
        name = re.sub(r"\.\d+$", "", parts[-1])
        if name.endswith(".jsonl"):
            return f"v3/{parts[-2]}/{name}"
    return None


def open_v3_ledger(path: str | os.PathLike, *args: Any, inventory: bool = False, **kwargs: Any):
    """``open`` for one V3 ledger file, recorded by the read monitor as guarded.

    Default (``inventory=False``): the caller MUST pass every parsed row through :func:`admit_v3_row` (hard
    rejection of PRE_EPOCH / STRADDLE / UNSTAMPED rows) before it reaches any result. ``inventory=True`` is only for
    byte-level work whose output is never a cohort row (whole-file digests, store dedup/truncation checks).
    Only the ``open`` itself runs inside the guarded block, so a suspended generator never marks the caller's
    other opens as guarded."""
    rel = v3_ledger_relpath(path) or stream_name(path)
    with guarded_read(rel, "inventory" if inventory else "epoch_filtered"):
        return Path(path).open(*args, **kwargs)


def admit_v3_row(path_or_rel: str | os.PathLike, row: Any) -> bool:
    """Hard fresh-epoch gate for one parsed V3 ledger row (EpochGuard.admit_v3_ledger_row under the analyzer's
    process guard). Non-dict rows are rejected. Outside an analyzer run (no declared epoch) everything is admitted."""
    if not isinstance(row, dict):
        return False
    text = os.fspath(path_or_rel)
    rel = text if text.startswith("v3/") else v3_ledger_relpath(text)
    if rel is None:
        return True
    return process_guard().admit_v3_ledger_row(rel, row)


def admit_v3_line(path_or_rel: str | os.PathLike, raw: str | bytes) -> bool:
    """:func:`admit_v3_row` for one raw JSONL line (parsed here; unparseable lines are rejected)."""
    guard = process_guard()
    if not guard.manifest:
        return True
    import json
    try:
        row = json.loads(raw)
    except (ValueError, TypeError):
        return False
    return admit_v3_row(path_or_rel, row)


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

    def report(self, manifest: dict | None, floor_ts: float | None = None) -> dict:
        """Pre-epoch rows reachable through unguarded opens (all of a raw-opened file's bad rows)."""
        with self._lock:
            files = {k: dict(v, raw_sites=list(v["raw_sites"])) for k, v in self.files.items()}
        admitted_by_stream: dict[str, int] = {}
        straddle_by_stream: dict[str, int] = {}
        unstamped_by_stream: dict[str, int] = {}
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
                    counts = file_epoch_row_counts(path, stream, manifest, floor_ts)
                except OSError:
                    counts = {"pre_epoch": 0, "straddle": 0, "unstamped": 0}
                bad = counts["pre_epoch"]
                if counts["straddle"]:
                    straddle_by_stream[stream] = straddle_by_stream.get(stream, 0) + counts["straddle"]
                if counts["unstamped"]:
                    unstamped_by_stream[stream] = unstamped_by_stream.get(stream, 0) + counts["unstamped"]
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
                "straddle_rows_unguarded": sum(straddle_by_stream.values()),
                "straddle_rows_unguarded_by_stream": dict(sorted(straddle_by_stream.items())),
                "unstamped_rows_unguarded": sum(unstamped_by_stream.values()),
                "unstamped_rows_unguarded_by_stream": dict(sorted(unstamped_by_stream.items())),
                "unguarded_stream_reads": dict(sorted(unguarded.items())),
                "epoch_filtered_stream_opens": dict(sorted(filtered.items())),
                "inventory_stream_opens": dict(sorted(inventory.items()))}


def active_monitor() -> "StreamReadMonitor | None":
    return _ACTIVE_MONITOR
