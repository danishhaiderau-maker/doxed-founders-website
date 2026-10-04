"""Laptop puller that applies sealed research segments to a SHADOW mirror.

Usage (normally via scripts/research-segment-pull.ps1, which loads the
scoped laptop credentials from the local vault):

    python research_segment_puller.py --shadow-root C:\\DoxxedCrypto\\fly-mirror-segments \
        --archive-root C:\\DoxxedCrypto\\fly-segments

Guarantees:
* Segments are applied strictly in sequence; a missing ``seq`` stops the run
  (never skipped). Every manifest must chain to the previous one and every
  segment/member must match its sha256.
* Raw manifests and segments are archived create-new and never overwritten.
* Member application is idempotent, so a crash or sleep mid-segment is
  repaired by re-running: partial appends are truncated back to their base
  offset and re-applied.
* The ACK object (``acks/laptop/<seq>.json``) is written only after the
  applied state is durable, and is cumulative ("through seq N").
* The shadow tree must never be the canonical legacy mirror.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

import research_segment_format as fmt
import segment_custody
from research_segment_store import (HttpSegmentSource, ObjectStore, PreconditionFailed, StoreError,
                                    store_from_env)
from storage_links import ensure_private

PULLER_VERSION = "research_segment_puller_v1"
STATE_SCHEMA = "research_segment_puller_state_v1"
LEGACY_MIRROR_MARKER = "canonical-research-data"
_WINDOWS_RESERVED = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?$", re.I)
_WINDOWS_BAD_CHARS = set('<>:"|?*')


STATUS_SCHEMA = "research_segment_puller_status_v1"
ATTEMPT_OK, ATTEMPT_LOCK_BUSY, ATTEMPT_ERROR = "OK", "LOCK_BUSY", "ERROR"
# Carried over from the previous status/state on every non-OK attempt; a failed
# attempt must never erase what the shadow mirror has already applied and ACKed.
_PRESERVED_STATUS_FIELDS = ("applied_seq", "acked_seq", "fly_acked", "last_success_at")


class PullerError(RuntimeError):
    """Fail-closed verification or application error; nothing past it is applied."""


class LockBusyError(PullerError):
    """Another process (puller, promotion, parity, retention) holds the shadow-root lock."""

    def __init__(self, message: str, holder: dict | None = None):
        super().__init__(message)
        self.holder = holder


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _fsync_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("wb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _sha256_file(path: Path, limit: int | None = None) -> str:
    digest = hashlib.sha256()
    remaining = limit
    with path.open("rb") as handle:
        while remaining is None or remaining > 0:
            chunk = handle.read(1024 * 1024 if remaining is None else min(1024 * 1024, remaining))
            if not chunk:
                break
            digest.update(chunk)
            if remaining is not None:
                remaining -= len(chunk)
    return digest.hexdigest()


def refuse_unsafe_root(path: Path, label: str) -> Path:
    resolved = Path(os.path.abspath(path))
    lowered = str(resolved).replace("/", "\\").lower()
    if "\\onedrive\\" in lowered or lowered.endswith("\\onedrive"):
        raise PullerError(f"{label} refuses OneDrive path: {resolved}")
    if LEGACY_MIRROR_MARKER in {part.lower() for part in resolved.parts}:
        raise PullerError(f"{label} must never target the canonical legacy mirror: {resolved}")
    return resolved


def _holder_path(lock_path: Path) -> Path:
    return lock_path.with_name(lock_path.name + ".holder.json")


def read_lock_holder(lock_path: Path) -> dict | None:
    """Best-effort identity of the current lock holder (stale if the holder crashed)."""
    try:
        holder = json.loads(_holder_path(lock_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return holder if isinstance(holder, dict) else None


class _RunLock:
    """Exclusive per-shadow-root lock released automatically if the process dies.

    The holder identity lives in a sidecar file: the locked byte of the lock
    file itself is unreadable by other processes on Windows.
    """

    def __init__(self, path: Path, holder: str | None = None):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._handle.close()
            raise LockBusyError("another puller run holds the shadow-root lock",
                                holder=read_lock_holder(path)) from exc
        self._path = path
        label = holder or Path(sys.argv[0] or "python").stem or "python"
        try:
            _fsync_write(_holder_path(path), json.dumps(
                {"pid": os.getpid(), "holder": label, "acquired_at": _utc_now()}, sort_keys=True).encode())
        except OSError:
            pass

    def release(self) -> None:
        holder = read_lock_holder(self._path)
        if holder and holder.get("pid") == os.getpid():
            try:
                _holder_path(self._path).unlink()
            except OSError:
                pass
        try:
            if os.name == "nt":
                import msvcrt
                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        self._handle.close()


def _read_json_dict(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _last_fly_ack(meta: Path) -> int | None:
    """``through_seq`` of the newest ACK receipt Fly accepted, from the receipt log tail."""
    try:
        with (meta / "ack-receipts.jsonl").open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - 8192))
            lines = handle.read().splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            receipt = json.loads(line)
        except ValueError:
            continue
        if isinstance(receipt, dict) and receipt.get("ok") and isinstance(receipt.get("through_seq"), int):
            return receipt["through_seq"]
    return None


def last_known_progress(meta: Path) -> dict:
    """Last durable applied/ACK progress: state.json wins, the previous status fills gaps."""
    previous = _read_json_dict(meta / "status.json")
    state = _read_json_dict(meta / "state.json")
    known = {field: previous.get(field) for field in _PRESERVED_STATUS_FIELDS}
    for field in ("applied_seq", "acked_seq"):
        if isinstance(state.get(field), int):
            known[field] = state[field]
    fly_acked = _last_fly_ack(meta)
    if fly_acked is not None:
        known["fly_acked"] = fly_acked
    failures = previous.get("consecutive_failures")
    known["consecutive_failures"] = failures if isinstance(failures, int) and failures >= 0 else 0
    return known


def record_attempt(meta: Path, prefix: str, outcome: str, *, result: dict | None = None,
                   error: str | None = None, lock_holder: dict | None = None) -> dict:
    """Write status.json for one attempt without ever nulling known sequence numbers."""
    known = last_known_progress(meta)
    now = _utc_now()
    fields = {key: known[key] for key in _PRESERVED_STATUS_FIELDS}
    if outcome == ATTEMPT_OK:
        fields.update(result or {})
        fields.update(last_success_at=now, consecutive_failures=0, lock_holder=None)
    else:
        fields.update(consecutive_failures=known["consecutive_failures"] + 1, lock_holder=lock_holder)
    payload = {"schema": STATUS_SCHEMA, "updated_at": now, "puller_version": PULLER_VERSION,
               "prefix": prefix, "last_error": error, "last_attempt_at": now,
               "last_attempt_result": outcome, **fields}
    _fsync_write(meta / "status.json", json.dumps(payload, sort_keys=True, indent=2).encode())
    return payload


class SegmentPuller:
    def __init__(self, *, store: ObjectStore, shadow_root: Path, archive_root: Path,
                 prefix: str = "v1", write_ack: bool = True):
        self.store = store
        self.prefix = fmt.validate_prefix(prefix)
        self.shadow_root = refuse_unsafe_root(shadow_root, "shadow root")
        self.archive_root = refuse_unsafe_root(archive_root, "archive root") / self.prefix
        self.tree = self.shadow_root / "tree"
        self.meta = self.shadow_root / ".puller"
        self.quarantine = self.shadow_root / "quarantine"
        self.tombstones = self.shadow_root / "tombstones"
        self.state_path = self.meta / "state.json"
        self.status_path = self.meta / "status.json"
        self.write_ack = write_ack
        # relpath -> epoch baseline; persisted in state with each applied seq.
        self.baselines: dict[str, dict] = {}
        # ``relpath -> tombstone seq`` for the run in progress (set by pull_once).
        self.tombstoned: dict[str, int] = {}
        for directory in (self.tree, self.meta, self.archive_root / "seg", self.archive_root / "man"):
            directory.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ state
    def load_state(self) -> dict:
        if not self.state_path.is_file():
            return {"schema": STATE_SCHEMA, "prefix": self.prefix, "applied_seq": 0,
                    "last_manifest_sha256": fmt.GENESIS_PREV_SHA256, "acked_seq": 0}
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        if state.get("schema") != STATE_SCHEMA or state.get("prefix") != self.prefix:
            raise PullerError("puller state schema/prefix mismatch")
        return state

    def save_state(self, state: dict) -> None:
        _fsync_write(self.state_path, json.dumps(state, sort_keys=True, indent=2).encode())

    def write_status(self, outcome: str, **kwargs) -> dict:
        return record_attempt(self.meta, self.prefix, outcome, **kwargs)

    # ---------------------------------------------------------------- fetching
    def _archived(self, kind: str, key: str) -> Path:
        return self.archive_root / kind / key.rsplit("/", 1)[-1]

    def _fetch(self, kind: str, key: str) -> bytes | None:
        """Return archived bytes, else download and archive create-new."""
        archived = self._archived(kind, key)
        if archived.is_file():
            return archived.read_bytes()
        raw = self.store.get(key)
        if raw is None:
            return None
        return raw

    def _archive(self, kind: str, key: str, raw: bytes) -> None:
        archived = self._archived(kind, key)
        if archived.is_file():
            if archived.read_bytes() != raw:
                raise PullerError(f"archived {archived.name} differs from verified bytes")
            return
        temporary = archived.with_name(f".{archived.name}.{os.getpid()}.part")
        with temporary.open("wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, archived)
        except FileExistsError:
            if archived.read_bytes() != raw:
                raise PullerError(f"archived {archived.name} differs from verified bytes") from None
        finally:
            temporary.unlink(missing_ok=True)

    # ---------------------------------------------------------------- applying
    def _local(self, relpath: str) -> Path:
        fmt.validate_relpath(relpath)
        for part in relpath.split("/"):
            if (_WINDOWS_RESERVED.match(part) or part.endswith((".", " "))
                    or _WINDOWS_BAD_CHARS.intersection(part)):
                raise PullerError(f"path cannot be represented on Windows: {relpath!r}")
        return self.tree.joinpath(*relpath.split("/"))

    def _append(self, target: Path, base: int, end: int, payload: bytes, label: str) -> None:
        size = target.stat().st_size if target.exists() else 0
        if size == end and end > base and _sha256_file_range(target, base, end) == fmt.sha256_bytes(payload):
            return
        if size == end == base:
            return
        # Settled tree files may be hardlinked into the promotion view and the
        # canonical store; an in-place write must never reach those snapshots.
        if target.exists():
            ensure_private(target)
        if base < size < end:
            # Crash mid-apply left a partial tail: truncate back to the known
            # base offset and re-apply.
            with target.open("r+b") as handle:
                handle.truncate(base)
            size = base
        if size != base:
            raise PullerError(f"{label}: local size {size} != base_offset {base}")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("ab") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())

    def _quarantine_copy(self, seq: int, relpath: str, target: Path) -> None:
        if not target.exists():
            return
        destination = self.quarantine / fmt.seq_token(seq) / Path(*relpath.split("/"))
        if destination.exists():
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        _fsync_write(destination, target.read_bytes())

    def _to_local(self, relpath: str, remote_offset: int, label: str) -> int:
        """Map a Fly byte offset onto a stream that started at an epoch baseline."""
        baseline = self.baselines.get(relpath)
        if baseline is None:
            return remote_offset
        if remote_offset < baseline["base_offset"]:
            raise PullerError(f"{label}: offset {remote_offset} precedes the epoch baseline")
        return remote_offset - baseline["base_offset"] + baseline["preamble_size"]

    def _is_applied_seal(self, source_rel: str, target: Path, member: dict, label: str) -> bool:
        """True when ``target`` already holds this seal's result (re-apply after a crash)."""
        if source_rel in self.baselines:
            return target.stat().st_size == self._to_local(source_rel, member["final_size"], label)
        return (target.stat().st_size == member["final_size"]
                and _sha256_file(target) == member["final_sha256"])

    def apply_member(self, seq: int, member: dict, payload: bytes) -> None:
        kind, relpath = member["kind"], member["path"]
        target = self._local(relpath)
        label = f"seq {seq} member {member['index']} {kind} {relpath}"
        if kind == fmt.KIND_BASELINE:
            recorded = {"base_offset": member["base_offset"], "preamble_size": member["size"],
                        "source_sha256": member["source_sha256"], "seq": seq}
            if self.baselines.get(relpath) == recorded:
                return
            if target.exists():
                if target.stat().st_size != len(payload) or _sha256_file(target) != member["sha256"]:
                    raise PullerError(f"{label}: a new epoch needs a fresh tree; local file exists")
            else:
                _fsync_write(target, payload)
            self.baselines[relpath] = recorded
        elif kind == fmt.KIND_APPEND:
            self._append(target, self._to_local(relpath, member["base_offset"], label),
                         self._to_local(relpath, member["end_offset"], label), payload, label)
        elif kind == fmt.KIND_SEAL:
            source_rel = member["source_path"]
            source = self._local(source_rel)
            if relpath in self.tombstoned:
                # Sealing onto a name Fly retired earlier: the retired custody copy
                # (and any baseline it carried from an older epoch) belongs to a
                # previous generation, so it must not shape this seal. Keep its
                # bytes in quarantine; never delete custody data.
                self.baselines.pop(relpath, None)
                if target.exists() and not self._is_applied_seal(source_rel, target, member, label):
                    self._quarantine_copy(seq, relpath, target)
                    target.unlink()
            baseline = self.baselines.get(source_rel) or self.baselines.get(relpath)
            final_local = self._to_local(source_rel if source_rel in self.baselines else relpath,
                                         member["final_size"], label)
            if target.exists():
                if baseline is None and _sha256_file(target) != member["final_sha256"]:
                    raise PullerError(f"{label}: sealed file exists with different content")
                if baseline is not None and target.stat().st_size != final_local:
                    raise PullerError(f"{label}: sealed file exists with different size")
                if source_rel in self.baselines:
                    self.baselines[relpath] = self.baselines.pop(source_rel)
                return
            if not source.exists() and member["base_offset"] != 0:
                raise PullerError(f"{label}: active source missing for seal")
            self._append(source, self._to_local(source_rel, member["base_offset"], label),
                         self._to_local(source_rel, member["end_offset"], label), payload, label)
            if source.stat().st_size != final_local:
                raise PullerError(f"{label}: sealed content size mismatch")
            # The pre-baseline prefix never left Fly, so only a stream shipped
            # from byte 0 can be checked against the whole-file digest.
            if baseline is None and _sha256_file(source) != member["final_sha256"]:
                raise PullerError(f"{label}: sealed content sha256 mismatch")
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, target)
            if source_rel in self.baselines:
                self.baselines[relpath] = self.baselines.pop(source_rel)
        elif kind == fmt.KIND_SNAPSHOT:
            self.baselines.pop(relpath, None)
            if target.exists() and _sha256_file(target) == member["sha256"]:
                return
            _fsync_write(target, payload)
        elif kind == fmt.KIND_REWRITE:
            self.baselines.pop(relpath, None)
            if target.exists() and _sha256_file(target) == member["sha256"]:
                return
            self._quarantine_copy(seq, relpath, target)
            _fsync_write(target, payload)
        elif kind == fmt.KIND_TOMBSTONE:
            marker = self.tombstones / f"{fmt.seq_token(seq)}-{member['index']:06d}.json"
            if not marker.exists():
                _fsync_write(marker, fmt.canonical_json({"seq": seq, **member}))
        else:  # pragma: no cover - validate_manifest already rejects unknown kinds
            raise PullerError(f"{label}: unknown kind")

    # ------------------------------------------------------------------- run
    def backfill_tombstoned(self, state: dict) -> dict:
        """``relpath -> seq`` of still-tombstoned paths for a tree applied before the map existed.

        Replays the archived manifests (create-new, never overwritten) when every
        one of them is on disk; otherwise falls back to the tombstone markers,
        treating a path as re-created when its tree file is newer than the marker.
        """
        through = int(state.get("applied_seq") or 0)
        manifests = []
        for seq in range(1, through + 1):
            archived = self._archived("man", fmt.manifest_key(self.prefix, seq))
            try:
                manifests.append((seq, json.loads(archived.read_bytes())))
            except (OSError, ValueError):
                manifests = None
                break
        if manifests is not None:
            state["tombstoned_backfill"] = "ARCHIVED_MANIFESTS"
            return segment_custody.rebuild_tombstoned(manifests)
        tombstoned: dict = {}
        marker_mtime: dict = {}
        for marker in sorted(self.tombstones.glob("*.json")) if self.tombstones.is_dir() else []:
            try:
                doc = json.loads(marker.read_text(encoding="utf-8"))
                seq, path = int(doc["seq"]), str(doc["path"])
            except (OSError, ValueError, KeyError, TypeError):
                continue
            if seq > through or seq < tombstoned.get(path, 0):
                continue
            tombstoned[path], marker_mtime[path] = seq, marker.stat().st_mtime_ns
        for path in list(tombstoned):
            target = self.tree.joinpath(*path.split("/"))
            if target.is_file() and target.stat().st_mtime_ns > marker_mtime[path]:
                tombstoned.pop(path)
        state["tombstoned_backfill"] = "MARKERS_MTIME"
        return tombstoned

    def pull_once(self, max_segments: int | None = None, max_run_seconds: float | None = None) -> dict:
        state = self.load_state()
        self.baselines = {path: dict(entry) for path, entry in (state.get("baselines") or {}).items()}
        if segment_custody.STATE_KEY not in state:
            state[segment_custody.STATE_KEY] = (self.backfill_tombstoned(state)
                                                if int(state.get("applied_seq") or 0) else {})
            if int(state.get("applied_seq") or 0):
                self.save_state(state)
        tombstoned = state[segment_custody.STATE_KEY]
        self.tombstoned = tombstoned
        applied = 0
        # Checked only between segments (each applied seq is already durable in
        # state.json), and never before the first one so every run makes progress.
        started = time.monotonic()
        deadline = started + max_run_seconds if max_run_seconds else None
        deadline_reached = False
        while max_segments is None or applied < max_segments:
            if deadline is not None and applied and time.monotonic() >= deadline:
                deadline_reached = True
                break
            seq = int(state["applied_seq"]) + 1
            manifest_key = fmt.manifest_key(self.prefix, seq)
            manifest_raw = self._fetch("man", manifest_key)
            if manifest_raw is None:
                break
            manifest = fmt.parse_manifest(manifest_raw, prefix=self.prefix, expected_seq=seq)
            fmt.verify_chain_link(manifest, state["last_manifest_sha256"])
            segment_raw = self._fetch("seg", manifest["segment_key"])
            if segment_raw is None:
                raise PullerError(f"manifest {seq} is published but its segment is missing")
            payloads = fmt.verify_segment(manifest, segment_raw)
            self._archive("man", manifest_key, manifest_raw)
            self._archive("seg", manifest["segment_key"], segment_raw)
            for member, payload in zip(manifest["members"], payloads):
                self.apply_member(seq, member, payload)
                # Custody copies stay on disk; the map lets promotion tell a Fly-retired file from a live one.
                segment_custody.update_tombstoned(tombstoned, seq, member)
            state[segment_custody.STATE_KEY] = dict(sorted(tombstoned.items()))
            state.update({"applied_seq": seq, "last_manifest_sha256": fmt.sha256_bytes(manifest_raw),
                          "last_applied_at": _utc_now(),
                          "last_source_git_rev": manifest["source_git_rev"],
                          "last_collection_epoch_id": manifest["collection_epoch_id"]})
            if self.baselines or "baselines" in state:
                state["baselines"] = {path: dict(entry) for path, entry in sorted(self.baselines.items())}
            self.save_state(state)
            applied += 1
        if hasattr(self.store, "last_ack_response"):
            self.store.last_ack_response = None
        acked = self.ack(state)
        result = {"applied_now": applied, "applied_seq": state["applied_seq"], "acked_seq": acked}
        if deadline_reached:
            result["deadline_reached"] = True
        receipt = getattr(self.store, "last_ack_response", None)
        if receipt:
            result["ack_receipt"] = receipt
        self.write_status(ATTEMPT_OK, result={**result, "deadline_reached": deadline_reached,
                                              "run_seconds": round(time.monotonic() - started, 1),
                                              "max_run_seconds": max_run_seconds or 0})
        return result

    def ack(self, state: dict) -> int:
        through = int(state["applied_seq"])
        if not self.write_ack or through <= int(state.get("acked_seq") or 0):
            return int(state.get("acked_seq") or 0)
        raw = fmt.build_ack(through_seq=through, manifest_sha256=state["last_manifest_sha256"],
                            applied_at=state.get("last_applied_at") or _utc_now(),
                            verifier_version=PULLER_VERSION)
        key = fmt.ack_key(self.prefix, through)
        try:
            self.store.put_if_absent(key, raw, sha256=fmt.sha256_bytes(raw),
                                     content_type="application/json")
        except PreconditionFailed:
            existing = self.store.get(key)
            if existing is None:
                raise PullerError(f"ACK {key} was refused and no matching ACK exists; "
                                  "existing ACK disagrees with applied chain") from None
            ack = fmt.parse_ack(existing)
            if ack["through_seq"] != through or ack["manifest_sha256"] != state["last_manifest_sha256"]:
                raise PullerError(f"existing ACK {key} disagrees with applied chain") from None
        state["acked_seq"] = through
        self.save_state(state)
        receipt = getattr(self.store, "last_ack_response", None)
        if receipt:
            with (self.meta / "ack-receipts.jsonl").open("ab") as handle:
                handle.write(json.dumps({"logged_at": _utc_now(), **receipt}, sort_keys=True).encode() + b"\n")
                handle.flush()
                os.fsync(handle.fileno())
        return through


