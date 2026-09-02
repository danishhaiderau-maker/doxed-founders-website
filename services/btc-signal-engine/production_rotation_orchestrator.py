"""Disabled-first production scheduler for V3 ledger generation rotation.

The scheduler is called only by the singleton lifecycle owner, after its child
has reported a completely caught-up cycle.  Decisions inspect the fixed V3
ledger roster with ``stat`` only; ledger contents and SQLite are never opened.
There is deliberately no deletion API in this module.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Mapping

from combo_pathway_config import active_tile_registry_signature
from research_v3_contract import LEDGER_NAMES, canonical_json
from research_v3_store import V3EvidenceStore


SCHEMA = "v3_production_rotation_orchestrator_v1"
DEFAULT_TARGET_BYTES = 64 * 1024 * 1024
MIN_TARGET_BYTES = 1024 * 1024
MAX_TARGET_BYTES = 1024 * 1024 * 1024


def _bounded_target(value: Any) -> int:
    try:
        target = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("ROTATION_TARGET_BYTES_INVALID") from exc
    if not MIN_TARGET_BYTES <= target <= MAX_TARGET_BYTES:
        raise ValueError("ROTATION_TARGET_BYTES_INVALID")
    return target


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(canonical_json(dict(payload)) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class ProductionRotationOrchestrator:
    """Choose and finish at most one V3 ledger generation per owner cycle."""

    def __init__(self, root: str | Path, *, source_revision: str, epoch_id: str,
                 enabled: bool = False, target_bytes: int = DEFAULT_TARGET_BYTES) -> None:
        self.root = Path(root).resolve(strict=True)
        self.source_revision = str(source_revision).lower()
        self.epoch_id = str(epoch_id or "")
        if not re.fullmatch(r"[0-9a-f]{40}", self.source_revision):
            raise ValueError("ROTATION_SOURCE_REVISION_INVALID")
        if not re.fullmatch(r"epoch-[A-Za-z0-9._-]+", self.epoch_id):
            raise ValueError("ROTATION_EPOCH_INVALID")
        self.enabled = bool(enabled)
        self.target_bytes = _bounded_target(target_bytes)
        self.pressure_target_bytes = max(MIN_TARGET_BYTES, self.target_bytes // 2)
        self.pressure_release_bytes = max(MIN_TARGET_BYTES, self.target_bytes // 4)
        self._pressure_latched = False
        self._cursor = 0
        self._status_path = self.root / "v3" / "receipts" / "production_rotation_v1" / "status.json"

    @property
    def config(self) -> dict[str, Any]:
        material = {
            "schema": SCHEMA, "enabled": self.enabled,
            "target_bytes": self.target_bytes,
            "pressure_target_bytes": self.pressure_target_bytes,
            "pressure_release_bytes": self.pressure_release_bytes,
            "ledger_order": list(LEDGER_NAMES),
        }
        return {**material, "config_sha256": hashlib.sha256(
            canonical_json(material).encode("utf-8")
        ).hexdigest()}

    def _receipt(self, *, status: str, pressure: bool, caught_up: bool,
                 ledger: str | None = None, generation: int | None = None,
                 active_bytes: int | None = None, reason: str | None = None) -> dict[str, Any]:
        payload = {
            "schema": SCHEMA, "status": status,
            "source_revision": self.source_revision, "epoch_id": self.epoch_id,
            "tile_config_signature": active_tile_registry_signature(),
            "config": self.config, "pressure": bool(pressure),
            "pressure_latched": self._pressure_latched, "caught_up": bool(caught_up),
            "ledger": ledger, "generation": generation, "active_bytes": active_bytes,
            "reason": reason, "deletion_invoked": False,
        }
        payload["receipt_sha256"] = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
        _atomic_json(self._status_path, payload)
        return payload

    def run_caught_up_cycle(self, *, caught_up: bool, pressure: bool,
                            overlap: str | None = None) -> dict[str, Any]:
        """Run one O(1)-by-stat decision; never rotate more than one ledger."""
        if overlap:
            return self._receipt(status="NOOP_OVERLAP", pressure=pressure,
                                 caught_up=caught_up, reason=str(overlap)[:160])
        if not caught_up:
            return self._receipt(status="NOOP_NOT_CAUGHT_UP", pressure=pressure,
                                 caught_up=False, reason="PIPELINE_BACKLOG")
        if not self.enabled:
            return self._receipt(status="NOOP_DISABLED", pressure=pressure,
                                 caught_up=True, reason="DISABLED_FIRST")

        # Recovery is addressed by exact fixed paths derived from each ACTIVE
        # pointer, not by enumerating journals or ledger contents. Finish only
        # one transaction so a retry cannot cascade into another cutover.
        store = V3EvidenceStore(self.root, epoch_id=self.epoch_id)
        for offset in range(len(LEDGER_NAMES)):
            index = (self._cursor + offset) % len(LEDGER_NAMES)
            ledger = LEDGER_NAMES[index]
            active = store._active_ledger_generation(ledger)
            generation = int(active["generation"])
            candidates = (generation - 1, generation)
            if any(
                candidate >= 1
                and store._rotation_transaction_path(ledger, candidate, "PREPARED").is_file()
                and not store._rotation_transaction_path(ledger, candidate, "COMMITTED").is_file()
                for candidate in candidates
            ):
                recovered = store.recover_ledger_rotations(ledger)
                if not recovered:
                    raise RuntimeError("V3_LEDGER_ROTATION_RECOVERY_NOT_FINALIZED")
                self._cursor = (index + 1) % len(LEDGER_NAMES)
                return self._receipt(
                    status="RECOVERED_ONE", pressure=pressure, caught_up=True,
                    ledger=ledger, generation=int(recovered[0]["generation"]),
                    active_bytes=int(self.root.joinpath(
                        "v3", "ledgers", f"{ledger}.jsonl"
                    ).stat().st_size),
                )

        sizes: dict[str, int] = {}
        for ledger in LEDGER_NAMES:
            try:
                sizes[ledger] = int((self.root / "v3" / "ledgers" / f"{ledger}.jsonl").stat().st_size)
            except FileNotFoundError:
                sizes[ledger] = 0
        largest = max(sizes.values(), default=0)
        if pressure:
            self._pressure_latched = True
        elif self._pressure_latched and largest <= self.pressure_release_bytes:
            self._pressure_latched = False
        threshold = self.pressure_target_bytes if self._pressure_latched else self.target_bytes

        for offset in range(len(LEDGER_NAMES)):
            index = (self._cursor + offset) % len(LEDGER_NAMES)
            ledger = LEDGER_NAMES[index]
            size = sizes[ledger]
            if size < threshold:
                continue
            active = store._active_ledger_generation(ledger)
            if int(active["generation"]) < 1:
                self._cursor = (index + 1) % len(LEDGER_NAMES)
                return self._receipt(
                    status="NOOP_LEGACY_ADOPTION_REQUIRED", pressure=pressure,
                    caught_up=True, ledger=ledger, generation=0, active_bytes=size,
                    reason="MANUAL_GUARDED_LEGACY_ADOPTION_REQUIRED",
                )
            result = store.rotate_ledger(ledger)
            self._cursor = (index + 1) % len(LEDGER_NAMES)
            return self._receipt(
                status="ROTATED_ONE", pressure=pressure, caught_up=True,
                ledger=ledger, generation=int(result["sealed_ref"]["generation"]),
                active_bytes=size,
            )
        return self._receipt(status="NOOP_BELOW_TARGET", pressure=pressure,
                             caught_up=True, active_bytes=largest,
                             reason="NO_LEDGER_AT_TARGET")
