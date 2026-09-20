"""Epoch-bound transactional authority for V3 record receipts.

The JSONL ledgers remain the payload authority in this migration slice.  This
database replaces the one-file-per-record idempotency index only after an
explicit, all-ledger import and atomic authority-marker publication.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Mapping

from research_v3_contract import LEDGER_NAMES, canonical_json


SCHEMA = "v3_transactional_record_receipt_authority_v1"
MARKER_SCHEMA = "v3_transactional_record_receipt_authority_marker_v1"
ACTIVATION_BOUNDARY_SCHEMA = "v3_receipt_authority_activation_boundary_v1"
AUTHORITY_RELATIVE = "v3/receipts/transactional_record_authority_v1"
DATABASE_NAME = "receipts.sqlite3"
MARKER_NAME = "ACTIVE.json"
MAX_RECORD_ID_BYTES = 1024
MAX_ROW_BYTES = 8 * 1024 * 1024
MAX_IMPORT_RECORDS = 256
MAX_IMPORT_BYTES = 16 * 1024 * 1024
MAX_LEGACY_RECEIPTS_PER_LEDGER = 500_000
BUSY_TIMEOUT_MS = 2500
MAX_DATABASE_BYTES = 512 * 1024 * 1024


class ReceiptAuthorityError(RuntimeError):
    """The configured receipt authority cannot be proved safe."""


def _identity_json(identity: Mapping[str, Any]) -> str:
    required = {"epoch_id", "source_revision", "deployed_revision", "tile_config_signature"}
    if (set(identity) != required
            or any(not isinstance(identity[key], str) or not identity[key] for key in required)
            or any(identity[key].strip().upper() in {
                "UNKNOWN", "UNAVAILABLE", "NOT_DEPLOYED_LOCAL",
            } for key in required)):
        raise ReceiptAuthorityError("RECEIPT_AUTHORITY_IDENTITY_INVALID")
    return canonical_json(dict(identity))


def _generation_json(generation: Mapping[str, Any]) -> str:
    required = {"schema", "state", "ledger", "generation", "relative_path"}
    if set(generation) != required:
        raise ReceiptAuthorityError("RECEIPT_AUTHORITY_GENERATION_INVALID")
    relative = str(generation.get("relative_path") or "")
    expected_prefix = f"v3/ledgers/{generation.get('ledger')}.jsonl"
    if (generation.get("schema") != "v3_ledger_generation_ref_v1"
            or generation.get("state") not in {"ACTIVE", "SEALED"}
            or generation.get("ledger") not in LEDGER_NAMES
            or type(generation.get("generation")) is not int
            or int(generation["generation"]) < 0
            or not isinstance(generation.get("relative_path"), str)
            or "\\" in relative or ".." in Path(relative).parts
            or not (relative == expected_prefix or relative.startswith(expected_prefix + "."))):
        raise ReceiptAuthorityError("RECEIPT_AUTHORITY_GENERATION_INVALID")
    return canonical_json(dict(generation))


def _signature_payload(path: Path) -> dict[str, int] | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    if path.is_symlink() or not path.is_file():
        raise ReceiptAuthorityError("RECEIPT_AUTHORITY_LEDGER_PATH_INVALID")
    return {"device": int(stat.st_dev), "inode": int(stat.st_ino),
            "size": int(stat.st_size), "mtime_ns": int(stat.st_mtime_ns)}


def _bounded_slice(path: Path, offset: int, length: int) -> bytes:
    if offset < 0 or not 0 < length <= MAX_ROW_BYTES:
        raise ReceiptAuthorityError("RECEIPT_AUTHORITY_ROW_RANGE_INVALID")
    try:
        with path.open("rb") as source:
            source.seek(offset)
            raw = source.read(length)
    except OSError as exc:
        raise ReceiptAuthorityError("RECEIPT_AUTHORITY_LEDGER_READ_FAILED") from exc
    if len(raw) != length:
        raise ReceiptAuthorityError("RECEIPT_AUTHORITY_LEDGER_RANGE_MISSING")
    return raw


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _is_link_or_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return path.is_symlink() or bool(
        int(getattr(info, "st_file_attributes", 0) or 0) & 0x400
    )


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    candidate = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with candidate.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(canonical_json(dict(payload)) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(candidate, path)
        _fsync_directory(path.parent)
    finally:
        candidate.unlink(missing_ok=True)


def _strict_json_file(path: Path, limit: int = 1024 * 1024) -> tuple[dict[str, Any], bytes]:
    try:
        if path.is_symlink() or not path.is_file():
            raise ReceiptAuthorityError("RECEIPT_AUTHORITY_FILE_INVALID")
        with path.open("rb") as source:
            raw = source.read(limit + 1)
        if len(raw) > limit:
            raise ReceiptAuthorityError("RECEIPT_AUTHORITY_FILE_OVERSIZE")
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs)
    except ReceiptAuthorityError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReceiptAuthorityError("RECEIPT_AUTHORITY_FILE_INVALID") from exc
    if not isinstance(value, dict):
        raise ReceiptAuthorityError("RECEIPT_AUTHORITY_FILE_INVALID")
    return value, raw


def _reject_duplicate_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


class TransactionalReceiptStore:
    """Explicit-transaction receipt backend; one connection per operation."""

    def __init__(self, root: str | Path, identity: Mapping[str, Any], *, create: bool = False):
        self.root = Path(root).resolve()
        self.directory = self.root / AUTHORITY_RELATIVE
        self.database_path = self.directory / DATABASE_NAME
        self.marker_path = self.directory / MARKER_NAME
        self.identity = dict(identity)
        self.identity_json = _identity_json(self.identity)
        self._create = bool(create)
        self._validate_paths()
        if create:
            self._initialize()
        else:
            self._validate_active_marker()

    @classmethod
    def active(cls, root: str | Path, identity: Mapping[str, Any]) -> "TransactionalReceiptStore | None":
        marker = Path(root).resolve() / AUTHORITY_RELATIVE / MARKER_NAME
        if not os.path.lexists(marker):
            return None
        return cls(root, identity, create=False)

    def _validate_paths(self) -> None:
        for path in (self.directory, self.database_path, self.marker_path):
            try:
                path.resolve().relative_to(self.root)
            except ValueError as exc:
                raise ReceiptAuthorityError("RECEIPT_AUTHORITY_PATH_ESCAPE") from exc
        for path in (self.directory, self.database_path, self.marker_path):
            if os.path.lexists(path) and _is_link_or_reparse(path):
                raise ReceiptAuthorityError("RECEIPT_AUTHORITY_LINK_REFUSED")

    def _connect(self, *, writable: bool = True) -> sqlite3.Connection:
        if not self.database_path.is_file() or self.database_path.is_symlink():
            raise ReceiptAuthorityError("RECEIPT_AUTHORITY_DATABASE_MISSING")
        sidecars = (
            self.database_path,
            self.database_path.with_name(self.database_path.name + "-wal"),
        )
        if any(path.exists() and path.stat().st_size > MAX_DATABASE_BYTES for path in sidecars):
            raise ReceiptAuthorityError("RECEIPT_AUTHORITY_DATABASE_CAPACITY_EXCEEDED")
        uri = f"file:{self.database_path.as_posix()}?mode={'rw' if writable else 'ro'}"
        try:
            connection = sqlite3.connect(uri, uri=True, timeout=BUSY_TIMEOUT_MS / 1000,
                                         isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
            connection.execute("PRAGMA foreign_keys=ON")
            if writable:
                connection.execute("PRAGMA synchronous=FULL")
                page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
                connection.execute(
                    f"PRAGMA max_page_count={max(1, MAX_DATABASE_BYTES // page_size)}"
                )
                connection.execute("PRAGMA wal_autocheckpoint=1000")
            return connection
        except sqlite3.Error as exc:
            raise ReceiptAuthorityError("RECEIPT_AUTHORITY_DATABASE_OPEN_FAILED") from exc

    @contextmanager
    def _transaction(self):
        connection = self._connect(writable=True)
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.execute("COMMIT")
        except BaseException:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        if os.path.lexists(self.marker_path):
            self._validate_active_marker()
            return
        connection = sqlite3.connect(str(self.database_path), timeout=BUSY_TIMEOUT_MS / 1000,
                                     isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
            connection.execute(
                f"PRAGMA max_page_count={max(1, MAX_DATABASE_BYTES // page_size)}"
            )
            connection.execute("PRAGMA wal_autocheckpoint=1000")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS authority_meta(
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    schema_name TEXT NOT NULL,
                    authority_id TEXT NOT NULL,
                    identity_json TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('IMPORTING','ACTIVE')),
                    created_unix REAL NOT NULL,
                    activation_boundary_sha256 TEXT
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS record_receipts(
                    ledger TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('DEFERRED','PREPARED','COMMITTED')),
                    row_sha256 TEXT NOT NULL,
                    byte_offset INTEGER,
                    byte_length INTEGER NOT NULL,
                    identity_json TEXT NOT NULL,
                    ledger_generation_json TEXT NOT NULL,
                    row_payload_utf8 TEXT,
                    legacy_receipt_sha256 TEXT,
                    legacy_receipt_name TEXT,
                    PRIMARY KEY(ledger,record_id)
                ) WITHOUT ROWID
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS ledger_state(
                    ledger TEXT PRIMARY KEY,
                    ledger_signature_json TEXT,
                    tail_anchor_json TEXT,
                    complete INTEGER NOT NULL CHECK(complete IN (0,1)),
                    import_cursor INTEGER NOT NULL,
                    imported_count INTEGER NOT NULL,
                    source_device INTEGER,
                    source_inode INTEGER
                ) WITHOUT ROWID
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS generation_import(
                    ledger TEXT NOT NULL,
                    ledger_generation_json TEXT NOT NULL,
                    ledger_signature_json TEXT,
                    tail_anchor_json TEXT,
                    complete INTEGER NOT NULL CHECK(complete IN (0,1)),
                    import_cursor INTEGER NOT NULL,
                    imported_count INTEGER NOT NULL,
                    source_device INTEGER,
                    source_inode INTEGER,
                    PRIMARY KEY(ledger,ledger_generation_json)
                ) WITHOUT ROWID
            """)
            meta = connection.execute("SELECT * FROM authority_meta").fetchall()
            receipt_count = int(connection.execute(
                "SELECT COUNT(*) FROM record_receipts"
            ).fetchone()[0])
            ledger_count = int(connection.execute(
                "SELECT COUNT(*) FROM ledger_state"
            ).fetchone()[0])
            generation_count = int(connection.execute(
                "SELECT COUNT(*) FROM generation_import"
            ).fetchone()[0])
            if not meta:
                if receipt_count or ledger_count or generation_count:
                    raise ReceiptAuthorityError(
                        "RECEIPT_AUTHORITY_PARTIAL_INITIALIZATION"
                    )
                authority_id = uuid.uuid4().hex
                connection.execute(
                    "INSERT INTO authority_meta VALUES(1,?,?,?,?,?,?)",
                    (SCHEMA, authority_id, self.identity_json, "IMPORTING",
                     time.time(), None),
                )
                for ledger in LEDGER_NAMES:
                    connection.execute(
                        "INSERT INTO ledger_state VALUES(?,?,?,?,?,?,?,?)",
                        (ledger, None, None, 0, 0, 0, None, None),
                    )
                connection.execute("PRAGMA user_version=1")
            connection.execute("COMMIT")
        except BaseException as exc:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            if isinstance(exc, ReceiptAuthorityError):
                raise
            if isinstance(exc, sqlite3.Error):
                raise ReceiptAuthorityError(
                    "RECEIPT_AUTHORITY_INITIALIZATION_FAILED"
                ) from exc
            raise
        finally:
            connection.close()
        if not self.database_path.is_file() or self.database_path.is_symlink():
            raise ReceiptAuthorityError("RECEIPT_AUTHORITY_DATABASE_MISSING")
        if self.database_path.exists():
            _fsync_directory(self.directory)
        self._validate_meta(expected_state={"IMPORTING", "ACTIVE"})

    def _validate_meta(self, *, expected_state: set[str], quick_check: bool = False) -> sqlite3.Row:
        try:
            with self._connect(writable=False) as connection:
                quick = (
                    connection.execute("PRAGMA quick_check(1)").fetchone()
                    if quick_check else ("ok",)
                )
                row = connection.execute("SELECT * FROM authority_meta WHERE singleton=1").fetchone()
                count = connection.execute("SELECT COUNT(*) FROM authority_meta").fetchone()[0]
        except sqlite3.Error as exc:
            raise ReceiptAuthorityError("RECEIPT_AUTHORITY_DATABASE_INVALID") from exc
        if (not quick or quick[0] != "ok" or count != 1 or row is None
                or row["schema_name"] != SCHEMA or row["identity_json"] != self.identity_json
                or row["state"] not in expected_state
                or not re.fullmatch(r"[0-9a-f]{32}", str(row["authority_id"] or ""))):
            raise ReceiptAuthorityError("RECEIPT_AUTHORITY_DATABASE_INVALID")
        boundary = row["activation_boundary_sha256"]
        if ((row["state"] == "IMPORTING" and boundary is not None)
                or (row["state"] == "ACTIVE"
                    and not re.fullmatch(r"[0-9a-f]{64}", str(boundary or "")))):
            raise ReceiptAuthorityError("RECEIPT_AUTHORITY_DATABASE_INVALID")
        return row

    def _validate_active_marker(self) -> None:
        marker, _ = _strict_json_file(self.marker_path, 64 * 1024)
        supplied = str(marker.get("binding_sha256") or "")
        material = dict(marker); material.pop("binding_sha256", None)
        expected = hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()
        row = self._validate_meta(expected_state={"ACTIVE"})
        if (marker.get("schema") != MARKER_SCHEMA
                or marker.get("state") != "ACTIVE"
                or marker.get("identity") != self.identity
                or marker.get("database_relative") != f"{AUTHORITY_RELATIVE}/{DATABASE_NAME}"
                or marker.get("authority_id") != row["authority_id"]
                or marker.get("activation_boundary_sha256")
                    != row["activation_boundary_sha256"]
                or not hmac.compare_digest(supplied, expected)):
            raise ReceiptAuthorityError("RECEIPT_AUTHORITY_MARKER_INVALID")

    @property
    def active_now(self) -> bool:
        if not os.path.lexists(self.marker_path):
            return False
        self._validate_active_marker()
        return True

    @staticmethod
    def _receipt_from_row(row: sqlite3.Row) -> dict[str, Any]:
        receipt = {
            "schema": "emergency_record_idempotency_v1",
            "state": row["state"], "ledger": row["ledger"],
            "record_id": row["record_id"], "row_sha256": row["row_sha256"],
            "length": int(row["byte_length"]),
            "identity": json.loads(row["identity_json"]),
            "ledger_generation": json.loads(row["ledger_generation_json"]),
        }
        if row["byte_offset"] is not None:
            receipt["offset"] = int(row["byte_offset"])
        if row["row_payload_utf8"] is not None:
            receipt["row_payload_utf8"] = row["row_payload_utf8"]
        return receipt

    def get(self, ledger: str, record_id: str) -> dict[str, Any] | None:
        if ledger not in LEDGER_NAMES or not isinstance(record_id, str):
            raise ReceiptAuthorityError("RECEIPT_AUTHORITY_LOOKUP_INVALID")
        self._validate_active_marker()
        try:
            with self._connect(writable=False) as connection:
                row = connection.execute(
                    "SELECT * FROM record_receipts WHERE ledger=? AND record_id=?",
                    (ledger, record_id),
                ).fetchone()
        except sqlite3.Error as exc:
            raise ReceiptAuthorityError("RECEIPT_AUTHORITY_READ_FAILED") from exc
        return None if row is None else self._receipt_from_row(row)

    def _put_on(self, connection: sqlite3.Connection, receipt: Mapping[str, Any], *,
                legacy_receipt_sha256: str | None = None,
                legacy_receipt_name: str | None = None) -> None:
        ledger = str(receipt.get("ledger") or "")
        record_id = str(receipt.get("record_id") or "")
        state = str(receipt.get("state") or "")
        row_sha = str(receipt.get("row_sha256") or "")
        length = receipt.get("length")
        offset = receipt.get("offset")
        payload = receipt.get("row_payload_utf8")
        if (ledger not in LEDGER_NAMES or not record_id
                or len(record_id.encode("utf-8")) > MAX_RECORD_ID_BYTES
                or state not in {"DEFERRED", "PREPARED", "COMMITTED"}
                or not re.fullmatch(r"[0-9a-f]{64}", row_sha)
                or type(length) is not int or not 0 < length <= MAX_ROW_BYTES
                or (state != "DEFERRED" and (type(offset) is not int or offset < 0))
                or (state == "DEFERRED" and (not isinstance(payload, str)
                    or len(payload.encode("utf-8")) != length))):
            raise ReceiptAuthorityError("RECEIPT_AUTHORITY_RECEIPT_INVALID")
        identity_json = _identity_json(receipt.get("identity") or {})
        generation_json = _generation_json(receipt.get("ledger_generation") or {})
        if identity_json != self.identity_json:
            raise ReceiptAuthorityError("RECEIPT_AUTHORITY_IDENTITY_MISMATCH")
        old = connection.execute(
            "SELECT * FROM record_receipts WHERE ledger=? AND record_id=?", (ledger, record_id)
        ).fetchone()
        values = (state, row_sha, offset, length, identity_json, generation_json,
                  payload, legacy_receipt_sha256, legacy_receipt_name, ledger, record_id)
        if old is None:
            connection.execute(
                "INSERT INTO record_receipts VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (ledger, record_id, state, row_sha, offset, length, identity_json,
                 generation_json, payload, legacy_receipt_sha256, legacy_receipt_name),
            )
            return
        immutable = (old["row_sha256"] == row_sha and old["byte_length"] == length
                     and old["identity_json"] == identity_json
                     and old["ledger_generation_json"] == generation_json
                     and (old["state"] == "DEFERRED"
                          or old["byte_offset"] == offset))
        transition = (old["state"], state) in {
            ("DEFERRED", "PREPARED"), ("PREPARED", "COMMITTED"),
        }
        exact = self._receipt_from_row(old) == {
            key: value for key, value in dict(receipt).items()
            if key in {"schema", "state", "ledger", "record_id", "row_sha256", "offset",
                       "length", "identity", "ledger_generation", "row_payload_utf8"}
        }
        if not exact and not (immutable and transition):
            raise ReceiptAuthorityError("RECEIPT_AUTHORITY_DUPLICATE_CONFLICT")
        if transition:
            connection.execute(
                "UPDATE record_receipts SET state=?,row_sha256=?,byte_offset=?,byte_length=?,"
                "identity_json=?,ledger_generation_json=?,row_payload_utf8=?,"
                "legacy_receipt_sha256=COALESCE(?,legacy_receipt_sha256),"
                "legacy_receipt_name=COALESCE(?,legacy_receipt_name) WHERE ledger=? AND record_id=?",
                values,
            )

    def put(self, receipt: Mapping[str, Any]) -> dict[str, Any]:
        self._validate_active_marker()
        try:
            with self._transaction() as connection:
                self._put_on(connection, receipt)
        except sqlite3.Error as exc:
            raise ReceiptAuthorityError("RECEIPT_AUTHORITY_WRITE_FAILED") from exc
        result = self.get(str(receipt["ledger"]), str(receipt["record_id"]))
        if result is None:
            raise ReceiptAuthorityError("RECEIPT_AUTHORITY_WRITE_UNPROVABLE")
        return result

    def set_ledger_complete(self, ledger: str, signature: Mapping[str, Any] | None,
                            anchor: Mapping[str, Any] | None) -> None:
        self._validate_active_marker()
        if ledger not in LEDGER_NAMES:
            raise ReceiptAuthorityError("RECEIPT_AUTHORITY_LEDGER_INVALID")
        try:
            with self._transaction() as connection:
                changed = connection.execute(
                    "UPDATE ledger_state SET ledger_signature_json=?,tail_anchor_json=?,complete=1,"
                    "import_cursor=?,source_device=?,source_inode=? WHERE ledger=?",
                    (None if signature is None else canonical_json(dict(signature)),
                     None if anchor is None else canonical_json(dict(anchor)),
                     0 if signature is None else int(signature["size"]),
                     None if signature is None else int(signature["device"]),
                     None if signature is None else int(signature["inode"]), ledger),
                ).rowcount
                if changed != 1:
                    raise ReceiptAuthorityError("RECEIPT_AUTHORITY_LEDGER_STATE_MISSING")
        except (KeyError, TypeError, ValueError, sqlite3.Error) as exc:
            raise ReceiptAuthorityError("RECEIPT_AUTHORITY_LEDGER_STATE_WRITE_FAILED") from exc

    def ledger_complete(self, ledger: str, signature: Mapping[str, Any] | None,
                        anchor_validator) -> bool:
        self._validate_active_marker()
        try:
            with self._connect(writable=False) as connection:
                row = connection.execute("SELECT * FROM ledger_state WHERE ledger=?", (ledger,)).fetchone()
        except sqlite3.Error as exc:
            raise ReceiptAuthorityError("RECEIPT_AUTHORITY_LEDGER_STATE_READ_FAILED") from exc
        if row is None or row["complete"] != 1:
            return False
        expected = None if signature is None else canonical_json(dict(signature))
        if row["ledger_signature_json"] != expected:
            return False
        anchor = None if row["tail_anchor_json"] is None else json.loads(row["tail_anchor_json"])
        return signature is None or int(signature["size"]) == 0 or bool(anchor_validator(anchor))

    def generation_import_complete(
        self, ledger: str, ledger_path: Path, generation: Mapping[str, Any],
    ) -> bool:
        generation_json = _generation_json(generation)
        signature = _signature_payload(ledger_path)
        expected = None if signature is None else canonical_json(signature)
        with self._connect(writable=False) as connection:
            row = connection.execute(
                "SELECT complete,ledger_signature_json FROM generation_import "
                "WHERE ledger=? AND ledger_generation_json=?",
                (ledger, generation_json),
            ).fetchone()
        return bool(row is not None and row["complete"] == 1
                    and row["ledger_signature_json"] == expected)

    def advance_import(self, ledger: str, ledger_path: Path,
                       generation: Mapping[str, Any], *, max_bytes: int,
                       max_records: int, receipt_generation_validator=None) -> dict[str, Any]:
        """Import one stable ledger-generation prefix and validate each receipt."""
        if os.path.lexists(self.marker_path):
            raise ReceiptAuthorityError("RECEIPT_AUTHORITY_ALREADY_ACTIVE")
        self._validate_meta(expected_state={"IMPORTING"})
        if ledger not in LEDGER_NAMES:
            raise ReceiptAuthorityError("RECEIPT_AUTHORITY_LEDGER_INVALID")
        byte_limit = max(1, min(int(max_bytes), MAX_IMPORT_BYTES))
        record_limit = max(1, min(int(max_records), MAX_IMPORT_RECORDS))
        generation_json = _generation_json(generation)
        before = _signature_payload(ledger_path)
        with self._transaction() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO generation_import VALUES(?,?,?,?,?,?,?,?,?)",
                (ledger, generation_json, None, None, 0, 0, 0, None, None),
            )
            progress = connection.execute(
                "SELECT * FROM generation_import WHERE ledger=? AND ledger_generation_json=?",
                (ledger, generation_json),
            ).fetchone()
        cursor = int(progress["import_cursor"])
        imported = int(progress["imported_count"])
        prior_signature = progress["ledger_signature_json"]
        if before is None:
            if cursor or imported:
                raise ReceiptAuthorityError("RECEIPT_AUTHORITY_IMPORT_SOURCE_DISAPPEARED")
            with self._transaction() as connection:
                connection.execute(
                    "UPDATE generation_import SET ledger_signature_json=NULL,"
                    "tail_anchor_json=NULL,complete=1 WHERE ledger=? "
                    "AND ledger_generation_json=?", (ledger, generation_json),
                )
            return {"ledger": ledger, "ledger_generation": dict(generation),
                    "complete": True, "cursor": 0,
                    "records_imported": 0, "bytes_imported": 0}
        if cursor > int(before["size"]):
            raise ReceiptAuthorityError("RECEIPT_AUTHORITY_IMPORT_SOURCE_REWOUND")
        if cursor and (progress["source_device"] != before["device"]
                       or progress["source_inode"] != before["inode"]):
            raise ReceiptAuthorityError("RECEIPT_AUTHORITY_IMPORT_SOURCE_REPLACED")
        if (prior_signature is not None
                and prior_signature != canonical_json(before)):
            raise ReceiptAuthorityError("RECEIPT_AUTHORITY_IMPORT_SOURCE_CHANGED")
        rows: list[tuple[dict[str, Any], str, str]] = []
        consumed = 0
        with ledger_path.open("rb") as source:
            source.seek(cursor)
            while (cursor < int(before["size"]) and consumed < byte_limit
                   and len(rows) < record_limit):
                offset = cursor
                raw = source.readline(MAX_ROW_BYTES + 1)
                if not raw or len(raw) > MAX_ROW_BYTES or not raw.endswith(b"\n"):
                    raise ReceiptAuthorityError("RECEIPT_AUTHORITY_IMPORT_LEDGER_ROW_INVALID")
                try:
                    payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs)
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                    raise ReceiptAuthorityError("RECEIPT_AUTHORITY_IMPORT_LEDGER_ROW_INVALID") from exc
                record_id = str(payload.get("record_id") or "") if isinstance(payload, dict) else ""
                if not record_id or len(record_id.encode("utf-8")) > MAX_RECORD_ID_BYTES:
                    raise ReceiptAuthorityError("RECEIPT_AUTHORITY_IMPORT_RECORD_ID_INVALID")
                digest = hashlib.sha256(f"{ledger}\0{record_id}".encode("utf-8")).hexdigest()
                legacy_name = f"{digest}.json"
                legacy_path = self.root / "v3/receipts/emergency_record_idempotency_v1" / ledger / legacy_name
                receipt, receipt_raw = _strict_json_file(legacy_path)
                expected = {
                    "schema": "emergency_record_idempotency_v1", "state": "COMMITTED",
                    "ledger": ledger, "record_id": record_id,
                    "row_sha256": hashlib.sha256(raw).hexdigest(),
                    "offset": offset, "length": len(raw), "identity": self.identity,
                }
                if (any(receipt.get(key) != value for key, value in expected.items())
                        or not isinstance(receipt.get("ledger_generation"), dict)):
                    raise ReceiptAuthorityError("RECEIPT_AUTHORITY_IMPORT_RECEIPT_MISMATCH")
                if receipt_generation_validator is not None:
                    try:
                        generation_valid = bool(receipt_generation_validator(receipt))
                    except (OSError, ValueError, RuntimeError):
                        generation_valid = False
                    if not generation_valid:
                        raise ReceiptAuthorityError("RECEIPT_AUTHORITY_IMPORT_RECEIPT_MISMATCH")
                authoritative = {**expected, "ledger_generation": receipt["ledger_generation"]}
                rows.append((authoritative, hashlib.sha256(receipt_raw).hexdigest(), legacy_name))
                cursor += len(raw); consumed += len(raw)
        after = _signature_payload(ledger_path)
        if after != before:
            raise ReceiptAuthorityError("RECEIPT_AUTHORITY_IMPORT_SOURCE_CHANGED")
        anchor = None if not rows else {
            "offset": int(rows[-1][0]["offset"]), "length": int(rows[-1][0]["length"]),
            "sha256": str(rows[-1][0]["row_sha256"]),
        }
        try:
            with self._transaction() as connection:
                current = connection.execute(
                    "SELECT * FROM generation_import WHERE ledger=? AND ledger_generation_json=?",
                    (ledger, generation_json),
                ).fetchone()
                if current is None or int(current["import_cursor"]) != cursor - consumed:
                    raise ReceiptAuthorityError("RECEIPT_AUTHORITY_IMPORT_CURSOR_CHANGED")
                for receipt, legacy_sha, legacy_name in rows:
                    self._put_on(connection, receipt, legacy_receipt_sha256=legacy_sha,
                                 legacy_receipt_name=legacy_name)
                complete = cursor == int(before["size"])
                prior_anchor = current["tail_anchor_json"]
                connection.execute(
                    "UPDATE generation_import SET ledger_signature_json=?,tail_anchor_json=?,complete=?,"
                    "import_cursor=?,imported_count=?,source_device=?,source_inode=? "
                    "WHERE ledger=? AND ledger_generation_json=?",
                    (canonical_json(before), canonical_json(anchor) if anchor is not None else prior_anchor,
                     int(complete), cursor, imported + len(rows), before["device"], before["inode"],
                     ledger, generation_json),
                )
        except sqlite3.Error as exc:
            raise ReceiptAuthorityError("RECEIPT_AUTHORITY_IMPORT_WRITE_FAILED") from exc
        return {"ledger": ledger, "ledger_generation": dict(generation),
                "complete": cursor == int(before["size"]),
                "cursor": cursor, "records_imported": len(rows), "bytes_imported": consumed}

    def finalize_ledger_import(
        self, ledger: str,
        generations: Iterable[tuple[Mapping[str, Any], Path]],
        active_signature: Mapping[str, Any] | None,
    ) -> None:
        """Publish aggregate completeness after every generation is exact."""
        generation_rows = list(generations)
        with self._transaction() as connection:
            total = 0
            active_anchor = None
            for generation, path in generation_rows:
                generation_json = _generation_json(generation)
                row = connection.execute(
                    "SELECT * FROM generation_import WHERE ledger=? AND ledger_generation_json=?",
                    (ledger, generation_json),
                ).fetchone()
                signature = _signature_payload(path)
                expected = None if signature is None else canonical_json(signature)
                if row is None or row["complete"] != 1 or row["ledger_signature_json"] != expected:
                    raise ReceiptAuthorityError("RECEIPT_AUTHORITY_IMPORT_INCOMPLETE")
                total += int(row["imported_count"])
                if generation.get("state") == "ACTIVE":
                    active_anchor = row["tail_anchor_json"]
            receipt_count = int(connection.execute(
                "SELECT COUNT(*) FROM record_receipts WHERE ledger=?", (ledger,)
            ).fetchone()[0])
            if total != receipt_count:
                raise ReceiptAuthorityError("RECEIPT_AUTHORITY_IMPORT_COUNT_MISMATCH")
            changed = connection.execute(
                "UPDATE ledger_state SET ledger_signature_json=?,tail_anchor_json=?,complete=1,"
                "import_cursor=?,imported_count=?,source_device=?,source_inode=? WHERE ledger=?",
                (None if active_signature is None else canonical_json(dict(active_signature)),
                 active_anchor, 0 if active_signature is None else int(active_signature["size"]),
                 receipt_count,
                 None if active_signature is None else int(active_signature["device"]),
                 None if active_signature is None else int(active_signature["inode"]), ledger),
            ).rowcount
            if changed != 1:
                raise ReceiptAuthorityError("RECEIPT_AUTHORITY_IMPORT_STATE_MISSING")

    def activate(self, ledger_paths: Mapping[str, Path], generations: Mapping[str, Mapping[str, Any]],
                 boundary_proof: Mapping[str, Any]) -> dict[str, Any]:
        """Atomically publish authority after caller freezes all writers/reset."""
        if (boundary_proof.get("schema") != ACTIVATION_BOUNDARY_SCHEMA
                or boundary_proof.get("identity") != self.identity
                or boundary_proof.get("writers_quiesced") is not True
                or boundary_proof.get("reset_mutators_quiesced") is not True
                or not isinstance(boundary_proof.get("nonce"), str)
                or not boundary_proof["nonce"]):
            raise ReceiptAuthorityError("RECEIPT_AUTHORITY_ACTIVATION_BOUNDARY_INVALID")
        boundary_sha256 = hashlib.sha256(
            canonical_json(dict(boundary_proof)).encode("utf-8")
        ).hexdigest()
        if os.path.lexists(self.marker_path):
            self._validate_active_marker()
            marker, _ = _strict_json_file(self.marker_path)
            return marker
        meta = self._validate_meta(expected_state={"IMPORTING", "ACTIVE"})
        ledger_summary = {}
        with self._connect(writable=False) as connection:
            for ledger in LEDGER_NAMES:
                if ledger not in ledger_paths or ledger not in generations:
                    raise ReceiptAuthorityError("RECEIPT_AUTHORITY_ACTIVATION_SCOPE_INCOMPLETE")
                signature = _signature_payload(ledger_paths[ledger])
                state = connection.execute("SELECT * FROM ledger_state WHERE ledger=?", (ledger,)).fetchone()
                count = connection.execute("SELECT COUNT(*) FROM record_receipts WHERE ledger=?", (ledger,)).fetchone()[0]
                expected_signature = None if signature is None else canonical_json(signature)
                if (state is None or state["complete"] != 1
                        or state["ledger_signature_json"] != expected_signature
                        or int(state["import_cursor"]) != (0 if signature is None else int(signature["size"]))
                        or int(state["imported_count"]) != int(count)):
                    raise ReceiptAuthorityError("RECEIPT_AUTHORITY_IMPORT_INCOMPLETE")
                receipt_dir = self.root / "v3/receipts/emergency_record_idempotency_v1" / ledger
                names = {}
                if receipt_dir.exists():
                    for entry in os.scandir(receipt_dir):
                        if entry.name in {"complete.json", "bootstrap.json"}:
                            continue
                        if len(names) >= MAX_LEGACY_RECEIPTS_PER_LEDGER:
                            raise ReceiptAuthorityError("RECEIPT_AUTHORITY_LEGACY_RECEIPT_LIMIT")
                        if not re.fullmatch(r"[0-9a-f]{64}\.json", entry.name) or not entry.is_file(follow_symlinks=False):
                            raise ReceiptAuthorityError("RECEIPT_AUTHORITY_LEGACY_RECEIPT_SHAPE_INVALID")
                        _receipt, receipt_raw = _strict_json_file(Path(entry.path))
                        names[entry.name] = hashlib.sha256(receipt_raw).hexdigest()
                database_names = {row[0]: row[1] for row in connection.execute(
                    "SELECT legacy_receipt_name,legacy_receipt_sha256 "
                    "FROM record_receipts WHERE ledger=?", (ledger,)
                )}
                if names != database_names:
                    raise ReceiptAuthorityError("RECEIPT_AUTHORITY_LEGACY_COVERAGE_MISMATCH")
                ledger_summary[ledger] = {"signature": signature, "record_count": int(count),
                                          "ledger_generation": dict(generations[ledger])}
        with self._transaction() as connection:
            if meta["state"] == "IMPORTING":
                changed = connection.execute(
                    "UPDATE authority_meta SET state='ACTIVE',activation_boundary_sha256=? "
                    "WHERE singleton=1 AND state='IMPORTING'",
                    (boundary_sha256,),
                ).rowcount
                if changed != 1:
                    raise ReceiptAuthorityError("RECEIPT_AUTHORITY_ACTIVATION_STATE_CHANGED")
            elif meta["activation_boundary_sha256"] != boundary_sha256:
                raise ReceiptAuthorityError("RECEIPT_AUTHORITY_ACTIVATION_PROOF_CHANGED")
            authority_id = connection.execute(
                "SELECT authority_id FROM authority_meta WHERE singleton=1"
            ).fetchone()[0]
        # Force the ACTIVE state and imported rows into the main file before it
        # becomes inventory-visible. The sync layer must still use online backup.
        with self._connect(writable=True) as connection:
            checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if checkpoint is None or int(checkpoint[0]) != 0:
                raise ReceiptAuthorityError("RECEIPT_AUTHORITY_CHECKPOINT_BUSY")
        marker = {
            "schema": MARKER_SCHEMA, "state": "ACTIVE", "identity": self.identity,
            "authority_id": authority_id,
            "database_relative": f"{AUTHORITY_RELATIVE}/{DATABASE_NAME}",
            "legacy_receipts_preserved": True, "ledgers": ledger_summary,
            "activation_boundary_sha256": boundary_sha256,
        }
        marker["binding_sha256"] = hashlib.sha256(
            canonical_json(marker).encode("utf-8")
        ).hexdigest()
        _atomic_json(self.marker_path, marker)
        self._validate_active_marker()
        return marker

    def audit(self) -> dict[str, Any]:
        marker, raw = _strict_json_file(self.marker_path, 64 * 1024)
        self._validate_active_marker()
        self._validate_meta(expected_state={"ACTIVE"}, quick_check=True)
        with self._connect(writable=False) as connection:
            receipt_count = int(connection.execute("SELECT COUNT(*) FROM record_receipts").fetchone()[0])
            states = {row[0]: int(row[1]) for row in connection.execute(
                "SELECT state,COUNT(*) FROM record_receipts GROUP BY state"
            )}
        return {"active": True, "identity": dict(self.identity),
                "authority_id": marker["authority_id"], "receipt_count": receipt_count,
                "state_counts": states, "marker_sha256": hashlib.sha256(raw).hexdigest(),
                "database_relative": marker["database_relative"]}


