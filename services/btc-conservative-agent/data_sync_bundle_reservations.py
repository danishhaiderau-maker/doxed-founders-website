"""Inventory-publication reservations backing coordinator maintenance.

Not wired into bot.py. Caller serializes hydrate/boundary with the coordinator
owner. Hydrate BEFORE any retained-generation restore/publication at startup.
Every retained writer and accessor must consult publication_allowed_locked()
while holding the supplied inventory condition. No worker lease is acquired
while that condition is held: HTTP keeps its worker -> inventory lock order.
"""
from contextlib import contextmanager
from pathlib import Path
import re

from data_sync_bundle_download_pins import DownloadProtection, _directory, _safe
from data_sync_bundle_maintenance import _read_operation, FIELDS


def require(value, reason):
    if not value:
        raise ValueError("BUNDLE_RESERVATION_" + reason)


def valid_id(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


class ReservationRegistry:
    def __init__(self, *, condition, source_root, output_root, pin_root, receipt_root,
                 current_identity, protected_generations):
        """Providers run only under inventory condition; never acquire worker lease.

        current_identity returns the exact four maintenance identity fields.
        protected_generations includes retained, served/ACK, and coordinator IDs.
        Providers must be bounded and raise when their authority is unavailable.
        """
        self.condition = condition
        self.source = _directory(source_root)
        self.output = _directory(output_root)
        self.pins = _directory(pin_root)
        self.receipts = _directory(receipt_root)
        self.owner = DownloadProtection(self.pins, self.output / ".bundle-worker.lease")
        self.identity_provider = current_identity
        self.protected_provider = protected_generations
        self.ready = False
        self.durable = frozenset()
        self.transient = set()

    def _locked(self):
        # threading.Condition exposes _is_owned for its wait/notify contract.
        require(callable(getattr(self.condition, "_is_owned", None))
                and self.condition._is_owned(), "INVENTORY_CONDITION_REQUIRED")

    def publication_allowed_locked(self, generation):
        """Same guard for restore, new retention, and retained lookup/serving."""
        self._locked()
        return bool(valid_id(generation) and self.ready
                    and generation not in self.durable and generation not in self.transient)

    def _hydrate(self):
        # No inventory mutex here. The OS lease provides a coherent pin/fence
        # view; it is released before publishing that view under the condition.
        found = set()
        with self.owner._locked():
            _directory(self.receipts)
            active = self.receipts / "active-maintenance.json"
            if active.exists() or active.is_symlink():
                operation = _read_operation(active)
                require(operation.get("source_root") == str(self.source)
                        and operation.get("output_root") == str(self.output)
                        and operation.get("pin_root") == str(self.pins), "INTENT_ROOT_MISMATCH")
                identity = operation.get("current_identity")
                require(isinstance(identity, dict) and set(identity) == FIELDS
                        and valid_id(identity.get("inventory_generation_id"))
                        and all(isinstance(v, str) and v for v in identity.values()), "INTENT_IDENTITY_INVALID")
                if operation["complete"] is False:
                    found.add(operation["candidate"])
            count = 0
            for path in self.pins.iterdir():
                count += 1
                require(count <= 128, "PIN_COUNT_LIMIT")
                require(re.fullmatch(r"[0-9a-f]{64}\.json", path.name) is not None, "PIN_METADATA_UNAVAILABLE")
                _safe(path)
                state = self.owner._load(path.stem)
                self.owner._now(state)  # Malformed/rolled-back clocks fail closed.
                if state["fence"] is not None:
                    found.add(path.stem)
        return frozenset(found)

    def hydrate(self):
        """No persistence mutation; a failed scan blocks all publication.

        Startup must call this before workers. Later refresh calls must be
        coordinator-serialized; transient reservations stay active while disk
        evidence is read, including the pin-unlink/completion crash gap.
        """
        require(not self.condition._is_owned(), "HYDRATION_LOCK_ORDER_INVALID")
        try:
            durable = self._hydrate()
        except Exception:
            with self.condition:
                self.ready = False
            raise
        with self.condition:
            self.durable = durable
            self.ready = True
        return {"status": "READY", "reserved_count": len(durable)}

    @contextmanager
    def protection_boundary(self, candidate):
        require(valid_id(candidate), "GENERATION_INVALID")
        require(not self.condition._is_owned(), "BOUNDARY_LOCK_ORDER_INVALID")
        installed = False
        with self.condition:
            require(self.ready, "HYDRATION_REQUIRED")
            require(candidate not in self.transient, "COORDINATOR_OVERLAP")
            identity = self.identity_provider()
            protected = self.protected_provider()
            require(isinstance(identity, dict) and set(identity) == FIELDS
                    and valid_id(identity.get("inventory_generation_id"))
                    and all(isinstance(v, str) and v for v in identity.values()), "IDENTITY_UNAVAILABLE")
            require(isinstance(protected, (set, frozenset, tuple, list)) and len(protected) <= 1024
                    and all(valid_id(v) for v in protected), "PROTECTION_UNAVAILABLE")
            protected = frozenset(protected) | {identity["inventory_generation_id"]}
            snapshot = {"current_identity": dict(identity), "protected_generations": protected}
            # Already-retained candidates are reported protected but never
            # denied reads transiently merely because maintenance inspected them.
            if candidate not in protected:
                self.transient.add(candidate)
                installed = True
        try:
            yield snapshot  # Inventory mutex is NOT held here.
        finally:
            if installed:
                try:
                    self.hydrate()  # Preserve newly durable intent/fence on failure.
                finally:
                    with self.condition:
                        self.transient.discard(candidate)
