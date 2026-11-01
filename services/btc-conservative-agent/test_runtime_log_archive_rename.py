"""Runtime-log archive-RENAME (never delete) + AMBER part_cap + latch fallback.

HM diagnosis cite e01b7073feb9: NOT OBSERVED locally on this store/workspace.
Implemented from OBSERVED SafeRotatingFileHandler / spool / part_cap on master
a70d502f0ced + sealed never-drop plan/card (R25/R34/R46).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ALLOWLIST = HERE / "ci" / "unit_test_allowlist.txt"


@pytest.fixture(autouse=True)
def _reset_stats(monkeypatch):
    import bot as bot_mod
    import retention_stop_write as rsw
    import disk_alert

    monkeypatch.setenv("LOG_SPOOL_MAX_BYTES", "64")
    monkeypatch.setenv("LOG_SPOOL_PART_CAP", "2")
    bot_mod.LOG_SPOOL_MAX_BYTES = 64
    bot_mod.LOG_SPOOL_PART_CAP = 2
    with bot_mod._RUNTIME_LOG_SPOOL_STATS_LOCK:
        bot_mod._RUNTIME_LOG_SPOOL_STATS.update({
            "diverted_records": 0,
            "spool_bytes": 0,
            "spool_parts_used": 0,
            "archived_parts": 0,
            "archive_renames": 0,
            "cap_refusals": 0,
            "spool_cap_red": False,
            "archive_amber": False,
            "last_divert_ts": None,
            "last_archive_ts": None,
            "last_error": None,
        })
    rsw.clear_latches("runtime_log")
    for row in list(disk_alert.amber_conditions()):
        disk_alert.clear_amber(row.get("reason"))
    for row in list(disk_alert.red_conditions()):
        disk_alert.clear_red(row.get("reason"))
    yield
    rsw.clear_latches("runtime_log")
    for row in list(disk_alert.amber_conditions()):
        disk_alert.clear_amber(row.get("reason"))


def _record(msg: str) -> logging.LogRecord:
    return logging.LogRecord("t", logging.INFO, __file__, 1, msg, (), None)


def test_dorollover_archive_renames_never_deletes_raises_amber(tmp_path, monkeypatch):
    import bot as bot_mod
    import disk_alert

    monkeypatch.chdir(tmp_path)
    log = tmp_path / "bot_runtime.log"
    payload = b"PRIMARY_FULL_SEGMENT\n" + b"x" * 40
    log.write_bytes(payload)
    (tmp_path / "bot_runtime.log.1").write_bytes(b"old-archive")
    handler = bot_mod.SafeRotatingFileHandler(str(log), maxBytes=20, backupCount=5, encoding="utf-8")
    handler.doRollover()
    assert handler._stop_write is False
    assert (tmp_path / "bot_runtime.log.1").read_bytes() == b"old-archive"
    assert (tmp_path / "bot_runtime.log.2").read_bytes() == payload
    assert not log.exists() or log.stat().st_size == 0
    ambers = disk_alert.amber_conditions()
    assert any(r.get("reason") == "RUNTIME_LOG_ARCHIVE_RENAME" for r in ambers), ambers
    status = bot_mod._runtime_log_retention_status()
    assert status["archive_renames"] >= 1
    assert status["archive_amber"] is True
    assert status["part_cap"] == 2
    assert status["part_cap_means"] == "amber_threshold_archived_segments"


def test_mutation_unlink_on_rollover_fails(tmp_path, monkeypatch):
    """Mutation proof: deleting an existing archive during rollover fails never-delete."""
    import bot as bot_mod

    monkeypatch.chdir(tmp_path)
    log = tmp_path / "bot_runtime.log"
    log.write_bytes(b"z" * 80)
    archived = tmp_path / "bot_runtime.log.1"
    archived.write_bytes(b"keep-me")
    handler = bot_mod.SafeRotatingFileHandler(str(log), maxBytes=20, backupCount=5, encoding="utf-8")

    real_rename = os.rename

    def _rename_and_unlink(src, dst):
        real_rename(src, dst)
        if archived.exists():
            archived.unlink()

    monkeypatch.setattr(os, "rename", _rename_and_unlink)
    handler.doRollover()
    assert not archived.exists()  # mutant deleted it
    # Production path must retain prior archives — re-run without mutant.
    log.write_bytes(b"z" * 80)
    archived.write_bytes(b"keep-me")
    monkeypatch.setattr(os, "rename", real_rename)
    handler2 = bot_mod.SafeRotatingFileHandler(str(log), maxBytes=20, backupCount=5, encoding="utf-8")
    handler2.doRollover()
    assert archived.exists() and archived.read_bytes() == b"keep-me"


def test_mutation_silent_rollover_without_amber_fails(tmp_path, monkeypatch):
    """Mutation proof: skipping raise_amber fails the loud-AMBER assert."""
    import bot as bot_mod
    import disk_alert

    monkeypatch.chdir(tmp_path)
    log = tmp_path / "bot_runtime.log"
    log.write_bytes(b"z" * 80)
    handler = bot_mod.SafeRotatingFileHandler(str(log), maxBytes=20, backupCount=5, encoding="utf-8")
    real_helper = bot_mod._runtime_log_raise_archive_amber
    monkeypatch.setattr(bot_mod, "_runtime_log_raise_archive_amber", lambda **kwargs: None)
    handler.doRollover()
    assert not any(r.get("reason") == "RUNTIME_LOG_ARCHIVE_RENAME" for r in disk_alert.amber_conditions())
    # Production helper must raise AMBER.
    real_helper(base=str(log), archived_to=str(log) + ".9", archived_count=1)
    assert any(r.get("reason") == "RUNTIME_LOG_ARCHIVE_RENAME" for r in disk_alert.amber_conditions())


def test_rename_failure_latches_stop_write_fallback(tmp_path, monkeypatch):
    """Keep existing STOP_WRITE latch as fallback when archive-rename fails."""
    import bot as bot_mod
    import retention_stop_write as rsw

    monkeypatch.chdir(tmp_path)
    log = tmp_path / "bot_runtime.log"
    log.write_bytes(b"z" * 80)
    handler = bot_mod.SafeRotatingFileHandler(str(log), maxBytes=20, backupCount=5, encoding="utf-8")

    def _boom(src, dst):
        raise OSError("simulated rename failure")

    monkeypatch.setattr(os, "rename", _boom)
    handler.doRollover()
    assert handler._stop_write is True
    assert rsw.is_stopped("runtime_log")
    latch = rsw.latch_state("runtime_log") or {}
    assert latch.get("reason") == "RUNTIME_LOG_ROLLOVER_STOP_WRITE"
    # Primary preserved (rename failed — never deleted).
    assert log.read_bytes() == b"z" * 80


def test_part_cap_is_amber_threshold_not_hard_refuse(tmp_path, monkeypatch):
    """Corrected part_cap: at/over threshold still archive-renames; AMBER; no refuse."""
    import bot as bot_mod
    import disk_alert

    monkeypatch.chdir(tmp_path)
    bot_mod.LOG_SPOOL_PART_CAP = 1  # threshold = 1 archived spool segment
    log = tmp_path / "bot_runtime.log"
    log.write_bytes(b"p")
    spool = Path(str(log) + ".spool")
    spool.write_bytes(b"a" * 64)
    (tmp_path / (log.name + ".spool.1")).write_bytes(b"prior")
    handler = bot_mod.SafeRotatingFileHandler(str(log), maxBytes=50, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler._stop_write = True
    handler.emit(_record("KEEP_AFTER_PART_CAP"))
    # Prior archive kept; full active renamed; new line on fresh spool — no cap_refusal.
    assert (tmp_path / (log.name + ".spool.1")).read_bytes() == b"prior"
    assert b"KEEP_AFTER_PART_CAP" in spool.read_bytes()
    status = bot_mod._runtime_log_retention_status()
    assert status["part_cap"] == 1
    assert status["cap_refusals"] == 0
    assert status["archive_amber"] is True
    assert status["archived_parts"] >= 1
    assert any(r.get("reason") == "RUNTIME_LOG_ARCHIVE_RENAME" for r in disk_alert.amber_conditions())


def test_disk_alert_folds_archive_amber(tmp_path, monkeypatch):
    """AMBER archive reason must surface on compute_disk_alert (not silent)."""
    import disk_alert

    monkeypatch.chdir(tmp_path)
    # Plant GREEN stream size-cap so H1 UNKNOWN does not force RED.
    size_cap = tmp_path / "strategist_stream_size_cap.json"
    size_cap.write_text(
        '{"level":"GREEN","sinks":{"bfx_trades.jsonl":{"level":"GREEN"},'
        '"bfx_book_10l_1s.jsonl":{"level":"GREEN"},'
        '"venue_bbo_1m.jsonl":{"level":"GREEN"},'
        '"bfx_deriv_status_1s.jsonl":{"level":"GREEN"},'
        '"bfx_ws_events.jsonl":{"level":"GREEN"}}}',
        encoding="utf-8",
    )
    disk_alert.raise_amber("RUNTIME_LOG_ARCHIVE_RENAME", {"path": "bot_runtime.log"})
    alert = disk_alert.compute_disk_alert({
        "total_bytes": 1000, "used_bytes": 100, "data_dir": str(tmp_path),
    })
    assert alert["level"] == "AMBER"
    assert any(r.get("reason") == "RUNTIME_LOG_ARCHIVE_RENAME" for r in alert.get("retention_amber") or [])


def test_mutation_part_cap_hard_refuse_fails(tmp_path, monkeypatch):
    """Mutation: restoring hard refuse-at-part_cap (no rename) fails retention."""
    import bot as bot_mod

    monkeypatch.chdir(tmp_path)
    log = tmp_path / "bot_runtime.log"
    log.write_bytes(b"p")
    spool = Path(str(log) + ".spool")
    spool.write_bytes(b"a" * 64)
    handler = bot_mod.SafeRotatingFileHandler(str(log), maxBytes=50, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler._stop_write = True

    def _refuse_full(record):
        # Mutant: if active spool is full, return without archive-rename or write.
        try:
            if os.path.getsize(str(spool)) >= bot_mod.LOG_SPOOL_MAX_BYTES:
                return
        except OSError:
            return
        bot_mod.SafeRotatingFileHandler._emit_to_spool(handler, record)

    monkeypatch.setattr(handler, "_emit_to_spool", _refuse_full)
    handler.emit(_record("MUST_NOT_VANISH"))
    with pytest.raises(AssertionError):
        assert b"MUST_NOT_VANISH" in spool.read_bytes() or any(
            b"MUST_NOT_VANISH" in p.read_bytes()
            for p in tmp_path.glob(log.name + ".spool*")
            if p.is_file()
        )


def test_allowlist_registers_this_module():
    text = ALLOWLIST.read_text(encoding="utf-8")
    assert "test_runtime_log_archive_rename.py" in text
