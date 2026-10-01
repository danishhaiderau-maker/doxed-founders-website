"""Low-priority Fly worker that ships sealed research-data segments (shadow mode).

Runs as its own process (started by fly-entrypoint.sh only when
``RESEARCH_SEGMENTS_ENABLED=1``). It never imports bot.py or Flask, never
takes the trade lock, never serves HTTP and never deletes or rewrites source
data: pruning is not implemented in this phase.

Each cycle:
1. Stat the file universe selected by ``research_segment_selection``.
2. Plan an ordered list of operations (SEAL, TOMBSTONE, REWRITE, APPEND,
   SNAPSHOT) and keep the longest prefix that fits the byte budget, so an
   operation is never shipped ahead of one it depends on.
3. Read payloads, build a deterministic tar.gz and a hash-chained manifest.
4. Persist an intent (segment bytes, manifest bytes, post-commit state)
   before any upload.
5. Upload segment then manifest with ``If-None-Match: *``. A 412 whose stored
   sha256 equals ours is an idempotent retry; any other 412 fails closed.
6. Only then replace the checkpoint and drop the intent.

``RESEARCH_SEGMENTS_SINK`` selects the store: ``tigris`` (default, S3) or
``volume`` (write-once, fsynced files under ``/app/data/segment-store``,
served to the laptop by ``research_segment_server``). Both sinks share the
same keys, determinism, chain and checkpoint-after-confirmed-write rules.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from pathlib import Path

import contextlib
import fnmatch
import sqlite3
import uuid

import research_segment_format as fmt
import research_segment_selection as selection
from research_segment_store import ObjectStore, PreconditionFailed, StoreError, store_from_env

STATE_SCHEMA = "research_segment_shipper_state_v1"
STATUS_SCHEMA = "research_segment_shipper_status_v1"
INTENT_SCHEMA = "research_segment_shipper_intent_v1"
PRUNING_ENABLED = False

# The volume sink duplicates source data on the trading volume until pruning
# exists, so it carries a hard size cap and a much higher free-space floor.
VOLUME_DEFAULT_MAX_STORE_BYTES = 10 * 1024 ** 3
VOLUME_DEFAULT_MIN_FREE_BYTES = 4 * 1024 ** 3
DEFAULT_MIN_FREE_BYTES = 200 * 1024 * 1024
SNAPSHOT_COPY_ATTEMPTS = 3
RACE_BACKOFF_BASE_SECONDS = 60.0
# A stream that races during a build is dropped from that build and the rest
# is rebuilt, so one hot snapshot cannot discard a whole cycle's work.
RACE_REBUILDS_PER_CYCLE = 4
# Backlog mode: above this many unshipped bytes a cycle ships a larger segment
# and the worker leaves SCHED_IDLE for a bounded nice level, because an idle-class
# process on a saturated core gets almost no CPU and each cycle's full scan
# would otherwise be amortised over only one small segment.
DEFAULT_BACKLOG_BOOST_BYTES = 8 * 1024 * 1024
DEFAULT_BOOST_SEGMENT_BYTES = 64 * 1024 * 1024
DEFAULT_BOOST_NICE = 10
IDLE_NICE = 19
# An idle-class cycle still running after this long is starved by the bot's
# load; it is raised to the backlog priority so seq keeps advancing.
DEFAULT_STARVED_CYCLE_SECONDS = 180.0
# Online-backup steps hold the source's SHARED lock only for one step, so the
# bot's rollback-journal writers (5 s busy timeout) are never starved.
SQLITE_BACKUP_PAGES_PER_STEP = 1024
SQLITE_BACKUP_STEP_SLEEP_SECONDS = 0.005
SQLITE_BACKUP_DEADLINE_SECONDS = 180.0
SQLITE_CONSISTENCY = "sqlite_online_backup_v1"

APPEND_SUFFIXES = frozenset({".jsonl", ".csv", ".log"})
RECORD_SUFFIXES = frozenset({".jsonl", ".csv", ".log"})
SQLITE_SUFFIXES = (".db", ".sqlite", ".sqlite3", ".db-wal", ".sqlite-wal", ".sqlite3-wal",
                   ".db-shm", ".sqlite-shm", ".sqlite3-shm")
ANCHOR_BYTES = 4096
READ_CHUNK = 1024 * 1024
LINKED_RESEARCH_DIRS = ("research", "research_accumulator", "research_archive")
_RANK = {fmt.KIND_SEAL: 0, fmt.KIND_TOMBSTONE: 1}


class ShipperConflict(RuntimeError):
    """An existing object differs from ours; the write-once store refused it."""


class PlanRace(RuntimeError):
    """A file changed identity between planning and reading; retry next cycle."""

    def __init__(self, message: str, stream: str | None = None):
        super().__init__(message)
        self.stream = stream


def load_selection_rules() -> dict:
    return {
        "extensions": selection.EXTENSIONS,
        "excluded_names": selection.EXCLUDED_NAMES,
        "excluded_suffixes": selection.EXCLUDED_SUFFIXES,
        "excluded_path_globs": selection.EXCLUDED_PATH_GLOBS,
        "excluded_dir_names": frozenset(name.lower() for name in selection.EXCLUDED_DIR_NAMES),
    }


def rotation_parts(name: str, extensions) -> tuple[str, int] | None:
    base_name, separator, generation = name.rpartition(".")
    if not separator or not generation.isdigit() or generation.startswith("0"):
        return None
    return (base_name, int(generation)) if Path(base_name).suffix.lower() in extensions else None


def _sha256_file_range(path: Path, start: int, end: int) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        handle.seek(start)
        remaining = end - start
        while remaining > 0:
            chunk = handle.read(min(READ_CHUNK, remaining))
            if not chunk:
                raise PlanRace(f"{path} shrank while hashing")
            digest.update(chunk)
            remaining -= len(chunk)
    return digest.hexdigest()


def _complete_record_size(path: Path, size: int) -> int:
    if size <= 0 or path.suffix.lower() not in RECORD_SUFFIXES:
        return max(0, int(size))
    cursor = int(size)
    with path.open("rb") as handle:
        while cursor > 0:
            start = max(0, cursor - 64 * 1024)
            handle.seek(start)
            block = handle.read(cursor - start)
            newline = block.rfind(b"\n")
            if newline >= 0:
                return start + newline + 1
            cursor = start
    return 0


def _anchors(path: Path, offset: int) -> tuple[str, str]:
    head = _sha256_file_range(path, 0, min(ANCHOR_BYTES, offset))
    tail = _sha256_file_range(path, max(0, offset - ANCHOR_BYTES), offset)
    return head, tail


def _anchors_from_bytes(raw_prefix_head: bytes, raw_tail: bytes) -> tuple[str, str]:
    return hashlib.sha256(raw_prefix_head).hexdigest(), hashlib.sha256(raw_tail).hexdigest()


def _atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("wb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _is_sqlite(relpath: str) -> bool:
    return relpath.lower().endswith(SQLITE_SUFFIXES)


def _is_sqlite_db(relpath: str) -> bool:
    return relpath.lower().endswith((".db", ".sqlite", ".sqlite3"))


SQLITE_MAGIC = b"SQLite format 3\x00"


def _has_sqlite_header(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(len(SQLITE_MAGIC)) == SQLITE_MAGIC
    except OSError:
        return False


def _wal_signature(path: Path) -> list[int]:
    """WAL frames can commit without touching the main file's size or mtime."""
    try:
        stat = Path(f"{path}-wal").stat()
    except OSError:
        return [0, 0, 0]
    return [int(stat.st_ino), int(stat.st_size), int(stat.st_mtime_ns)]


