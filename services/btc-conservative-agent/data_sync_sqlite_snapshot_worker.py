"""Bounded subprocess worker for one immutable SQLite sync snapshot."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


def _apply_resource_bounds(deadline_seconds: float, memory_bytes: int) -> None:
    try:
        os.nice(10)
    except (AttributeError, OSError):
        pass
    try:
        import resource
        cpu_seconds = max(2, int(math.ceil(deadline_seconds)))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
    except (ImportError, OSError, ValueError):
        pass


@contextmanager
def _bounded_resources(deadline_seconds: float, memory_bytes: int):
    """Apply worker limits for one snapshot, then restore the caller state.

    The normal CLI path is a short-lived subprocess, but tests and embedded
    callers invoke ``build_snapshot`` in-process. Leaving RLIMIT_AS/RLIMIT_CPU
    installed permanently makes unrelated later imports and SQLite operations
    fail with misleading disk-I/O or shared-library mapping errors.
    """
    limits = None
    resource_module = None
    try:
        import resource as resource_module
        limits = {
            resource_module.RLIMIT_CPU: resource_module.getrlimit(resource_module.RLIMIT_CPU),
            resource_module.RLIMIT_AS: resource_module.getrlimit(resource_module.RLIMIT_AS),
        }
    except (ImportError, OSError, ValueError):
        resource_module = None
    _apply_resource_bounds(deadline_seconds, memory_bytes)
    try:
        yield
    finally:
        if resource_module is not None and limits is not None:
            for kind, value in limits.items():
                try:
                    resource_module.setrlimit(kind, value)
                except (OSError, ValueError):
                    # A constrained host may refuse to raise a limit again;
                    # the bounded operation has still failed closed.
                    pass


def build_snapshot(request: dict) -> dict:
    source_path = Path(str(request["source_path"])).resolve(strict=True)
    destination_path = Path(str(request["destination_path"])).resolve()
    deadline_seconds = max(15.0, min(120.0, float(request["deadline_seconds"])))
    max_output_bytes = max(1, int(request["max_output_bytes"]))
    memory_bytes = max(128 * 1024 * 1024, int(request["memory_bytes"]))
    with _bounded_resources(deadline_seconds, memory_bytes):
        started = time.monotonic()

        def require_bounds() -> None:
            if time.monotonic() - started >= deadline_seconds:
                raise TimeoutError("SQLite snapshot exceeded its bounded build deadline")
            try:
                if destination_path.exists() and destination_path.stat().st_size > max_output_bytes:
                    raise ValueError("SQLite snapshot exceeded its bounded output size")
            except FileNotFoundError:
                pass

        destination_path.parent.mkdir(parents=True, exist_ok=True)
        source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True, timeout=15)
        destination = sqlite3.connect(str(destination_path), timeout=15)
        try:
            def progress(_status: int, _remaining: int, _total: int) -> None:
                require_bounds()
                # Yield between bounded backup pages so request serving retains CPU.
                time.sleep(0.005)

            source.backup(destination, pages=128, progress=progress, sleep=0.02)
            require_bounds()
            destination.set_progress_handler(
                lambda: 1 if time.monotonic() - started >= deadline_seconds else 0,
                1000,
            )
            integrity = destination.execute("PRAGMA integrity_check").fetchone()
            destination.set_progress_handler(None, 0)
            if not integrity or str(integrity[0]).lower() != "ok":
                raise sqlite3.DatabaseError("online backup failed integrity_check")
        finally:
            destination.close()
            source.close()

        digest = hashlib.sha256()
        size = 0
        with destination_path.open("rb") as handle:
            while True:
                require_bounds()
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_output_bytes:
                    raise ValueError("SQLite snapshot exceeded its bounded output size")
                digest.update(chunk)
        return {"snapshot_size": size, "snapshot_sha256": digest.hexdigest()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args()
    request_path = Path(args.request).resolve(strict=True)
    result_path = Path(args.result).resolve()
    request = json.loads(request_path.read_text(encoding="utf-8"))
    try:
        payload = {"ok": True, **build_snapshot(request)}
    except Exception as exc:
        payload = {"ok": False, "error_code": type(exc).__name__}
    temporary = result_path.with_suffix(result_path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    os.replace(temporary, result_path)
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