def _sha256_file_range(path: Path, start: int, end: int) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        handle.seek(start)
        remaining = end - start
        while remaining > 0:
            chunk = handle.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            digest.update(chunk)
            remaining -= len(chunk)
    return digest.hexdigest()


# The analyzer cycle's segment promotion holds the shadow-root lock for ~20-80 s
# while it stages the view. A pull that lands inside that window used to exit 2
# (LOCK_BUSY) at once, which reads as a failed pull on the watcher; wait it out.
DEFAULT_LOCK_WAIT_SEC = 150.0
LOCK_POLL_SEC = 2.0


def acquire_run_lock(path: Path, holder: str, wait_sec: float = 0.0, poll_sec: float = LOCK_POLL_SEC,
                     sleep=None, clock=None) -> "_RunLock":
    """Take the shadow-root run lock, retrying a busy lock for up to ``wait_sec``.

    Raises LockBusyError (with the current holder) once the wait is spent, so a lock
    that stays held longer than any promotion still surfaces as LOCK_BUSY / exit 2.
    """
    sleep = sleep or time.sleep
    clock = clock or time.monotonic
    deadline = clock() + max(0.0, float(wait_sec or 0.0))
    while True:
        try:
            return _RunLock(path, holder=holder)
        except LockBusyError:
            remaining = deadline - clock()
            if remaining <= 0:
                raise
            sleep(min(poll_sec, remaining))