def legacy_receipt_path(root: str | Path, ledger: str, record_id: str) -> Path:
    digest = hashlib.sha256(f"{ledger}\0{record_id}".encode("utf-8")).hexdigest()
    return Path(root).resolve() / "v3/receipts/emergency_record_idempotency_v1" / ledger / f"{digest}.json"


def unpublished_authority_state(
    root: str | Path, expected_identity: Mapping[str, Any],
) -> str | None:
    """Classify an unmarked database without ever treating ambiguity as legacy."""
    database = Path(root).resolve() / AUTHORITY_RELATIVE / DATABASE_NAME
    if not database.exists():
        return None
    if database.is_symlink() or not database.is_file():
        return "INVALID"
    try:
        uri = f"file:{database.as_posix()}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=BUSY_TIMEOUT_MS / 1000) as connection:
            row = connection.execute(
                "SELECT schema_name,identity_json,state FROM authority_meta WHERE singleton=1"
            ).fetchone()
        expected_identity_json = _identity_json(expected_identity)
    except (sqlite3.Error, ReceiptAuthorityError):
        return "INVALID"
    if (row is None or row[0] != SCHEMA or row[2] not in {"IMPORTING", "ACTIVE"}):
        return "INVALID"
    if row[1] != expected_identity_json:
        return "IDENTITY_MISMATCH"
    return str(row[2])


