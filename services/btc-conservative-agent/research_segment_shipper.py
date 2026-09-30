"""Low-priority Fly worker that ships sealed research-data segments (shadow mode).

Runs as its own process (started by fly-entrypoint.sh only when
``RESEARCH_SEGMENTS_ENABLED=1``). It never imports bot.py or Flask, never
takes the trade lock, never serves HTTP and never deletes or rewrites source
data: pruning is not implemented in this phase.

Each cycle:
1. Stat the same file universe the legacy inventory advertises.
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

import ast
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

import research_segment_format as fmt
from research_segment_store import ObjectStore, PreconditionFailed, StoreError, store_from_env

STATE_SCHEMA = "research_segment_shipper_state_v1"
STATUS_SCHEMA = "research_segment_shipper_status_v1"
INTENT_SCHEMA = "research_segment_shipper_intent_v1"
PRUNING_ENABLED = False

# The volume sink duplicates source data on the trading volume until pruning
# exists, so it carries a hard size cap and a much higher free-space floor.
VOLUME_DEFAULT_MAX_STORE_BYTES = 3 * 1024 ** 3
VOLUME_DEFAULT_MIN_FREE_BYTES = 4 * 1024 ** 3
DEFAULT_MIN_FREE_BYTES = 200 * 1024 * 1024

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


def _extract_frozenset(source: str, name: str) -> frozenset:
    match = re.search(rf"^{re.escape(name)}\s*=\s*frozenset\((\{{.*?\}})\)", source, re.M | re.S)
    if not match:
        raise RuntimeError(f"cannot locate {name} in bot.py")
    value = ast.literal_eval(match.group(1))
    if not isinstance(value, set) or not all(isinstance(item, str) for item in value):
        raise RuntimeError(f"{name} is not a set of strings")
    return frozenset(value)


def load_selection_rules(bot_source_path: Path) -> dict:
    """Read the inventory's selection sets from bot.py without importing it."""
    source = bot_source_path.read_text(encoding="utf-8", errors="replace")
    return {
        "extensions": _extract_frozenset(source, "_DATA_SYNC_EXTENSIONS"),
        "excluded_names": _extract_frozenset(source, "_DATA_SYNC_EXCLUDED_NAMES"),
        "excluded_dir_names": frozenset(
            name.lower() for name in _extract_frozenset(source, "_DATA_SYNC_EXCLUDED_DIR_NAMES")
        ),
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


class SegmentShipper:
    def __init__(
        self, *, store: ObjectStore, volume_root: Path, runtime_root: Path, state_dir: Path,
        rules: dict, prefix: str = "v1", max_segment_bytes: int = 8 * 1024 * 1024,
        max_member_bytes: int = 64 * 1024 * 1024, source_git_rev: str = "unknown",
        large_snapshot_bytes: int = 1024 * 1024, large_snapshot_interval: float = 3600.0,
        clock=time.time, sink: str = "tigris", max_store_bytes: int = 0,
    ):
        self.sink = sink
        self.max_store_bytes = max(0, int(max_store_bytes))
        self.large_snapshot_bytes = max(0, int(large_snapshot_bytes))
        self.large_snapshot_interval = max(0.0, float(large_snapshot_interval))
        self.throttled: list[str] = []
        self.next_cursor = ""
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
        self.state_dir.mkdir(parents=True, exist_ok=True)

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
        if name in self.rules["excluded_names"]:
            return False
        if lower.startswith(".env") or "secret" in lower or "credential" in lower:
            return False
        rotation = rotation_parts(name, self.rules["extensions"])
        if rotation is not None and rotation[0] in self.rules["excluded_names"]:
            return False
        return Path(name).suffix.lower() in self.rules["extensions"] or rotation is not None

    def scan(self) -> dict[str, tuple[Path, os.stat_result]]:
        found = {}
        excluded_dirs = self.rules["excluded_dir_names"]
        for root, prefix in self._roots():
            for directory, dirnames, filenames in os.walk(root, followlinks=False):
                current = Path(directory)
                dirnames[:] = sorted(
                    name for name in dirnames
                    if name.lower() not in excluded_dirs
                    and not (current / name).is_symlink()
                    and (current / name).resolve() != self.state_dir
                )
                for name in sorted(filenames):
                    path = current / name
                    if path.is_symlink() or not self._allowed_name(name):
                        continue
                    try:
                        stat = path.stat()
                    except OSError:
                        continue
                    relative = path.relative_to(root).as_posix()
                    relpath = f"{prefix}/{relative}" if prefix else relative
                    try:
                        fmt.validate_relpath(relpath)
                    except fmt.SegmentFormatError:
                        continue
                    found[relpath] = (path, stat)
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
                ) != (tracked["size"], tracked["mtime_ns"], tracked["inode"])
                # Large, continuously mutating snapshots (e.g. research.db) are
                # re-shipped at most once per interval to bound bucket growth.
                if (changed and tracked is not None and size > self.large_snapshot_bytes
                        and self.clock() - float(tracked.get("shipped_at") or 0.0)
                        < self.large_snapshot_interval):
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
                ops.append(op)
        ops.sort(key=lambda item: (item["stream"], _RANK.get(item["kind"], 2), item["path"]))
        return ops

    def _clamp_append(self, op: dict) -> None:
        """Split large appends at a record boundary so they fit one segment."""
        op["pending_bytes"] = op["end_offset"] - op["base_offset"]
        if op["pending_bytes"] <= self.max_segment_bytes:
            return
        limit = op["base_offset"] + self.max_segment_bytes
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
        selected, total, deferred, blocked = [], 0, 0, set()
        for index, op in enumerate(ops):
            pending = op.get("pending_bytes", op["bytes"])
            if op["stream"] in blocked:
                deferred += pending
                continue
            if op["bytes"] > self.max_member_bytes:
                op["oversized"] = True
                blocked.add(op["stream"])
                deferred += pending
                continue
            if selected and total + op["bytes"] > self.max_segment_bytes:
                deferred += sum(item.get("pending_bytes", item["bytes"]) for item in ops[index:])
                self.next_cursor = op["stream"]
                break
            selected.append(op)
            total += op["bytes"]
            deferred += pending - op["bytes"]
        return selected, deferred

    # ---------------------------------------------------------------- reading
    def _read(self, op: dict) -> tuple[bytes, dict]:
        path, stat = op.get("abs"), op.get("stat")
        kind = op["kind"]
        if kind == fmt.KIND_TOMBSTONE:
            tracked = op["tracked"]
            return b"", {"last_sha256": tracked.get("sha256"), "last_offset": tracked.get("offset")}
        with path.open("rb") as handle:
            live = os.fstat(handle.fileno())
            if (int(live.st_dev), int(live.st_ino)) != (int(stat.st_dev), int(stat.st_ino)):
                raise PlanRace(f"{op['path']} changed identity")
            if kind in (fmt.KIND_SNAPSHOT,) or (kind == fmt.KIND_REWRITE and not self._is_append_class(op["path"])):
                raw = handle.read()
                after = os.fstat(handle.fileno())
                if (int(after.st_size), int(after.st_mtime_ns)) != (int(stat.st_size), int(stat.st_mtime_ns)) \
                        or len(raw) != int(stat.st_size):
                    raise PlanRace(f"{op['path']} changed while copying")
                extra = {"consistency": "raw_stable_copy" if _is_sqlite(op["path"]) else "atomic_file"}
                return raw, extra
            if kind == fmt.KIND_SEAL:
                whole = handle.read()
                if len(whole) != op["end_offset"]:
                    raise PlanRace(f"{op['path']} changed while sealing")
                tracked = op["tracked"]
                head, tail = _anchors_from_bytes(
                    whole[:min(ANCHOR_BYTES, tracked["offset"])],
                    whole[max(0, tracked["offset"] - ANCHOR_BYTES):tracked["offset"]],
                )
                if (head, tail) != (tracked["head_sha256"], tracked["tail_sha256"]):
                    raise PlanRace(f"{op['path']} rotated file prefix does not match shipped bytes")
                return whole[op["base_offset"]:], {
                    "final_size": len(whole), "final_sha256": hashlib.sha256(whole).hexdigest(),
                }
            handle.seek(op["base_offset"])
            raw = handle.read(op["end_offset"] - op["base_offset"])
            if len(raw) != op["end_offset"] - op["base_offset"]:
                raise PlanRace(f"{op['path']} shrank while reading")
            return raw, {}

    # --------------------------------------------------------------- building
    def build(self, state: dict, selected: list[dict]) -> tuple[bytes, bytes, dict]:
        new_state = json.loads(json.dumps(state))
        files, tombstones = new_state["files"], new_state.setdefault("tombstones", {})
        payloads, members = [], []
        seq = int(state["seq"]) + 1
        generation_of = lambda rel: int((files.get(rel) or tombstones.get(rel) or {}).get("generation", 0))
        for op in selected:
            raw, extra = self._read(op)
            relpath, kind = op["path"], op["kind"]
            member = {"index": len(members), "kind": kind, "path": relpath,
                      "size": len(raw), "sha256": fmt.sha256_bytes(raw)}
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
                    head, tail = _anchors(op["abs"], end)
                files[relpath] = {"class": "append", "offset": end, "size": int(stat.st_size),
                                  "mtime_ns": int(stat.st_mtime_ns), "inode": int(stat.st_ino),
                                  "dev": int(stat.st_dev), "head_sha256": head, "tail_sha256": tail,
                                  "generation": member.get("generation", generation_of(relpath))}
                tombstones.pop(relpath, None)
            else:
                files[relpath] = {"class": "snapshot", "size": len(raw),
                                  "mtime_ns": int(stat.st_mtime_ns), "inode": int(stat.st_ino),
                                  "dev": int(stat.st_dev), "sha256": member["sha256"],
                                  "generation": member.get("generation", generation_of(relpath)),
                                  "shipped_at": self.clock(), "shipped_seq": seq}
                tombstones.pop(relpath, None)
        segment_raw = fmt.build_segment(payloads)
        # The window is derived from source mtimes, not the wall clock, so a
        # rebuild over the same bytes yields a byte-identical manifest.
        previous_end = float(state.get("last_window_end") or 0.0)
        mtimes = [op["stat"].st_mtime_ns / 1e9 for op in selected if op.get("stat") is not None]
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
        ops = self.plan(state, self.scan())
        selected, deferred = self.select(ops, cursor=str(state.get("select_cursor") or ""))
        oversized = sorted(op["path"] for op in ops if op.get("oversized"))
        throttled = sorted(self.throttled)[:50]
        if not selected:
            self.write_status(shipped_seq=state["seq"], unshipped_bytes=deferred,
                              oversized_paths=oversized[:50], throttled_snapshots=throttled,
                              last_error=None, last_segment_at=state.get("last_segment_at"),
                              last_manifest_sha256=state["last_manifest_sha256"],
                              store_bytes=store_bytes)
            return {"shipped": None, "recovered": recovered, "deferred_bytes": deferred}
        segment_raw, manifest_raw, new_state = self.build(state, selected)
        self.write_intent(segment_raw, manifest_raw, new_state)
        shipped = self.complete_intent()
        self.write_status(shipped_seq=new_state["seq"], unshipped_bytes=deferred,
                          oversized_paths=oversized[:50], throttled_snapshots=throttled,
                          last_error=None, last_segment_at=new_state["last_segment_at"],
                          last_manifest_sha256=new_state["last_manifest_sha256"],
                          store_bytes=new_state["store_bytes"])
        return {"shipped": shipped, "recovered": recovered, "deferred_bytes": deferred,
                "members": len(selected)}

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


