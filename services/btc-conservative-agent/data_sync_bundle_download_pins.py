"""Durable transport reader pins and retirement fences (not wired into HTTP).

All participants must use the same existing bundle-worker lease. Provision the
metadata directory outside both raw evidence and transport artifacts, under a
trusted owner; this module never creates directories or deletes artifacts.
Readers pin before lookup/read, renew before each bounded chunk, and use
read_chunk around all artifact access. A crashed reader's session expires;
an in-flight chunk holds the OS lease even past expiry. Fences NEVER expire.
Retirement readiness is exclusion only, not source/ACK/deletion authority.
"""
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import re
import stat
import time

from data_sync_bundle_worker import _singleton_lease

HEX = re.compile(r"[0-9a-f]{64}")
MAX_GENERATIONS = 64
MAX_SESSIONS = 128
MAX_BYTES = 64 * 1024
MAX_TTL = 7200
SCHEMA = "bundle_download_protection_v1"


def _require(ok, reason):
    if not ok:
        raise ValueError("BUNDLE_DOWNLOAD_" + reason)


def _hex(value):
    return isinstance(value, str) and HEX.fullmatch(value) is not None


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def _safe(path, directory=False):
    info = path.lstat()
    _require(not stat.S_ISLNK(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400,
             "LINK_FORBIDDEN")
    _require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode), "PATH_INVALID")
    if not directory:
        _require(info.st_nlink == 1, "HARDLINK_FORBIDDEN")
    return info


def _directory(raw):
    path = Path(raw)
    _require(path.is_absolute() and ".." not in path.parts, "ABSOLUTE_PATH_REQUIRED")
    for parent in (*reversed(path.parents), path):
        _safe(parent, True)
    _require(path.resolve(strict=True) == path, "PATH_INVALID")
    return path


def _pairs(rows):
    result = {}
    for key, value in rows:
        _require(key not in result, "DUPLICATE_KEY")
        result[key] = value
    return result


