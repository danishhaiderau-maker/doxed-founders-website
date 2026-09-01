import ast
import threading
import time
from pathlib import Path


BOT_PATH = Path(__file__).with_name("bot.py")
SOURCE = BOT_PATH.read_text(encoding="utf-8")


def _function(name, namespace):
    tree = ast.parse(SOURCE)
    node = next(item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == name)
    scope = dict(namespace)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(BOT_PATH), "exec"), scope)
    return scope[name], scope


class _OneCycleEvent:
    def __init__(self):
        self.done = False

    def is_set(self):
        return self.done

    def wait(self, _timeout):
        self.done = True
        return True


def test_heartbeat_advances_while_state_lock_is_held():
    state = {}
    lock = threading.Lock()
    lock.acquire()
    event = _OneCycleEvent()
    heartbeat, scope = _function("heartbeat_loop", {
        "time": time,
        "shutdown_event": event,
        "state": state,
        "state_lock": lock,
        "last_heartbeat": 0.0,
        "PROCESS_HEARTBEAT_INTERVAL_SEC": 1.0,
        "_maybe_record_heartbeat_cycle_3m": lambda: None,
    })
    worker = threading.Thread(target=heartbeat)
    worker.start()
    worker.join(0.5)
    lock.release()
    assert not worker.is_alive()
    assert scope["last_heartbeat"] > 0
    assert state["last_heartbeat"] == scope["last_heartbeat"]


def test_heartbeat_starts_before_synchronous_bootstrap_and_only_once():
    bind = SOURCE.index("dashboard_httpd = _create_dashboard_server()")
    validate = SOURCE.index("_validate_research_ledgers_on_startup()", bind)
    start = SOURCE.index("target=safe_thread(heartbeat_loop)", bind)
    assert bind < start < validate
    assert SOURCE.count("target=safe_thread(heartbeat_loop)") == 1
    # Process liveness must not promote trading readiness during restore.
    ready = SOURCE.index("_DASHBOARD_BOOTSTRAP_COMPLETE = True", bind)
    assert start < validate < ready


def test_identity_manifest_does_not_wait_for_state_lock():
    start = SOURCE.index("def api_data_sync_manifest():")
    end = SOURCE.index("@app.route('/api/data-sync/sqlite-snapshot')", start)
    body = SOURCE[start:end]
    assert 'state.get("fresh_collection_signal_ts")' in body
    assert "with state_lock:" not in body


def test_internal_revision_fences_are_exact_and_no_undefined_global_remains():
    start = SOURCE.index("def _start_lifecycle_pipeline_runtime()")
    end = SOURCE.index("def _stop_lifecycle_pipeline_runtime", start)
    assert "_runtime_git_rev_exact()" in SOURCE[start:end]
    tree = ast.parse(SOURCE)
    bare_source_rev = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id == "SOURCE_GIT_REV"
    ]
    assert bare_source_rev == []
