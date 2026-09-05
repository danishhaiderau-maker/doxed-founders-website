"""Bounded operational diagnostics, never reset/deletion authority.

The reset receipt directory is retained by research_reset_inventory.
One atomic latest receipt bounds disk usage; callers retain request IDs in logs.
"""
import errno
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from datetime import datetime, timezone

from research_reset_failure_detail import reset_failure_fields

STAGES = frozenset({"ADMISSION", "STATE_LOCK", "TRADE_LOCK", "LIFECYCLE_STOP",
                    "SYNC_SCHEDULER", "CLEANUP_LEASE", "EPOCH_LOCK", "RESEARCH_LOCK",
                    "BOUNDARY", "ALL_SCOPES_PREFLIGHT"})
CLASSES = frozenset({"OSError", "PermissionError", "FileNotFoundError", "TimeoutError",
                     "RuntimeError", "ValueError", "ResearchDeletionRejected"})
REFUSALS = frozenset({"reset_already_in_progress", "fresh_collection_requires_paused_disarmed_flat_boundary",
    "fresh_collection_lifecycle_not_quiescent", "fresh_collection_sync_scheduler_busy",
    "fresh_collection_sync_builder_active", "fresh_collection_cleanup_lease_busy",
    "fresh_collection_epoch_writer_busy", "fresh_collection_research_writer_busy",
    "fresh_collection_state_lock_busy", "fresh_collection_trade_lock_busy"})


def _guard(path):
    for item in (path, *path.parents):
        if item.exists() or item.is_symlink():
            info = item.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise OSError("unsafe diagnostic path")


def write_reset_preflight_diagnostic(runtime_root, *, attempt_id, stage, error=None,
                                     status="FAILED", refusal_code=None):
    """Best effort, nonthrowing on storage failure; no exception text is persisted."""
    if not isinstance(attempt_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", attempt_id):
        raise ValueError("INVALID_ATTEMPT_ID")
    if stage not in STAGES or status not in {"STARTED", "FAILED", "REFUSED", "PASSED"}:
        raise ValueError("INVALID_DIAGNOSTIC_STATE")
    fields = reset_failure_fields(error) if error is not None else {}
    if fields.get("error") not in CLASSES:
        fields["error"] = "UNCLASSIFIED_FAILURE" if error is not None else None
    if refusal_code is not None:
        fields["refusal_code"] = refusal_code if isinstance(refusal_code, str) and refusal_code in REFUSALS else "UNCLASSIFIED_REFUSAL"
    payload = {"schema": "research_reset_preflight_diagnostic_v1", "attempt_id": attempt_id,
               "stage": stage, "status": status, "recorded_at": datetime.now(timezone.utc).isoformat(),
               "deletion_authority": False, **fields}
    data = (json.dumps(payload, sort_keys=True) + "\n").encode()
    assert len(data) < 4096
    temporary = None
    try:
        root = Path(runtime_root).absolute()
        directory = root / "research_reset_receipts" / "_preflight"
        destination = directory / "latest.json"
        _guard(destination)
        if not root.is_dir():
            raise OSError("missing runtime root")
        directory.mkdir(parents=True, exist_ok=True)
        _guard(destination)
        if destination.exists() and not stat.S_ISREG(destination.lstat().st_mode):
            raise OSError("nonregular diagnostic")
        fd, temporary = tempfile.mkstemp(prefix=".diag-", dir=directory)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        _guard(destination)
        os.replace(temporary, destination)
        temporary = None
        if os.name != "nt":
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        return {"diagnostic_written": True, "diagnostic": payload}
    except OSError as exc:
        return {"diagnostic_written": False, "diagnostic": payload,
                "diagnostic_storage_code": "ENOSPC" if exc.errno == errno.ENOSPC else "STORAGE_UNAVAILABLE"}
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass
