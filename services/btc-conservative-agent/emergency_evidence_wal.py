"""Fixed-capacity, fail-closed reserve for mandatory V3 evidence.

Slice A intentionally has no release path. An authenticated immutable mirror
ACK bridge is a later integration slice; until then every extent is retained.
"""
from __future__ import annotations
import ctypes, hashlib, json, math, os, re, struct, threading, time, uuid, zlib
from contextlib import contextmanager
from pathlib import Path
from typing import Any

MAX_ROW_BYTES = 8 * 1024 * 1024
DEFAULT_EXTENTS = 4
HEADER_BYTES = 4096
CONTROL_SLOT_BYTES = 64 * 1024
CONTROL_COPIES = 2
DATA_PREFIX_BYTES = 1024
DATA_TRAILER_BYTES = 64
EXTENT_BYTES = DATA_PREFIX_BYTES + MAX_ROW_BYTES + DATA_TRAILER_BYTES
MAX_IDENTITY_VALUE_BYTES, MAX_LEDGER_BYTES, MAX_RECORD_ID_BYTES = 512, 128, 1024
_CONTROL_MAGIC, _HEADER_MAGIC, _DATA_MAGIC, _TRAILER_MAGIC = b"EVW2", b"EVH2", b"EVD2", b"EVT2"
_REQUIRED_IDENTITY = ("epoch_id", "source_revision", "deployed_revision", "tile_config_signature")
_local_guard = threading.Lock()
_local_locks: dict[str, threading.RLock] = {}
MAX_LOCK_TIMEOUT_SEC = 60.0
MAX_LOCK_POLL_SEC = 1.0

def _fsync_parent(path: Path) -> None:
    if os.name == "nt": return
    fd = os.open(str(path), os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)

def _encode_block(magic: bytes, value: dict[str, Any], size: int) -> bytes:
    body = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    if len(body) > size - 12:
        raise ValueError("EMERGENCY_WAL_METADATA_TOO_LARGE")
    return magic + struct.pack(">II", len(body), zlib.crc32(body) & 0xffffffff) + body + bytes(size - 12 - len(body))

def _decode_block(raw: bytes, magic: bytes) -> dict[str, Any] | None:
    if raw == bytes(len(raw)):
        return None
    if len(raw) < 12 or raw[:4] != magic:
        raise RuntimeError("EMERGENCY_WAL_CONTROL_CORRUPT")
    length, checksum = struct.unpack(">II", raw[4:12])
    if length > len(raw) - 12:
        raise RuntimeError("EMERGENCY_WAL_CONTROL_CORRUPT")
    body = raw[12:12 + length]
    if zlib.crc32(body) & 0xffffffff != checksum:
        raise RuntimeError("EMERGENCY_WAL_CONTROL_CORRUPT")
    try:
        value = json.loads(body.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("EMERGENCY_WAL_CONTROL_CORRUPT") from exc
    if not isinstance(value, dict):
        raise RuntimeError("EMERGENCY_WAL_CONTROL_CORRUPT")
    return value

def _allocated_bytes(path: Path) -> int:
    stat = path.stat()
    if os.name != "nt":
        blocks = getattr(stat, "st_blocks", None)
        if blocks is None:
            raise RuntimeError("EMERGENCY_WAL_ALLOCATION_UNPROVABLE")
        return int(blocks) * 512
    class FILE_STANDARD_INFO(ctypes.Structure):
        _fields_ = [("AllocationSize", ctypes.c_longlong), ("EndOfFile", ctypes.c_longlong),
                    ("NumberOfLinks", ctypes.c_ulong), ("DeletePending", ctypes.c_ubyte),
                    ("Directory", ctypes.c_ubyte)]
    import msvcrt
    with path.open("rb") as handle:
        info = FILE_STANDARD_INFO()
        raw = msvcrt.get_osfhandle(handle.fileno())
        if not ctypes.windll.kernel32.GetFileInformationByHandleEx(ctypes.c_void_p(raw), 1, ctypes.byref(info), ctypes.sizeof(info)):
            raise RuntimeError("EMERGENCY_WAL_ALLOCATION_UNPROVABLE")
        return int(info.AllocationSize)

def _allocate_file(handle, size: int) -> None:
    handle.truncate(size)
    if hasattr(os, "posix_fallocate"):
        os.posix_fallocate(handle.fileno(), 0, size)
    elif os.name == "nt":
        zeroes = bytes(min(size, 1024 * 1024)); handle.seek(0); remaining = size
        while remaining:
            count = min(remaining, len(zeroes)); handle.write(zeroes[:count]); remaining -= count
    else:
        raise RuntimeError("EMERGENCY_WAL_ALLOCATION_UNPROVABLE")
    handle.flush(); os.fsync(handle.fileno())

@contextmanager
def _cross_process_lock(path: Path, *, timeout: float = 5.0, poll_interval: float = .01):
    if (not isinstance(timeout, (int, float)) or isinstance(timeout, bool)
            or not math.isfinite(float(timeout)) or not 0 <= float(timeout) <= MAX_LOCK_TIMEOUT_SEC
            or not isinstance(poll_interval, (int, float)) or isinstance(poll_interval, bool)
            or not math.isfinite(float(poll_interval)) or not 0 < float(poll_interval) <= MAX_LOCK_POLL_SEC):
        raise ValueError("EMERGENCY_WAL_LOCK_TIMEOUT_INVALID")
    timeout, poll_interval = float(timeout), float(poll_interval)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _local_guard:
        local = _local_locks.setdefault(str(path.resolve()), threading.RLock())
    with local, path.open("a+b") as handle:
        deadline = time.monotonic() + timeout
        if os.name == "nt":
            import msvcrt
            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b"\0"); handle.flush(); os.fsync(handle.fileno())
            while True:
                try:
                    handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1); break
                except OSError as exc:
                    if time.monotonic() >= deadline: raise TimeoutError("EMERGENCY_WAL_LOCK_TIMEOUT") from exc
                    time.sleep(min(poll_interval, max(0., deadline - time.monotonic())))
            try: yield
            finally:
                handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            while True:
                try: fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB); break
                except BlockingIOError as exc:
                    if time.monotonic() >= deadline: raise TimeoutError("EMERGENCY_WAL_LOCK_TIMEOUT") from exc
                    time.sleep(min(poll_interval, max(0., deadline - time.monotonic())))
            try: yield
            finally: fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