def _lower_priority() -> None:
    try:
        os.nice(10)
    except (AttributeError, OSError):
        pass
    try:
        os.sched_setscheduler(0, os.SCHED_IDLE, os.sched_param(0))
    except (AttributeError, OSError):
        pass


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
    here = Path(__file__).resolve().parent
    sink = sink_from_env(env)
    default_cap = VOLUME_DEFAULT_MAX_STORE_BYTES if sink == "volume" else 0
    return SegmentShipper(
        sink=sink,
        max_store_bytes=int(env.get("RESEARCH_SEGMENTS_VOLUME_MAX_BYTES") or default_cap),
        store=store_from_env(env), volume_root=volume, runtime_root=volume / "runtime",
        state_dir=Path(env.get("RESEARCH_SEGMENTS_STATE_DIR") or volume / "segment-shipper"),
        rules=load_selection_rules(Path(env.get("RESEARCH_SEGMENTS_BOT_SOURCE") or here / "bot.py")),
        prefix=(env.get("RESEARCH_SEGMENTS_PREFIX") or "v1").strip(),
        max_segment_bytes=int(env.get("RESEARCH_SEGMENTS_MAX_SEGMENT_BYTES") or 8 * 1024 * 1024),
        max_member_bytes=int(env.get("RESEARCH_SEGMENTS_MAX_MEMBER_BYTES") or 64 * 1024 * 1024),
        source_git_rev=(env.get("SOURCE_GIT_REV") or "unknown").strip(),
        large_snapshot_bytes=int(env.get("RESEARCH_SEGMENTS_LARGE_SNAPSHOT_BYTES") or 1024 * 1024),
        large_snapshot_interval=float(env.get("RESEARCH_SEGMENTS_LARGE_SNAPSHOT_INTERVAL_SECONDS") or 3600),
    )


