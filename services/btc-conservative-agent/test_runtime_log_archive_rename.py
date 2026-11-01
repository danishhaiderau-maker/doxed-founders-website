"""Runtime-log archive-RENAME (never delete) + AMBER part_cap + latch fallback.

HM diagnosis: docs/20261101-runtimelog-latch-and-a70d-gen.md sha12 **e01b7073feb9**.
HM R2 conditional FAIL review **db3b097fdcf0** (design OK; add boot/unlink/status/AMBER).
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
    monkeypatch.setenv("LOG_ARCHIVE_AMBER_BYTES", "32")
    bot_mod.LOG_SPOOL_MAX_BYTES = 64
    bot_mod.LOG_SPOOL_PART_CAP = 2
    bot_mod.LOG_ARCHIVE_AMBER_BYTES = 32
    with bot_mod._RUNTIME_LOG_SPOOL_STATS_LOCK:
        bot_mod._RUNTIME_LOG_SPOOL_STATS.update({
            "diverted_records": 0,
            "spool_bytes": 0,
            "spool_parts_used": 0,
            "archived_parts": 0,
            "archive_bytes": 0,
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


def test_boot_over_cap_archives_same_bytes_fresh_primary_no_latch(tmp_path, monkeypatch):
    """HM R2 (2): primary already over cap at boot → archive holds same bytes; fresh primary; no latch."""
    import bot as bot_mod
    import retention_stop_write as rsw

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BOT_LOG_FILE", str(tmp_path / "bot_runtime.log"))
    log = tmp_path / "bot_runtime.log"
    payload = b"BOOT_OVER_CAP_BYTES" + b"x" * 80
    log.write_bytes(payload)
    handler = bot_mod.SafeRotatingFileHandler(str(log), maxBytes=40, backupCount=5, encoding="utf-8")
    assert handler._stop_write is False
    assert not rsw.is_stopped("runtime_log")
    archived = sorted(
        [p for p in tmp_path.glob("bot_runtime.log.*") if p.name.rsplit(".", 1)[-1].isdigit()],
        key=lambda p: int(p.name.rsplit(".", 1)[-1]),
    )
    assert archived, "expected archive after boot-over-cap"
    assert archived[-1].read_bytes() == payload
    assert not log.exists() or log.stat().st_size == 0
    status = bot_mod._runtime_log_retention_status()
    assert status["archive_bytes"] == len(payload)
    assert status["part_cap"] >= 1
    assert status["part_cap"] == 2
    assert status["archive_amber_clears_on_restart"] is True


def test_dorollover_archive_renames_never_deletes_raises_amber(tmp_path, monkeypatch):
    import bot as bot_mod
    import disk_alert

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BOT_LOG_FILE", str(tmp_path / "bot_runtime.log"))
    log = tmp_path / "bot_runtime.log"
    # Under-cap at init so boot path does not archive; then grow + rollover.
    log.write_bytes(b"seed\n")
    handler = bot_mod.SafeRotatingFileHandler(str(log), maxBytes=40, backupCount=5, encoding="utf-8")
    (tmp_path / "bot_runtime.log.1").write_bytes(b"old-archive")
    payload = b"PRIMARY_FULL_SEGMENT\n" + b"x" * 40
    log.write_bytes(payload)
    handler.doRollover()
    assert handler._stop_write is False
    assert (tmp_path / "bot_runtime.log.1").read_bytes() == b"old-archive"
    assert (tmp_path / "bot_runtime.log.2").read_bytes() == payload
    assert not log.exists() or log.stat().st_size == 0
    # archive_bytes >= LOG_ARCHIVE_AMBER_BYTES (32) → AMBER
    ambers = disk_alert.amber_conditions()
    assert any(r.get("reason") == "RUNTIME_LOG_ARCHIVE_RENAME" for r in ambers), ambers
    status = bot_mod._runtime_log_retention_status()
    assert status["archive_renames"] >= 1
    assert status["archive_amber"] is True
    assert status["archive_bytes"] >= len(payload)
    assert status["part_cap"] == 2
    assert status["part_cap"] >= 1
    assert status["part_cap_means"] == "amber_threshold_archived_segments"


def test_unlink_remove_path_unlink_raise_both_rename_paths(tmp_path, monkeypatch):
    """HM R2 (3): os.unlink / os.remove / Path.unlink raise in BOTH rename paths → nothing deleted."""
    import bot as bot_mod

    monkeypatch.chdir(tmp_path)
    log = tmp_path / "bot_runtime.log"
    log.write_bytes(b"seed\n")
    prior = tmp_path / "bot_runtime.log.1"
    prior.write_bytes(b"keep-me")
    spool_prior = tmp_path / "bot_runtime.log.spool.1"
    spool_prior.write_bytes(b"spool-keep")

    def _boom_unlink(*_a, **_k):
        raise AssertionError("unlink must never be called")

    monkeypatch.setattr(os, "unlink", _boom_unlink)
    monkeypatch.setattr(os, "remove", _boom_unlink)
    monkeypatch.setattr(Path, "unlink", _boom_unlink)

    # Path 1 — primary doRollover archive-rename
    handler = bot_mod.SafeRotatingFileHandler(str(log), maxBytes=40, backupCount=5, encoding="utf-8")
    log.write_bytes(b"z" * 80)
    handler.doRollover()
    assert prior.exists() and prior.read_bytes() == b"keep-me"
    assert spool_prior.exists() and spool_prior.read_bytes() == b"spool-keep"
    assert any(
        p.read_bytes() == b"z" * 80
        for p in tmp_path.glob("bot_runtime.log.*")
        if p.name.rsplit(".", 1)[-1].isdigit() and ".spool." not in p.name
    )

    # Path 2 — spool archive-rename under STOP_WRITE
    spool = Path(str(log) + ".spool")
    spool.write_bytes(b"a" * 64)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler._stop_write = True
    handler.emit(_record("SPOOL_LINE"))
    assert spool_prior.exists() and spool_prior.read_bytes() == b"spool-keep"
    assert prior.exists() and prior.read_bytes() == b"keep-me"
    assert b"SPOOL_LINE" in spool.read_bytes()


def test_mutation_silent_rollover_without_amber_fails(tmp_path, monkeypatch):
    """Mutation proof: skipping raise_amber fails the loud-AMBER assert."""
    import bot as bot_mod
    import disk_alert

    monkeypatch.chdir(tmp_path)
    log = tmp_path / "bot_runtime.log"
    log.write_bytes(b"seed\n")
    handler = bot_mod.SafeRotatingFileHandler(str(log), maxBytes=40, backupCount=5, encoding="utf-8")
    log.write_bytes(b"z" * 80)
    real_helper = bot_mod._runtime_log_raise_archive_amber
    monkeypatch.setattr(bot_mod, "_runtime_log_raise_archive_amber", lambda **kwargs: None)
    handler.doRollover()
    assert not any(r.get("reason") == "RUNTIME_LOG_ARCHIVE_RENAME" for r in disk_alert.amber_conditions())
    # Production helper must raise AMBER when over part_cap or byte threshold.
    real_helper(
        base=str(log), archived_to=str(log) + ".9",
        archived_count=2, archive_bytes=64,
    )
    assert any(r.get("reason") == "RUNTIME_LOG_ARCHIVE_RENAME" for r in disk_alert.amber_conditions())


def test_rename_failure_latches_stop_write_fallback(tmp_path, monkeypatch):
    """Keep existing STOP_WRITE latch as fallback when archive-rename fails."""
    import bot as bot_mod
    import retention_stop_write as rsw

    monkeypatch.chdir(tmp_path)
    log = tmp_path / "bot_runtime.log"
    log.write_bytes(b"seed\n")
    handler = bot_mod.SafeRotatingFileHandler(str(log), maxBytes=40, backupCount=5, encoding="utf-8")
    payload = b"z" * 80
    log.write_bytes(payload)

    def _boom(src, dst):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(os, "replace", _boom)
    handler.doRollover()
    assert handler._stop_write is True
    assert rsw.is_stopped("runtime_log")
    latch = rsw.latch_state("runtime_log") or {}
    assert latch.get("reason") == "RUNTIME_LOG_ROLLOVER_STOP_WRITE"
    assert log.read_bytes() == payload


def test_part_cap_never_raw_zero_and_archive_bytes_exposed(tmp_path, monkeypatch):
    """HM R2 (4): status exposes archive_bytes and effective part_cap (>=1), never raw 0."""
    import bot as bot_mod

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BOT_LOG_FILE", str(tmp_path / "bot_runtime.log"))
    # Simulate Fly env that set part_cap to 0 — effective must still be >= 1.
    bot_mod.LOG_SPOOL_PART_CAP = 0
    assert bot_mod._runtime_log_effective_part_cap() >= 1
    log = tmp_path / "bot_runtime.log"
    log.write_bytes(b"seed\n")
    (tmp_path / "bot_runtime.log.1").write_bytes(b"a" * 20)
    status = bot_mod._runtime_log_retention_status()
    assert "archive_bytes" in status
    assert status["archive_bytes"] == 20
    assert status["part_cap"] >= 1
    assert status["part_cap"] != 0


def test_part_cap_is_amber_threshold_not_hard_refuse(tmp_path, monkeypatch):
    """Corrected part_cap: at/over threshold still archive-renames; AMBER; no refuse."""
    import bot as bot_mod
    import disk_alert

    monkeypatch.chdir(tmp_path)
    bot_mod.LOG_SPOOL_PART_CAP = 1  # threshold = 1 archived spool segment
    bot_mod.LOG_ARCHIVE_AMBER_BYTES = 10**12  # force part_cap path for AMBER
    log = tmp_path / "bot_runtime.log"
    log.write_bytes(b"p")
    spool = Path(str(log) + ".spool")
    spool.write_bytes(b"a" * 64)
    (tmp_path / (log.name + ".spool.1")).write_bytes(b"prior")
    handler = bot_mod.SafeRotatingFileHandler(str(log), maxBytes=50, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler._stop_write = True
    handler.emit(_record("KEEP_AFTER_PART_CAP"))
    assert (tmp_path / (log.name + ".spool.1")).read_bytes() == b"prior"
    assert b"KEEP_AFTER_PART_CAP" in spool.read_bytes()
    status = bot_mod._runtime_log_retention_status()
    assert status["part_cap"] == 1
    assert status["cap_refusals"] == 0
    assert status["archive_amber"] is True
    assert status["archived_parts"] >= 1
    assert "archive_bytes" in status
    assert any(r.get("reason") == "RUNTIME_LOG_ARCHIVE_RENAME" for r in disk_alert.amber_conditions())


def test_disk_alert_folds_archive_amber(tmp_path, monkeypatch):
    """AMBER archive reason must surface on compute_disk_alert (not silent)."""
    import disk_alert

    monkeypatch.chdir(tmp_path)
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
