"""Opt-in preallocated JSON snapshot pair.

All readers must share the caller's exclusive generation lease before enabling
this writer: an inactive inode is reused. This is not safe for unleased readers.
"""
import json
import os
from pathlib import Path
from emergency_evidence_wal import _allocate_file, _allocated_bytes


class PaperSnapshotReserve:
    def __init__(self, path, capacity):
        self.path = Path(path)
        if type(capacity) is not int or not 4096 <= capacity <= 64 * 1024 * 1024:
            raise ValueError("PAPER_SNAPSHOT_CAPACITY_INVALID")
        self.capacity = capacity
        self.slots = [self.path.with_name(self.path.name + f".reserve-{i}") for i in range(2)]
        self.next_path = self.path.with_name(self.path.name + ".reserve-next")

    def _check(self, path):
        if path.is_symlink() or (path.exists() and getattr(path.lstat(), "st_file_attributes", 0) & 0x400):
            raise RuntimeError("PAPER_SNAPSHOT_LINK_REFUSED")
        if path.exists() and not path.is_file():
            raise RuntimeError("PAPER_SNAPSHOT_NOT_REGULAR")

    def provision(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for parent in (self.path.parent, *self.path.parent.parents):
            self._check(parent) if not parent.is_dir() else None
            if parent.is_symlink() or getattr(parent.lstat(), "st_file_attributes", 0) & 0x400:
                raise RuntimeError("PAPER_SNAPSHOT_LINK_REFUSED")
        self._check(self.path)
        for slot in self.slots:
            self._check(slot)
            if not slot.exists():
                with slot.open("xb") as handle:
                    _allocate_file(handle, self.capacity)
            if slot.stat().st_size != self.capacity or _allocated_bytes(slot) < self.capacity:
                raise RuntimeError("PAPER_SNAPSHOT_RESERVE_UNPROVEN")
        if os.path.samefile(*self.slots):
            raise RuntimeError("PAPER_SNAPSHOT_RESERVE_ALIASED")
        self._fsync_parent()

    def _fsync_parent(self):
        if os.name != "nt":
            fd = os.open(str(self.path.parent), os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    def write(self, value):
        # Serialize and bound before touching either durable slot.
        payload = json.dumps(value, separators=(",", ":"), sort_keys=True, allow_nan=False).encode()
        if len(payload) > self.capacity:
            raise RuntimeError("PAPER_SNAPSHOT_CAPACITY_EXCEEDED")
        self.provision()
        active = next((slot for slot in self.slots if self.path.exists() and os.path.samefile(slot, self.path)), None)
        slot = next(slot for slot in self.slots if slot != active)
        with slot.open("r+b", buffering=0) as handle:
            def write_all(data):
                view = memoryview(data)
                while view:
                    written = handle.write(view)
                    if not written:
                        raise OSError("PAPER_SNAPSHOT_SHORT_WRITE")
                    view = view[written:]
            write_all(payload)
            remaining = self.capacity - len(payload)
            padding = b" " * min(65536, remaining)
            while remaining:
                count = min(len(padding), remaining)
                write_all(padding[:count])
                remaining -= count
            os.fsync(handle.fileno())
        # Only metadata is allocated here; no fresh payload-sized temp file.
        self._check(self.next_path)
        if self.next_path.exists():
            if not any(os.path.samefile(self.next_path, candidate) for candidate in self.slots):
                raise RuntimeError("PAPER_SNAPSHOT_STAGING_CONFLICT")
            self.next_path.unlink()
        os.link(slot, self.next_path)
        os.replace(self.next_path, self.path)
        self._fsync_parent()
