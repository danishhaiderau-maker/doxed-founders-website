"""Preallocated protected postimages; canonical JSON inodes stay immutable.

Caller serializes access under lifecycle lock. Two slots permit crash-safe
replacement without allocating data blocks. This journal is private and must
not be served as an ordinary mutable sync member.
"""
import hashlib
import json
import os
import re
from pathlib import Path
from emergency_evidence_wal import _allocate_file, _allocated_bytes

HEADER = 512


class PaperSnapshotJournal:
    def __init__(self, canonical_path, capacity, *, epoch_id):
        if type(capacity) is not int or not 4096 <= capacity <= 64 * 1024 * 1024:
            raise ValueError("PAPER_JOURNAL_CAPACITY_INVALID")
        self.capacity = capacity
        if not isinstance(epoch_id, str) or not re.fullmatch(r"epoch-[A-Za-z0-9._-]{1,100}", epoch_id):
            raise ValueError("PAPER_JOURNAL_EPOCH_INVALID")
        self.epoch_id = epoch_id
        path = Path(canonical_path)
        self.paths = [path.with_name(path.name + f".protected-{i}") for i in range(2)]

    def provision(self):
        for path in self.paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.is_symlink() or (path.exists() and getattr(path.lstat(), "st_file_attributes", 0) & 0x400):
                raise RuntimeError("PAPER_JOURNAL_LINKED")
            if not path.exists():
                with path.open("xb") as handle:
                    _allocate_file(handle, self.capacity + HEADER)
            if path.stat().st_size != self.capacity + HEADER or _allocated_bytes(path) < self.capacity + HEADER:
                raise RuntimeError("PAPER_JOURNAL_ALLOCATION_UNPROVEN")
        if os.path.samefile(*self.paths):
            raise RuntimeError("PAPER_JOURNAL_ALIASED")
        if os.name != "nt":
            fd = os.open(str(self.paths[0].parent), os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    def records(self):
        result = []
        for index, path in enumerate(self.paths):
            if not path.exists():
                continue
            with path.open("rb") as handle:
                header = handle.read(HEADER)
                if not header.strip(b"\x00 "):
                    continue
                try:
                    receipt = json.loads(header.rstrip(b"\x00 "))
                    if receipt.get("epoch_id") != self.epoch_id:
                        raise RuntimeError("PAPER_JOURNAL_EPOCH_MISMATCH")
                    length = receipt["length"]
                    if (receipt.get("schema") != "paper_protected_snapshot_v1" or type(length) is not int
                            or not 0 < length <= self.capacity or type(receipt.get("sequence")) is not int
                            or receipt["sequence"] < 1):
                        continue
                    payload = handle.read(length)
                    if hashlib.sha256(payload).hexdigest() != receipt["sha256"]:
                        continue
                    value = json.loads(payload)
                    if not isinstance(value, dict):
                        continue
                    if not re.fullmatch("[0-9a-f]{64}", str(receipt.get("predecessor_sha256") or "")):
                        raise RuntimeError("PAPER_JOURNAL_PREDECESSOR_MISSING")
                    result.append({"slot": index, "sequence": receipt["sequence"], "value": value,
                                   "predecessor_sha256": receipt["predecessor_sha256"]})
                except (ValueError, KeyError, TypeError, UnicodeError):
                    continue
        return sorted(result, key=lambda row: row["sequence"])

    def latest(self):
        rows = self.records()
        return rows[-1] if rows else None

    @staticmethod
    def _write_all(handle, data):
        remaining = memoryview(data)
        while remaining:
            count = handle.write(remaining)
            if not count:
                raise OSError("PAPER_JOURNAL_SHORT_WRITE")
            remaining = remaining[count:]

    def commit(self, value, *, predecessor_sha256):
        if not isinstance(predecessor_sha256, str) or not re.fullmatch("[0-9a-f]{64}", predecessor_sha256):
            raise ValueError("PAPER_JOURNAL_PREDECESSOR_INVALID")
        payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        if len(payload) > self.capacity:
            raise RuntimeError("PAPER_JOURNAL_PAYLOAD_OVERSIZE")
        self.provision()
        prior = self.latest()
        slot = 1 - prior["slot"] if prior else 0
        sequence = prior["sequence"] + 1 if prior else 1
        header = json.dumps({"schema": "paper_protected_snapshot_v1", "sequence": sequence,
                             "length": len(payload), "sha256": hashlib.sha256(payload).hexdigest(),
                             "epoch_id": self.epoch_id, "predecessor_sha256": predecessor_sha256},
                            sort_keys=True, separators=(",", ":")).encode()
        if len(header) > HEADER:
            raise RuntimeError("PAPER_JOURNAL_HEADER_OVERSIZE")
        with self.paths[slot].open("r+b", buffering=0) as handle:
            # Invalidate inactive header durably before changing its payload.
            self._write_all(handle, bytes(HEADER))
            os.fsync(handle.fileno())
            self._write_all(handle, payload)
            os.fsync(handle.fileno())
            handle.seek(0)
            self._write_all(handle, header.ljust(HEADER, b" "))
            os.fsync(handle.fileno())
        return sequence
