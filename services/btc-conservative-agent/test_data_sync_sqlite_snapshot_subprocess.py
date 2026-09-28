import json
import sqlite3
from pathlib import Path

import data_sync_sqlite_snapshot_worker as worker


AGENT_DIR = Path(__file__).resolve().parent
BOT_SOURCE = (AGENT_DIR / "bot.py").read_text(encoding="utf-8")


def _database(path: Path, rows: int = 1000) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE evidence (id INTEGER PRIMARY KEY, payload TEXT)")
        connection.executemany(
            "INSERT INTO evidence(payload) VALUES (?)",
            ((f"row-{index}",) for index in range(rows)),
        )
        connection.commit()
    finally:
        connection.close()


def test_worker_creates_integrity_checked_checksum_bound_snapshot(tmp_path):
    source = tmp_path / "source.db"
    destination = tmp_path / "snapshot.db"
    _database(source)
    result = worker.build_snapshot({
        "source_path": str(source), "destination_path": str(destination),
        "deadline_seconds": 30, "max_output_bytes": 32 * 1024 * 1024,
        "memory_bytes": 256 * 1024 * 1024,
    })
    assert result["snapshot_size"] == destination.stat().st_size
    assert len(result["snapshot_sha256"]) == 64
    connection = sqlite3.connect(destination)
    try:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("SELECT COUNT(*) FROM evidence").fetchone() == (1000,)
    finally:
        connection.close()


def test_worker_fails_closed_when_output_bound_is_too_small(tmp_path):
    source = tmp_path / "source.db"
    _database(source, rows=5000)
    try:
        worker.build_snapshot({
            "source_path": str(source), "destination_path": str(tmp_path / "snapshot.db"),
            "deadline_seconds": 30, "max_output_bytes": 1,
            "memory_bytes": 256 * 1024 * 1024,
        })
    except ValueError as exc:
        assert "bounded output size" in str(exc)
    else:
        raise AssertionError("oversized snapshot did not fail closed")


def test_server_uses_bounded_subprocess_and_terminates_timeout():
    worker_body = BOT_SOURCE[
        BOT_SOURCE.index("def _data_sync_sqlite_snapshot_worker"):
        BOT_SOURCE.index("def _data_sync_request_sqlite_snapshot")
    ]
    assert "_data_sync_sqlite_snapshot_subprocess" in worker_body
    assert "subprocess.Popen" in worker_body
    assert "process.wait(timeout=" in worker_body
    assert "process.terminate()" in worker_body
    assert "process.kill()" in worker_body
    assert "start_new_session=True" in worker_body
    assert "snapshot.unlink(missing_ok=True)" in worker_body
