"""Explicit, hash-pinned local research exclusions; never rewrite source evidence.

This is an analysis input, not a trading control or an inferred incident detector.
An open receipt excludes every overlapping or temporally unresolvable episode.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
from typing import Any, Iterable, Mapping

REASON = "UNKNOWN_DEPLOYED_SOURCE_IDENTITY_INCIDENT"
PATH_ENV = "BTC_ANALYZER_IDENTITY_INCIDENT_RECEIPT"
HASH_ENV = "BTC_ANALYZER_IDENTITY_INCIDENT_SHA256"
MAX_RECEIPT_BYTES = 256 * 1024
MAX_TEMPORAL_NODES = 100_000  # accommodates complete two-hour 1s paths, still bounded
_TIME_KEYS = frozenset({
    "ts", "ts_ms", "timestamp", "timestamp_ms", "time", "observed_ts",
    "signal_ts", "signal_utc", "signal_time_utc", "signal_timestamp_utc",
    "created_at", "created_utc", "created_ts", "event_ts", "event_utc",
    "start_ts", "end_ts", "start_utc", "end_utc", "terminal_ts",
    "entry_ts", "exit_ts", "filled_ts", "executed_ts", "closed_ts",
    "complete_through_ts", "coverage_start_ts", "coverage_end_ts",
    "window_start_ts", "window_end_ts", "captured_at", "captured_ts",
    "collected_at", "evidence_collected_at", "timestamp_utc", "utc",
    "bucket_ts", "expiry_ts", "signal_timestamp", "signal_time", "t",
    "fill_ts", "close_ts", "captured_at_ts", "terminal_exit_ts",
})
_NON_EVENT_CONTEXT = frozenset({
    "signed_quantity_constraints", "quantity_constraints", "feature_snapshot_at_signal",
    "regime_features_at_signal", "provenance", "baseline_spec", "policy_spec", "paper_policy_spec",
})


def _utc(value: Any) -> float:
    if not isinstance(value, str):
        raise ValueError("IDENTITY_INCIDENT_UTC_INVALID")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError("IDENTITY_INCIDENT_UTC_INVALID")
    return parsed.timestamp()


def _read(path: Path) -> bytes:
    for parent in (path, *path.parents):
        info = parent.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("IDENTITY_INCIDENT_LINK_FORBIDDEN")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_RECEIPT_BYTES:
        raise ValueError("IDENTITY_INCIDENT_SIZE_INVALID")
    with path.open("rb") as handle:
        data = handle.read(MAX_RECEIPT_BYTES + 1)
    if len(data) > MAX_RECEIPT_BYTES:
        raise ValueError("IDENTITY_INCIDENT_SIZE_INVALID")
    return data


@dataclass(frozen=True)
class IncidentInput:
    path: Path | None = None
    sha256: str = ""
    incident_id: str = ""
    start: float = 0
    end: float | None = None

    @property
    def enabled(self) -> bool:
        return self.path is not None

    def provenance(self) -> dict[str, Any]:
        return {"schema": "runtime_identity_incident_input_v1", "enabled": self.enabled,
                "receipt_sha256": self.sha256, "incident_id": self.incident_id,
                "receipt_path": str(self.path) if self.path else None,
                "exclusion_enforced": self.enabled,
                "unknown_reason": REASON if self.enabled else None}

    def assert_unchanged(self) -> None:
        if self.enabled and hashlib.sha256(_read(self.path)).hexdigest() != self.sha256:
            raise ValueError("IDENTITY_INCIDENT_INPUT_CHANGED")

    def affected(self, row: Mapping[str, Any]) -> bool:
        if not self.enabled:
            return False
        times: list[float] = []
        unresolved = False
        nodes = 0

        def visit(value: Any, depth: int = 0) -> None:
            nonlocal nodes, unresolved
            nodes += 1
            if nodes > MAX_TEMPORAL_NODES or depth > 10:
                unresolved = True
                return
            if isinstance(value, Mapping):
                if len(value) > MAX_TEMPORAL_NODES:
                    unresolved = True
                    return
                for begin, finish in (("start_ts", "end_ts"), ("start_utc", "end_utc"),
                                      ("window_start_ts", "window_end_ts"),
                                      ("coverage_start_ts", "coverage_end_ts")):
                    if (begin in value) != (finish in value):
                        unresolved = True
                for key, item in value.items():
                    # Historical feature/lot captures are not the lifecycle's
                    # event interval and cannot supply a missing event time.
                    if str(key) in _NON_EVENT_CONTEXT:
                        continue
                    if str(key) in _TIME_KEYS:
                        try:
                            if isinstance(item, bool) or item in (None, ""):
                                raise ValueError()
                            if isinstance(item, (int, float)) or (
                                isinstance(item, str) and re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", item)
                            ):
                                stamp = float(item)
                                if str(key).endswith("_ms") or stamp > 100_000_000_000:
                                    stamp /= 1000
                            else:
                                stamp = _utc(item)
                            if not math.isfinite(stamp) or stamp < 0:
                                raise ValueError()
                            times.append(stamp)
                        except (ValueError, TypeError, OverflowError):
                            unresolved = True
                    elif isinstance(item, (Mapping, list, tuple)):
                        visit(item, depth + 1)
                    if nodes > MAX_TEMPORAL_NODES:
                        break
            elif isinstance(value, (list, tuple)):
                if len(value) > MAX_TEMPORAL_NODES:
                    unresolved = True
                    return
                for item in value:
                    visit(item, depth + 1)
                    if nodes > MAX_TEMPORAL_NODES:
                        break

        visit(row)
        # Never infer an absent event time from another row's signal time.
        if unresolved or not times:
            return True
        return max(times) >= self.start and (self.end is None or min(times) < self.end)


def load_incident_input(path: str | Path | None = None, sha256: str | None = None) -> IncidentInput:
    path = path if path is not None else os.environ.get(PATH_ENV, "")
    sha256 = sha256 if sha256 is not None else os.environ.get(HASH_ENV, "")
    if not path and not sha256:
        return IncidentInput()
    if not path or not re.fullmatch(r"[0-9a-f]{64}", str(sha256)):
        raise ValueError("IDENTITY_INCIDENT_PIN_REQUIRED")
    source = Path(path)
    if not source.is_absolute():
        raise ValueError("IDENTITY_INCIDENT_PATH_NOT_ABSOLUTE")
    data = _read(source)
    if hashlib.sha256(data).hexdigest() != sha256:
        raise ValueError("IDENTITY_INCIDENT_HASH_MISMATCH")
    receipt = json.loads(data.decode("utf-8-sig"))
    if not isinstance(receipt, dict) or receipt.get("schema") != "btc_runtime_revision_incident_receipt_v1":
        raise ValueError("IDENTITY_INCIDENT_SCHEMA_INVALID")
    if receipt.get("status") not in {"OPEN", "CLOSED"} or not re.fullmatch(
        r"[A-Za-z0-9_-]{1,160}", str(receipt.get("incident_id") or "")
    ):
        raise ValueError("IDENTITY_INCIDENT_STATUS_INVALID")
    for field in ("expected_source_revision", "observed_process_source_revision"):
        if not re.fullmatch(r"[0-9a-f]{40}", str(receipt.get(field) or "")):
            raise ValueError("IDENTITY_INCIDENT_REVISION_INVALID")
    if receipt["expected_source_revision"] == receipt["observed_process_source_revision"]:
        raise ValueError("IDENTITY_INCIDENT_REVISION_NOT_CONFLICTING")
    if receipt.get("qualification_allowed_from_affected_interval") is not False:
        raise ValueError("IDENTITY_INCIDENT_QUALIFICATION_NOT_EXCLUDED")
    start = _utc(receipt.get("conservative_start_utc"))
    end_value = receipt.get("verified_end_utc")
    if receipt["status"] == "OPEN" and end_value is not None:
        raise ValueError("IDENTITY_INCIDENT_OPEN_END_INVALID")
    end = _utc(end_value) if receipt["status"] == "CLOSED" else None
    if end is not None and end <= start:
        raise ValueError("IDENTITY_INCIDENT_INTERVAL_INVALID")
    return IncidentInput(source, str(sha256), receipt["incident_id"], start, end)


def episode_key(row: Mapping[str, Any]) -> tuple[str, str]:
    causal = row.get("causal_identity")
    causal = causal if isinstance(causal, Mapping) else {}
    return (str(row.get("epoch_id") or row.get("dataset_epoch") or causal.get("dataset_epoch") or ""),
            str(row.get("episode_id") or causal.get("episode_id") or ""))


def assert_publication_incident_input(manifest: Mapping[str, Any]) -> None:
    """Reject old, changed, removed or newly enabled exclusions at publication."""
    source = load_incident_input()
    provenance = manifest.get("analysis_provenance") or {}
    recorded = provenance.get("runtime_identity_incident_input")
    if not source.enabled and not recorded:
        return
    if recorded != source.provenance():
        raise ValueError("IDENTITY_INCIDENT_PUBLICATION_INPUT_MISMATCH")
    source.assert_unchanged()


class IncidentEpisodeIndex:
    """One small set per affected episode, shared by every policy variant."""
    def __init__(self, source: IncidentInput):
        self.source = source
        self.affected_keys: set[tuple[str, str]] = set()
        self.affected_episodes_without_epoch: set[str] = set()
        self.affected_opportunities: set[tuple[str, str]] = set()
        self.unassociated_unknown_rows = 0

    @staticmethod
    def opportunity_key(row: Mapping[str, Any]) -> tuple[str, str]:
        causal = row.get("causal_identity")
        causal = causal if isinstance(causal, Mapping) else {}
        return episode_key(row)[0], str(row.get("opportunity_id") or causal.get("opportunity_id") or "")

    def add(self, rows: Iterable[Mapping[str, Any]]) -> None:
        if not self.source.enabled:
            return
        for row in rows:
            if self.source.affected(row):
                key = episode_key(row)
                opportunity = self.opportunity_key(row)
                if all(key):
                    self.affected_keys.add(key)
                elif key[1]:
                    self.affected_episodes_without_epoch.add(key[1])
                if opportunity[1]:
                    self.affected_opportunities.add(opportunity)
                if not key[1] and not opportunity[1]:
                    # Retain an explicit UNKNOWN input count, not an invented
                    # causal link to every future episode. Complete matching
                    # evidence remains required by each producer's other gates.
                    self.unassociated_unknown_rows += 1

    def reasons(self, row: Mapping[str, Any]) -> list[str]:
        key = episode_key(row)
        opportunity = self.opportunity_key(row)
        if self.source.enabled and (
                key in self.affected_keys or key[1] in self.affected_episodes_without_epoch or
                (bool(opportunity[1]) and (opportunity in self.affected_opportunities or
                    ("", opportunity[1]) in self.affected_opportunities)) or self.source.affected(row)):
            return [REASON]
        return []

    def coverage(self) -> dict[str, Any]:
        return {"affected_episode_count": len(self.affected_keys),
                "ambiguous_epoch_episode_count": len(self.affected_episodes_without_epoch),
                "affected_opportunity_count": len(self.affected_opportunities),
                "unassociated_unknown_input_rows": self.unassociated_unknown_rows,
                "unassociated_rows_qualified": False}