class EmergencyEvidenceWal:
    @classmethod
    def inspect_existing(cls, root, *, identity, extents=DEFAULT_EXTENTS, lock_timeout=5.0):
        """Validate an existing reserve without provisioning, replay or repair.

        Uses its existing lock. Missing or corrupt authorities are errors, never
        an empty-reserve assertion. Even validation failures leave bytes intact.
        """
        import stat
        instance = cls.__new__(cls)
        instance.root = Path(root).absolute()
        for path in (instance.root, *instance.root.parents):
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise RuntimeError("EMERGENCY_WAL_SYMLINK_REFUSED")
        if type(extents) is not int or not 1 <= extents <= 64:
            raise ValueError("EMERGENCY_WAL_CONFIGURATION_INVALID")
        instance.extents, instance.lock_timeout = extents, lock_timeout
        instance.identity = cls._validate_identity(identity)
        instance.identity_sha256 = hashlib.sha256(json.dumps(instance.identity,
            sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        for attribute, name in (("data_path", "mandatory-reserve.bin"),
                ("header_path", "mandatory-reserve.headers"),
                ("control_path", "mandatory-reserve.control"),
                ("lock_path", "mandatory-reserve.lock")):
            path = instance.root / name
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise RuntimeError("EMERGENCY_WAL_REGULAR_FILE_REQUIRED")
            setattr(instance, attribute, path)
        if os.name == "nt" and instance.lock_path.stat().st_size == 0:
            raise RuntimeError("EMERGENCY_WAL_EXISTING_LOCK_INVALID")
        with _cross_process_lock(instance.lock_path, timeout=lock_timeout):
            for path, size in ((instance.data_path, extents * EXTENT_BYTES),
                    (instance.header_path, extents * HEADER_BYTES),
                    (instance.control_path, CONTROL_COPIES * CONTROL_SLOT_BYTES)):
                if path.stat().st_size != size:
                    raise RuntimeError("EMERGENCY_WAL_CAPACITY_MISMATCH")
            with instance.header_path.open("rb") as handle:
                headers = instance._read_headers(handle)
            instance._validate_headers(headers)
            with instance.data_path.open("rb") as data:
                instance._validate_all_extents(data, headers)
            controls, invalid = instance._controls()
            if invalid or not controls:
                raise RuntimeError("EMERGENCY_WAL_CONTROL_REDUNDANCY_LOST")
            latest = max(controls, key=lambda row: int(row["version"]))
            expected = {"identity_sha256": instance.identity_sha256,
                "capacity_extents": extents, **instance._derived(headers)}
            if any(latest.get(key) != value for key, value in expected.items()):
                raise RuntimeError("EMERGENCY_WAL_CONTROL_HEADER_MISMATCH")
            return {**latest, "identity": dict(instance.identity),
                    "records": [dict(row) for row in headers if row]}

    def __init__(self, root: str | Path, *, identity: dict[str, str], extents: int = DEFAULT_EXTENTS,
                 lock_timeout: float = 5.0) -> None:
        raw_root = Path(root)
        absolute_root = raw_root.absolute()
        if any(candidate.exists() and candidate.is_symlink()
               for candidate in (absolute_root, *absolute_root.parents)):
            raise RuntimeError("EMERGENCY_WAL_SYMLINK_REFUSED")
        if (not 1 <= int(extents) <= 64 or not isinstance(lock_timeout, (int, float))
                or isinstance(lock_timeout, bool) or not math.isfinite(float(lock_timeout))
                or not 0 <= float(lock_timeout) <= MAX_LOCK_TIMEOUT_SEC):
            raise ValueError("EMERGENCY_WAL_CONFIGURATION_INVALID")
        self.root = raw_root.resolve(); root_created = not self.root.exists()
        self.root.mkdir(parents=True, exist_ok=True)
        if root_created: _fsync_parent(self.root.parent)
        self.extents, self.lock_timeout = int(extents), float(lock_timeout)
        self.identity = self._validate_identity(identity)
        self.identity_sha256 = hashlib.sha256(json.dumps(self.identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.data_path = self.root / "mandatory-reserve.bin"
        self.header_path = self.root / "mandatory-reserve.headers"
        self.control_path = self.root / "mandatory-reserve.control"
        self.lock_path = self.root / "mandatory-reserve.lock"
        self._provision(); self.recover()

    @staticmethod
    def _validate_identity(identity: dict[str, str]) -> dict[str, str]:
        if not isinstance(identity, dict) or set(identity) != set(_REQUIRED_IDENTITY): raise ValueError("EMERGENCY_WAL_IDENTITY_INVALID")
        result = {}
        for key in _REQUIRED_IDENTITY:
            value = identity.get(key)
            if (not isinstance(value, str) or not value or value != value.strip()
                    or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)
                    or value.upper() in {"UNKNOWN", "UNAVAILABLE", "NONE", "NULL", "NOT_DEPLOYED_LOCAL"}
                    or len(value.encode()) > MAX_IDENTITY_VALUE_BYTES):
                raise ValueError("EMERGENCY_WAL_IDENTITY_INVALID")
            if key == "epoch_id" and not re.fullmatch(r"epoch-[A-Za-z0-9._-]+", value):
                raise ValueError("EMERGENCY_WAL_IDENTITY_INVALID")
            if key in {"source_revision", "deployed_revision"} and not re.fullmatch(r"[0-9a-f]{7,64}", value):
                raise ValueError("EMERGENCY_WAL_IDENTITY_INVALID")
            if key == "tile_config_signature" and not re.fullmatch(r"[0-9a-f]{64}", value):
                raise ValueError("EMERGENCY_WAL_IDENTITY_INVALID")
            result[key] = value
        return result

    def _assert_regular(self, path: Path) -> None:
        if path.is_symlink() or path.resolve().parent != self.root: raise RuntimeError("EMERGENCY_WAL_SYMLINK_REFUSED")
        if path.exists() and not path.is_file(): raise RuntimeError("EMERGENCY_WAL_NONREGULAR_REFUSED")

    def _initial_control(self, version: int) -> dict[str, Any]:
        return {"schema": "emergency_evidence_wal_control_v2", "version": version,
                "identity_sha256": self.identity_sha256, "capacity_extents": self.extents,
                "deferred_count": 0, "deferred_bytes": 0, "free_extents": self.extents,
                "alarms": [], "incident_alarms": []}

    def _provision(self) -> None:
        for path in (self.data_path, self.header_path, self.control_path, self.lock_path): self._assert_regular(path)
        with _cross_process_lock(self.lock_path, timeout=self.lock_timeout):
            specs = ((self.data_path, self.extents * EXTENT_BYTES), (self.header_path, self.extents * HEADER_BYTES),
                     (self.control_path, CONTROL_COPIES * CONTROL_SLOT_BYTES))
            created_control = False
            for path, size in specs:
                if path.exists() and path.stat().st_size != size: raise RuntimeError("EMERGENCY_WAL_CAPACITY_MISMATCH")
                if not path.exists():
                    with path.open("xb") as handle: _allocate_file(handle, size)
                    created_control |= path == self.control_path
                if _allocated_bytes(path) < size: raise RuntimeError("EMERGENCY_WAL_RESERVE_NOT_PHYSICALLY_ALLOCATED")
            if created_control:
                with self.control_path.open("r+b") as handle:
                    for slot in range(CONTROL_COPIES):
                        handle.seek(slot * CONTROL_SLOT_BYTES); handle.write(_encode_block(_CONTROL_MAGIC, self._initial_control(slot), CONTROL_SLOT_BYTES))
                    handle.flush(); os.fsync(handle.fileno())
            _fsync_parent(self.root)

    def _read_headers(self, handle) -> list[dict[str, Any] | None]:
        result = []
        for slot in range(self.extents):
            handle.seek(slot * HEADER_BYTES)
            try:
                result.append(_decode_block(handle.read(HEADER_BYTES), _HEADER_MAGIC))
            except RuntimeError as exc:
                raise RuntimeError("EMERGENCY_WAL_HEADER_CORRUPT") from exc
        return result

    def _write_header(self, handle, slot: int, value: dict[str, Any] | None) -> None:
        handle.seek(slot * HEADER_BYTES); handle.write(bytes(HEADER_BYTES) if value is None else _encode_block(_HEADER_MAGIC, value, HEADER_BYTES)); handle.flush(); os.fsync(handle.fileno())

    @staticmethod
    def _valid_record_identity(ledger: Any, record_id: Any) -> bool:
        return bool(
            isinstance(ledger, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,127}", ledger)
            and len(ledger.encode()) <= MAX_LEDGER_BYTES
            and isinstance(record_id, str) and record_id == record_id.strip()
            and not any(ord(ch) < 32 or ord(ch) == 127 for ch in record_id)
            and 0 < len(record_id.encode()) <= MAX_RECORD_ID_BYTES
        )

    def _validate_header(self, value: dict[str, Any], slot: int) -> None:
        try:
            uuid.UUID(str(value["generation"])); ledger, record_id = value["ledger"], value["record_id"]
            valid = (value.get("schema") == "emergency_evidence_wal_record_v2" and value.get("state") in {"PREPARED", "DEFERRED", "REPLAYED"}
                     and value.get("identity_sha256") == self.identity_sha256 and int(value.get("slot", -1)) == slot
                     and int(value.get("offset", -1)) == slot * EXTENT_BYTES and 1 <= int(value.get("length", -1)) <= MAX_ROW_BYTES
                     and isinstance(value.get("row_sha256"), str) and len(value["row_sha256"]) == 64
                     and isinstance(value.get("sequence"), int) and value["sequence"] >= 1
                     and all(ch in "0123456789abcdef" for ch in value["row_sha256"])
                     and self._valid_record_identity(ledger, record_id))
            if valid and value.get("state") == "REPLAYED":
                valid = (
                    isinstance(value.get("replay_offset"), int)
                    and value["replay_offset"] >= 0
                    and value.get("replay_length") == value.get("length")
                    and value.get("replay_sha256") == value.get("row_sha256")
                    and isinstance(value.get("replay_receipt_sha256"), str)
                    and re.fullmatch(r"[0-9a-f]{64}", value["replay_receipt_sha256"])
                )
        except (KeyError, TypeError, ValueError, UnicodeEncodeError): valid = False
        if not valid: raise RuntimeError("EMERGENCY_WAL_HEADER_INVALID")

    def _validate_headers(self, headers) -> None:
        generations, identities, sequences = set(), set(), set()
        for slot, header in enumerate(headers):
            if header is None: continue
            self._validate_header(header, slot)
            generation = header["generation"]
            record_identity = (header["ledger"], header["record_id"])
            if generation in generations or record_identity in identities:
                raise RuntimeError("EMERGENCY_WAL_HEADER_DUPLICATE_IDENTITY")
            if value_sequence := header.get("sequence"):
                if value_sequence in sequences:
                    raise RuntimeError("EMERGENCY_WAL_HEADER_DUPLICATE_IDENTITY")
                sequences.add(value_sequence)
            generations.add(generation); identities.add(record_identity)

    def _controls(self, *, enforce_binding: bool = True) -> tuple[list[dict[str, Any]], int]:
        valid, invalid = [], 0
        with self.control_path.open("rb") as handle:
            for slot in range(CONTROL_COPIES):
                handle.seek(slot * CONTROL_SLOT_BYTES)
                try: value = _decode_block(handle.read(CONTROL_SLOT_BYTES), _CONTROL_MAGIC)
                except RuntimeError: invalid += 1; continue
                try:
                    structurally_valid = (
                        value is not None and value.get("schema") == "emergency_evidence_wal_control_v2"
                        and isinstance(value.get("version"), int) and value["version"] >= 0
                        and isinstance(value.get("identity_sha256"), str) and len(value["identity_sha256"]) == 64
                        and isinstance(value.get("capacity_extents"), int) and value["capacity_extents"] > 0
                        and all(isinstance(value.get(k), int) and value[k] >= 0
                                for k in ("deferred_count", "deferred_bytes", "free_extents"))
                        and isinstance(value.get("alarms"), list) and len(value["alarms"]) <= 32
                        and all(isinstance(alarm, str) and 0 < len(alarm) <= 128 for alarm in value["alarms"])
                        and isinstance(value.get("incident_alarms", []), list)
                        and len(value.get("incident_alarms", [])) <= 32
                        and all(isinstance(alarm, str) and 0 < len(alarm) <= 128
                                for alarm in value.get("incident_alarms", []))
                    )
                except (KeyError, TypeError):
                    structurally_valid = False
                if structurally_valid and enforce_binding:
                    structurally_valid = (
                        value["identity_sha256"] == self.identity_sha256
                        and value["capacity_extents"] == self.extents
                    )
                if not structurally_valid: invalid += 1
                else: valid.append(value)
        return valid, invalid

    def _derived(self, headers):
        populated = [h for h in headers if h]
        return {"deferred_count": len(populated), "deferred_bytes": sum(int(h["length"]) for h in populated), "free_extents": self.extents - len(populated)}

    def _publish_control(self, headers, alarms, incident_alarms=None):
        valid, _ = self._controls(); version = max([int(v.get("version", -1)) for v in valid], default=-1) + 1
        if incident_alarms is None:
            incident_alarms = [a for control in valid for a in control.get("incident_alarms", [])]
        value = {"schema": "emergency_evidence_wal_control_v2", "version": version,
                 "identity_sha256": self.identity_sha256, "capacity_extents": self.extents,
                 **self._derived(headers), "alarms": sorted(set(alarms))[:32],
                 "incident_alarms": sorted(set(incident_alarms))[:32]}
        with self.control_path.open("r+b") as handle:
            handle.seek((version % CONTROL_COPIES) * CONTROL_SLOT_BYTES); handle.write(_encode_block(_CONTROL_MAGIC, value, CONTROL_SLOT_BYTES)); handle.flush(); os.fsync(handle.fileno())
        return value

    def _reconstruct_both_controls(self, headers, alarms, incident_alarms=None):
        """Replace both unusable control copies with monotonic fresh versions."""
        base = max(time.time_ns(), 1)
        incident_alarms = incident_alarms or []
        values = []
        with self.control_path.open("r+b") as handle:
            for slot in range(CONTROL_COPIES):
                value = {"schema": "emergency_evidence_wal_control_v2", "version": base + slot,
                         "identity_sha256": self.identity_sha256, "capacity_extents": self.extents,
                         **self._derived(headers), "alarms": sorted(set(alarms))[:32],
                         "incident_alarms": sorted(set(incident_alarms))[:32]}
                handle.seek(slot * CONTROL_SLOT_BYTES)
                handle.write(_encode_block(_CONTROL_MAGIC, value, CONTROL_SLOT_BYTES)); values.append(value)
            handle.flush(); os.fsync(handle.fileno())
        _fsync_parent(self.root)
        return values[-1]

    def _rewrite_controls_from_trusted_snapshot(self, snapshot, alarms):
        """Persist an alarm without consulting an untrusted header region."""
        base = max(time.time_ns(), int(snapshot["version"]) + 1)
        values = []
        with self.control_path.open("r+b") as handle:
            for slot in range(CONTROL_COPIES):
                value = {
                    "schema": "emergency_evidence_wal_control_v2", "version": base + slot,
                    "identity_sha256": snapshot["identity_sha256"],
                    "capacity_extents": int(snapshot["capacity_extents"]),
                    "deferred_count": int(snapshot["deferred_count"]),
                    "deferred_bytes": int(snapshot["deferred_bytes"]),
                    "free_extents": int(snapshot["free_extents"]),
                    "alarms": sorted(set(alarms))[:32],
                    "incident_alarms": sorted(set(snapshot.get("incident_alarms", [])))[:32],
                }
                handle.seek(slot * CONTROL_SLOT_BYTES)
                handle.write(_encode_block(_CONTROL_MAGIC, value, CONTROL_SLOT_BYTES)); values.append(value)
            handle.flush(); os.fsync(handle.fileno())
        _fsync_parent(self.root)
        return values[-1]

    def _persist_control_only_alarm_locked(self, code: str) -> None:
        controls, invalid = self._controls(enforce_binding=False)
        if not controls:
            raise RuntimeError("EMERGENCY_WAL_HEADER_ALARM_UNPUBLISHABLE_NO_TRUSTED_CONTROL")
        latest = max(controls, key=lambda value: int(value["version"]))
        alarms = [a for control in controls for a in control.get("alarms", []) if isinstance(a, str)]
        if invalid: alarms.append("EMERGENCY_WAL_CONTROL_COPY_CORRUPT")
        self._rewrite_controls_from_trusted_snapshot(latest, alarms + [code])

    def _read_validate_headers_or_alarm(self, handle):
        try:
            headers = self._read_headers(handle)
            self._validate_headers(headers)
            return headers
        except RuntimeError as exc:
            code = str(exc)
            if code not in {"EMERGENCY_WAL_HEADER_CORRUPT", "EMERGENCY_WAL_HEADER_INVALID",
                            "EMERGENCY_WAL_HEADER_DUPLICATE_IDENTITY"}:
                code = "EMERGENCY_WAL_HEADER_INVALID"
            self._persist_control_only_alarm_locked(code)
            raise RuntimeError(code) from exc

    def _ensure_control_health_locked(self, headers):
        controls, invalid = self._controls()
        alarms = [a for c in controls for a in c.get("alarms", []) if isinstance(a, str)]
        incidents = [a for c in controls for a in c.get("incident_alarms", []) if isinstance(a, str)]
        latest = max(controls, key=lambda v: int(v["version"]), default=None)
        mismatch = latest is None or any(latest.get(k) != v for k, v in {
            "identity_sha256": self.identity_sha256, "capacity_extents": self.extents,
            **self._derived(headers),
        }.items())
        if not controls:
            return self._reconstruct_both_controls(
                headers, alarms + ["EMERGENCY_WAL_CONTROL_RECONSTRUCTED"], incidents
            )
        if invalid or mismatch:
            if invalid: alarms.append("EMERGENCY_WAL_CONTROL_COPY_CORRUPT")
            if mismatch: alarms.append("EMERGENCY_WAL_CONTROL_TELEMETRY_RECOVERED")
            # Repair both copies; one healthy but stale copy must not survive as
            # an apparently authoritative rollback candidate.
            return self._reconstruct_both_controls(headers, alarms, incidents)
        return latest

    def _validate_extent(self, data, header) -> bool:
        offset, length = int(header["offset"]), int(header["length"]); data.seek(offset)
        prefix_raw = data.read(DATA_PREFIX_BYTES)
        try: prefix = _decode_block(prefix_raw, _DATA_MAGIC)
        except RuntimeError: return False
        if prefix is None or any(prefix.get(k) != header.get(k) for k in (
                "generation", "length", "row_sha256", "identity_sha256",
                "ledger", "record_id", "slot", "offset", "sequence")): return False
        payload = data.read(length); data.seek(offset + DATA_PREFIX_BYTES + MAX_ROW_BYTES); trailer = data.read(DATA_TRAILER_BYTES)
        expected = _TRAILER_MAGIC + hashlib.sha256(prefix_raw + payload).digest()
        return len(payload) == length and hashlib.sha256(payload).hexdigest() == header["row_sha256"] and trailer[:len(expected)] == expected

    def _validate_all_extents(self, data, headers) -> None:
        for header in headers:
            if header is not None and not self._validate_extent(data, header):
                code = ("EMERGENCY_WAL_PREPARED_PAYLOAD_UNPROVABLE"
                        if header["state"] == "PREPARED" else "EMERGENCY_WAL_DEFERRED_PAYLOAD_UNPROVABLE")
                raise RuntimeError(code)

    def _persist_alarm_locked(self, headers, code: str) -> None:
        controls, _ = self._controls()
        alarms = [a for control in controls for a in control.get("alarms", []) if isinstance(a, str)]
        self._reconstruct_both_controls(headers, alarms + [code])

    def recover(self):
        with _cross_process_lock(self.lock_path, timeout=self.lock_timeout), self.header_path.open("r+b") as hf, self.data_path.open("rb") as data:
            headers = self._read_validate_headers_or_alarm(hf)
            controls, invalid = self._controls(); alarms = [a for c in controls for a in c.get("alarms", []) if isinstance(a, str)]
            incidents = [a for c in controls for a in c.get("incident_alarms", []) if isinstance(a, str)]
            if invalid: alarms.append("EMERGENCY_WAL_CONTROL_COPY_CORRUPT")
            latest = max(controls, key=lambda v: int(v.get("version", -1)), default=None)
            if latest is None: alarms.append("EMERGENCY_WAL_CONTROL_RECONSTRUCTED")
            elif any(latest.get(k) != v for k, v in self._derived(headers).items()): alarms.append("EMERGENCY_WAL_CONTROL_TELEMETRY_RECOVERED")
            for slot, header in enumerate(headers):
                if header is None: continue
                if not self._validate_extent(data, header):
                    code = "EMERGENCY_WAL_PREPARED_PAYLOAD_UNPROVABLE" if header["state"] == "PREPARED" else "EMERGENCY_WAL_DEFERRED_PAYLOAD_UNPROVABLE"
                    self._reconstruct_both_controls(headers, alarms + [code], incidents); raise RuntimeError(code)
                if header["state"] == "PREPARED":
                    header = dict(header, state="DEFERRED"); self._write_header(hf, slot, header); headers[slot] = header
            if not controls:
                self._reconstruct_both_controls(headers, alarms)
            else:
                self._ensure_control_health_locked(headers)
            # A successful empty-reserve recovery proves every header slot,
            # data extent, binding, capacity value, and both rewritten control
            # copies.  Recovery alarms are then historical incidents, not a
            # reason to keep the currently healthy reserve unavailable.
            if not any(headers):
                healthy, unhealthy = self._controls()
                if not unhealthy and len(healthy) == CONTROL_COPIES:
                    active = [a for c in healthy for a in c.get("alarms", []) if isinstance(a, str)]
                    prior = [a for c in healthy for a in c.get("incident_alarms", []) if isinstance(a, str)]
                    if active:
                        self._reconstruct_both_controls(headers, [], prior + active)
            return self._status_locked(headers, data)

    def _status_locked(self, headers, data=None):
        self._validate_headers(headers)
        try:
            if data is None:
                with self.data_path.open("rb") as data_handle: self._validate_all_extents(data_handle, headers)
            else:
                self._validate_all_extents(data, headers)
        except RuntimeError as exc:
            self._persist_alarm_locked(headers, str(exc)); raise
        controls, invalid = self._controls()
        if invalid or not controls: raise RuntimeError("EMERGENCY_WAL_CONTROL_REDUNDANCY_LOST")
        latest = max(controls, key=lambda v: int(v.get("version", -1)))
        expected = {"identity_sha256": self.identity_sha256, "capacity_extents": self.extents, **self._derived(headers)}
        if any(latest.get(k) != v for k, v in expected.items()): raise RuntimeError("EMERGENCY_WAL_CONTROL_HEADER_MISMATCH")
        return {**latest, "records": [dict(h) for h in headers if h]}

    def status(self):
        with _cross_process_lock(self.lock_path, timeout=self.lock_timeout), self.header_path.open("rb") as handle, self.data_path.open("rb") as data:
            return self._status_locked(self._read_validate_headers_or_alarm(handle), data)

    def defer(self, *, ledger: str, record_id: str, payload: bytes):
        if (not isinstance(payload, bytes) or not 1 <= len(payload) <= MAX_ROW_BYTES
                or not self._valid_record_identity(ledger, record_id)):
            raise ValueError("EMERGENCY_WAL_RECORD_INVALID_OR_OVERSIZE")
        digest = hashlib.sha256(payload).hexdigest()
        with _cross_process_lock(self.lock_path, timeout=self.lock_timeout), self.header_path.open("r+b") as hf, self.data_path.open("r+b", buffering=0) as data:
            headers = self._read_validate_headers_or_alarm(hf)
            try: self._validate_all_extents(data, headers)
            except RuntimeError as exc:
                self._persist_alarm_locked(headers, str(exc)); raise
            self._ensure_control_health_locked(headers)
            for slot, header in enumerate(headers):
                if header is None: continue
                self._validate_header(header, slot)
                if header["ledger"] == ledger and header["record_id"] == record_id:
                    if header["row_sha256"] != digest or header["length"] != len(payload):
                        prior = self._status_locked(headers, data).get("alarms", []); self._publish_control(headers, prior + ["EMERGENCY_WAL_RECORD_ID_CONFLICT"]); raise RuntimeError("EMERGENCY_WAL_RECORD_ID_CONFLICT")
                    return {**header, "duplicate": True}
            try: slot = headers.index(None)
            except ValueError:
                prior = self._status_locked(headers, data).get("alarms", []); self._publish_control(headers, prior + ["EMERGENCY_WAL_CAPACITY_EXHAUSTED"]); raise RuntimeError("EMERGENCY_WAL_CAPACITY_EXHAUSTED")
            generation, offset = str(uuid.uuid4()), slot * EXTENT_BYTES
            sequence = max((int(row.get("sequence", 0)) for row in headers if row), default=0) + 1
            header = {"schema": "emergency_evidence_wal_record_v2", "state": "PREPARED", "generation": generation,
                      "slot": slot, "offset": offset, "length": len(payload), "row_sha256": digest,
                      "ledger": ledger, "record_id": record_id, "identity_sha256": self.identity_sha256,
                      "sequence": sequence}
            self._write_header(hf, slot, header)
            prefix = _encode_block(_DATA_MAGIC, {k: header[k] for k in (
                "generation", "length", "row_sha256", "identity_sha256",
                "ledger", "record_id", "slot", "offset", "sequence")}, DATA_PREFIX_BYTES)
            data.seek(offset); data.write(prefix); data.write(payload); data.seek(offset + DATA_PREFIX_BYTES + MAX_ROW_BYTES)
            data.write(_TRAILER_MAGIC + hashlib.sha256(prefix + payload).digest()); os.fsync(data.fileno())
            header = dict(header, state="DEFERRED"); self._write_header(hf, slot, header); headers[slot] = header
            controls, _ = self._controls(); alarms = [a for c in controls for a in c.get("alarms", []) if isinstance(a, str)]
            self._publish_control(headers, alarms); return {**header, "duplicate": False}

    def oldest_record(self) -> dict[str, Any] | None:
        """Return a verified copy of the oldest retained payload.

        This does not mutate replay state and does not accept callbacks.  A
        drainer can therefore release the WAL lock before touching a canonical
        ledger, avoiding lock inversion with evidence producers.
        """
        with _cross_process_lock(self.lock_path, timeout=self.lock_timeout), self.header_path.open("rb") as hf, self.data_path.open("rb") as data:
            headers = self._read_validate_headers_or_alarm(hf)
            self._validate_all_extents(data, headers)
            occupied = [row for row in headers if row]
            if not occupied:
                return None
            header = min(occupied, key=lambda row: int(row["sequence"]))
            data.seek(int(header["offset"]) + DATA_PREFIX_BYTES)
            payload = data.read(int(header["length"]))
            return {**header, "payload": payload}

    def mark_replayed(
        self, generation: str, *, canonical_ledger: str | Path,
        canonical_receipt: str | Path,
    ) -> dict[str, Any]:
        """Persist exact canonical replay proof, without releasing capacity."""
        ledger_path = Path(canonical_ledger).resolve(strict=True)
        receipt_path = Path(canonical_receipt).resolve(strict=True)
        volume_root = self.root.parent.parent
        expected_ledgers = (volume_root / "v3" / "ledgers").resolve()
        expected_receipts = (volume_root / "v3" / "receipts").resolve()
        try:
            ledger_path.relative_to(expected_ledgers)
            receipt_path.relative_to(expected_receipts)
        except ValueError as exc:
            raise RuntimeError("EMERGENCY_WAL_REPLAY_PATH_OUTSIDE_VOLUME") from exc
        receipt_raw = receipt_path.read_bytes()
        try:
            receipt = json.loads(receipt_raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("EMERGENCY_WAL_REPLAY_RECEIPT_INVALID") from exc
        with _cross_process_lock(self.lock_path, timeout=self.lock_timeout), self.header_path.open("r+b") as hf, self.data_path.open("rb") as data:
            headers = self._read_validate_headers_or_alarm(hf)
            self._validate_all_extents(data, headers)
            occupied = sorted((row for row in headers if row), key=lambda row: int(row["sequence"]))
            if not occupied or occupied[0]["generation"] != generation:
                raise RuntimeError("EMERGENCY_WAL_REPLAY_OUT_OF_ORDER")
            header = occupied[0]
            if header["state"] == "REPLAYED":
                return {**header, "duplicate": True}
            if ledger_path.name != f'{header["ledger"]}.jsonl':
                raise RuntimeError("EMERGENCY_WAL_REPLAY_LEDGER_MISMATCH")
            expected_receipt = {
                "schema": "emergency_record_idempotency_v1",
                "state": "COMMITTED", "ledger": header["ledger"],
                "record_id": header["record_id"],
                "row_sha256": header["row_sha256"], "length": header["length"],
            }
            if any(receipt.get(key) != value for key, value in expected_receipt.items()):
                raise RuntimeError("EMERGENCY_WAL_REPLAY_RECEIPT_MISMATCH")
            if receipt.get("identity") != self.identity:
                raise RuntimeError("EMERGENCY_WAL_REPLAY_IDENTITY_MISMATCH")
            try:
                offset = int(receipt["offset"])
                with ledger_path.open("rb") as source:
                    source.seek(offset); replayed = source.read(int(header["length"]))
            except (KeyError, OSError, TypeError, ValueError) as exc:
                raise RuntimeError("EMERGENCY_WAL_CANONICAL_REPLAY_UNPROVABLE") from exc
            if hashlib.sha256(replayed).hexdigest() != header["row_sha256"]:
                raise RuntimeError("EMERGENCY_WAL_CANONICAL_REPLAY_UNPROVABLE")
            updated = dict(
                header, state="REPLAYED", replay_offset=offset,
                replay_length=header["length"], replay_sha256=header["row_sha256"],
                replay_receipt_sha256=hashlib.sha256(receipt_raw).hexdigest(),
            )
            self._write_header(hf, int(header["slot"]), updated)
            headers[int(header["slot"])] = updated
            controls, _ = self._controls()
            alarms = [alarm for control in controls for alarm in control.get("alarms", [])]
            self._publish_control(headers, alarms)
            return {**updated, "duplicate": False}

    def acknowledge(self, generation: str, proof: dict[str, Any]):
        del proof
        ack_root = (self.root.parent / "emergency_wal_release_acks").resolve()
        return self.release_from_persisted_ack(
            ack_root / f"{generation}.json", generation=generation,
        )

    def release_oldest_if_acknowledged(self) -> dict[str, Any]:
        record = self.oldest_record()
        if record is None:
            return {"released": False, "empty": True}
        if record.get("state") != "REPLAYED":
            return {"released": False, "reason": "OLDEST_NOT_REPLAYED"}
        ack = self.root.parent / "emergency_wal_release_acks" / f'{record["generation"]}.json'
        if not ack.exists():
            return {"released": False, "reason": "ACK_MISSING"}
        return self.release_from_persisted_ack(ack, generation=record["generation"])

    def release_from_persisted_ack(
        self, ack_path: str | Path, *, generation: str,
    ) -> dict[str, Any]:
        """Release one oldest replayed extent from a server-persisted ACK.

        The ACK path is fixed under the volume's V3 receipt namespace.  It is
        expected to be published only by the authenticated lifecycle cleanup
        bridge after bundle verification and two lease snapshots.
        """
        expected_root = (self.root.parent / "emergency_wal_release_acks").resolve()
        try: candidate = Path(ack_path).resolve(strict=True)
        except OSError as exc: raise RuntimeError("EMERGENCY_WAL_ACK_MISSING") from exc
        try: candidate.relative_to(expected_root)
        except ValueError as exc: raise RuntimeError("EMERGENCY_WAL_ACK_PATH_INVALID") from exc
        if candidate != expected_root / f"{generation}.json":
            raise RuntimeError("EMERGENCY_WAL_ACK_PATH_INVALID")
        raw = candidate.read_bytes()
        try: proof = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc: raise RuntimeError("EMERGENCY_WAL_ACK_INVALID") from exc
        supplied = str(proof.get("binding_sha256") or "")
        material = dict(proof); material.pop("binding_sha256", None)
        actual = hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        before, after = proof.get("lease_snapshot_before"), proof.get("lease_snapshot_after")
        leases_clear = lambda value: isinstance(value, dict) and set(value) == {"runtime", "sync", "analyzer", "lifecycle_worker"} and all(value[key] == [] for key in value)
        required_hashes = (
            "row_sha256", "manifest_sha256", "replay_receipt_sha256",
            "cleanup_transaction_sha256", "lifecycle_ack_sha256", "config_signature",
        )
        valid = (
            proof.get("schema") == "emergency_wal_lifecycle_release_ack_v1"
            and supplied == actual and re.fullmatch(r"[0-9a-f]{64}", supplied)
            and proof.get("source_cleanup_authorized") is True
            and proof.get("generation") == generation
            and proof.get("identity") == self.identity
            and leases_clear(before) and leases_clear(after)
            and all(re.fullmatch(r"[0-9a-f]{64}", str(proof.get(key) or "")) for key in required_hashes)
            and re.fullmatch(r"lifecycle-[0-9a-f]{64}", str(proof.get("bundle_id") or ""))
            and isinstance(proof.get("lifecycle_id"), str) and bool(proof["lifecycle_id"])
            and proof.get("ledger") in {"lifecycle", "execution", "order_intent", "order_schedule"}
            and self._valid_record_identity(proof.get("ledger"), proof.get("record_id"))
        )
        if not valid: raise RuntimeError("EMERGENCY_WAL_ACK_INVALID")
        with _cross_process_lock(self.lock_path, timeout=self.lock_timeout), self.header_path.open("r+b") as hf, self.data_path.open("rb") as data:
            headers = self._read_validate_headers_or_alarm(hf); self._validate_all_extents(data, headers)
            occupied = sorted((row for row in headers if row), key=lambda row: int(row["sequence"]))
            if not occupied or occupied[0]["generation"] != generation:
                raise RuntimeError("EMERGENCY_WAL_ACK_STALE_OR_OUT_OF_ORDER")
            header = occupied[0]
            if header["state"] != "REPLAYED": raise RuntimeError("EMERGENCY_WAL_ACK_BEFORE_REPLAY")
            exact = {
                "ledger": header["ledger"], "record_id": header["record_id"],
                "row_sha256": header["row_sha256"],
                "replay_receipt_sha256": header["replay_receipt_sha256"],
            }
            if any(proof.get(key) != value for key, value in exact.items()):
                raise RuntimeError("EMERGENCY_WAL_ACK_BINDING_MISMATCH")
            slot = int(header["slot"]); self._write_header(hf, slot, None); headers[slot] = None
            controls, _ = self._controls(); alarms = [alarm for control in controls for alarm in control.get("alarms", [])]
            self._publish_control(headers, alarms)
            return {"released": True, "generation": generation, "slot": slot}

    def reset_alarm(self, proof: dict[str, Any]):
        del proof
        raise RuntimeError("EMERGENCY_WAL_ALARM_RESET_BRIDGE_UNAVAILABLE")