@contextlib.contextmanager
def _normal_cpu_class():
    """Leave SCHED_IDLE while a SQLite step holds the source's SHARED lock.

    An idle-class holder can be starved by the very bot thread waiting on that
    lock. Niceness stays lowered; failures fall back to the current class.
    """
    try:
        previous = os.sched_getscheduler(0)
        os.sched_setscheduler(0, os.SCHED_OTHER, os.sched_param(0))
    except (AttributeError, OSError):
        yield
        return
    try:
        yield
    finally:
        try:
            os.sched_setscheduler(0, previous, os.sched_param(0))
        except OSError:
            pass


def sqlite_online_backup(source: Path, target: Path, *, deadline_seconds: float,
                         clock=time.monotonic) -> None:
    """Write a transactionally consistent, integrity-checked copy of ``source``.

    SQLite restarts the backup whenever another connection writes the source
    between steps, so the result is never torn; a source too hot to finish
    within the deadline raises ``TimeoutError`` and nothing is shipped.
    """
    deadline = clock() + max(1.0, float(deadline_seconds))

    def progress(_status, _remaining, _total):
        if clock() >= deadline:
            raise TimeoutError(f"{source.name} online backup exceeded {deadline_seconds:.0f}s")

    target.parent.mkdir(parents=True, exist_ok=True)
    reader = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True, timeout=15)
    writer = sqlite3.connect(str(target))
    try:
        with _normal_cpu_class():
            reader.backup(writer, pages=SQLITE_BACKUP_PAGES_PER_STEP, progress=progress,
                          sleep=SQLITE_BACKUP_STEP_SLEEP_SECONDS)
        if clock() >= deadline:
            raise TimeoutError(f"{source.name} online backup exceeded {deadline_seconds:.0f}s")
        result = writer.execute("PRAGMA integrity_check").fetchone()
        if not result or str(result[0]).lower() != "ok":
            raise sqlite3.DatabaseError(f"{source.name} online backup failed integrity_check")
    finally:
        writer.close()
        reader.close()


