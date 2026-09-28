"""Extract only the startup hook; never import/start the trading runtime."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace
import threading
import pytest

from test_research_timing_startup import fixture
from research_timing_capture import load_runtime_timing_config, materialize_timing_declarations


def _hook(tmp_path, pins):
    source = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
    node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef)
                and n.name == "_prepare_research_timing_at_startup")
    logs = []
    class LocalPath:
        @staticmethod
        def cwd(): return tmp_path
    namespace = {"Path": LocalPath, "os": SimpleNamespace(environ=dict(pins)),
                 "time": SimpleNamespace(time=lambda: 200), "state": {},
                 "state_lock": threading.Lock(),
                 "logger": SimpleNamespace(info=lambda *args: logs.append(args), error=lambda *args: logs.append(args)),
                 "_runtime_git_rev_exact": lambda: "c" * 40,
                 "_collector_v22_epoch_id": lambda: "epoch-current",
                 "active_tile_registry_signature": lambda: "d" * 64}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "<startup-hook>", "exec"), namespace)
    return namespace, logs


def test_startup_switch_uses_actual_identity_and_time_preserving_model(tmp_path):
    old, old_path, args = fixture(tmp_path)
    namespace, logs = _hook(tmp_path, args["environ"])
    result = namespace["_prepare_research_timing_at_startup"]()
    assert result["status"] == "DECLARED_FORWARD_ONLY"
    loaded = load_runtime_timing_config(namespace["os"].environ)
    config = loaded["research_timing_config"]
    assert config["source_revision"] == "c" * 40 and config["epoch_id"] == "epoch-current"
    assert config["activated_at_ts"] == 200 and config["tile_config_signature"] == "d" * 64
    assert config["delay_seconds"] == old["delay_seconds"]
    assert json.loads(old_path.read_text()) == old
    captures = {"directional_schedules": {s: {"direction": s, "capture_signature": s} for s in ("LONG", "SHORT")}}
    row = {**config, **loaded, "signal_ts": 201}
    assert len(materialize_timing_declarations(row, captures)["declarations"]) == 8
    assert materialize_timing_declarations({**row, "signal_ts": 199}, captures)["reason"] == "TIMING_CONFIG_NOT_PRE_SIGNAL"
    assert materialize_timing_declarations({**row, "epoch_id": "epoch-next"}, captures)["reason"] == "TIMING_CONFIG_IDENTITY_MISMATCH"
    assert namespace["state"]["research_timing_startup"] == result and logs


def test_missing_or_invalid_pins_remain_visible_without_defaults(tmp_path):
    for pins in ({}, {"BTC_RESEARCH_TIMING_CONFIG_FILE": "untrusted-relative",
                     "BTC_RESEARCH_TIMING_CONFIG_SHA256": "bad"}):
        namespace, logs = _hook(tmp_path, pins)
        result = namespace["_prepare_research_timing_at_startup"]()
        assert result == {"status": "UNAVAILABLE", "reason": "TIMING_STARTUP_PINNED_CONFIG_UNAVAILABLE", "error_type": "ValueError"}
        assert namespace["os"].environ == pins
        assert logs and not (tmp_path / "research-timing-declarations").exists()


def test_invalid_actual_identity_has_exact_safe_reason(tmp_path):
    _, _, args = fixture(tmp_path)
    namespace, _ = _hook(tmp_path, args["environ"])
    namespace["_runtime_git_rev_exact"] = lambda: "short-revision"
    result = namespace["_prepare_research_timing_at_startup"]()
    assert result == {"status": "UNAVAILABLE", "reason": "TIMING_STARTUP_IDENTITY_OR_TIME_INVALID", "error_type": "ValueError"}
    assert namespace["os"].environ == args["environ"]


def test_actual_publication_failure_has_exact_safe_reason(tmp_path, monkeypatch):
    import research_timing_startup
    _, _, args = fixture(tmp_path)
    namespace, logs = _hook(tmp_path, args["environ"])
    def fail_link(*args):
        raise OSError("private publication path")
    monkeypatch.setattr(research_timing_startup.os, "link", fail_link)
    result = namespace["_prepare_research_timing_at_startup"]()
    assert result == {"status": "UNAVAILABLE", "reason": "TIMING_STARTUP_PUBLICATION_FAILED", "error_type": "ValueError"}
    assert namespace["os"].environ == args["environ"]
    assert "private publication path" not in repr(logs)


@pytest.mark.parametrize("failure", [OSError("private path must not be logged"),
                                     ValueError("TIMING_STARTUP_PUBLICATION_FAILED private path must not be logged")])
def test_publication_failure_keeps_original_pins_and_reports_unavailable(tmp_path, monkeypatch, failure):
    import research_timing_startup
    _, _, args = fixture(tmp_path)
    namespace, logs = _hook(tmp_path, args["environ"])
    def fail_publication(**kwargs):
        raise failure
    monkeypatch.setattr(research_timing_startup, "prepare_startup_timing_declaration", fail_publication)
    result = namespace["_prepare_research_timing_at_startup"]()
    assert result == {"status": "UNAVAILABLE", "reason": "TIMING_STARTUP_REBIND_FAILED", "error_type": type(failure).__name__}
    assert namespace["os"].environ == args["environ"]
    assert "private path" not in repr(logs)


def test_startup_order_after_identity_restore_before_worker_owners():
    tree = ast.parse(Path(__file__).with_name("bot.py").read_text(encoding="utf-8"))
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    calls = [(n.func.id, n.lineno) for n in ast.walk(main) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    line = next(line for name, line in calls if name == "_prepare_research_timing_at_startup")
    for name in ("_ensure_collector_v22_epoch", "_load_local_dotenv", "load_persistent_config",
                 "reset_transient_runtime_state", "_write_research_session", "_record_execution_settings_epoch"):
        positions = [position for called, position in calls if called == name]
        assert positions and all(position < line for position in positions), name
    for name in ("_start_api_state_cache_refresher", "_start_lifecycle_pipeline_runtime"):
        positions = [position for called, position in calls if called == name]
        assert positions and all(position > line for position in positions), name
    worker_targets = {"periodic_pipeline_loop", "engine_loop", "microstructure_capture_loop",
                      "all_opportunity_future_path_evidence_loop"}
    assert worker_targets <= {n.id for n in ast.walk(main) if isinstance(n, ast.Name)}
    for n in ast.walk(main):
        if isinstance(n, ast.Name) and n.id in worker_targets:
            assert n.lineno > line
