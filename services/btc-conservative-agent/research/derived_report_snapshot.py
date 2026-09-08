"""Bounded in-process report snapshots; derived computation never holds writer gate.

Publication is atomic per file, NOT an atomic multi-report generation. A partial
replace failure is reported explicitly; every public file remains valid JSON.
"""
import json
import os
import stat
import tempfile
import time
from pathlib import Path


class SnapshotUnavailable(ValueError):
    pass


def file_identity(path):
    try:
        value = os.lstat(path)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(value.st_mode):
        raise SnapshotUnavailable('REPORT_SOURCE_NOT_REGULAR')
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def capture_files(root, names, *, max_bytes=16 * 1024 * 1024,
                  max_seconds=0.25, clock=time.monotonic):
    """Cooperative time budget, not an OS read timeout. Never truncate a dataset."""
    started = clock()
    identities = {name: file_identity(root / name) for name in names}
    if sum(value[2] for value in identities.values() if value) > max_bytes:
        raise SnapshotUnavailable('REPORT_SNAPSHOT_BYTE_BUDGET')
    result = {}
    for name, identity in identities.items():
        if clock() - started > max_seconds:
            raise SnapshotUnavailable('REPORT_SNAPSHOT_TIME_BUDGET')
        if identity is None:
            result[name] = b''
            continue
        chunks = []
        remaining = identity[2]
        with (root / name).open('rb') as stream:
            while remaining:
                if clock() - started > max_seconds:
                    raise SnapshotUnavailable('REPORT_SNAPSHOT_TIME_BUDGET')
                raw = stream.read(min(65536, remaining))
                if not raw:
                    raise SnapshotUnavailable('REPORT_SOURCE_CHANGED')
                chunks.append(raw)
                remaining -= len(raw)
        result[name] = b''.join(chunks)
        if result[name] and not result[name].endswith(b'\n'):
            raise SnapshotUnavailable('REPORT_SOURCE_INCOMPLETE_RECORD')
    if clock() - started > max_seconds:
        raise SnapshotUnavailable('REPORT_SNAPSHOT_TIME_BUDGET')
    if any(file_identity(root / name) != value for name, value in identities.items()):
        raise SnapshotUnavailable('REPORT_SOURCE_CHANGED')
    return result


def run_snapshot_report(*, gate, reset_active, identity, root, inputs, compute,
                        max_bytes=16 * 1024 * 1024, max_seconds=0.25):
    """compute(snapshot) returns (legacy result, {fixed report filename: dict})."""
    root = Path(root)
    def skipped(reason):
        return {'refresh_status': 'SKIPPED', 'reason_code': reason}
    if not gate.acquire(blocking=False):
        return skipped('RESEARCH_WRITER_GATE_BUSY')
    try:
        if reset_active() is not False:
            return skipped('RESEARCH_RESET_ACTIVE_OR_UNKNOWN')
        fence = identity()
        snapshot = capture_files(root, inputs, max_bytes=max_bytes, max_seconds=max_seconds)
    except SnapshotUnavailable as exc:
        return skipped(str(exc))
    finally:
        gate.release()
    result, reports = compute(snapshot)
    staged = []
    try:
        # All encoding and durable temporary writes precede any public replacement.
        total = 0
        for name, report in reports.items():
            if Path(name).name != name:
                raise ValueError('REPORT_OUTPUT_NAME_INVALID')
            raw = json.dumps(report, indent=2, allow_nan=False).encode('utf-8')
            total += len(raw)
            if total > 4 * 1024 * 1024:
                raise SnapshotUnavailable('REPORT_OUTPUT_BYTE_BUDGET')
            fd, temporary = tempfile.mkstemp(prefix='.derived-report-', suffix='.tmp', dir=root)
            staged.append((temporary, root / name))
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        if not gate.acquire(blocking=False):
            return skipped('RESEARCH_WRITER_GATE_BUSY')
        published = []
        try:
            if reset_active() is not False:
                return skipped('RESEARCH_RESET_ACTIVE_OR_UNKNOWN')
            if identity() != fence:
                return skipped('REPORT_RESET_GENERATION_CHANGED')
            for temporary, target in staged:
                try:
                    os.replace(temporary, target)
                except OSError:
                    return {'refresh_status': 'FAILED', 'reason_code': 'REPORT_PUBLICATION_FAILED',
                            'published_reports': published, 'atomicity': 'PER_FILE_ONLY'}
                published.append(target.name)
            return result
        finally:
            gate.release()
    finally:
        for temporary, _ in staged:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
