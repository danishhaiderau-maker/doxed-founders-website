"""R46: SafeRotatingFileHandler never silently drops runtime_log lines under STOP_WRITE.

Card seal cite: docs/card-runtime-log-never-drop-r46-20261031.md (joint PASS 04bd8df0f41d).
Rules R0–R48 ``1120edc5b538``. Size cap = LOG_SPOOL_MAX_BYTES (default 50 MiB);
part cap = LOG_SPOOL_PART_CAP (default 5). GET read-back: ``/api/runtime_log/retained``.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ALLOWLIST = HERE / "ci" / "unit_test_allowlist.txt"


@pytest.fixture(autouse=True)
def _reset_spool_stats(monkeypatch):
    import bot as bot_mod
    import retention_stop_write as rsw

    monkeypatch.setenv("LOG_SPOOL_MAX_BYTES", "64")
    monkeypatch.setenv("LOG_SPOOL_PART_CAP", "2")
    bot_mod.LOG_SPOOL_MAX_BYTES = 64
    bot_mod.LOG_SPOOL_PART_CAP = 2
    with bot_mod._RUNTIME_LOG_SPOOL_STATS_LOCK:
        bot_mod._RUNTIME_LOG_SPOOL_STATS.update({
            "diverted_records": 0,
            "spool_bytes": 0,
            "spool_parts_used": 0,
            "cap_refusals": 0,
            "spool_cap_red": False,
            "last_divert_ts": None,
            "last_error": None,
        })
    rsw.clear_latches("runtime_log")
    yield
    rsw.clear_latches("runtime_log")


def _record(msg: str) -> logging.LogRecord:
    return logging.LogRecord("t", logging.INFO, __file__, 1, msg, (), None)


def test_stop_write_retains_record_in_spool(tmp_path, monkeypatch):
    """Under _stop_write=True, emit keeps the record (spool grows)."""
    import bot as bot_mod

    monkeypatch.chdir(tmp_path)
    log = tmp_path / "bot_runtime.log"
    log.write_bytes(b"primary\n")
    handler = bot_mod.SafeRotatingFileHandler(str(log), maxBytes=50, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler._stop_write = True
    before = log.read_bytes()
    handler.emit(_record("KEEP_ME_UNDER_STOP_WRITE"))
    assert log.read_bytes() == before  # primary frozen
    spool = Path(str(log) + ".spool")
    assert spool.is_file()
    assert b"KEEP_ME_UNDER_STOP_WRITE" in spool.read_bytes()
    status = bot_mod._runtime_log_retention_status()
    assert status["diverted_records"] >= 1
    assert status["spool_bytes"] > 0
    assert status["size_cap_bytes"] == 64
    assert status["part_cap"] == 2


def test_mutation_silent_return_fails_retention(tmp_path, monkeypatch):
    """Mutation proof: bare ``if self._stop_write: return`` fails retention assert."""
    import bot as bot_mod

    monkeypatch.chdir(tmp_path)
    log = tmp_path / "bot_runtime.log"
    log.write_bytes(b"x")
    handler = bot_mod.SafeRotatingFileHandler(str(log), maxBytes=50, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler._stop_write = True

    def _silent_drop(record):
        if handler._stop_write:
            return
        logging.Handler.emit(handler, record)

    monkeypatch.setattr(handler, "emit", _silent_drop)
    handler.emit(_record("SHOULD_NOT_VANISH"))
    spool = Path(str(log) + ".spool")
    assert not spool.exists() or b"SHOULD_NOT_VANISH" not in spool.read_bytes()
    # Production emit path must retain — re-bind and prove.
    handler2 = bot_mod.SafeRotatingFileHandler(str(log), maxBytes=50, backupCount=5, encoding="utf-8")
    handler2.setFormatter(logging.Formatter("%(message)s"))
    handler2._stop_write = True
    handler2.emit(_record("SHOULD_NOT_VANISH"))
    assert b"SHOULD_NOT_VANISH" in Path(str(log) + ".spool").read_bytes()


def test_spool_cap_loud_red_no_unlink(tmp_path, monkeypatch):
    """At size/part cap: loud RED + stop without unlinking existing spool parts."""
    import bot as bot_mod
    import retention_stop_write as rsw

    monkeypatch.chdir(tmp_path)
    log = tmp_path / "bot_runtime.log"
    log.write_bytes(b"p")
    # Pre-fill both spool parts to the size cap.
    p1 = Path(str(log) + ".spool")
    p2 = Path(str(log) + ".spool.2")
    p1.write_bytes(b"a" * 64)
    p2.write_bytes(b"b" * 64)
    before = {p1.name: p1.read_bytes(), p2.name: p2.read_bytes()}
    handler = bot_mod.SafeRotatingFileHandler(str(log), maxBytes=50, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler._stop_write = True
    handler.emit(_record("OVERFLOW_AT_CAP"))
    assert p1.read_bytes() == before[p1.name]
    assert p2.read_bytes() == before[p2.name]
    assert rsw.is_stopped("runtime_log")
    latch = rsw.latch_state("runtime_log") or {}
    assert latch.get("reason") == "RUNTIME_LOG_SPOOL_CAP"
    status = bot_mod._runtime_log_retention_status()
    assert status["cap_refusals"] >= 1
    assert status["spool_cap_red"] is True


def test_status_counter_advances_under_stop_write(tmp_path, monkeypatch):
    import bot as bot_mod

    monkeypatch.chdir(tmp_path)
    log = tmp_path / "bot_runtime.log"
    log.write_bytes(b"p")
    handler = bot_mod.SafeRotatingFileHandler(str(log), maxBytes=50, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler._stop_write = True
    start = bot_mod._runtime_log_retention_status()["diverted_records"]
    handler.emit(_record("one"))
    handler.emit(_record("two"))
    status = bot_mod._runtime_log_retention_status()
    assert status["diverted_records"] >= start + 2


def test_get_retained_route_named_and_shows_spool(tmp_path, monkeypatch):
    """GET ``/api/runtime_log/retained`` proves spool lines after STOP_WRITE."""
    import bot as bot_mod

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BOT_LOG_FILE", str(tmp_path / "bot_runtime.log"))
    log = tmp_path / "bot_runtime.log"
    log.write_bytes(b"primary-line\n")
    handler = bot_mod.SafeRotatingFileHandler(str(log), maxBytes=50, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler._stop_write = True
    handler.emit(_record("SPOOL_VISIBLE_VIA_GET"))
    # Call the view helper pieces the route uses (auth is separate).
    paths = bot_mod._runtime_log_spool_paths(str(log))
    assert paths
    assert b"SPOOL_VISIBLE_VIA_GET" in Path(paths[0]).read_bytes()
    assert hasattr(bot_mod, "api_runtime_log_retained")
    src = (HERE / "bot.py").read_text(encoding="utf-8")
    assert "@app.route('/api/runtime_log/retained'" in src
    assert '"/api/runtime_log/retained"' in src
    assert "test_runtime_log_never_drop_r46.py" in ALLOWLIST.read_text(encoding="utf-8")


def test_no_silent_stop_write_return_in_emit_source():
    """Source-level guard: emit must not bare-return on _stop_write."""
    src = (HERE / "bot.py").read_text(encoding="utf-8")
    start = src.index("class SafeRotatingFileHandler")
    end = src.index("NEAR_EDGE_LOG_MAX_BYTES", start)
    body = src[start:end]
    assert "def emit(self, record):" in body
    assert "_emit_to_spool" in body
    # The old silent discard pattern must be gone.
    assert "if self._stop_write:\n            return\n        super().emit(record)" not in body


REMOTE = {"REMOTE_ADDR": "198.51.100.9"}


def _status_json(bot_mod):
    """GET /api/status with probe cache disabled (full payload, not boot stub)."""
    bot_mod._PROBE_CACHE_TTL_SEC = 0.0
    for cache in getattr(bot_mod, "_PROBE_CACHES", []):
        try:
            cache.clear()
        except Exception:
            pass
    resp = bot_mod.app.test_client().get("/api/status", environ_base=REMOTE)
    assert resp.status_code == 200
    body = resp.get_json()
    assert isinstance(body, dict)
    return body


def _assert_status_has_cap_refusals(body: dict) -> None:
    """Shared status-surface assert (M5 target)."""
    assert "runtime_log_retention" in body, "status payload missing runtime_log_retention"
    retention = body["runtime_log_retention"]
    assert isinstance(retention, dict)
    assert "cap_refusals" in retention, "runtime_log_retention missing cap_refusals"
    assert isinstance(retention["cap_refusals"], int)


def test_api_status_exposes_runtime_log_retention_cap_refusals(monkeypatch):
    """GET /api/status must expose runtime_log_retention.cap_refusals (R46 / R23)."""
    import bot as bot_mod

    monkeypatch.setattr(bot_mod, "_DASHBOARD_BOOTSTRAP_COMPLETE", True)
    body = _status_json(bot_mod)
    _assert_status_has_cap_refusals(body)
    # Source pin: status() must assign the retention block (M5 target line).
    src = (HERE / "bot.py").read_text(encoding="utf-8")
    assert 'payload["runtime_log_retention"] = _runtime_log_retention_status()' in src


def test_mutation_m5_removing_runtime_log_retention_fails(monkeypatch):
    """Mutation M5: removing runtime_log_retention from status payload must fail.

    HM FAIL c67e285f184e — prior suite only hit ``_runtime_log_retention_status()``
    and survived deleting ``payload['runtime_log_retention'] = …`` in ``status()``.
    """
    import bot as bot_mod

    monkeypatch.setattr(bot_mod, "_DASHBOARD_BOOTSTRAP_COMPLETE", True)
    body = _status_json(bot_mod)
    _assert_status_has_cap_refusals(body)

    mutant = dict(body)
    del mutant["runtime_log_retention"]
    with pytest.raises(AssertionError):
        _assert_status_has_cap_refusals(mutant)

    # Field present but cap_refusals stripped (except-path shape) also fails.
    thin = dict(body)
    thin["runtime_log_retention"] = {"schema": "runtime_log_retention_v1"}
    with pytest.raises(AssertionError):
        _assert_status_has_cap_refusals(thin)
