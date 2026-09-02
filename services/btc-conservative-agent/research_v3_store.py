"""Crash-safe normalized evidence store for Research Collector V3."""
from __future__ import annotations

import hashlib
import errno
import json
import os
import subprocess
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from research_v3_contract import EVIDENCE_SCHEMA, LEDGER_NAMES, canonical_json
from combo_pathway_config import active_tile_registry_signature
from collector_storage import emergency_admission, storage_blocks_new_nonessential_research
from emergency_evidence_wal import EmergencyEvidenceWal

_locks_guard = threading.Lock()
_locks: dict[str, threading.RLock] = {}
_id_cache: dict[str, tuple[tuple[int, int, int, int] | None, frozenset[str]]] = {}
_segment_hash_cache: dict[str, tuple[tuple[int, int, int, int], str]] = {}
_provenance_cache: dict[str, str] | None = None
_MAX_MEMBERSHIP_EPISODE_ID_BYTES = 512
_MAX_RECEIPT_RECORD_ID_BYTES = 1024
_MAX_RECEIPT_ROW_BYTES = 8 * 1024 * 1024
_BOOTSTRAP_BYTES_PER_STEP = 8 * 1024 * 1024


def _first_present(*values: Any) -> Any:
    return next((value for value in values if value not in (None, "")), None)


def project_opportunity_causal_identity(row: dict[str, Any]) -> dict[str, Any]:
    """Project one truthful, immutable identity for a shared opportunity.

    This function is also the compatibility reader for historical rows.  It
    never guesses a missing value: absent historical fields are rendered as
    ``UNKNOWN`` while future appends persist this projection centrally.
    Analyzer revision is deliberately unknown at collection time and is bound
    later by the derived binding generation.
    """
    feature = row.get("feature_snapshot_at_signal")
    feature = feature if isinstance(feature, dict) else {}
    market_context = feature.get("market_context")
    market_context = market_context if isinstance(market_context, dict) else {}
    cycle = feature.get("cycle_3m_universe")
    cycle = cycle if isinstance(cycle, dict) else {}
    source_features = feature.get("source_features")
    source_features = source_features if isinstance(source_features, dict) else {}

    signal_ts = _first_present(row.get("signal_ts"), feature.get("captured_for_signal_ts"))
    try:
        signal_ts = float(signal_ts)
        signal_iso = datetime.fromtimestamp(signal_ts, timezone.utc).isoformat().replace("+00:00", "Z")
        signal_timezone = str(_first_present(row.get("signal_timezone"), "UTC"))
    except (TypeError, ValueError, OverflowError, OSError):
        signal_ts, signal_iso, signal_timezone = None, "UNKNOWN", "UNKNOWN"

    direction = str(_first_present(
        row.get("raw_direction"), row.get("executed_direction"),
        source_features.get("raw_direction"), source_features.get("executed_direction"),
        source_features.get("final_direction"),
    ) or "UNKNOWN").upper()
    if direction not in {"LONG", "SHORT"}:
        direction = "UNKNOWN"

    identity = {
        "schema": "v3_opportunity_causal_identity_v1",
        "source_revision": str(row.get("source_revision") or "UNKNOWN"),
        "deployed_revision": str(row.get("deployed_revision") or "UNKNOWN"),
        "analyzer_revision": str(row.get("analyzer_revision") or "UNKNOWN"),
        "dataset_epoch": str(_first_present(row.get("epoch_id"), row.get("dataset_epoch")) or "UNKNOWN"),
        "tile_config_signature": str(row.get("tile_config_signature") or "UNKNOWN"),
        "opportunity_id": str(_first_present(row.get("opportunity_id"), row.get("record_id")) or "UNKNOWN"),
        "episode_id": str(row.get("episode_id") or "UNKNOWN"),
        "shared_ai_call_id": str(row.get("shared_ai_call_id") or "UNKNOWN"),
        "signal_ts": signal_ts,
        "signal_timestamp_utc": signal_iso,
        "signal_timezone": signal_timezone,
        "market": str(_first_present(
            row.get("market"), row.get("exchange"), row.get("venue"),
            source_features.get("market"), source_features.get("exchange"),
            market_context.get("market"), market_context.get("exchange"),
        ) or "UNKNOWN").upper(),
        "symbol": str(_first_present(
            row.get("symbol"), feature.get("symbol"), source_features.get("symbol"),
            market_context.get("symbol"),
        ) or "UNKNOWN").upper(),
        "direction": direction,
        "policy_identity_scope": "SHARED_OPPORTUNITY_MULTI_POLICY",
        "regime_volatility": {
            "market_regime": str(_first_present(
                row.get("market_regime"), row.get("regime"), feature.get("market_regime"),
                feature.get("regime"), source_features.get("entry_regime"),
                source_features.get("regime"), market_context.get("regime_label"),
                market_context.get("regime"),
            ) or "UNKNOWN").upper(),
            "atr14_pct_3m": _first_present(
                row.get("atr14_pct_3m"), feature.get("atr14_pct_3m"),
                cycle.get("atr14_pct_3m"), market_context.get("atr14_pct_3m"),
            ),
            "realized_volatility": _first_present(
                row.get("realized_volatility"), feature.get("realized_volatility"),
                feature.get("realized_volatility_pct"), cycle.get("realized_volatility"),
                cycle.get("realized_volatility_pct"), cycle.get("realized_volatility_30m_pct"),
                market_context.get("realized_volatility"),
            ),
            "volatility_of_volatility": _first_present(
                row.get("volatility_of_volatility"), feature.get("volatility_of_volatility"),
                cycle.get("volatility_of_volatility"), cycle.get("volatility_of_volatility_30m_pct"),
                market_context.get("volatility_of_volatility"),
            ),
            "adx": _first_present(
                row.get("adx"), feature.get("adx"), cycle.get("adx14"),
                cycle.get("adx"), market_context.get("adx"),
            ),
        },
    }
    required_paths = {
        "source_revision": identity["source_revision"],
        "deployed_revision": identity["deployed_revision"],
        "dataset_epoch": identity["dataset_epoch"],
        "tile_config_signature": identity["tile_config_signature"],
        "opportunity_id": identity["opportunity_id"],
        "episode_id": identity["episode_id"],
        "shared_ai_call_id": identity["shared_ai_call_id"],
        "signal_timestamp_utc": identity["signal_timestamp_utc"],
        "signal_timezone": identity["signal_timezone"],
        "market": identity["market"],
        "symbol": identity["symbol"],
        "direction": identity["direction"],
        "market_regime": identity["regime_volatility"]["market_regime"],
        "atr14_pct_3m": identity["regime_volatility"]["atr14_pct_3m"],
        "realized_volatility": identity["regime_volatility"]["realized_volatility"],
    }
    identity["missing_fields"] = sorted(
        name for name, value in required_paths.items() if value in (None, "", "UNKNOWN")
    )
    identity["collection_identity_complete"] = not identity["missing_fields"]
    return identity