def _remote_head_summary(store) -> dict:
    try:
        head = store.head()
    except (StoreError, ValueError) as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    return {key: head.get(key) for key in (
        "published_seq", "laptop_acked", "store_bytes", "max_store_bytes",
        "unshipped_bytes", "shipper_last_error", "pruning_enabled")}


def _record_failure(args, puller: SegmentPuller | None, exc: BaseException) -> dict:
    """Persist a non-OK attempt; returns the preserved progress for the stdout receipt."""
    outcome = ATTEMPT_LOCK_BUSY if isinstance(exc, LockBusyError) else ATTEMPT_ERROR
    try:
        if puller is not None:
            meta, prefix = puller.meta, puller.prefix
        else:
            meta, prefix = refuse_unsafe_root(Path(args.shadow_root), "shadow root") / ".puller", args.prefix
            if not meta.is_dir():
                return {"last_attempt_result": outcome}
        status = record_attempt(meta, prefix, outcome, error=f"{type(exc).__name__}: {exc}",
                                lock_holder=getattr(exc, "holder", None))
    except (OSError, PullerError):
        return {"last_attempt_result": outcome}
    return {key: status.get(key) for key in (*_PRESERVED_STATUS_FIELDS, "last_attempt_at",
                                             "last_attempt_result", "consecutive_failures", "lock_holder")}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--shadow-root", default=r"C:\DoxxedCrypto\fly-mirror-segments")
    parser.add_argument("--archive-root", default=r"C:\DoxxedCrypto\fly-segments")
    parser.add_argument("--prefix", default=os.getenv("RESEARCH_SEGMENTS_PREFIX") or "v1")
    parser.add_argument("--max-segments", type=int, default=None)
    parser.add_argument("--no-ack", action="store_true")
    parser.add_argument("--source", choices=("store", "http"),
                        default=os.getenv("RESEARCH_SEGMENTS_SOURCE") or "store",
                        help="store: bucket/local store from env; http: Fly volume-sink endpoint")
    parser.add_argument("--base-url", default=os.getenv("RESEARCH_SEGMENTS_BASE_URL")
                        or "https://doxed-btc-bot.fly.dev")
    parser.add_argument("--max-run-seconds", type=float,
                        default=float(os.getenv("RESEARCH_SEGMENTS_MAX_RUN_SEC") or 900),
                        help="stop fetching new segments after this long (0 = unbounded); "
                             "applied segments stay durable and the next run resumes")
    parser.add_argument("--lock-wait-seconds", type=float,
                        default=float(os.getenv("RESEARCH_SEGMENTS_LOCK_WAIT_SEC") or DEFAULT_LOCK_WAIT_SEC),
                        help="wait this long for a busy shadow-root lock (e.g. a segment promotion) "
                             "before giving up with LOCK_BUSY / exit 2 (0 = fail at once)")
    args = parser.parse_args(argv)
    lock = None
    puller = None
    try:
        if args.source == "http":
            # The admin token comes from the environment only and is never echoed.
            store = HttpSegmentSource(base_url=args.base_url, prefix=args.prefix,
                                      admin_token=os.getenv("BOT_ADMIN_TOKEN") or "")
        else:
            store = store_from_env()
        puller = SegmentPuller(store=store, shadow_root=Path(args.shadow_root),
                               archive_root=Path(args.archive_root), prefix=args.prefix,
                               write_ack=not args.no_ack)
        lock = acquire_run_lock(puller.meta / "run.lock", "research_segment_puller", args.lock_wait_seconds)
        result = puller.pull_once(max_segments=args.max_segments, max_run_seconds=args.max_run_seconds)
        if args.source == "http":
            result["remote_head"] = _remote_head_summary(store)
        print(json.dumps({"ok": True, **result}, sort_keys=True))
        return 0
    except (PullerError, fmt.SegmentFormatError, StoreError) as exc:
        status = _record_failure(args, puller, exc)
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}", **status}, sort_keys=True))
        return 2
    except Exception as exc:
        _record_failure(args, puller, exc)
        raise
    finally:
        if lock is not None:
            lock.release()


if __name__ == "__main__":
    sys.exit(main())
