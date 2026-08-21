"""Crash-safe normalized evidence store for Research Collector V3."""
from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Any, Iterable

from research_v3_contract import EVIDENCE_SCHEMA, LEDGER_NAMES, canonical_json

_locks_guard = threading.Lock()
_locks: dict[str, threading.RLock] = {}


def _path_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _locks_guard:
        return _locks.setdefault(key, threading.RLock())


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class V3EvidenceStore:
    """One JSONL ledger per entity plus content-addressed market segments.

    Ledger rows are immutable and idempotent by ``record_id``.  An index is a
    cache only; on startup it is rebuilt from the durable ledger, so an append
    that survives a crash can never be duplicated merely because index update
    did not complete.
    """

    def __init__(self, root: str | Path, *, epoch_id: str):
        self.root = Path(root)
        self.epoch_id = str(epoch_id)
        self.ledger_dir = self.root / "v3" / "ledgers"
        self.segment_dir = self.root / "v3" / "market_segments"
        self.receipt_dir = self.root / "v3" / "receipts"
        for path in (self.ledger_dir, self.segment_dir, self.receipt_dir):
            path.mkdir(parents=True, exist_ok=True)

    def ledger_path(self, name: str) -> Path:
        if name not in LEDGER_NAMES:
            raise ValueError(f"unknown V3 ledger: {name}")
        return self.ledger_dir / f"{name}.jsonl"

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
        })
        line = canonical_json(material) + "\n"
        with _path_lock(path):
            durable_ids = self._load_ids(path)
            if record_id in durable_ids:
                return {"written": False, "duplicate": True, "record_id": record_id, "ledger": ledger}
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
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
        with _path_lock(target):
            if target.exists():
                existing = target.read_bytes()
                if hashlib.sha256(existing).hexdigest() != sha:
                    raise ValueError("CONTENT_ADDRESS_COLLISION_OR_CORRUPTION")
            else:
                temporary = target.with_suffix(f".{os.getpid()}.tmp")
                with temporary.open("wb") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, target)
                _fsync_directory(target.parent)
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

    def verify(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        defects: list[dict[str, str]] = []
        for ledger in LEDGER_NAMES:
            path = self.ledger_path(ledger)
            try:
                counts[ledger] = len(self._load_ids(path))
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                counts[ledger] = 0
                defects.append({"ledger": ledger, "reason": str(exc)})
        segment_count = 0
        for path in self.segment_dir.glob("*/*.json"):
            try:
                expected = path.stem
                actual = hashlib.sha256(path.read_bytes()).hexdigest()
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

