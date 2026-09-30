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
from pathlib import Path

import research_segment_format as fmt
from research_segment_store import (HttpSegmentSource, ObjectStore, PreconditionFailed, StoreError,
                                    store_from_env)

PULLER_VERSION = "research_segment_puller_v1"
STATE_SCHEMA = "research_segment_puller_state_v1"
LEGACY_MIRROR_MARKER = "canonical-research-data"
_WINDOWS_RESERVED = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?$", re.I)
_WINDOWS_BAD_CHARS = set('<>:"|?*')


class PullerError(RuntimeError):
    """Fail-closed verification or application error; nothing past it is applied."""


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


class _RunLock:
    """Exclusive per-shadow-root lock released automatically if the process dies."""

    def __init__(self, path: Path):
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
            raise PullerError("another puller run holds the shadow-root lock") from exc

    def release(self) -> None:
        try:
            if os.name == "nt":
                import msvcrt
                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        self._handle.close()


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

    def write_status(self, **fields) -> None:
        payload = {"schema": "research_segment_puller_status_v1", "updated_at": _utc_now(),
                   "puller_version": PULLER_VERSION, "prefix": self.prefix, **fields}
        _fsync_write(self.status_path, json.dumps(payload, sort_keys=True, indent=2).encode())

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
    def pull_once(self, max_segments: int | None = None) -> dict:
        state = self.load_state()
        self.baselines = {path: dict(entry) for path, entry in (state.get("baselines") or {}).items()}
        applied = 0
        while max_segments is None or applied < max_segments:
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
        receipt = getattr(self.store, "last_ack_response", None)
        if receipt:
            result["ack_receipt"] = receipt
        self.write_status(last_error=None, **result)
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


def _remote_head_summary(store) -> dict:
    try:
        head = store.head()
    except (StoreError, ValueError) as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    return {key: head.get(key) for key in (
        "published_seq", "laptop_acked", "store_bytes", "max_store_bytes",
        "unshipped_bytes", "shipper_last_error", "pruning_enabled")}


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
        lock = _RunLock(puller.meta / "run.lock")
        result = puller.pull_once(max_segments=args.max_segments)
        if args.source == "http":
            result["remote_head"] = _remote_head_summary(store)
        print(json.dumps({"ok": True, **result}, sort_keys=True))
        return 0
    except (PullerError, fmt.SegmentFormatError, StoreError) as exc:
        if puller is not None:
            try:
                puller.write_status(last_error=f"{type(exc).__name__}: {exc}")
            except OSError:
                pass
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, sort_keys=True))
        return 2
    finally:
        if lock is not None:
            lock.release()


if __name__ == "__main__":
    sys.exit(main())
