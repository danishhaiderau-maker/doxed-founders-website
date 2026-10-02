import ast
import os
from pathlib import Path
from unittest.mock import Mock

import crash_journal_writer
from runtime_incident_history import build_runtime_incident_history


SOURCE = Path(__file__).with_name("bot.py")


def handler_scope(monkeypatch, tmp_path):
    path = tmp_path / "custom-incidents.jsonl"
    monkeypatch.setenv("BOT_CRASH_DUMP_FILE", str(path))
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    helpers = [n for n in tree.body if isinstance(n, ast.FunctionDef)
               and n.name in {"_pause_after_thread_crash", "_record_thread_crash_receipt"}]
    scope = dict(os=os, utc_iso=lambda: "2026-09-08T12:26:00Z",
                 _runtime_git_rev_exact=lambda: "abc123", BOT_INSTANCE_ID="instance",
                 logger=Mock(), set_execution_paused=Mock())
    exec(compile(ast.Module(body=helpers, type_ignores=[]), str(SOURCE), "exec"), scope)
    engine_handler = next(n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)
                          and any(isinstance(c, ast.Constant) and c.value == "[CRITICAL] Engine loop fatal crash"
                                  for c in ast.walk(n)))
    return path, scope, compile(ast.Module(body=engine_handler.body, type_ignores=[]), str(SOURCE), "exec")


def test_actual_engine_fatal_handler_reaches_history(monkeypatch, tmp_path):
    path, scope, handler = handler_scope(monkeypatch, tmp_path)
    exec(handler, scope)
    scope["set_execution_paused"].assert_called_once_with("THREAD_CRASH")
    result = build_runtime_incident_history(path, current_started_at=None,
                                            current_instance_id=None, current_revision=None)
    row = result["application_incidents"][0]
    assert row["classification"] == "APPLICATION_THREAD_CRASH"
    assert row["reason"] == "THREAD_CRASH: engine_loop"
    assert row["source_revision"] == "abc123"
    assert row["time"] == "2026-09-08T12:26:00Z"
    assert row["restart_requested"] is None
    assert not (tmp_path / "crash_dump.json").exists()


def test_failed_journal_does_not_prevent_pause_or_log_exception_payload(monkeypatch, tmp_path):
    _, scope, handler = handler_scope(monkeypatch, tmp_path)
    monkeypatch.setattr(crash_journal_writer, "append_crash_snapshot",
                        Mock(side_effect=OSError("SENSITIVE_PAYLOAD")))
    exec(handler, scope)
    scope["set_execution_paused"].assert_called_once_with("THREAD_CRASH")
    assert "SENSITIVE_PAYLOAD" not in str(scope["logger"].error.call_args_list)


def test_watchdog_writer_and_reader_use_same_configured_path():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    writer = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "dump_system_state")
    call = next(n for n in ast.walk(writer) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name) and n.func.id == "append_crash_snapshot")
    assert ast.unparse(call.args[0]) == "os.getenv('BOT_CRASH_DUMP_FILE', 'crash_dump.json')"


def test_safety_pause_precedes_journal_and_receipt_attempt_survives_pause_failure(monkeypatch, tmp_path):
    _, scope, handler = handler_scope(monkeypatch, tmp_path)
    events = []
    scope["set_execution_paused"] = lambda reason: events.append("pause")
    monkeypatch.setattr(crash_journal_writer, "append_crash_snapshot",
                        lambda *a, **k: events.append("journal"))
    exec(handler, scope)
    assert events == ["pause", "journal"]
    def failed_pause(reason):
        events.append("pause_failed")
        raise RuntimeError("pause failed")
    scope["set_execution_paused"] = failed_pause
    import pytest
    with pytest.raises(RuntimeError, match="pause failed"):
        exec(handler, scope)
    assert events[-2:] == ["pause_failed", "journal"]


def test_thread_component_is_bounded_and_guard_preserves_function_name(monkeypatch, tmp_path):
    path, scope, _ = handler_scope(monkeypatch, tmp_path)
    scope["_pause_after_thread_crash"]("worker_" + "x" * 200)
    import json
    assert len(json.loads(path.read_text())["thread_crash"]["component"]) == 80
    assert '_pause_after_thread_crash(getattr(fn, "__name__", "unknown_thread"))' in SOURCE.read_text(encoding="utf-8")