def read_legacy_receipt(root: str | Path, ledger: str, record_id: str) -> dict[str, Any] | None:
    path = legacy_receipt_path(root, ledger, record_id)
    if not path.exists():
        return None
    receipt, _ = _strict_json_file(path)
    return receipt


def audit_transactional_receipt_authority(
    root: str | Path, expected_identity: Mapping[str, Any],
) -> dict[str, Any]:
    """Read-only bounded audit for reset/recovery and operational diagnostics."""
    resolved = Path(root).resolve()
    directory = resolved / AUTHORITY_RELATIVE
    if not directory.exists():
        return {"present": False, "active": False}
    if directory.is_symlink() or not directory.is_dir():
        raise ReceiptAuthorityError("RECEIPT_AUTHORITY_DIRECTORY_INVALID")
    allowed = {DATABASE_NAME, DATABASE_NAME + "-wal", DATABASE_NAME + "-shm", MARKER_NAME}
    entries = list(directory.iterdir())
    if len(entries) > len(allowed) or any(
        entry.name not in allowed or entry.is_symlink() or not entry.is_file()
        for entry in entries
    ):
        raise ReceiptAuthorityError("RECEIPT_AUTHORITY_DIRECTORY_SHAPE_INVALID")
    marker = directory / MARKER_NAME
    database = directory / DATABASE_NAME
    if not marker.exists():
        if database.exists():
            return {
                "present": True, "active": False,
                "state": "IMPORTING_OR_UNPUBLISHED",
                "database_relative": f"{AUTHORITY_RELATIVE}/{DATABASE_NAME}",
            }
        raise ReceiptAuthorityError("RECEIPT_AUTHORITY_DIRECTORY_EMPTY_OR_INVALID")
    backend = TransactionalReceiptStore(resolved, expected_identity, create=False)
    return {"present": True, **backend.audit()}