def main() -> int:
    if (os.getenv("RESEARCH_SEGMENTS_ENABLED") or "0").strip() != "1":
        _log("RESEARCH_SEGMENTS_ENABLED!=1 -> disabled")
        return 0
    _lower_priority()
    interval = max(30.0, float(os.getenv("RESEARCH_SEGMENTS_INTERVAL_SECONDS") or 300))
    backlog_pause = max(1.0, float(os.getenv("RESEARCH_SEGMENTS_BACKLOG_PAUSE_SECONDS") or 5))
    ack_poll = max(60.0, float(os.getenv("RESEARCH_SEGMENTS_ACK_POLL_SECONDS") or 1800))
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
    while True:
        pause = interval
        try:
            if _free_bytes(shipper.state_dir) < min_free:
                shipper.write_status(last_error="LOW_DISK_SKIPPED")
                _log("free space below floor -> cycle skipped")
            else:
                result = shipper.cycle()
                if result.get("shipped"):
                    _log(f"shipped seq={result['shipped']['seq']} members={result['members']} "
                         f"bytes={result['shipped']['segment_bytes']} deferred={result['deferred_bytes']}")
                if result.get("deferred_bytes"):
                    pause = backlog_pause
            if time.time() - last_ack_poll >= ack_poll:
                last_ack_poll = time.time()
                shipper.poll_laptop_ack()
        except PlanRace as exc:
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
        time.sleep(pause)


if __name__ == "__main__":
    sys.exit(main())
