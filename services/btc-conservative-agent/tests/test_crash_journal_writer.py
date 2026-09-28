import json
import multiprocessing
from concurrent.futures import ThreadPoolExecutor

import pytest

from crash_journal_writer import append_crash_snapshot, crash_journal_lock


def _write_many(path, worker):
    for sequence in range(15):
        append_crash_snapshot(path, {"worker": worker, "sequence": sequence,
                                     "text": "é" * 1000}, timeout_seconds=10)


def test_threads_preserve_whole_records(tmp_path):
    path = tmp_path / "crash_dump.json"
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda worker: _write_many(path, worker), range(6)))
    rows = [json.loads(line) for line in path.read_bytes().splitlines()]
    assert len(rows) == 90
    assert len({(row["worker"], row["sequence"]) for row in rows}) == 90
    assert path.read_bytes().endswith(b"\n")


def test_processes_preserve_whole_records(tmp_path):
    path = tmp_path / "crash_dump.json"
    context = multiprocessing.get_context("spawn")
    workers = [context.Process(target=_write_many, args=(path, i)) for i in range(3)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(20)
        assert worker.exitcode == 0
    rows = [json.loads(line) for line in path.read_bytes().splitlines()]
    assert len(rows) == 45
    assert len({(row["worker"], row["sequence"]) for row in rows}) == 45


def test_lock_timeout_preserves_journal(tmp_path):
    path = tmp_path / "crash_dump.json"
    append_crash_snapshot(path, {"ok": 1})
    before = path.read_bytes()
    with crash_journal_lock(path):
        with pytest.raises(TimeoutError):
            append_crash_snapshot(path, {"ok": 2}, timeout_seconds=0)
    assert path.read_bytes() == before
    append_crash_snapshot(path, {"ok": 3})
    assert PathLock(path).exists()


def PathLock(path):
    return path.with_name(path.name + ".lock")


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_rejected_without_write(tmp_path, value):
    path = tmp_path / "crash_dump.json"
    with pytest.raises(ValueError):
        append_crash_snapshot(path, {"value": value})
    assert not path.exists()


@pytest.mark.parametrize("timeout", [True, -1, 31, float("nan"), 10**400, "1"])
def test_invalid_timeout(tmp_path, timeout):
    with pytest.raises(ValueError):
        append_crash_snapshot(tmp_path / "x", {}, timeout_seconds=timeout)


def test_incomplete_tail_not_extended(tmp_path):
    path = tmp_path / "crash_dump.json"
    path.write_bytes(b'{"incomplete":')
    with pytest.raises(ValueError, match="unterminated"):
        append_crash_snapshot(path, {})
    assert path.read_bytes() == b'{"incomplete":'


def test_utf8_and_embedded_newline(tmp_path):
    path = tmp_path / "crash_dump.json"
    snapshot = {"text": "中文\nsecond line"}
    count = append_crash_snapshot(path, snapshot)
    data = path.read_bytes()
    assert count == len(data)
    assert len(data.splitlines()) == 1
    assert json.loads(data) == snapshot


def test_object_and_size_limits(tmp_path):
    path = tmp_path / "crash_dump.json"
    with pytest.raises(TypeError):
        append_crash_snapshot(path, [])
    with pytest.raises(ValueError, match="1 MiB"):
        append_crash_snapshot(path, {"text": "x" * (1024 * 1024)})
    assert not path.exists()
