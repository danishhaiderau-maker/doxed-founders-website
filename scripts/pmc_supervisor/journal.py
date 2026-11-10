"""Append-only authoritative state. No implicit initialization, repair or expiry."""
from __future__ import annotations
import copy
import hashlib
import json
import os
import time
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from .policy import Hold, ROLES, REASONS, digest, packed, no_reparse

CAP = 32 * 1024 * 1024
RESERVE = 64 * 1024
MAX_RECORD = 16384
SCHEMA = 1


def local_time(wall):
    return datetime.fromtimestamp(wall, ZoneInfo("Australia/Sydney")).strftime("%Y-%m-%d %H:%M:%S %Z")


def config_identity(cfg):
    # Genesis and one-use recovery receipt cannot be part of their own hashes.
    return digest({k: v for k, v in cfg.items() if k not in {"genesis_sha256", "human_clear_receipt_sha256"}})


def empty_role():
    return {"phase": "NEVER_STARTED", "reason": None, "latch_id": None, "identity": None,
            "exit": None, "reservations": [], "attempt": 0, "deadline": None}


def schema_hash():
    return digest({"schema": SCHEMA, "role_fields": sorted(empty_role()),
                   "envelope": ["seq", "previous_hash", "hash", "run_id", "role", "reason", "wall", "sydney", "config_hash", "code_hash", "state", "human"]})


def envelope(seq, previous, run_id, role, reason, wall, config_hash, code_hash, state, human=None):
    obj = {"schema": SCHEMA, "seq": seq, "previous_hash": previous, "run_id": run_id,
           "role": role, "reason": reason, "wall": wall, "sydney": local_time(wall),
           "schema_hash": schema_hash(),
           "config_hash": config_hash, "code_hash": code_hash, "state": copy.deepcopy(state), "human": human}
    return {**obj, "hash": digest(obj)}


def genesis_record(cfg, wall, run_id):
    """Pure construction only. An approved install card must persist/pin this."""
    return envelope(0, "0" * 64, run_id, None, "GENESIS", wall, config_identity(cfg),
                    cfg["implementation_sha256"], {r: empty_role() for r in ROLES},
                    human={"public_key_sha256": cfg.get("human_clear_public_key_sha256")})