class SegmentShipper:
    def __init__(
        self, *, store: ObjectStore, volume_root: Path, runtime_root: Path, state_dir: Path,
        rules: dict, prefix: str = "v1", max_segment_bytes: int = 8 * 1024 * 1024,
        max_member_bytes: int = 64 * 1024 * 1024, source_git_rev: str = "unknown",
        large_snapshot_bytes: int = 1024 * 1024, large_snapshot_interval: float = 3600.0,
        clock=time.time, sink: str = "tigris", max_store_bytes: int = 0,
        max_sqlite_bytes: int = 512 * 1024 * 1024, huge_snapshot_interval: float = 6 * 3600.0,
        sqlite_backup_deadline: float = SQLITE_BACKUP_DEADLINE_SECONDS,
        baseline_genesis: bool = False,
        backlog_boost_bytes: int = 0, boost_segment_bytes: int = 0,
    ):
        self.sink = sink
        # 0 disables backlog mode; the regular budget then always applies.
        self.backlog_boost_bytes = max(0, int(backlog_boost_bytes))
        self.boost_segment_bytes = max(0, int(boost_segment_bytes))
        self.last_deferred: int | None = None
        self.boosted = False
        # A fresh epoch starts at "now": seq 1 records existing bytes instead
        # of shipping them, so only data written after the cutover travels.
        self.baseline_genesis = bool(baseline_genesis)
        self.max_sqlite_bytes = max(1, int(max_sqlite_bytes))
        # Snapshots above the regular member cap are re-shipped this rarely so
        # a large hot DB cannot consume the unpruned store.
        self.huge_snapshot_interval = max(0.0, float(huge_snapshot_interval))
        self.sqlite_backup_deadline = float(sqlite_backup_deadline)
        self.max_store_bytes = max(0, int(max_store_bytes))
        self.large_snapshot_bytes = max(0, int(large_snapshot_bytes))
        self.large_snapshot_interval = max(0.0, float(large_snapshot_interval))
        self.throttled: list[str] = []
        self.next_cursor = ""
        # stream -> (consecutive races, retry-not-before); in memory only, so a
        # restart retries every stream once.
        self.race_backoff: dict[str, tuple[int, float]] = {}
        self.build_raced: list[PlanRace] = []
        self.store = store
        self.volume_root = Path(volume_root).resolve()
        self.runtime_root = Path(runtime_root).resolve()
        self.state_dir = Path(state_dir).resolve()
        self.rules = rules
        self.prefix = fmt.validate_prefix(prefix)
        self.max_segment_bytes = max(1, int(max_segment_bytes))
        self.max_member_bytes = max(1, int(max_member_bytes))
        self.source_git_rev = source_git_rev
        self.clock = clock
        self.state_path = self.state_dir / "state.json"
        self.status_path = self.state_dir / "status.json"
        self.intent_dir = self.state_dir / "intent"
        self.sqlite_scratch = self.state_dir / "sqlite-snapshots"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._clear_sqlite_scratch()

    # ------------------------------------------------------------ backlog mode
    @property
    def segment_budget(self) -> int:
        if self.boosted:
            return max(self.max_segment_bytes, self.boost_segment_bytes)
        return self.max_segment_bytes

    def _backlog_hint(self) -> int:
        if self.last_deferred is not None:
            return self.last_deferred
        # A restarted worker resumes in the mode its last published status implies.
        try:
            status = json.loads(self.status_path.read_text(encoding="utf-8"))
            return int(status.get("unshipped_bytes") or 0)
        except (OSError, ValueError, TypeError):
            return 0

    def boost_due(self, state: dict | None = None) -> bool:
        if not self.backlog_boost_bytes or self.boost_segment_bytes <= self.max_segment_bytes:
            return False
        if self._backlog_hint() <= self.backlog_boost_bytes:
            return False
        if self.max_store_bytes:
            if state is None:
                try:
                    state = self.load_state()
                except (OSError, ValueError, RuntimeError):
                    return False
            # A boosted segment must still fit under the store cap.
            if int(state.get("store_bytes") or 0) + self.boost_segment_bytes > self.max_store_bytes:
                return False
        return True

    def publish_mode(self, worker_state: str, *, priority: str, priority_error: str | None,
                     next_cycle_at: float | None = None) -> None:
        """Publish the mode the worker is in now, not the one its last cycle ran in."""
        self.write_status(worker_state=worker_state, next_cycle_at=next_cycle_at,
                          backlog_mode=self.boosted, segment_budget_bytes=self.segment_budget,
                          priority=priority, priority_error=priority_error)

    def _clear_sqlite_scratch(self) -> None:
        # Scratch backups are the shipper's own copies, never source evidence.
        if self.sqlite_scratch.is_dir():
            for leftover in self.sqlite_scratch.glob("*.db"):
                leftover.unlink(missing_ok=True)

    # ------------------------------------------------------------------ state
    def load_state(self) -> dict:
        if not self.state_path.is_file():
            return {"schema": STATE_SCHEMA, "prefix": self.prefix, "seq": 0,
                    "last_manifest_sha256": fmt.GENESIS_PREV_SHA256, "files": {},
                    "tombstones": {}, "last_segment_at": None}
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        if state.get("schema") != STATE_SCHEMA or state.get("prefix") != self.prefix:
            raise RuntimeError("shipper checkpoint schema/prefix mismatch; refusing to continue")
        return state

    def write_status(self, **fields) -> None:
        previous = {}
        if self.status_path.is_file():
            try:
                previous = json.loads(self.status_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                previous = {}
        previous.update(fields)
        previous.update({"schema": STATUS_SCHEMA, "pruning_enabled": PRUNING_ENABLED,
                         "updated_at": self.clock(), "prefix": self.prefix, "sink": self.sink,
                         "max_store_bytes": self.max_store_bytes})
        _atomic_write(self.status_path, json.dumps(previous, sort_keys=True, indent=2).encode())

    # --------------------------------------------------------------- universe
    def _roots(self) -> list[tuple[Path, str]]:
        roots = [(self.runtime_root, "")]
        for name in LINKED_RESEARCH_DIRS:
            link = self.runtime_root / name
            try:
                target = link.resolve(strict=True)
                target.relative_to(self.volume_root)
            except (OSError, ValueError):
                continue
            if not target.is_dir():
                continue
            try:
                target.relative_to(self.runtime_root)
                continue
            except ValueError:
                roots.append((target, name))
        return roots

    def _allowed_name(self, name: str) -> bool:
        lower = name.lower()
        if name in self.rules["excluded_names"] or lower.endswith(self.rules.get("excluded_suffixes", ())):
            return False
        if lower.startswith(".env") or "secret" in lower or "credential" in lower:
            return False
        rotation = rotation_parts(name, self.rules["extensions"])
        if rotation is not None and rotation[0] in self.rules["excluded_names"]:
            return False
        return Path(name).suffix.lower() in self.rules["extensions"] or rotation is not None

    def scan(self) -> dict[str, tuple[Path, os.stat_result]]:
        # scandir entries carry the file type, so each file costs one stat();
        # the universe is tens of thousands of files and is rescanned per cycle.
        found = {}
        excluded_dirs = self.rules["excluded_dir_names"]
        state_dir = str(self.state_dir)
        path_globs = tuple(self.rules.get("excluded_path_globs", ()))
        # Windows DirEntry.stat() reports no inode/device, which checkpoints need.
        full_stat = os.name == "nt"
        for root, prefix in self._roots():
            pending = [(str(root), prefix)]
            while pending:
                directory, rel_dir = pending.pop()
                try:
                    with os.scandir(directory) as iterator:
                        entries = sorted(iterator, key=lambda entry: entry.name)
                except OSError:
                    continue
                subdirs = []
                for entry in entries:
                    name = entry.name
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            if (name.lower() not in excluded_dirs
                                    and os.path.realpath(entry.path) != state_dir):
                                subdirs.append((entry.path, f"{rel_dir}/{name}" if rel_dir else name))
                            continue
                    except OSError:
                        continue
                    if not self._allowed_name(name):
                        continue
                    try:
                        stat = os.stat(entry.path) if full_stat else entry.stat()
                    except OSError:
                        continue
                    relpath = f"{rel_dir}/{name}" if rel_dir else name
                    if any(fnmatch.fnmatchcase(relpath, pattern) for pattern in path_globs):
                        continue
                    try:
                        fmt.validate_relpath(relpath)
                    except fmt.SegmentFormatError:
                        continue
                    found[relpath] = (Path(entry.path), stat)
                pending.extend(reversed(subdirs))
        return found

    def _is_append_class(self, relpath: str) -> bool:
        name = relpath.rsplit("/", 1)[-1]
        return (Path(name).suffix.lower() in APPEND_SUFFIXES
                and rotation_parts(name, self.rules["extensions"]) is None)

    # --------------------------------------------------------------- planning
    def plan(self, state: dict, universe: dict) -> list[dict]:
        files = state["files"]
        tombstones = state.get("tombstones", {})
        ops, claimed = [], set()
        by_inode = {
            (int(stat.st_dev), int(stat.st_ino)): relpath
            for relpath, (_path, stat) in universe.items()
        }
        for relpath, tracked in sorted(files.items()):
            if tracked.get("class") != "append":
                continue
            current = universe.get(relpath)
            identity = (tracked["dev"], tracked["inode"])
            if current is not None and (int(current[1].st_dev), int(current[1].st_ino)) == identity:
                continue
            rotated = by_inode.get(identity)
            name = relpath.rsplit("/", 1)[-1]
            if (rotated and rotated not in files and rotated.startswith(relpath + ".")
                    and rotation_parts(rotated.rsplit("/", 1)[-1], self.rules["extensions"])
                    and rotation_parts(rotated.rsplit("/", 1)[-1], self.rules["extensions"])[0] == name):
                path, stat = universe[rotated]
                if int(stat.st_size) >= tracked["offset"]:
                    ops.append({"kind": fmt.KIND_SEAL, "stream": relpath, "path": rotated,
                                "source_path": relpath, "abs": path, "stat": stat,
                                "base_offset": tracked["offset"], "end_offset": int(stat.st_size),
                                "tracked": tracked, "bytes": int(stat.st_size) - tracked["offset"]})
                    claimed.add(rotated)
        sealed_sources = {op["source_path"] for op in ops}
        for relpath, tracked in sorted(files.items()):
            if relpath not in universe and relpath not in sealed_sources:
                ops.append({"kind": fmt.KIND_TOMBSTONE, "stream": relpath, "path": relpath,
                            "tracked": tracked, "bytes": 0})
        for relpath, (path, stat) in sorted(universe.items()):
            if relpath in claimed:
                continue
            tracked = files.get(relpath) if relpath not in sealed_sources else None
            try:
                op = self._plan_entry(relpath, path, stat, tracked, tombstones)
            except FileNotFoundError:
                # Deleted after the scan: the next scan tombstones or ignores it.
                continue
            except PlanRace:
                # A file rewritten between the scan and its anchor hash backs
                # off alone; every other stream still plans and ships.
                self._back_off(relpath)
                continue
            if op is not None:
                ops.append(op)
        ops.sort(key=lambda item: (item["stream"], _RANK.get(item["kind"], 2), item["path"]))
        return ops

    def _plan_entry(self, relpath: str, path: Path, stat: os.stat_result, tracked: dict | None,
                    tombstones: dict) -> dict | None:
        size = int(stat.st_size)
        op = None
        if self._is_append_class(relpath):
            if tracked is None:
                kind = fmt.KIND_REWRITE if relpath in tombstones else fmt.KIND_APPEND
                end = _complete_record_size(path, size)
                if kind == fmt.KIND_REWRITE or end > 0:
                    op = {"kind": kind, "base_offset": 0, "end_offset": end}
            elif (int(stat.st_dev), int(stat.st_ino)) != (tracked["dev"], tracked["inode"]) \
                    or size < tracked["offset"]:
                op = {"kind": fmt.KIND_REWRITE, "base_offset": 0,
                      "end_offset": _complete_record_size(path, size)}
            elif (size, int(stat.st_mtime_ns)) != (tracked["size"], tracked["mtime_ns"]) \
                    or tracked["offset"] < size:
                if _anchors(path, tracked["offset"]) != (tracked["head_sha256"], tracked["tail_sha256"]):
                    op = {"kind": fmt.KIND_REWRITE, "base_offset": 0,
                          "end_offset": _complete_record_size(path, size)}
                else:
                    end = _complete_record_size(path, size)
                    if end > tracked["offset"]:
                        op = {"kind": fmt.KIND_APPEND, "base_offset": tracked["offset"],
                              "end_offset": end}
            if op is not None:
                op["bytes"] = op["end_offset"] - op["base_offset"]
        else:
            changed = tracked is None or tracked.get("class") != "snapshot" or (
                size, int(stat.st_mtime_ns), int(stat.st_ino)
            ) != (tracked["size"], tracked["mtime_ns"], tracked["inode"]) or (
                "wal" in tracked and tracked["wal"] != _wal_signature(path))
            # Large, continuously mutating snapshots (e.g. research.db) are
            # re-shipped at most once per interval to bound bucket growth.
            interval = (self.huge_snapshot_interval if size > self.max_member_bytes
                        else self.large_snapshot_interval)
            if (changed and tracked is not None and size > self.large_snapshot_bytes
                    and self.clock() - float(tracked.get("shipped_at") or 0.0)
                    < interval):
                self.throttled.append(relpath)
                changed = False
            if changed:
                kind = (fmt.KIND_REWRITE if tracked is None and relpath in tombstones
                        else fmt.KIND_SNAPSHOT)
                op = {"kind": kind, "bytes": size}
        if op is not None:
            op.update({"stream": relpath, "path": relpath, "abs": path, "stat": stat,
                       "tracked": tracked})
            if op["kind"] == fmt.KIND_APPEND:
                self._clamp_append(op)
            # Only a real SQLite file can be backed up online; anything else
            # named *.db keeps the plain stable-copy path.
            op["sqlite"] = (_is_sqlite_db(relpath) and not self._is_append_class(relpath)
                            and _has_sqlite_header(path))
            # Flagged here, not in select, so every oversized path stays
            # visible in status even when a budget break ends selection first.
            op["oversized"] = op["bytes"] > self._member_cap(op)
        return op

    def _member_cap(self, op: dict) -> int:
        if self._sqlite_snapshot_op(op):
            return self.max_sqlite_bytes
        return self.max_member_bytes

    def _sqlite_snapshot_op(self, op: dict) -> bool:
        return op["kind"] in (fmt.KIND_SNAPSHOT, fmt.KIND_REWRITE) and bool(op.get("sqlite"))

    def _clamp_append(self, op: dict) -> None:
        """Split large appends at a record boundary so they fit one segment."""
        op["pending_bytes"] = op["end_offset"] - op["base_offset"]
        if op["pending_bytes"] <= self.segment_budget:
            return
        limit = op["base_offset"] + self.segment_budget
        boundary = _complete_record_size(op["abs"], limit)
        if boundary > op["base_offset"]:
            op["end_offset"] = boundary
            op["bytes"] = boundary - op["base_offset"]

    def select(self, ops: list[dict], cursor: str = "") -> tuple[list[dict], int]:
        """Keep a dependency-safe subset of ``ops`` within the byte budget.

        Ops are ordered per stream (SEAL, TOMBSTONE, then content). An
        oversized op blocks only the rest of its own stream; hitting the
        segment budget stops selection entirely, so no op ever ships ahead of
        an op it depends on.

        Selection starts at ``cursor``, the stream that last hit the budget,
        and wraps around. Otherwise a snapshot larger than the remaining
        budget would be starved forever by alphabetically earlier streams
        that grow every cycle. Streams never depend on each other, and the
        per-stream order is preserved by the stable sort.
        """
        if cursor:
            ops = sorted(ops, key=lambda item: item["stream"] < cursor)
        self.next_cursor = ""
        now = self.clock()
        blocked = {stream for stream, (_count, until) in self.race_backoff.items() if until > now}
        selected, total, deferred = [], 0, 0
        for index, op in enumerate(ops):
            pending = op.get("pending_bytes", op["bytes"])
            if op["stream"] in blocked:
                deferred += pending
                continue
            if op.get("oversized") or op["bytes"] > self._member_cap(op):
                op["oversized"] = True
                blocked.add(op["stream"])
                deferred += pending
                continue
            # A SQLite snapshot always travels alone so its backup can be
            # streamed from disk instead of held in memory.
            if selected and (total + op["bytes"] > self.segment_budget
                             or self._sqlite_snapshot_op(op)):
                deferred += sum(item.get("pending_bytes", item["bytes"]) for item in ops[index:])
                self.next_cursor = op["stream"]
                break
            selected.append(op)
            total += op["bytes"]
            deferred += pending - op["bytes"]
            if self._sqlite_snapshot_op(op):
                later = ops[index + 1:]
                deferred += sum(item.get("pending_bytes", item["bytes"]) for item in later)
                if later:
                    self.next_cursor = later[0]["stream"]
                break
        return selected, deferred

    # ---------------------------------------------------------------- reading
    def _read(self, op: dict) -> tuple[bytes, dict]:
        path, stat = op.get("abs"), op.get("stat")
        kind = op["kind"]
        if kind == fmt.KIND_TOMBSTONE:
            tracked = op["tracked"]
            return b"", {"last_sha256": tracked.get("sha256"), "last_offset": tracked.get("offset")}
        atomic_file = (
            (kind == fmt.KIND_SNAPSHOT
             or (kind == fmt.KIND_REWRITE and not self._is_append_class(op["path"])))
            and not _is_sqlite(op["path"])
        )
        with path.open("rb") as handle:
            live = os.fstat(handle.fileno())
            # A whole-file JSON receipt is replaced by atomic rename on every
            # ledger append; the new inode is a complete newer version, so it
            # ships as-is. Append-class and SQLite identity changes still race.
            if ((int(live.st_dev), int(live.st_ino)) != (int(stat.st_dev), int(stat.st_ino))
                    and not atomic_file):
                raise PlanRace(f"{op['path']} changed identity", op["stream"])
            if kind in (fmt.KIND_SNAPSHOT,) or (kind == fmt.KIND_REWRITE and not self._is_append_class(op["path"])):
                # Stability is required only during the copy itself: a hot
                # file (research.db) is always written between scan and read.
                for _attempt in range(SNAPSHOT_COPY_ATTEMPTS):
                    handle.seek(0)
                    before = os.fstat(handle.fileno())
                    raw = handle.read()
                    after = os.fstat(handle.fileno())
                    if ((int(after.st_size), int(after.st_mtime_ns))
                            == (int(before.st_size), int(before.st_mtime_ns))
                            and len(raw) == int(after.st_size)):
                        break
                else:
                    raise PlanRace(f"{op['path']} changed while copying", op["stream"])
                op["stat"] = after
                extra = {"consistency": "raw_stable_copy" if _is_sqlite(op["path"]) else "atomic_file"}
                return raw, extra
            if kind == fmt.KIND_SEAL:
                whole = handle.read()
                if len(whole) != op["end_offset"]:
                    raise PlanRace(f"{op['path']} changed while sealing", op["stream"])
                tracked = op["tracked"]
                head, tail = _anchors_from_bytes(
                    whole[:min(ANCHOR_BYTES, tracked["offset"])],
                    whole[max(0, tracked["offset"] - ANCHOR_BYTES):tracked["offset"]],
                )
                if (head, tail) != (tracked["head_sha256"], tracked["tail_sha256"]):
                    raise PlanRace(f"{op['path']} rotated file prefix does not match shipped bytes", op["stream"])
                return whole[op["base_offset"]:], {
                    "final_size": len(whole), "final_sha256": hashlib.sha256(whole).hexdigest(),
                }
            handle.seek(op["base_offset"])
            raw = handle.read(op["end_offset"] - op["base_offset"])
            if len(raw) != op["end_offset"] - op["base_offset"]:
                raise PlanRace(f"{op['path']} shrank while reading", op["stream"])
            return raw, {}

    # --------------------------------------------------------------- building
    def build(self, state: dict, selected: list[dict]) -> tuple[bytes, bytes, dict]:
        new_state = json.loads(json.dumps(state))
        files, tombstones = new_state["files"], new_state.setdefault("tombstones", {})
        payloads, members = [], []
        seq = int(state["seq"]) + 1
        generation_of = lambda rel: int((files.get(rel) or tombstones.get(rel) or {}).get("generation", 0))
        try:
            return self._build(state, selected, new_state, files, tombstones, payloads, members,
                               seq, generation_of)
        finally:
            self._clear_sqlite_scratch()

    def _snapshot_sqlite(self, op: dict) -> tuple[Path, int, str]:
        """Consistent online backup of a live SQLite DB into shipper scratch."""
        path = op["abs"]
        live = path.stat()
        if (int(live.st_dev), int(live.st_ino)) != (int(op["stat"].st_dev), int(op["stat"].st_ino)):
            raise PlanRace(f"{op['path']} changed identity", op["stream"])
        target = self.sqlite_scratch / f"{uuid.uuid4().hex}.db"
        try:
            sqlite_online_backup(path, target, deadline_seconds=self.sqlite_backup_deadline)
        except (TimeoutError, sqlite3.Error) as exc:
            target.unlink(missing_ok=True)
            raise PlanRace(f"{op['path']} online backup not completed: {exc}", op["stream"]) from exc
        op["stat"] = live
        op["wal"] = _wal_signature(path)
        size = target.stat().st_size
        if size > self.max_sqlite_bytes:
            target.unlink(missing_ok=True)
            raise PlanRace(f"{op['path']} backup {size} exceeds the SQLite cap", op["stream"])
        digest = hashlib.sha256()
        with target.open("rb") as handle:
            for chunk in iter(lambda: handle.read(READ_CHUNK), b""):
                digest.update(chunk)
        return target, size, digest.hexdigest()

    def _build(self, state, selected, new_state, files, tombstones, payloads, members, seq,
               generation_of) -> tuple[bytes, bytes, dict]:
        snapshot_file = None
        # A racing stream is skipped in place (with any later ops of the same
        # stream, which depend on it) so one hot file costs neither a rebuild
        # nor the rest of the segment.
        self.build_raced = []
        skipped: set[str] = set()
        shipped_ops: list[dict] = []
        for op in selected:
            relpath, kind = op["path"], op["kind"]
            if op["stream"] in skipped:
                continue
            try:
                try:
                    if self._sqlite_snapshot_op(op):
                        if len(selected) != 1:
                            raise RuntimeError("a SQLite snapshot must be the only segment member")
                        snapshot_file, size, digest = self._snapshot_sqlite(op)
                        raw, extra = b"", {"consistency": SQLITE_CONSISTENCY}
                    else:
                        raw, extra = self._read(op)
                        size, digest = len(raw), fmt.sha256_bytes(raw)
                        if kind == fmt.KIND_APPEND and self._is_append_class(relpath):
                            try:
                                op["anchors"] = _anchors(op["abs"], op["end_offset"])
                            except PlanRace as exc:
                                raise PlanRace(str(exc), op["stream"]) from exc
                except FileNotFoundError as exc:
                    raise PlanRace(f"{op['path']} vanished before it was read", op["stream"]) from exc
            except PlanRace as exc:
                if exc.stream is None:
                    raise
                skipped.add(op["stream"])
                self.build_raced.append(exc)
                continue
            shipped_ops.append(op)
            member = {"index": len(members), "kind": kind, "path": relpath,
                      "size": size, "sha256": digest}
            if kind in (fmt.KIND_APPEND, fmt.KIND_SEAL):
                member["base_offset"] = op["base_offset"]
                member["end_offset"] = op["base_offset"] + len(raw)
            if kind == fmt.KIND_SEAL:
                member["source_path"] = op["source_path"]
                member.update(extra)
            elif kind == fmt.KIND_TOMBSTONE:
                member.update({key: value for key, value in extra.items() if value is not None})
            elif kind == fmt.KIND_SNAPSHOT:
                member["consistency"] = extra["consistency"]
            elif kind == fmt.KIND_REWRITE:
                member["generation"] = generation_of(relpath) + 1
                if "consistency" in extra:
                    member["consistency"] = extra["consistency"]
            members.append(member)
            payloads.append(raw)
            stat = op.get("stat")
            if kind == fmt.KIND_TOMBSTONE:
                tombstones[relpath] = {**op["tracked"], "tombstoned": True}
                files.pop(relpath, None)
            elif kind == fmt.KIND_SEAL:
                files.pop(op["source_path"], None)
                files[relpath] = {"class": "snapshot", "size": extra["final_size"],
                                  "mtime_ns": int(stat.st_mtime_ns), "inode": int(stat.st_ino),
                                  "dev": int(stat.st_dev), "sha256": extra["final_sha256"],
                                  "generation": 0, "shipped_seq": seq}
            elif self._is_append_class(relpath):
                end = op["end_offset"]
                if kind == fmt.KIND_REWRITE:
                    head, tail = _anchors_from_bytes(raw[:ANCHOR_BYTES], raw[max(0, end - ANCHOR_BYTES):end])
                else:
                    head, tail = op.get("anchors") or _anchors(op["abs"], end)
                files[relpath] = {"class": "append", "offset": end, "size": int(stat.st_size),
                                  "mtime_ns": int(stat.st_mtime_ns), "inode": int(stat.st_ino),
                                  "dev": int(stat.st_dev), "head_sha256": head, "tail_sha256": tail,
                                  "generation": member.get("generation", generation_of(relpath))}
                tombstones.pop(relpath, None)
            else:
                files[relpath] = {"class": "snapshot", "size": size,
                                  "mtime_ns": int(stat.st_mtime_ns), "inode": int(stat.st_ino),
                                  "dev": int(stat.st_dev), "sha256": member["sha256"],
                                  "generation": member.get("generation", generation_of(relpath)),
                                  "shipped_at": self.clock(), "shipped_seq": seq}
                if snapshot_file is not None:
                    # Change detection compares the live source, not the backup.
                    files[relpath].update({"size": int(stat.st_size), "snapshot_size": size,
                                           "wal": op["wal"]})
                tombstones.pop(relpath, None)
        if not members:
            raise self.build_raced[-1]
        if snapshot_file is not None:
            segment_raw = fmt.build_segment_from_file(snapshot_file, members[0]["size"])
        else:
            segment_raw = fmt.build_segment(payloads)
        # The window is derived from source mtimes, not the wall clock, so a
        # rebuild over the same bytes yields a byte-identical manifest.
        previous_end = float(state.get("last_window_end") or 0.0)
        mtimes = [op["stat"].st_mtime_ns / 1e9 for op in shipped_ops if op.get("stat") is not None]
        window_end = max([previous_end, *mtimes])
        manifest = fmt.build_manifest(
            prefix=self.prefix, seq=seq, prev_manifest_sha256=state["last_manifest_sha256"],
            segment_raw=segment_raw, members=members, source_git_rev=self.source_git_rev,
            collection_epoch_id=self._epoch_id(), window_start=previous_end,
            window_end=window_end,
        )
        manifest_raw = fmt.canonical_json(manifest)
        new_state.update({"seq": seq, "last_manifest_sha256": fmt.sha256_bytes(manifest_raw),
                          "last_window_end": manifest["window_end"],
                          "last_segment_at": self.clock(),
                          "select_cursor": self.next_cursor,
                          "store_bytes": int(state.get("store_bytes") or 0)
                          + len(segment_raw) + len(manifest_raw)})
        return segment_raw, manifest_raw, new_state

    # ---------------------------------------------------------------- genesis
    def _ships_at_genesis(self, relpath: str, path: Path) -> bool:
        """Current state travels with the new epoch; historical records do not.

        Top-level runtime files are the bot's live state and reports; SQLite
        databases ship as consistent online backups. Rotations, append-stream
        prefixes and per-record directories are baselined instead.
        """
        name = relpath.rsplit("/", 1)[-1]
        if rotation_parts(name, self.rules["extensions"]) is not None:
            return False
        if _is_sqlite_db(relpath) and _has_sqlite_header(path):
            return True
        return "/" not in relpath

    def build_genesis(self, state: dict, universe: dict) -> tuple[bytes, bytes, dict]:
        new_state = json.loads(json.dumps(state))
        files = new_state["files"]
        members, payloads, append_stats = [], [], []
        baselined_bytes, tracked_only = 0, 0
        for relpath, (path, stat) in sorted(universe.items()):
            if self._is_append_class(relpath):
                offset = _complete_record_size(path, int(stat.st_size))
                source_sha = _sha256_file_range(path, 0, offset)
                head, tail = _anchors(path, offset)
                preamble = b""
                if offset and path.suffix.lower() == ".csv":
                    with path.open("rb") as handle:
                        first = handle.readline(min(offset, READ_CHUNK))
                    preamble = first if first.endswith(b"\n") else b""
                after = path.stat()
                if ((int(after.st_dev), int(after.st_ino)) != (int(stat.st_dev), int(stat.st_ino))
                        or int(after.st_size) < offset):
                    raise PlanRace(f"{relpath} rotated while baselining", relpath)
                members.append({"index": len(members), "kind": fmt.KIND_BASELINE, "path": relpath,
                                "size": len(preamble), "sha256": fmt.sha256_bytes(preamble),
                                "base_offset": offset, "source_sha256": source_sha,
                                "source_size": int(stat.st_size)})
                payloads.append(preamble)
                files[relpath] = {"class": "append", "offset": offset, "size": int(stat.st_size),
                                  "mtime_ns": int(stat.st_mtime_ns), "inode": int(stat.st_ino),
                                  "dev": int(stat.st_dev), "head_sha256": head, "tail_sha256": tail,
                                  "generation": 0, "baseline_offset": offset}
                append_stats.append(stat)
                baselined_bytes += offset
            elif not self._ships_at_genesis(relpath, path):
                files[relpath] = {"class": "snapshot", "size": int(stat.st_size),
                                  "mtime_ns": int(stat.st_mtime_ns), "inode": int(stat.st_ino),
                                  "dev": int(stat.st_dev), "generation": 0, "shipped_at": 0.0,
                                  "baseline": True}
                baselined_bytes += int(stat.st_size)
                tracked_only += 1
        segment_raw = fmt.build_segment(payloads)
        window_end = max([0.0, *(item.st_mtime_ns / 1e9 for item in append_stats)])
        manifest = fmt.build_manifest(
            prefix=self.prefix, seq=1, prev_manifest_sha256=state["last_manifest_sha256"],
            segment_raw=segment_raw, members=members, source_git_rev=self.source_git_rev,
            collection_epoch_id=self._epoch_id(), window_start=0.0, window_end=window_end,
        )
        manifest_raw = fmt.canonical_json(manifest)
        new_state.update({"seq": 1, "last_manifest_sha256": fmt.sha256_bytes(manifest_raw),
                          "last_window_end": manifest["window_end"],
                          "last_segment_at": self.clock(), "select_cursor": "",
                          "store_bytes": int(state.get("store_bytes") or 0)
                          + len(segment_raw) + len(manifest_raw),
                          "baseline": {"seq": 1, "created_at": self.clock(),
                                       "append_streams": len(members),
                                       "tracked_only_files": tracked_only,
                                       "baselined_bytes": baselined_bytes}})
        return segment_raw, manifest_raw, new_state

    def _epoch_id(self) -> str:
        try:
            session = json.loads((self.runtime_root / "research_session.json").read_text("utf-8"))
            return str(session.get("collector_v22_epoch_id") or "")
        except (OSError, json.JSONDecodeError, AttributeError):
            return ""

    # ------------------------------------------------------------ intent flow
    def write_intent(self, segment_raw: bytes, manifest_raw: bytes, new_state: dict) -> dict:
        self.intent_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write(self.intent_dir / "segment.tar.gz", segment_raw)
        _atomic_write(self.intent_dir / "manifest.json", manifest_raw)
        intent = {"schema": INTENT_SCHEMA, "seq": new_state["seq"], "prefix": self.prefix,
                  "segment_sha256": fmt.sha256_bytes(segment_raw),
                  "manifest_sha256": fmt.sha256_bytes(manifest_raw), "new_state": new_state}
        _atomic_write(self.intent_dir / "intent.json", fmt.canonical_json(intent))
        return intent

    def _put_once(self, key: str, raw: bytes, content_type: str) -> str:
        digest = fmt.sha256_bytes(raw)
        try:
            self.store.put_if_absent(key, raw, sha256=digest, content_type=content_type)
            return "CREATED"
        except PreconditionFailed:
            existing = self.store.head_sha256(key)
            if existing == digest:
                return "ALREADY_PRESENT"
            raise ShipperConflict(f"{key} exists with different content; refusing to overwrite")

    def complete_intent(self) -> dict | None:
        intent_path = self.intent_dir / "intent.json"
        if not intent_path.is_file():
            return None
        intent = json.loads(intent_path.read_text(encoding="utf-8"))
        if intent.get("schema") != INTENT_SCHEMA or intent.get("prefix") != self.prefix:
            raise RuntimeError("intent schema/prefix mismatch; refusing to continue")
        segment_raw = (self.intent_dir / "segment.tar.gz").read_bytes()
        manifest_raw = (self.intent_dir / "manifest.json").read_bytes()
        if (fmt.sha256_bytes(segment_raw) != intent["segment_sha256"]
                or fmt.sha256_bytes(manifest_raw) != intent["manifest_sha256"]):
            raise RuntimeError("intent spool is corrupt; refusing to upload")
        seq = int(intent["seq"])
        committed = int(self.load_state()["seq"])
        if seq not in (committed, committed + 1):
            raise RuntimeError(f"stale intent seq {seq} vs checkpoint {committed}; refusing")
        segment_result = self._put_once(fmt.segment_key(self.prefix, seq), segment_raw,
                                        "application/gzip")
        manifest_result = self._put_once(fmt.manifest_key(self.prefix, seq), manifest_raw,
                                         "application/json")
        _atomic_write(self.state_path, json.dumps(intent["new_state"], sort_keys=True).encode())
        for name in ("intent.json", "segment.tar.gz", "manifest.json"):
            (self.intent_dir / name).unlink(missing_ok=True)
        return {"seq": seq, "segment": segment_result, "manifest": manifest_result,
                "segment_bytes": len(segment_raw)}

    # ------------------------------------------------------------------ cycle
    def cycle(self) -> dict:
        recovered = self.complete_intent()
        state = self.load_state()
        store_bytes = int(state.get("store_bytes") or 0)
        if self.max_store_bytes and store_bytes >= self.max_store_bytes:
            # Fail closed: the store duplicates source data until pruning
            # exists, so it must never grow into the trading volume's headroom.
            self.write_status(shipped_seq=state["seq"], store_bytes=store_bytes,
                              last_error="STORE_CAP_REACHED",
                              last_segment_at=state.get("last_segment_at"))
            return {"shipped": None, "recovered": recovered, "deferred_bytes": 0,
                    "store_cap_reached": True}
        self.throttled = []
        if self.baseline_genesis and int(state["seq"]) == 0 and not state["files"]:
            try:
                segment_raw, manifest_raw, new_state = self.build_genesis(state, self.scan())
            except PlanRace as exc:
                self.write_status(shipped_seq=0, last_error=f"PLAN_RACE: {exc}")
                return {"shipped": None, "recovered": recovered, "deferred_bytes": 0,
                        "race": exc.stream or "genesis"}
            self.write_intent(segment_raw, manifest_raw, new_state)
            shipped = self.complete_intent()
            self.write_status(shipped_seq=1, unshipped_bytes=None, oversized_paths=[],
                              throttled_snapshots=[], racing_paths=[], last_error=None,
                              last_segment_at=new_state["last_segment_at"],
                              last_manifest_sha256=new_state["last_manifest_sha256"],
                              store_bytes=new_state["store_bytes"], baseline=new_state["baseline"])
            # The first delta cycle follows immediately.
            return {"shipped": shipped, "recovered": recovered, "deferred_bytes": 1,
                    "members": new_state["baseline"]["append_streams"], "genesis": True}
        self.boosted = self.boost_due(state)
        mode = {"backlog_mode": self.boosted, "segment_budget_bytes": self.segment_budget}
        universe = self.scan()
        # A stream that no longer exists can never ship to clear its backoff;
        # left in racing_paths it would block laptop promotion forever.
        for stream in [s for s in self.race_backoff if s not in universe]:
            del self.race_backoff[stream]
        ops = self.plan(state, universe)
        selected, deferred = self.select(ops, cursor=str(state.get("select_cursor") or ""))
        oversized = sorted(op["path"] for op in ops if op.get("oversized"))
        throttled = sorted(self.throttled)[:50]
        raced: list[str] = []
        new_state = None
        while selected:
            try:
                segment_raw, manifest_raw, new_state = self.build(state, selected)
            except PlanRace as exc:
                if exc.stream is None:
                    raise
                # Nothing was written: every selected stream raced.
                failure = exc
            else:
                failure = None
            # Racing streams are backed off and deferred; the rest of the build
            # (streams never depend on each other) ships in this same pass.
            race_streams = list(dict.fromkeys(item.stream for item in self.build_raced))
            for stream in race_streams:
                self._back_off(stream)
            raced.extend(race_streams)
            dropped = [op for op in selected if op["stream"] in race_streams]
            selected = [op for op in selected if op["stream"] not in race_streams]
            deferred += sum(op["bytes"] for op in dropped)
            if failure is None:
                break
            if not dropped or len(raced) > RACE_REBUILDS_PER_CYCLE:
                deferred += sum(op["bytes"] for op in selected)
                selected = []
            if not selected:
                self.last_deferred = deferred
                self.write_status(shipped_seq=state["seq"], unshipped_bytes=deferred,
                                  last_error=f"PLAN_RACE: {failure}", racing_paths=self.racing_paths(),
                                  **mode)
                return {"shipped": None, "recovered": recovered, "deferred_bytes": deferred,
                        "race": failure.stream, **mode}
        self.last_deferred = deferred
        if new_state is None:
            self.write_status(shipped_seq=state["seq"], unshipped_bytes=deferred,
                              oversized_paths=oversized[:50], throttled_snapshots=throttled,
                              racing_paths=self.racing_paths(), last_error=None, last_segment_at=state.get("last_segment_at"),
                              last_manifest_sha256=state["last_manifest_sha256"],
                              store_bytes=store_bytes, **mode)
            return {"shipped": None, "recovered": recovered, "deferred_bytes": deferred, **mode}
        self.write_intent(segment_raw, manifest_raw, new_state)
        shipped = self.complete_intent()
        for op in selected:
            self.race_backoff.pop(op["stream"], None)
        self.write_status(shipped_seq=new_state["seq"], unshipped_bytes=deferred,
                          oversized_paths=oversized[:50], throttled_snapshots=throttled,
                          racing_paths=self.racing_paths(),
                          last_error=None, last_segment_at=new_state["last_segment_at"],
                          last_manifest_sha256=new_state["last_manifest_sha256"],
                          store_bytes=new_state["store_bytes"], **mode)
        result = {"shipped": shipped, "recovered": recovered, "deferred_bytes": deferred,
                  "members": len(selected), **mode}
        if raced:
            result["race"] = raced[0]
        return result

    def _back_off(self, stream: str) -> None:
        count = self.race_backoff.get(stream, (0, 0.0))[0] + 1
        delay = min(RACE_BACKOFF_BASE_SECONDS * 2 ** (count - 1),
                    max(RACE_BACKOFF_BASE_SECONDS, self.large_snapshot_interval))
        self.race_backoff[stream] = (count, self.clock() + delay)

    def racing_paths(self) -> list[dict]:
        return [{"path": stream, "races": count, "retry_at": round(until, 3)}
                for stream, (count, until) in sorted(self.race_backoff.items())][:50]

    def poll_laptop_ack(self) -> int | None:
        state = self.load_state()
        known = int(state.get("laptop_acked_seq") or 0)
        start_after = fmt.ack_key(self.prefix, known) if known else ""
        keys = self.store.list_keys(fmt.ack_prefix(self.prefix), start_after=start_after)
        best = known
        for key in keys:
            token = key.rsplit("/", 1)[-1].removesuffix(".json")
            if token.isdigit() and int(token) > best:
                raw = self.store.get(key)
                if raw is None:
                    continue
                ack = fmt.parse_ack(raw)
                if ack["through_seq"] == int(token) and ack["through_seq"] <= int(state["seq"]):
                    best = ack["through_seq"]
        self.write_status(laptop_acked_seq=best)
        return best


def _set_priority(boosted: bool, boost_nice: int = DEFAULT_BOOST_NICE, tid: int = 0) -> str | None:
    """Idle class when caught up; a bounded nice level while draining a backlog.

    Linux applies both calls per thread; ``tid`` 0 is the calling thread.
    Returns an error string when the platform refuses the change (the worker
    then keeps whatever priority it has).
    """
    try:
        if boosted:
            os.sched_setscheduler(tid, os.SCHED_OTHER, os.sched_param(0))
            os.setpriority(os.PRIO_PROCESS, tid, max(boost_nice, 1))
        else:
            os.setpriority(os.PRIO_PROCESS, tid, IDLE_NICE)
            os.sched_setscheduler(tid, os.SCHED_IDLE, os.sched_param(0))
    except (AttributeError, OSError) as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


def _free_bytes(path: Path) -> int:
    try:
        stats = os.statvfs(path)
        return int(stats.f_bavail) * int(stats.f_frsize)
    except (AttributeError, OSError):
        return sys.maxsize


def _acquire_single_instance(state_dir: Path):
    handle = (state_dir / "shipper.lock").open("a+")
    try:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except ImportError:
        pass
    except OSError:
        handle.close()
        return None
    return handle


def _log(message: str) -> None:
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    print(f"[{stamp}] [segment-shipper] {message}", flush=True)


def sink_from_env(environ=None) -> str:
    env = os.environ if environ is None else environ
    return (env.get("RESEARCH_SEGMENTS_SINK") or "tigris").strip().lower()


def shipper_from_env(environ=None) -> SegmentShipper:
    env = os.environ if environ is None else environ
    volume = Path(env.get("BOT_DATA_DIR") or "/app/data")
    sink = sink_from_env(env)
    default_cap = VOLUME_DEFAULT_MAX_STORE_BYTES if sink == "volume" else 0
    return SegmentShipper(
        sink=sink,
        max_store_bytes=int(env.get("RESEARCH_SEGMENTS_VOLUME_MAX_BYTES") or default_cap),
        store=store_from_env(env), volume_root=volume, runtime_root=volume / "runtime",
        state_dir=Path(env.get("RESEARCH_SEGMENTS_STATE_DIR") or volume / "segment-shipper"),
        rules=load_selection_rules(),
        prefix=(env.get("RESEARCH_SEGMENTS_PREFIX") or "v1").strip(),
        max_segment_bytes=int(env.get("RESEARCH_SEGMENTS_MAX_SEGMENT_BYTES") or 8 * 1024 * 1024),
        max_member_bytes=int(env.get("RESEARCH_SEGMENTS_MAX_MEMBER_BYTES") or 64 * 1024 * 1024),
        source_git_rev=(env.get("SOURCE_GIT_REV") or "unknown").strip(),
        large_snapshot_bytes=int(env.get("RESEARCH_SEGMENTS_LARGE_SNAPSHOT_BYTES") or 1024 * 1024),
        large_snapshot_interval=float(env.get("RESEARCH_SEGMENTS_LARGE_SNAPSHOT_INTERVAL_SECONDS") or 3600),
        max_sqlite_bytes=int(env.get("RESEARCH_SEGMENTS_MAX_SQLITE_BYTES") or 512 * 1024 * 1024),
        huge_snapshot_interval=float(env.get("RESEARCH_SEGMENTS_HUGE_SNAPSHOT_INTERVAL_SECONDS")
                                     or 6 * 3600),
        baseline_genesis=(env.get("RESEARCH_SEGMENTS_BASELINE_GENESIS") or "0").strip() == "1",
        backlog_boost_bytes=int(env.get("RESEARCH_SEGMENTS_BACKLOG_BOOST_BYTES")
                                or DEFAULT_BACKLOG_BOOST_BYTES),
        boost_segment_bytes=int(env.get("RESEARCH_SEGMENTS_BOOST_SEGMENT_BYTES")
                                or DEFAULT_BOOST_SEGMENT_BYTES),
    )


def run_guarded_cycle(shipper: "SegmentShipper", *, starved_after: float, on_starved) -> dict:
    """Run one cycle; call ``on_starved`` once if it outlives ``starved_after``."""
    timer = None
    if starved_after > 0:
        timer = threading.Timer(starved_after, on_starved)
        timer.daemon = True
        timer.start()
    try:
        return shipper.cycle()
    finally:
        if timer is not None:
            timer.cancel()


def main() -> int:
    if (os.getenv("RESEARCH_SEGMENTS_ENABLED") or "0").strip() != "1":
        _log("RESEARCH_SEGMENTS_ENABLED!=1 -> disabled")
        return 0
    boost_nice = int(os.getenv("RESEARCH_SEGMENTS_BOOST_NICE") or DEFAULT_BOOST_NICE)
    priority_boosted = False
    priority_error = _set_priority(False)
    interval = max(30.0, float(os.getenv("RESEARCH_SEGMENTS_INTERVAL_SECONDS") or 300))
    backlog_pause = max(1.0, float(os.getenv("RESEARCH_SEGMENTS_BACKLOG_PAUSE_SECONDS") or 5))
    ack_poll = max(60.0, float(os.getenv("RESEARCH_SEGMENTS_ACK_POLL_SECONDS") or 300))
    starved_after = float(os.getenv("RESEARCH_SEGMENTS_STARVED_CYCLE_SECONDS")
                          or DEFAULT_STARVED_CYCLE_SECONDS)
    main_tid = threading.get_native_id()
    default_floor = (VOLUME_DEFAULT_MIN_FREE_BYTES if sink_from_env() == "volume"
                     else DEFAULT_MIN_FREE_BYTES)
    min_free = int(os.getenv("RESEARCH_SEGMENTS_MIN_FREE_BYTES") or default_floor)
    try:
        shipper = shipper_from_env()
    except (RuntimeError, StoreError, OSError, ValueError) as exc:
        _log(f"configuration refused: {type(exc).__name__}: {exc}")
        return 2
    lock = _acquire_single_instance(shipper.state_dir)
    if lock is None:
        _log("another shipper holds the lock -> exiting")
        return 0
    _log(f"started prefix={shipper.prefix} sink={shipper.sink} interval={interval:.0f}s "
         f"max_store_bytes={shipper.max_store_bytes} min_free={min_free} pruning=OFF")
    last_ack_poll = 0.0

    def settle_mode() -> str:
        nonlocal priority_boosted, priority_error
        boost = shipper.boost_due()
        shipper.boosted = boost
        if boost != priority_boosted:
            priority_error = _set_priority(boost, boost_nice)
            priority_boosted = boost
            _log(f"backlog mode {'ON' if boost else 'OFF'} budget={shipper.segment_budget} "
                 f"priority={'nice ' + str(boost_nice) if boost else 'idle'}"
                 + (f" priority_error={priority_error}" if priority_error else ""))
        return "boost" if priority_boosted else "idle"

    def escalate_starved() -> None:
        nonlocal priority_boosted, priority_error
        priority_error = _set_priority(True, boost_nice, tid=main_tid)
        priority_boosted = True
        _log(f"idle cycle starved >{starved_after:.0f}s -> priority nice {boost_nice}"
             + (f" priority_error={priority_error}" if priority_error else ""))

    def poll_ack() -> None:
        nonlocal last_ack_poll
        if time.time() - last_ack_poll < ack_poll:
            return
        last_ack_poll = time.time()
        try:
            shipper.poll_laptop_ack()
        except Exception as exc:  # an ack read never blocks shipping
            _log(f"ack poll error: {type(exc).__name__}: {exc}")

    while True:
        pause = interval
        # Outside the cycle's try: a slow or failed cycle must not leave
        # laptop_acked_seq stale on /health.
        poll_ack()
        try:
            if _free_bytes(shipper.state_dir) < min_free:
                shipper.write_status(last_error="LOW_DISK_SKIPPED")
                _log("free space below floor -> cycle skipped")
            else:
                shipper.publish_mode("CYCLING", priority=settle_mode(),
                                     priority_error=priority_error)
                started = time.monotonic()
                result = run_guarded_cycle(
                    shipper, starved_after=0.0 if priority_boosted else starved_after,
                    on_starved=escalate_starved)
                if result.get("shipped"):
                    _log(f"shipped seq={result['shipped']['seq']} members={result['members']} "
                         f"bytes={result['shipped']['segment_bytes']} deferred={result['deferred_bytes']} "
                         f"budget={result.get('segment_budget_bytes')} cycle_s={time.monotonic() - started:.1f}")
                if result.get("race"):
                    _log(f"snapshot kept changing while copying, backing off: {result['race']}")
                if result.get("deferred_bytes"):
                    pause = backlog_pause
        except PlanRace as exc:
            try:
                shipper.write_status(last_error=f"PLAN_RACE: {exc}")
            except Exception:
                pass
            _log(f"file changed during cycle, retrying: {exc}")
            pause = backlog_pause
        except ShipperConflict as exc:
            shipper.write_status(last_error=f"CONFLICT: {exc}")
            _log(f"FAIL-CLOSED conflict: {exc}")
        except Exception as exc:  # keep the worker alive; never touch the bot
            try:
                shipper.write_status(last_error=f"{type(exc).__name__}: {exc}")
            except Exception:
                pass
            _log(f"cycle error: {type(exc).__name__}: {exc}")
        try:
            # Switch now so the sleep runs at, and the head reports, the next cycle's mode.
            shipper.publish_mode("SLEEPING", priority=settle_mode(), priority_error=priority_error,
                                 next_cycle_at=shipper.clock() + pause)
        except Exception as exc:
            _log(f"mode publish error: {type(exc).__name__}: {exc}")
        time.sleep(pause)


if __name__ == "__main__":
    sys.exit(main())
