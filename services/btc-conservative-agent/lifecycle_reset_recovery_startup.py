"""One explicitly pinned reset recovery step before starting the index owner."""
from __future__ import annotations

import re
from pathlib import Path

from lifecycle_index_recovery import recover_reset_index


def initialize_reset_recovery(*, root, operation_path=None, operation_sha256=None,
                              trigger=None, epoch_provider, reset_lock,
                              owner_quiescent):
    """Caller supplies the real reset lock and authoritative owner/epoch probes.

    No configuration means no-op, not a successful repair. Partial or invalid
    configuration fails closed. This never starts a worker or changes ledgers.
    """
    values = (operation_path, operation_sha256, trigger)
    if all(value is None for value in values):
        return {"status": "NOT_CONFIGURED", "started": False}
    if (any(value is None for value in values)
            or not isinstance(operation_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", operation_sha256)
            or not isinstance(trigger, str)
            or not re.fullmatch(r"SOURCE_LEDGER_TRUNCATED:[A-Za-z0-9_.-]+\.jsonl", trigger)
            or not isinstance(operation_path, (str, Path)) or not str(operation_path)):
        raise ValueError("RESET_RECOVERY_STARTUP_CONFIG_INVALID")
    if not reset_lock.acquire(blocking=False):
        raise ValueError("RESET_RECOVERY_STARTUP_RESET_BUSY")
    try:
        if owner_quiescent() is not True:
            raise ValueError("RESET_RECOVERY_STARTUP_OWNER_NOT_QUIESCENT")
        epoch = epoch_provider()
        if not isinstance(epoch, str) or not re.fullmatch(r"epoch-[A-Za-z0-9._-]+", epoch):
            raise ValueError("RESET_RECOVERY_STARTUP_EPOCH_UNAVAILABLE")
        result = recover_reset_index(root, trigger, operation_path=operation_path,
                                     operation_sha256=operation_sha256, current_epoch_id=epoch)
        return {"status": "INITIALIZED", "started": True, "recovery": result}
    finally:
        reset_lock.release()