class InstanceLock:
    """Cross-session byte-range lock on Windows; flock on Linux test workers."""
    def __init__(self, path: Path):
        no_reparse(path)  # install must pre-create the permanent lock file
        self.fd = os.open(path, os.O_RDWR | getattr(os, "O_BINARY", 0))
        try:
            if os.fstat(self.fd).st_size < 1:
                raise Hold()
            if os.name == "nt":
                import msvcrt
                os.lseek(self.fd, 0, os.SEEK_SET)
                msvcrt.locking(self.fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(self.fd)
            raise Hold("OWNERSHIP_FAILURE") from None

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


class Journal:
    def __init__(self, path, cfg, *, wall=time.time, sync=os.fsync, cap=CAP):
        self.path, self.cfg, self.wall, self.sync, self.cap = Path(path), cfg, wall, sync, cap
        self.run_id = uuid.uuid4().hex
        self.fd = None
        self.broken = False
        self.expected_size = None
        self.config_hash, self.code_hash = config_identity(cfg), cfg["implementation_sha256"]
        no_reparse(self.path)
        size = self.path.stat().st_size
        if size == 0 or size > cap:
            raise Hold("UNCERTAIN_PREVIOUS_RUN")
        previous, last_wall = "0" * 64, None
        self.applied_receipts = set()
        last = None
        with self.path.open("rb") as f:
            for seq, line in enumerate(f):
                if len(line) > MAX_RECORD or not line.endswith(b"\n"):
                    raise Hold("UNCERTAIN_PREVIOUS_RUN")
                try:
                    row = json.loads(line)
                    body = {k: v for k, v in row.items() if k != "hash"}
                    if (row["schema"] != SCHEMA or row.get("schema_hash") != schema_hash() or row["seq"] != seq or row["previous_hash"] != previous
                            or digest(body) != row["hash"] or row["reason"] not in REASONS
                            or row["role"] not in (*ROLES, None)
                            or (last_wall is not None and row["wall"] < last_wall)):
                        raise ValueError()
                    validate_state(row["state"])
                    if seq == 0 and (row["reason"] != "GENESIS" or row["hash"] != cfg["genesis_sha256"]):
                        raise ValueError()
                    if seq == 0 and row["human"]["public_key_sha256"] != cfg.get("human_clear_public_key_sha256"):
                        raise ValueError()
                    if last and (row["config_hash"] != last["config_hash"] or row["code_hash"] != last["code_hash"]) and row["reason"] != "HUMAN_CLEAR":
                        raise ValueError()
                    if row["reason"] == "HUMAN_CLEAR":
                        from .recovery import verify_signature
                        key_path = no_reparse(self.path.parent / "human-clear-public-key.json")
                        from .policy import file_hash
                        if file_hash(key_path) != cfg.get("human_clear_public_key_sha256"):
                            raise ValueError()
                        receipt = row["human"]["receipt"]
                        if (not verify_signature(receipt["body"], receipt["signature"], json.loads(key_path.read_bytes()))
                                or receipt["body"]["previous_hash"] != previous
                                or receipt["body"]["latch_id"] != last["state"][row["role"]]["latch_id"]
                                or receipt["body"]["new_config_hash"] != row["config_hash"]
                                or receipt["body"]["new_code_hash"] != row["code_hash"]):
                            raise ValueError()
                        self.applied_receipts.add(row["human"]["receipt_sha256"])
                    previous, last_wall, last = row["hash"], row["wall"], row
                except (ValueError, KeyError, TypeError, OverflowError):
                    raise Hold("UNCERTAIN_PREVIOUS_RUN") from None
        if last is None:
            raise Hold("UNCERTAIN_PREVIOUS_RUN")
        self.last, self.state = last, copy.deepcopy(last["state"])
        self.fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | getattr(os, "O_BINARY", 0))
        self.expected_size = size
        opened, named = os.fstat(self.fd), self.path.stat()
        if (opened.st_dev, opened.st_ino, opened.st_size) != (named.st_dev, named.st_ino, size):
            self.close()
            raise Hold("UNCERTAIN_PREVIOUS_RUN")
        try:
            if last["config_hash"] != self.config_hash or last["code_hash"] != self.code_hash:
                # Only a pinned, independently signed human receipt can move identities.
                self.apply_human_clear()
            elif cfg.get("human_clear_receipt_sha256"):
                self.apply_human_clear()
            if self.wall() < self.last["wall"]:
                # Cannot append a trustworthy timestamp; unresolved state stays blocking.
                raise Hold("CLOCK_UNCERTAIN")
            for role in ROLES:
                if self.state[role]["phase"] not in {"NEVER_STARTED", "LATCHED"}:
                    self.latch(role, "UNCERTAIN_PREVIOUS_RUN")
            if size >= cap - RESERVE:
                for role in ROLES:
                    self.latch(role, "JOURNAL_CAP")
        except BaseException:
            self.close()
            raise

    def write(self, role, reason, state=None, *, human=None, terminal=False):
        if self.broken or reason not in REASONS or self.wall() < self.last["wall"]:
            raise Hold("DURABILITY_FAILURE")
        try:
            no_reparse(self.path)
            opened, named = os.fstat(self.fd), self.path.stat()
            if (opened.st_dev, opened.st_ino, opened.st_size) != (named.st_dev, named.st_ino, self.expected_size):
                raise OSError()
        except (OSError, Hold):
            self.broken = True
            raise Hold("DURABILITY_FAILURE") from None
        row = envelope(self.last["seq"] + 1, self.last["hash"], self.run_id, role, reason,
                       self.wall(), self.config_hash, self.code_hash, self.state if state is None else state, human)
        validate_state(row["state"])
        line = packed(row) + b"\n"
        limit = self.cap if terminal else self.cap - RESERVE
        if len(line) > MAX_RECORD or os.fstat(self.fd).st_size + len(line) > limit:
            raise Hold("JOURNAL_CAP")
        try:
            view = memoryview(line)
            while view:
                n = os.write(self.fd, view)
                if n <= 0:
                    raise OSError()
                view = view[n:]
            self.sync(self.fd)  # Windows _commit/FlushFileBuffers via CPython fsync
        except BaseException:
            self.broken = True
            raise Hold("DURABILITY_FAILURE") from None
        self.expected_size += len(line)
        self.last, self.state = row, copy.deepcopy(row["state"])
        return row

    def latch(self, role, reason):
        if self.state[role]["phase"] == "LATCHED":
            return  # immutable until HUMAN_CLEAR, never renew/expire a latch
        state = copy.deepcopy(self.state)
        state[role].update(phase="LATCHED", reason=reason, latch_id=uuid.uuid4().hex, deadline=None)
        self.write(role, reason, state, terminal=True)

    def apply_human_clear(self):
        """No reset URL/override. Receipt is signed OFFLINE by a human-held key.

        The public key and receipt digest are installed through a separate card.
        The process never holds, discovers or reads a private key. Replay verifies
        already-applied receipts by preserving the journal entry, not applying twice.
        """
        receipt_pin = self.cfg.get("human_clear_receipt_sha256")
        if not receipt_pin:
            raise Hold("IDENTITY_MISMATCH")
        from .recovery import read_receipt
        receipt = read_receipt(self.path.parent, receipt_pin, self.cfg.get("human_clear_public_key_sha256"))
        if receipt_pin in self.applied_receipts and self.last["config_hash"] == self.config_hash and self.last["code_hash"] == self.code_hash:
            return
        body = receipt["body"]
        role = body["role"]
        prior = self.state[role]
        if (body["previous_hash"] != self.last["hash"] or prior["phase"] != "LATCHED"
                or body["latch_id"] != prior["latch_id"] or body["new_config_hash"] != self.config_hash
                or body["new_code_hash"] != self.code_hash):
            raise Hold("IDENTITY_MISMATCH")
        # No other role can be running during an identity-changing recovery.
        if any(self.state[r]["phase"] not in {"LATCHED", "NEVER_STARTED"} for r in ROLES):
            raise Hold("UNCERTAIN_PREVIOUS_RUN")
        state = copy.deepcopy(self.state)
        # Keep rolling reservations/index; human clear is not free retry budget.
        state[role].update(phase="NEVER_STARTED", reason=None, latch_id=None, identity=None, deadline=None)
        self.write(role, "HUMAN_CLEAR", state, human={"who": body["who"], "why": body["why"],
                   "remediation": body["remediation"], "receipt_sha256": receipt_pin, "receipt": receipt}, terminal=True)
        self.applied_receipts.add(receipt_pin)

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def validate_state(state):
    if not isinstance(state, dict) or set(state) != set(ROLES):
        raise Hold("UNCERTAIN_PREVIOUS_RUN")
    for row in state.values():
        if (not isinstance(row, dict) or set(row) != set(empty_role())
                or row["phase"] not in {"NEVER_STARTED", "START_PENDING", "RUNNING", "EXIT_OBSERVED", "BACKOFF", "LATCHED"}
                or type(row["attempt"]) is not int or row["attempt"] < 0
                or not isinstance(row["reservations"], list) or len(row["reservations"]) > 3
                or any(type(t) not in (float, int) for t in row["reservations"])
                or (row["phase"] == "LATCHED" and (row["reason"] not in REASONS or not row["latch_id"]))):
            raise Hold("UNCERTAIN_PREVIOUS_RUN")