def _collection_provenance() -> dict[str, str]:
    """Bind every newly appended ledger row to its running source/config.

    Epoch identity intentionally survives deployments, so it cannot by itself
    prove which code/config emitted a row.  Resolve this once per process and
    stamp centrally; individual collectors must not be able to omit it.
    """
    global _provenance_cache
    if _provenance_cache is not None:
        return dict(_provenance_cache)
    deployed = str(os.getenv("SOURCE_GIT_REV") or "").strip()
    source = deployed
    if not source:
        for name in ("GIT_COMMIT", "GITHUB_SHA", "RAILWAY_GIT_COMMIT_SHA"):
            candidate = str(os.getenv(name) or "").strip()
            if candidate:
                source = candidate
                break
    if not source:
        try:
            completed = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=Path(__file__).resolve().parent,
                capture_output=True,
                text=True,
                timeout=4,
                check=False,
            )
            if completed.returncode == 0:
                source = str(completed.stdout or "").strip()
        except (OSError, subprocess.SubprocessError):
            source = ""
    _provenance_cache = {
        "evidence_provenance_schema": "v3_collection_provenance_v1",
        "source_revision": source or "UNKNOWN",
        "deployed_revision": deployed or "NOT_DEPLOYED_LOCAL",
        "tile_config_signature": active_tile_registry_signature(),
    }
    return dict(_provenance_cache)


def _path_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _locks_guard:
        return _locks.setdefault(key, threading.RLock())


