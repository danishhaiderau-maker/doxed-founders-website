"""Production owner for disabled-first sealed V3 raw-generation reclamation."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import os
import threading
import uuid
from pathlib import Path
from typing import Callable, Mapping

from raw_generation_cleanup import (_has_symlink_component, RawGenerationCleanupRejected,
                                    RawGenerationCleanupTransaction, verify_generation)

_ID = re.compile(r"V3:([1-9][0-9]*)")


class RawGenerationCleanupOwner:
    """Use only persisted server authority; process at most one ledger generation."""
    def __init__(self, volume_root: Path, *, enabled: bool = False,
                 identity: Callable[[], Mapping[str, str]],
                 leases: Callable[[str], Mapping[str, list[str]]]):
        self.root = volume_root.resolve(); self.enabled = bool(enabled)
        self.identity = identity; self.leases = leases; self.lock = threading.Lock()
        self.manifests = self.root / "v3" / "raw_generation_manifests"
        self.acks = self.root / "v3" / "raw_generation_laptop_acks"
        self.tx = RawGenerationCleanupTransaction(self.root, enabled=self.enabled)

    @staticmethod
    def _key(generation_id: str) -> str:
        if not _ID.fullmatch(generation_id):
            raise RawGenerationCleanupRejected(["V3_GENERATION_ID_INVALID"])
        return hashlib.sha256(generation_id.encode()).hexdigest()

    @staticmethod
    def _persist_once(path: Path, value: Mapping) -> None:
        encoded = json.dumps(value, separators=(",", ":"), sort_keys=True).encode() + b"\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_bytes() != encoded:
                raise RawGenerationCleanupRejected(["PERSISTED_GENERATION_AUTHORITY_CONFLICT"])
            return
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(encoded); handle.flush(); os.fsync(handle.fileno())
            os.link(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _source(self, manifest: Mapping) -> Path:
        relative = str(manifest.get("source_relative_path") or "").replace("\\", "/")
        lexical = self.root / relative
        if _has_symlink_component(lexical, self.root):
            raise RawGenerationCleanupRejected(["SEALED_V3_SOURCE_PATH_INVALID"])
        try:
            source = lexical.resolve(strict=True)
            source.relative_to((self.root / "v3" / "ledgers").resolve())
        except (OSError, ValueError) as exc:
            raise RawGenerationCleanupRejected(["SEALED_V3_SOURCE_PATH_INVALID"]) from exc
        members = manifest.get("members") or []
        if not source.is_file() or len(members) != 1 or source.name != str((members[0] or {}).get("path") or ""):
            raise RawGenerationCleanupRejected(["SEALED_V3_SOURCE_PATH_INVALID"])
        return source

    def persist_authority(self, manifest: Mapping, ack: Mapping) -> dict:
        """Verify then immutably register a complete laptop-acknowledged mapping."""
        generation_id = str(manifest.get("generation_id") or "")
        key = self._key(generation_id)
        if manifest.get("caught_up_cycle_complete") is not True or len(manifest.get("members") or []) != 1:
            raise RawGenerationCleanupRejected(["GENERATION_NOT_CAUGHT_UP_OR_BOUNDED"])
        source = self._source(manifest)
        proof = verify_generation(source, manifest, ack, current_identity=self.identity(),
                                  active_leases=self.leases(generation_id))
        self._persist_once(self.manifests / f"{key}.json", manifest)
        self._persist_once(self.acks / f"{key}.json", ack)
        return {"status": "RAW_GENERATION_AUTHORITY_REGISTERED_SOURCE_RETAINED",
                "generation_id": generation_id, "proof_sha256": proof["proof_sha256"],
                "source_cleanup_authorized": False}

    def _authority(self, generation_id: str, *, quarantined: bool = False):
        key = self._key(generation_id)
        try:
            manifest = json.loads((self.manifests / f"{key}.json").read_text("utf-8"))
            ack = json.loads((self.acks / f"{key}.json").read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RawGenerationCleanupRejected(["PERSISTED_GENERATION_AUTHORITY_MISSING"]) from exc
        if (manifest.get("generation_id") != generation_id
                or manifest.get("caught_up_cycle_complete") is not True
                or len(manifest.get("members") or []) != 1):
            raise RawGenerationCleanupRejected(["GENERATION_NOT_CAUGHT_UP_OR_BOUNDED"])
        if quarantined:
            _tx, root = self.tx._paths(generation_id)
            members = list(root.iterdir()) if root.is_dir() else []
            if len(members) != 1:
                raise RawGenerationCleanupRejected(["QUARANTINE_MEMBER_COUNT_INVALID"])
            source = members[0]
        else:
            source = self._source(manifest)
        proof = verify_generation(source, manifest, ack, current_identity=self.identity(),
                                  active_leases=self.leases(generation_id))
        return source, proof

    def quarantine(self, generation_id: str, *, dry_run: bool = True) -> dict:
        if not self.lock.acquire(blocking=False):
            raise RawGenerationCleanupRejected(["RAW_GENERATION_CLEANUP_BUSY"])
        try:
            source, proof = self._authority(generation_id)
            before = shutil.disk_usage(self.root).free
            result = self.tx.quarantine(source, proof, dry_run=dry_run,
                                        revalidate=lambda: self._authority(generation_id)[1])
            after = shutil.disk_usage(self.root).free
            return {**result, "free_bytes_before": before, "free_bytes_after": after,
                    "free_bytes_delta": after - before}
        finally: self.lock.release()

    def purge(self, generation_id: str, *, dry_run: bool = True) -> dict:
        if not self.lock.acquire(blocking=False):
            raise RawGenerationCleanupRejected(["RAW_GENERATION_CLEANUP_BUSY"])
        try:
            self._authority(generation_id, quarantined=True)
            before = shutil.disk_usage(self.root).free
            result = self.tx.purge(generation_id, dry_run=dry_run)
            after = shutil.disk_usage(self.root).free
            return {**result, "free_bytes_before": before, "free_bytes_after": after,
                    "free_bytes_delta": after - before}
        finally: self.lock.release()

    def reconcile(self) -> dict:
        return {"quarantines": self.tx.reconcile(), "purges": self.tx.reconcile_purges()}