class DownloadProtection:
    """A new object after restart observes the same pins/fence, not empty state.

    lease_path must be the coordinator's exact .bundle-worker.lease. Do not call
    methods while already holding that lease. The metadata root is a separate
    preprovisioned directory; it cannot be the derivative root or its child.
    The caller must also keep it outside source evidence. Capacity exhaustion
    fails closed; archival of old metadata is deliberately not implemented.
    """
    def __init__(self, metadata_root, lease_path, *, clock=time.time):
        self.root = _directory(metadata_root)
        self.lease = Path(lease_path)
        _require(self.lease.is_absolute() and self.lease.name == ".bundle-worker.lease", "LEASE_PATH_INVALID")
        parent = _directory(self.lease.parent)
        _require(self.root != parent and parent not in self.root.parents
                 and self.root not in parent.parents, "METADATA_ROOT_NOT_SEPARATE")
        self.clock = clock

    @contextmanager
    def _locked(self):
        _directory(self.root)
        _directory(self.lease.parent)
        if self.lease.exists() or self.lease.is_symlink():
            _safe(self.lease)
        with _singleton_lease(self.lease):
            _directory(self.root)
            _safe(self.lease)
            yield

    def _load(self, generation):
        _require(_hex(generation), "GENERATION_INVALID")
        # Bound enumeration; unknown metadata cannot silently lose protection.
        count = 0
        with os.scandir(self.root) as entries:
            for entry in entries:
                count += 1
                _require(count <= MAX_GENERATIONS * 2, "GENERATION_LIMIT")
                _require(re.fullmatch(r"[0-9a-f]{64}\.(?:json|tmp)", entry.name) is not None,
                         "UNEXPECTED_METADATA")
                _safe(Path(entry.path))
        path = self.root / (generation + ".json")
        if not path.exists():
            _require(count < MAX_GENERATIONS, "GENERATION_LIMIT")
            return {"schema": SCHEMA, "generation_id": generation, "updated_at": 0,
                    "sessions": {}, "fence": None}
        before = _safe(path)
        _require(before.st_size <= MAX_BYTES, "METADATA_LIMIT")
        with path.open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        after = _safe(path)
        _require(len(raw) == before.st_size and (before.st_ino, before.st_mtime_ns, before.st_size)
                 == (after.st_ino, after.st_mtime_ns, after.st_size), "METADATA_CHANGED")
        state = json.loads(raw, object_pairs_hook=_pairs)
        _require(isinstance(state, dict) and set(state) == {"schema", "generation_id", "updated_at", "sessions", "fence"}
                 and state["schema"] == SCHEMA and state["generation_id"] == generation
                 and _number(state["updated_at"]), "METADATA_INVALID")
        sessions = state["sessions"]
        _require(isinstance(sessions, dict) and len(sessions) <= MAX_SESSIONS, "SESSIONS_INVALID")
        for session, expiry in sessions.items():
            _require(_hex(session) and _number(expiry) and expiry <= state["updated_at"] + MAX_TTL,
                     "SESSION_INVALID")
        fence = state["fence"]
        _require(fence is None or (isinstance(fence, dict) and set(fence) == {"token", "created_at"}
                 and _hex(fence["token"]) and _number(fence["created_at"])
                 and fence["created_at"] <= state["updated_at"]), "FENCE_INVALID")
        return state

    def _now(self, state):
        now = self.clock()
        _require(_number(now) and now >= state["updated_at"], "CLOCK_INVALID_OR_ROLLED_BACK")
        return now

    def _save(self, state, now):
        state["updated_at"] = now
        raw = json.dumps(state, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        _require(len(raw) <= MAX_BYTES, "METADATA_LIMIT")
        path = self.root / (state["generation_id"] + ".json")
        temporary = path.with_suffix(".tmp")
        if temporary.exists() or temporary.is_symlink():
            _safe(temporary)
        # An interrupted temporary is not a committed pin/fence. The shared
        # lease permits replacing that one bounded staging slot on retry.
        with temporary.open("wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def pin(self, generation, session, *, ttl_seconds=300):
        """Admit/renew a session before reading; no file lookup precedes this."""
        _require(_hex(session) and _number(ttl_seconds) and ttl_seconds <= MAX_TTL, "PIN_INVALID")
        with self._locked():
            state = self._load(generation)
            now = self._now(state)
            _require(state["fence"] is None, "RETIRING")
            active = {key: value for key, value in state["sessions"].items() if value > now}
            _require(session in active or len(active) < MAX_SESSIONS, "SESSION_LIMIT")
            active[session] = max(active.get(session, 0), now + ttl_seconds)
            state["sessions"] = active
            self._save(state, now)
            return {"generation_id": generation, "session_id": session, "expires_at": active[session]}

    def release(self, generation, session):
        """Caller releases only after all its concurrent chunk reads finish."""
        _require(_hex(session), "SESSION_INVALID")
        with self._locked():
            state = self._load(generation)
            now = self._now(state)
            state["sessions"] = {key: value for key, value in state["sessions"].items()
                                 if key != session and value > now}
            self._save(state, now)

    @contextmanager
    def read_chunk(self, generation, session):
        """Hold shared worker exclusion across lookup, metadata, and chunk I/O.

        The adapter must use this for every artifact read (including indexes
        and descriptors), not only TAR reads. Read bounded bytes into memory
        before exiting; never return an unconsumed streaming file handle.
        A process suspension past TTL cannot defeat the still-held OS lease.
        """
        _require(_hex(session), "SESSION_INVALID")
        with self._locked():
            state = self._load(generation)
            now = self._now(state)
            _require(state["fence"] is None, "RETIRING")
            _require(state["sessions"].get(session, 0) > now, "SESSION_EXPIRED_OR_MISSING")
            yield

    def retirement(self, generation, *, fence_token):
        """Fence new readers, then report whether existing bounded reads drained.

        Caller establishes and durably records the exact token BEFORE calling;
        the same token resumes after a lost response or restart. A wrong token
        cannot take over a fence. It is never cleared
        by TTL or process exit. No deletion is performed or authorized here.
        Fencing interrupts further chunks even for already-pinned sessions;
        integration must not fence a retained/protected download prematurely.
        """
        _require(_hex(fence_token), "FENCE_TOKEN_INVALID")
        with self._locked():
            state = self._load(generation)
            now = self._now(state)
            fence = state["fence"]
            if fence is None:
                fence = {"token": fence_token, "created_at": now}
                state["fence"] = fence
            else:
                _require(_hex(fence_token) and fence_token == fence["token"], "FENCE_TOKEN_MISMATCH")
            state["sessions"] = {key: value for key, value in state["sessions"].items() if value > now}
            self._save(state, now)
            return {"generation_id": generation, "fence_token": fence["token"],
                    "ready": not state["sessions"], "active_sessions": len(state["sessions"]),
                    "deletion_authorized": False}