def _path_signature(path: Path) -> tuple[int, int, int, int] | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return (int(stat.st_dev), int(stat.st_ino), int(stat.st_size), int(stat.st_mtime_ns))


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def _advisory_file_lock(path: Path):
    """Hold one exclusive byte-range/file lock across processes.

    A separate lock file is used so replacing a content-addressed object never
    changes the inode carrying the lock.  Both supported implementations are
    released by the operating system if a writer crashes.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
                os.fsync(handle.fileno())
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class V3EvidenceStore:
    """One JSONL ledger per entity plus content-addressed market segments.

    Ledger rows are immutable and idempotent by ``record_id``.  An index is a
    cache only; on startup it is rebuilt from the durable ledger, so an append
    that survives a crash can never be duplicated merely because index update
    did not complete.
    """

    def __init__(self, root: str | Path, *, epoch_id: str):
        self.root = Path(root).resolve()
        self.epoch_id = str(epoch_id)
        self.ledger_dir = self.root / "v3" / "ledgers"
        self.segment_dir = self.root / "v3" / "market_segments"
        self.receipt_dir = self.root / "v3" / "receipts"
        self.lock_dir = self.root / "v3" / ".locks"
        for path in (self.ledger_dir, self.segment_dir, self.receipt_dir, self.lock_dir):
            path.mkdir(parents=True, exist_ok=True)
        self.__emergency_wal: EmergencyEvidenceWal | None = None
        # Reserve must exist before pressure/ENOSPC. Local inspect-only runs do
        # not possess a deployed revision and therefore cannot create a
        # misleading production-bound reserve.
        if self._emergency_wal_identity_available():
            self._emergency_wal()

    def _emergency_wal(self) -> EmergencyEvidenceWal:
        """Open the preallocated mandatory-evidence reserve lazily.

        The reserve is deliberately outside the receipt tree: receipt growth is
        exactly what cannot be relied on after the filesystem enters pressure.
        A production identity mismatch fails closed in ``EmergencyEvidenceWal``.
        """
        if self.__emergency_wal is None:
            self.__emergency_wal = EmergencyEvidenceWal(
                self.root / "v3" / "emergency_evidence_wal_v2",
                identity=self._identity_binding(),
            )
        return self.__emergency_wal

    def _emergency_wal_identity_available(self) -> bool:
        identity = self._identity_binding()
        return all(
            value not in {"", "UNKNOWN", "UNAVAILABLE", "NOT_DEPLOYED_LOCAL"}
            for value in identity.values()
        )

    def _defer_mandatory_to_wal(
        self, ledger: str, record_id: str, material: dict[str, Any], line: str,
    ) -> dict[str, Any]:
        if not self._mandatory_lifecycle_write(ledger, material):
            raise RuntimeError("EMERGENCY_WAL_OPTIONAL_ROW_REFUSED")
        record = self._emergency_wal().defer(
            ledger=ledger, record_id=record_id, payload=line.encode("utf-8"),
        )
        return {
            "written": False, "duplicate": bool(record.get("duplicate")),
            "deferred": True, "record_id": record_id, "ledger": ledger,
            "wal_generation": record["generation"],
            "reason": "MANDATORY_ROW_DURABLY_DEFERRED_TO_PREALLOCATED_WAL",
        }

    def replay_one_emergency_wal_record(self) -> dict[str, Any]:
        """Replay at most one retained row into its canonical ledger.

        Replay is intentionally separate from release.  A crash at any point
        is repaired by canonical ``record_id`` idempotency followed by the
        exact receipt/byte proof persisted by ``mark_replayed``.
        """
        if storage_blocks_new_nonessential_research(str(self.root)):
            return {"replayed": False, "blocked": True, "reason": "STORAGE_EMERGENCY"}
        wal = self._emergency_wal()
        released = wal.release_oldest_if_acknowledged()
        if released.get("released") is True:
            return {"replayed": False, "released": True, **released}
        record = wal.oldest_record()
        if record is None:
            return {"replayed": False, "empty": True}
        try:
            row = json.loads(record["payload"].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("EMERGENCY_WAL_ROW_JSON_INVALID") from exc
        if not isinstance(row, dict) or row.get("record_id") != record["record_id"]:
            raise RuntimeError("EMERGENCY_WAL_ROW_IDENTITY_MISMATCH")
        result = self.append(record["ledger"], row)
        if not (result.get("written") is True or result.get("duplicate") is True):
            raise RuntimeError("EMERGENCY_WAL_CANONICAL_REPLAY_BLOCKED")
        replay = wal.mark_replayed(
            record["generation"], canonical_ledger=self.ledger_path(record["ledger"]),
            canonical_receipt=self._record_receipt_path(record["ledger"], record["record_id"]),
        )
        return {
            "replayed": True, "canonical_duplicate": bool(result.get("duplicate")),
            "generation": record["generation"], "state": replay["state"],
        }

    def emergency_wal_runtime_status(self) -> dict[str, Any]:
        """Return a bounded, content-free status for the fixed-size reserve."""
        wal = self._emergency_wal()
        raw = wal.status()
        records = raw.get("records") if isinstance(raw.get("records"), list) else []
        state_counts = {"PREPARED": 0, "DEFERRED": 0, "REPLAYED": 0}
        for record in records[:64]:
            if isinstance(record, dict) and record.get("state") in state_counts:
                state_counts[record["state"]] += 1
        oldest = min(
            (record for record in records if isinstance(record, dict)),
            key=lambda record: int(record.get("sequence") or 0), default=None,
        )
        return {
            "schema": "emergency_evidence_wal_runtime_status_v1",
            "observed_unix": time.time(),
            "identity": dict(wal.identity),
            "identity_sha256": str(raw.get("identity_sha256") or ""),
            "capacity_extents": int(raw.get("capacity_extents") or 0),
            "free_extents": int(raw.get("free_extents") or 0),
            "retained_count": int(raw.get("deferred_count") or 0),
            "retained_bytes": int(raw.get("deferred_bytes") or 0),
            "state_counts": state_counts,
            "oldest_generation": str((oldest or {}).get("generation") or "") or None,
            "oldest_state": str((oldest or {}).get("state") or "") or None,
            "alarms": [str(value)[:128] for value in list(raw.get("alarms") or [])[:32]],
        }

    def _assert_contained(self, path: Path) -> Path:
        resolved = path.resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(f"V3_STORE_PATH_OUTSIDE_ROOT:{resolved}") from exc
        return resolved

    def _lock_path(self, path: Path) -> Path:
        resolved = self._assert_contained(path)
        key = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()
        return self._assert_contained(self.lock_dir / f"{key}.lock")

    @contextmanager
    def _exclusive(self, path: Path):
        """Serialize one object in this process and across worker processes."""
        resolved = self._assert_contained(path)
        with _path_lock(resolved):
            with _advisory_file_lock(self._lock_path(resolved)):
                yield

    def ledger_path(self, name: str) -> Path:
        if name not in LEDGER_NAMES:
            raise ValueError(f"unknown V3 ledger: {name}")
        return self.ledger_dir / f"{name}.jsonl"

    def _active_ledger_generation(self, ledger: str) -> dict[str, Any]:
        """Stable identity for the pre-rotation active generation.

        Readers accept an absent value only as this ACTIVE generation. Future
        rotation may publish SEALED numeric generations without reinterpreting
        legacy receipts or their byte offsets.
        """
        path = self.ledger_path(ledger)
        return {
            "schema": "v3_ledger_generation_ref_v1",
            "state": "ACTIVE",
            "ledger": ledger,
            "generation": 0,
            "relative_path": path.relative_to(self.root).as_posix(),
        }

    def _receipt_targets_active_generation(self, ledger: str, receipt: dict[str, Any]) -> bool:
        generation = receipt.get("ledger_generation")
        return generation is None or generation == self._active_ledger_generation(ledger)

    @property
    def _lifecycle_membership_dir(self) -> Path:
        return self.receipt_dir / "lifecycle_membership_v1"

    def _atomic_json_receipt(self, path: Path, payload: dict[str, Any]) -> None:
        """Publish one small receipt without exposing a partial JSON file."""
        path = self._assert_contained(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        candidate: str | None = None
        try:
            fd, candidate = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(canonical_json(payload) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(candidate, path)
            candidate = None
            _fsync_directory(path.parent)
        finally:
            if candidate:
                try:
                    os.unlink(candidate)
                except OSError:
                    pass

    @staticmethod
    def _signature_payload(signature: tuple[int, int, int, int]) -> dict[str, int]:
        return dict(zip(("device", "inode", "size", "mtime_ns"), signature))

    @staticmethod
    def _bounded_slice(path: Path, offset: int, length: int) -> bytes | None:
        if offset < 0 or length < 1 or length > _MAX_RECEIPT_ROW_BYTES:
            return None
        try:
            fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_BINARY", 0))
            try:
                os.lseek(fd, offset, os.SEEK_SET)
                payload = os.read(fd, length)
            finally:
                os.close(fd)
        except OSError:
            return None
        return payload if len(payload) == length else None

    def _anchor_valid(self, ledger: Path, anchor: Any) -> bool:
        if not isinstance(anchor, dict):
            return False
        try:
            offset, length = int(anchor["offset"]), int(anchor["length"])
            expected = str(anchor["sha256"])
        except (KeyError, TypeError, ValueError):
            return False
        payload = self._bounded_slice(ledger, offset, length)
        return payload is not None and hashlib.sha256(payload).hexdigest() == expected

    def _identity_binding(self) -> dict[str, str]:
        provenance = _collection_provenance()
        return {
            "epoch_id": self.epoch_id,
            "source_revision": provenance["source_revision"],
            "deployed_revision": provenance["deployed_revision"],
            "tile_config_signature": provenance["tile_config_signature"],
        }

    def _record_lifecycle_membership(
        self, episode_id: str, signature: tuple[int, int, int, int], anchor: dict[str, Any]
    ) -> None:
        """Record membership before publishing the exact ledger-generation fence.

        A crash before the final binding replace leaves the old generation and
        therefore fails optional writes closed. It can never make an episode
        appear to exist in a ledger generation that was not durably appended.
        """
        encoded_episode = episode_id.encode("utf-8")
        if not encoded_episode or len(encoded_episode) > _MAX_MEMBERSHIP_EPISODE_ID_BYTES:
            return
        directory = self._lifecycle_membership_dir
        generation_id = ""
        try:
            previous = json.loads((directory / "current.json").read_text("utf-8"))
            prior_signature = previous.get("lifecycle_ledger") or {}
            same_lineage = (
                prior_signature.get("device") == signature[0]
                and prior_signature.get("inode") == signature[1]
            )
            append_only_advance = int(signature[2]) > int(prior_signature.get("size", -1))
            exact_same_file = prior_signature == self._signature_payload(signature)
            same_identity = previous.get("identity") == self._identity_binding()
            prior_anchor_valid = self._anchor_valid(self.ledger_path("lifecycle"), previous.get("tail_anchor"))
            if same_identity and same_lineage and prior_anchor_valid and (append_only_advance or exact_same_file):
                generation_id = str(previous.get("generation_id") or "")
        except (FileNotFoundError, OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
        generation_id = generation_id or str(uuid.uuid4())
        episode_hash = hashlib.sha256(episode_id.encode("utf-8")).hexdigest()
        self._atomic_json_receipt(directory / f"{episode_hash}.json", {
            "schema": "lifecycle_episode_membership_v1",
            "episode_id": episode_id,
            "episode_sha256": episode_hash,
            "generation_id": generation_id,
            "identity": self._identity_binding(),
        })
        self._atomic_json_receipt(directory / "current.json", {
            "schema": "lifecycle_membership_generation_v1",
            "generation_id": generation_id,
            "identity": self._identity_binding(),
            "lifecycle_ledger": self._signature_payload(signature),
            "tail_anchor": anchor,
            "ledger_generation": self._active_ledger_generation("lifecycle"),
        })

    def _episode_has_lifecycle_receipt(self, episode_id: str) -> bool:
        """O(1) fail-closed membership lookup; never opens the lifecycle ledger."""
        encoded_episode = episode_id.encode("utf-8")
        if not encoded_episode or len(encoded_episode) > _MAX_MEMBERSHIP_EPISODE_ID_BYTES:
            return False
        ledger = self.ledger_path("lifecycle")
        before = _path_signature(ledger)
        if before is None:
            return False
        episode_hash = hashlib.sha256(episode_id.encode("utf-8")).hexdigest()
        try:
            binding = json.loads((self._lifecycle_membership_dir / "current.json").read_text("utf-8"))
            marker = json.loads((self._lifecycle_membership_dir / f"{episode_hash}.json").read_text("utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return False
        after = _path_signature(ledger)
        return bool(
            before == after
            and binding.get("schema") == "lifecycle_membership_generation_v1"
            and isinstance(binding.get("generation_id"), str)
            and bool(binding.get("generation_id"))
            and binding.get("identity") == self._identity_binding()
            and self._receipt_targets_active_generation("lifecycle", binding)
            and binding.get("lifecycle_ledger") == self._signature_payload(before)
            and self._anchor_valid(ledger, binding.get("tail_anchor"))
            and marker.get("schema") == "lifecycle_episode_membership_v1"
            and marker.get("generation_id") == binding.get("generation_id")
            and marker.get("identity") == self._identity_binding()
            and marker.get("episode_id") == episode_id
            and marker.get("episode_sha256") == episode_hash
        )

    @staticmethod
    def _mandatory_lifecycle_write(ledger: str, row: dict[str, Any]) -> bool:
        record_id = str(row.get("record_id") or "").lower()
        state = str(row.get("outcome_state") or row.get("status") or "").upper()
        terminal = bool(row.get("terminal") or row.get("lifecycle_final")) or state in {
            "FULL_FILL", "PARTIAL_FILL", "NO_FILL", "NO_TRADE", "REJECTED",
            "REALIZED_PROFIT", "REALIZED_LOSS", "REALIZED_ZERO_PNL", "CLOSED",
            "CANCELLED", "EXPIRED",
        }
        return terminal or any(token in record_id for token in (
            ":terminal", ":primary-fill", ":paper-fill", ":paper-filled",
            ":paper-close", ":paper-closed", "qualification-horizon",
            "lane-entry:no-order", ":cancel", ":expired", ":reconciliation",
        ))

    def _emergency_admission(self, ledger: str, row: dict[str, Any]) -> dict[str, Any]:
        episode_id = str(row.get("episode_id") or "")
        mandatory = self._mandatory_lifecycle_write(ledger, row)
        emergency = storage_blocks_new_nonessential_research(str(self.root))
        return emergency_admission(
            data_dir=str(self.root), purpose=f"v3:{ledger}",
            lifecycle_required=mandatory,
            lifecycle_existing=(
                False if mandatory or not emergency
                else self._episode_has_lifecycle_receipt(episode_id)
            ),
        )

    def _record_receipt_path(self, ledger: str, record_id: str) -> Path:
        digest = hashlib.sha256(f"{ledger}\0{record_id}".encode("utf-8")).hexdigest()
        return self.receipt_dir / "emergency_record_idempotency_v1" / ledger / f"{digest}.json"

    def _completeness_path(self, ledger: str) -> Path:
        return self.receipt_dir / "emergency_record_idempotency_v1" / ledger / "complete.json"

    def _bootstrap_path(self, ledger: str) -> Path:
        return self.receipt_dir / "emergency_record_idempotency_v1" / ledger / "bootstrap.json"

    def _complete_generation(self, ledger: str, signature: tuple[int, int, int, int] | None) -> bool:
        try:
            receipt = json.loads(self._completeness_path(ledger).read_text("utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return False
        return bool(
            receipt.get("schema") == "emergency_record_index_complete_v1"
            and receipt.get("identity") == self._identity_binding()
            and receipt.get("ledger") == ledger
            and self._receipt_targets_active_generation(ledger, receipt)
            and receipt.get("ledger_signature") == (
                None if signature is None else self._signature_payload(signature)
            )
            and (
                signature is None or signature[2] == 0
                or self._anchor_valid(self.ledger_path(ledger), receipt.get("tail_anchor"))
            )
        )

    def _publish_record_receipt(
        self, ledger: str, record_id: str, *, offset: int, payload: bytes, state: str
    ) -> dict[str, Any]:
        receipt = {
            "schema": "emergency_record_idempotency_v1", "state": state,
            "ledger": ledger, "record_id": record_id,
            "row_sha256": hashlib.sha256(payload).hexdigest(),
            "offset": int(offset), "length": len(payload), "identity": self._identity_binding(),
            "ledger_generation": self._active_ledger_generation(ledger),
        }
        self._atomic_json_receipt(self._record_receipt_path(ledger, record_id), receipt)
        return receipt

    def advance_emergency_idempotency_bootstrap(
        self, ledger: str, *, max_bytes: int = _BOOTSTRAP_BYTES_PER_STEP
    ) -> dict[str, Any]:
        """Cooperatively index a bounded ledger prefix outside pressure only."""
        path = self.ledger_path(ledger)
        if storage_blocks_new_nonessential_research(str(self.root)):
            return {"complete": False, "blocked": True, "reason": "STORAGE_EMERGENCY"}
        limit = max(1, min(int(max_bytes), _BOOTSTRAP_BYTES_PER_STEP))
        with self._exclusive(path):
            signature = _path_signature(path)
            if signature is None:
                self._atomic_json_receipt(self._completeness_path(ledger), {
                    "schema": "emergency_record_index_complete_v1", "ledger": ledger,
                    "identity": self._identity_binding(), "ledger_signature": None,
                    "ledger_generation": self._active_ledger_generation(ledger),
                    "tail_anchor": None,
                })
                return {"complete": True, "bytes_indexed": 0, "cursor": 0}
            if self._complete_generation(ledger, signature):
                return {"complete": True, "bytes_indexed": 0, "cursor": signature[2]}
            state = {}
            try:
                state = json.loads(self._bootstrap_path(ledger).read_text("utf-8"))
            except (FileNotFoundError, OSError, json.JSONDecodeError):
                pass
            same_source = (
                state.get("schema") == "emergency_record_index_bootstrap_v1"
                and state.get("identity") == self._identity_binding()
                and (state.get("source") or {}).get("device") == signature[0]
                and (state.get("source") or {}).get("inode") == signature[1]
                and int(state.get("cursor", -1)) <= signature[2]
                and (
                    int(state.get("cursor", 0)) == 0
                    or self._anchor_valid(path, state.get("cursor_anchor"))
                )
            )
            cursor = int(state.get("cursor", 0)) if same_source else 0
            cursor_anchor = state.get("cursor_anchor") if same_source else None
            consumed = 0
            try:
                fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_BINARY", 0))
                with os.fdopen(fd, "rb") as handle:
                    handle.seek(cursor)
                    while consumed < limit and cursor < signature[2]:
                        offset = cursor
                        payload = handle.readline(_MAX_RECEIPT_ROW_BYTES + 1)
                        if not payload or len(payload) > _MAX_RECEIPT_ROW_BYTES or not payload.endswith(b"\n"):
                            raise ValueError("BOOTSTRAP_INVALID_OR_OVERSIZE_JSONL_ROW")
                        row = json.loads(payload.decode("utf-8"))
                        record_id = str(row.get("record_id") or "")
                        if not record_id or len(record_id.encode("utf-8")) > _MAX_RECEIPT_RECORD_ID_BYTES:
                            raise ValueError("BOOTSTRAP_INVALID_OR_OVERSIZE_RECORD_ID")
                        self._publish_record_receipt(
                            ledger, record_id, offset=offset, payload=payload, state="COMMITTED"
                        )
                        cursor += len(payload)
                        consumed += len(payload)
                        cursor_anchor = {
                            "offset": offset, "length": len(payload),
                            "sha256": hashlib.sha256(payload).hexdigest(),
                        }
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                return {"complete": False, "blocked": True, "reason": str(exc), "cursor": cursor}
            after = _path_signature(path)
            self._atomic_json_receipt(self._bootstrap_path(ledger), {
                "schema": "emergency_record_index_bootstrap_v1", "ledger": ledger,
                "identity": self._identity_binding(), "source": self._signature_payload(signature),
                "cursor": cursor, "cursor_anchor": cursor_anchor,
            })
            complete = after == signature and cursor == signature[2]
            if complete:
                self._atomic_json_receipt(self._completeness_path(ledger), {
                    "schema": "emergency_record_index_complete_v1", "ledger": ledger,
                    "identity": self._identity_binding(),
                    "ledger_generation": self._active_ledger_generation(ledger),
                    "ledger_signature": self._signature_payload(signature),
                    "tail_anchor": cursor_anchor,
                })
            return {"complete": complete, "bytes_indexed": consumed, "cursor": cursor}

    def _bootstrap_empty_ledgers(self) -> None:
        """Fence absent/empty ledgers cheaply before an emergency transition."""
        for name in LEDGER_NAMES:
            signature = _path_signature(self.ledger_path(name))
            if signature is None or signature[2] == 0:
                self.advance_emergency_idempotency_bootstrap(name, max_bytes=1)

    def _emergency_append(
        self, ledger: str, path: Path, record_id: str, material: dict[str, Any], line: str
    ) -> dict[str, Any]:
        """Append with bounded, crash-replayable idempotency at emergency."""
        payload = line.encode("utf-8")
        if (
            len(record_id.encode("utf-8")) > _MAX_RECEIPT_RECORD_ID_BYTES
            or len(payload) > _MAX_RECEIPT_ROW_BYTES
        ):
            return {
                "written": False, "duplicate": False, "blocked": True,
                "record_id": record_id, "ledger": ledger,
                "reason": "EMERGENCY_ID_OR_ROW_EXCEEDS_BOUNDED_RECEIPT_LIMIT",
            }
        receipt_path = self._record_receipt_path(ledger, record_id)
        expected_identity = self._identity_binding()
        row_sha = hashlib.sha256(payload).hexdigest()
        receipt = None
        try:
            receipt = json.loads(receipt_path.read_text("utf-8"))
        except FileNotFoundError:
            pass
        except (OSError, json.JSONDecodeError):
            return {
                "written": False, "duplicate": False, "blocked": True,
                "record_id": record_id, "ledger": ledger,
                "reason": "EMERGENCY_IDEMPOTENCY_RECEIPT_INVALID",
            }
        if receipt is not None:
            structurally_valid = (
                receipt.get("schema") == "emergency_record_idempotency_v1"
                and receipt.get("ledger") == ledger
                and receipt.get("record_id") == record_id
                and receipt.get("identity") == expected_identity
                and self._receipt_targets_active_generation(ledger, receipt)
            )
            exact_prepared = (
                structurally_valid and receipt.get("state") == "PREPARED"
                and receipt.get("row_sha256") == row_sha
                and receipt.get("length") == len(payload)
            )
            committed = structurally_valid and receipt.get("state") == "COMMITTED"
            deferred = (
                structurally_valid and receipt.get("state") == "DEFERRED"
                and receipt.get("row_sha256") == row_sha
                and receipt.get("length") == len(payload)
                and receipt.get("row_payload_utf8") == line
            )
            if not (exact_prepared or committed or deferred):
                return {
                    "written": False, "duplicate": False, "blocked": True,
                    "record_id": record_id, "ledger": ledger,
                    "reason": "EMERGENCY_IDEMPOTENCY_RECEIPT_MISMATCH",
                }
            if deferred:
                source_signature = _path_signature(path)
                if not self._complete_generation(ledger, source_signature):
                    return {
                        "written": False, "duplicate": False, "deferred": True,
                        "record_id": record_id, "ledger": ledger,
                        "reason": "MANDATORY_ROW_DURABLY_DEFERRED_PENDING_IDEMPOTENCY_BOOTSTRAP",
                    }
                offset = source_signature[2] if source_signature is not None else 0
                prepared = self._publish_record_receipt(
                    ledger, record_id, offset=offset, payload=payload, state="PREPARED"
                )
                with path.open("ab") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                anchor = {"offset": offset, "length": len(payload), "sha256": row_sha}
                if ledger == "lifecycle":
                    signature = _path_signature(path)
                    if signature is None:
                        raise RuntimeError("LIFECYCLE_LEDGER_MISSING_AFTER_DEFERRED_APPEND")
                    self._record_lifecycle_membership(
                        str(material.get("episode_id") or ""), signature, anchor
                    )
                prepared["state"] = "COMMITTED"
                self._atomic_json_receipt(receipt_path, prepared)
                final_signature = _path_signature(path)
                if final_signature is not None:
                    self._atomic_json_receipt(self._completeness_path(ledger), {
                        "schema": "emergency_record_index_complete_v1", "ledger": ledger,
                        "identity": self._identity_binding(),
                        "ledger_generation": self._active_ledger_generation(ledger),
                        "ledger_signature": self._signature_payload(final_signature),
                        "tail_anchor": anchor,
                    })
                return {
                    "written": True, "duplicate": False, "resumed_deferred": True,
                    "record_id": record_id, "ledger": ledger,
                }
            try:
                receipt_offset = int(receipt.get("offset", -1))
            except (TypeError, ValueError):
                receipt_offset = -1
            receipt_length = int(receipt.get("length", -1))
            durable = self._bounded_slice(path, receipt_offset, receipt_length)
            durable_hash = hashlib.sha256(durable).hexdigest() if durable is not None else ""
            if durable_hash != str(receipt.get("row_sha256") or ""):
                return {
                    "written": False, "duplicate": False, "blocked": True,
                    "record_id": record_id, "ledger": ledger,
                    "reason": "EMERGENCY_PREPARED_RECORD_NOT_PROVABLE",
                }
            anchor = {
                "offset": receipt_offset, "length": receipt_length,
                "sha256": str(receipt.get("row_sha256") or ""),
            }
            if ledger == "lifecycle" and exact_prepared:
                signature = _path_signature(path)
                if signature is None:
                    raise RuntimeError("LIFECYCLE_LEDGER_MISSING_DURING_RECEIPT_REPAIR")
                self._record_lifecycle_membership(str(material.get("episode_id") or ""), signature, anchor)
            repaired = dict(receipt)
            repaired["state"] = "COMMITTED"
            self._atomic_json_receipt(receipt_path, repaired)
            return {
                "written": False, "duplicate": True, "record_id": record_id, "ledger": ledger,
                "idempotency_receipt_repaired": exact_prepared,
            }

        source_signature = _path_signature(path)
        if not self._complete_generation(ledger, source_signature):
            # Mandatory terminal/reconciliation rows are still journalled and
            # replay-safe from this point forward. Optional rows require a
            # complete historical index before they may expand the ledger.
            if not self._mandatory_lifecycle_write(ledger, material):
                return {
                    "written": False, "duplicate": False, "blocked": True,
                    "record_id": record_id, "ledger": ledger,
                    "reason": "EMERGENCY_IDEMPOTENCY_INDEX_INCOMPLETE",
                }
            deferred = {
                "schema": "emergency_record_idempotency_v1", "state": "DEFERRED",
                "ledger": ledger, "record_id": record_id, "row_sha256": row_sha,
                "length": len(payload), "identity": expected_identity,
                "ledger_generation": self._active_ledger_generation(ledger),
                "row_payload_utf8": line,
            }
            self._atomic_json_receipt(receipt_path, deferred)
            return {
                "written": False, "duplicate": False, "deferred": True,
                "record_id": record_id, "ledger": ledger,
                "reason": "MANDATORY_ROW_DURABLY_DEFERRED_PENDING_IDEMPOTENCY_BOOTSTRAP",
            }
        offset = int(path.stat().st_size) if path.exists() else 0
        prepared = self._publish_record_receipt(
            ledger, record_id, offset=offset, payload=payload, state="PREPARED"
        )
        with path.open("ab") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        anchor = {"offset": offset, "length": len(payload), "sha256": row_sha}
        if ledger == "lifecycle":
            signature = _path_signature(path)
            if signature is None:
                raise RuntimeError("LIFECYCLE_LEDGER_MISSING_AFTER_DURABLE_APPEND")
            self._record_lifecycle_membership(str(material.get("episode_id") or ""), signature, anchor)
        prepared["state"] = "COMMITTED"
        self._atomic_json_receipt(receipt_path, prepared)
        final_signature = _path_signature(path)
        if final_signature is not None and self._complete_generation(ledger, source_signature):
            self._atomic_json_receipt(self._completeness_path(ledger), {
                "schema": "emergency_record_index_complete_v1", "ledger": ledger,
                "identity": self._identity_binding(),
                "ledger_generation": self._active_ledger_generation(ledger),
                "ledger_signature": self._signature_payload(final_signature),
                "tail_anchor": anchor,
            })
        return {"written": True, "duplicate": False, "record_id": record_id, "ledger": ledger}

    @staticmethod
    def _load_ids(path: Path) -> set[str]:
        ids: set[str] = set()
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line_no, line in enumerate(handle, 1):
                    if not line.endswith("\n"):
                        raise ValueError(f"TRUNCATED_JSONL_LINE:{line_no}")
                    row = json.loads(line)
                    record_id = str(row.get("record_id") or "")
                    if not record_id:
                        raise ValueError(f"MISSING_RECORD_ID:{line_no}")
                    if record_id in ids:
                        raise ValueError(f"DUPLICATE_RECORD_ID:{record_id}")
                    ids.add(record_id)
        except FileNotFoundError:
            pass
        return ids

    @classmethod
    def _cached_ids(cls, path: Path) -> set[str]:
        """Return durable IDs without reparsing an unchanged append ledger.

        The file signature makes the cache safe across separate store objects
        and invalidates it when another writer or recovery tool changes the
        ledger. The path lock held by ``append`` serializes the signature,
        parse and write sequence.
        """
        key = str(path.resolve())
        signature = _path_signature(path)
        cached = _id_cache.get(key)
        if cached is not None and cached[0] == signature:
            return set(cached[1])
        ids = cls._load_ids(path)
        _id_cache[key] = (signature, frozenset(ids))
        return ids

    def append(self, ledger: str, row: dict[str, Any]) -> dict[str, Any]:
        path = self.ledger_path(ledger)
        record_id = str(row.get("record_id") or "")
        if not record_id:
            raise ValueError("record_id is required")
        material = dict(row)
        material.update({
            "schema": EVIDENCE_SCHEMA,
            "ledger": ledger,
            "epoch_id": self.epoch_id,
            **_collection_provenance(),
        })
        if ledger == "opportunity":
            # Every producer path converges here.  Stamp the complete available
            # causal identity once rather than relying on each bridge caller to
            # remember an evolving metadata contract.
            material["causal_identity"] = project_opportunity_causal_identity(material)
            from research_entry_baselines import materialize_signal_time_baseline_schedules
            snapshot = materialize_signal_time_baseline_schedules(material)
            material["baseline_schedule_snapshot"] = snapshot
            material["baseline_schedules"] = snapshot["schedules"]
        line = canonical_json(material) + "\n"
        initial_emergency = storage_blocks_new_nonessential_research(str(self.root))
        if not initial_emergency:
            self._bootstrap_empty_ledgers()
            self.advance_emergency_idempotency_bootstrap(ledger)
        with self._exclusive(path):
            # A durable PREPARED receipt proves the exact bounded row location.
            # Let its replay repair a membership publication that crashed after
            # ledger fsync; ordinary admission would correctly see that stale
            # membership and otherwise strand the lifecycle forever.
            emergency_now = storage_blocks_new_nonessential_research(str(self.root))
            if emergency_now and self._record_receipt_path(ledger, record_id).exists():
                return self._emergency_append(ledger, path, record_id, material, line)
            admission = self._emergency_admission(ledger, material)
            if not admission["allowed"]:
                return {
                    "written": False, "duplicate": False, "blocked": True,
                    "record_id": record_id, "ledger": ledger,
                    "storage_admission": admission,
                }
            if admission.get("emergency"):
                if (
                    self._mandatory_lifecycle_write(ledger, material)
                    and self._emergency_wal_identity_available()
                ):
                    return self._defer_mandatory_to_wal(
                        ledger, record_id, material, line,
                    )
                return self._emergency_append(ledger, path, record_id, material, line)
            durable_ids = self._cached_ids(path)
            if record_id in durable_ids:
                return {"written": False, "duplicate": True, "record_id": record_id, "ledger": ledger}
            source_signature = _path_signature(path)
            source_complete = self._complete_generation(ledger, source_signature)
            offset = source_signature[2] if source_signature is not None else 0
            try:
                with path.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(line)
                    handle.flush()
                    os.fsync(handle.fileno())
            except OSError as exc:
                if (
                    exc.errno != errno.ENOSPC
                    or not self._mandatory_lifecycle_write(ledger, material)
                    or not self._emergency_wal_identity_available()
                ):
                    raise
                return self._defer_mandatory_to_wal(
                    ledger, record_id, material, line,
                )
            durable_ids.add(record_id)
            _id_cache[str(path.resolve())] = (_path_signature(path), frozenset(durable_ids))
            payload = line.encode("utf-8")
            self._publish_record_receipt(
                ledger, record_id, offset=offset, payload=payload, state="COMMITTED"
            )
            if ledger == "lifecycle":
                signature = _path_signature(path)
                if signature is None:
                    raise RuntimeError("LIFECYCLE_LEDGER_MISSING_AFTER_DURABLE_APPEND")
                anchor = {
                    "offset": signature[2] - len(line.encode("utf-8")),
                    "length": len(line.encode("utf-8")),
                    "sha256": hashlib.sha256(line.encode("utf-8")).hexdigest(),
                }
                self._record_lifecycle_membership(str(material.get("episode_id") or ""), signature, anchor)
            final_signature = _path_signature(path)
            if source_complete and final_signature is not None:
                self._atomic_json_receipt(self._completeness_path(ledger), {
                    "schema": "emergency_record_index_complete_v1", "ledger": ledger,
                    "identity": self._identity_binding(),
                    "ledger_generation": self._active_ledger_generation(ledger),
                    "ledger_signature": self._signature_payload(final_signature),
                    "tail_anchor": anchor if ledger == "lifecycle" else {
                        "offset": offset, "length": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                    },
                })
            return {"written": True, "duplicate": False, "record_id": record_id, "ledger": ledger}

    def put_market_segment(
        self,
        *,
        source: str,
        symbol: str,
        timeframe: str,
        start_ts: float,
        end_ts: float,
        rows: Iterable[dict[str, Any]],
        lifecycle_existing: bool = False,
    ) -> dict[str, Any]:
        frozen_rows = tuple(dict(row) for row in rows)
        envelope = {
            "schema": "market_segment_v3",
            "source": str(source),
            "symbol": str(symbol),
            "timeframe": str(timeframe),
            "start_ts": float(start_ts),
            "end_ts": float(end_ts),
            "rows": frozen_rows,
        }
        payload = canonical_json(envelope).encode("utf-8")
        sha = hashlib.sha256(payload).hexdigest()
        target = self.segment_dir / sha[:2] / f"{sha}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        with self._exclusive(target):
            if target.exists():
                if self._cached_segment_hash(target) != sha:
                    raise ValueError("CONTENT_ADDRESS_COLLISION_OR_CORRUPTION")
            else:
                admission = emergency_admission(
                    data_dir=str(self.root), purpose="v3:market_segment",
                    lifecycle_existing=bool(lifecycle_existing),
                )
                if not admission["allowed"]:
                    return {
                        "written": False, "duplicate": False, "blocked": True,
                        "sha256": sha, "storage_admission": admission,
                    }
                temporary = target.with_suffix(f".{os.getpid()}.tmp")
                with temporary.open("wb") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, target)
                _fsync_directory(target.parent)
                signature = _path_signature(target)
                if signature is not None:
                    _segment_hash_cache[str(target.resolve())] = (signature, sha)
        return {
            "schema": "market_segment_ref_v3",
            "sha256": sha,
            "relative_path": target.relative_to(self.root).as_posix(),
            "source": str(source),
            "symbol": str(symbol),
            "timeframe": str(timeframe),
            "start_ts": float(start_ts),
            "end_ts": float(end_ts),
            "row_count": len(frozen_rows),
        }

    @staticmethod
    def _hash_segment(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    @classmethod
    def _cached_segment_hash(cls, path: Path) -> str:
        """Hash a segment once per stable filesystem identity.

        Market segments are immutable content-addressed objects.  Reusing a
        digest is safe only while device, inode, size and nanosecond mtime all
        remain unchanged.  An external write, replacement, truncation or
        recovery therefore invalidates the cache and forces the bytes to be
        hashed again.
        """
        key = str(path.resolve())
        signature = _path_signature(path)
        if signature is None:
            raise FileNotFoundError(path)
        cached = _segment_hash_cache.get(key)
        if cached is not None and cached[0] == signature:
            return cached[1]
        digest = cls._hash_segment(path)
        # Re-stat after reading so a concurrent external replacement cannot
        # bless a digest under the pre-read signature.
        final_signature = _path_signature(path)
        if final_signature is None or final_signature != signature:
            raise OSError("MARKET_SEGMENT_CHANGED_DURING_VERIFICATION")
        _segment_hash_cache[key] = (final_signature, digest)
        return digest

    def verify(self) -> dict[str, Any]:
        """Verify durable ledgers without reparsing unchanged append files.

        ``append`` updates the signature-bound ID cache only after the row has
        been flushed and fsynced. Reusing that cache preserves duplicate and
        truncation detection while keeping synchronous collector work
        independent of total ledger history. Any external write, replacement,
        truncation, or recovery changes the signature and forces a full parse.
        """
        counts: dict[str, int] = {}
        defects: list[dict[str, str]] = []
        for ledger in LEDGER_NAMES:
            path = self.ledger_path(ledger)
            try:
                with self._exclusive(path):
                    counts[ledger] = len(self._cached_ids(path))
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                counts[ledger] = 0
                defects.append({"ledger": ledger, "reason": str(exc)})
        segment_count = 0
        for path in self.segment_dir.glob("*/*.json"):
            try:
                expected = path.stem
                with self._exclusive(path):
                    actual = self._cached_segment_hash(path)
                if actual != expected:
                    defects.append({"segment": path.as_posix(), "reason": "SHA256_MISMATCH"})
                else:
                    segment_count += 1
            except OSError as exc:
                defects.append({"segment": path.as_posix(), "reason": str(exc)})
        return {
            "schema": "v3_store_verification_v1",
            "epoch_id": self.epoch_id,
            "ledger_counts": counts,
            "market_segment_count": segment_count,
            "defects": defects,
            "passed": not defects,
        }

    def verify_write_set(
        self,
        *,
        ledgers: Iterable[str],
        segment_refs: Iterable[dict[str, Any]] = (),
    ) -> dict[str, Any]:
        """Synchronously verify only the durable objects touched by one write.

        This is a hot-path receipt, not a replacement for :meth:`verify`.
        Startup, analyzer and qualification code must continue to use the full
        store verification so corruption in untouched historical objects is
        still detected.
        """
        ledger_names = tuple(dict.fromkeys(str(name) for name in ledgers))
        counts: dict[str, int] = {}
        defects: list[dict[str, str]] = []
        for ledger in ledger_names:
            if ledger not in LEDGER_NAMES:
                counts[ledger] = 0
                defects.append({"ledger": ledger, "reason": "UNKNOWN_V3_LEDGER"})
                continue
            path = self.ledger_path(ledger)
            try:
                with self._exclusive(path):
                    counts[ledger] = len(self._cached_ids(path))
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                counts[ledger] = 0
                defects.append({"ledger": ledger, "reason": str(exc)})

        segment_count = 0
        seen_sha: set[str] = set()
        for ref in segment_refs:
            expected = str((ref or {}).get("sha256") or "").lower()
            if expected in seen_sha:
                continue
            seen_sha.add(expected)
            if len(expected) != 64 or any(ch not in "0123456789abcdef" for ch in expected):
                defects.append({"segment": expected or "MISSING", "reason": "INVALID_SEGMENT_REF"})
                continue
            path = self.segment_dir / expected[:2] / f"{expected}.json"
            try:
                with self._exclusive(path):
                    actual = self._cached_segment_hash(path)
                if actual != expected:
                    defects.append({"segment": path.as_posix(), "reason": "SHA256_MISMATCH"})
                else:
                    segment_count += 1
            except OSError as exc:
                defects.append({"segment": path.as_posix(), "reason": str(exc)})
        return {
            "schema": "v3_store_write_set_verification_v1",
            "scope": "TOUCHED_OBJECTS_ONLY",
            "full_store_verified": False,
            "epoch_id": self.epoch_id,
            "ledger_counts": counts,
            "market_segment_count": segment_count,
            "defects": defects,
            "passed": not defects,
        }

