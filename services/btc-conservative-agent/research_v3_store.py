"""Crash-safe normalized evidence store for Research Collector V3."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from research_v3_contract import EVIDENCE_SCHEMA, LEDGER_NAMES, canonical_json
from combo_pathway_config import active_tile_registry_signature
from collector_storage import emergency_admission

_locks_guard = threading.Lock()
_locks: dict[str, threading.RLock] = {}
_id_cache: dict[str, tuple[tuple[int, int, int, int] | None, frozenset[str]]] = {}
_segment_hash_cache: dict[str, tuple[tuple[int, int, int, int], str]] = {}
_lifecycle_episode_cache: dict[str, tuple[tuple[int, int, int, int] | None, frozenset[str]]] = {}
_provenance_cache: dict[str, str] | None = None


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

    def _episode_has_lifecycle(self, episode_id: str) -> bool:
        if not episode_id:
            return False
        path = self.ledger_path("lifecycle")
        key = str(path.resolve())
        signature = _path_signature(path)
        cached = _lifecycle_episode_cache.get(key)
        if cached is not None and cached[0] == signature:
            return episode_id in cached[1]
        episodes: set[str] = set()
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line_no, line in enumerate(handle, 1):
                    if not line.endswith("\n"):
                        raise ValueError(f"TRUNCATED_JSONL_LINE:{line_no}")
                    row = json.loads(line)
                    value = str(row.get("episode_id") or "")
                    if value:
                        episodes.add(value)
        except FileNotFoundError:
            pass
        except (OSError, json.JSONDecodeError, ValueError):
            # Optional/new research fails closed. Mandatory lifecycle writes do
            # not consult this cache and therefore remain available for repair.
            return False
        frozen = frozenset(episodes)
        _lifecycle_episode_cache[key] = (signature, frozen)
        return episode_id in frozen

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
        return emergency_admission(
            data_dir=str(self.root), purpose=f"v3:{ledger}",
            lifecycle_required=mandatory,
            lifecycle_existing=(False if mandatory else self._episode_has_lifecycle(episode_id)),
        )

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
        with self._exclusive(path):
            durable_ids = self._cached_ids(path)
            if record_id in durable_ids:
                return {"written": False, "duplicate": True, "record_id": record_id, "ledger": ledger}
            admission = self._emergency_admission(ledger, material)
            if not admission["allowed"]:
                return {
                    "written": False, "duplicate": False, "blocked": True,
                    "record_id": record_id, "ledger": ledger,
                    "storage_admission": admission,
                }
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
            durable_ids.add(record_id)
            _id_cache[str(path.resolve())] = (_path_signature(path), frozenset(durable_ids))
            if ledger == "lifecycle":
                _lifecycle_episode_cache.pop(str(path.resolve()), None)
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

