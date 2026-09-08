import ast
from pathlib import Path
from types import SimpleNamespace
import threading
import pytest
from test_lifecycle_pipeline_runtime import _runtime


def child(gate, run):
    tree = ast.parse(Path(__file__).with_name('bot.py').read_text(encoding='utf-8'))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_run_admitted_inventory_child')
    ns = dict(_EVIDENCE_WORKER_ADMISSION_GATE=gate,
              _data_sync_inventory_cache_condition=threading.Condition(),
              _data_sync_async_inventory={'worker_active': True},
              _DATA_SYNC_INVENTORY_WORKER_TIMEOUT_SECONDS=300,
              subprocess=SimpleNamespace(run=run, DEVNULL=-3))
    exec(compile(ast.Module(body=[fn], type_ignores=[]), 'actual-bot-admission', 'exec'), ns)
    return ns[fn.name], ns


@pytest.mark.parametrize('inventory_first', [True, False])
def test_actual_interleavings_both_directions(tmp_path, monkeypatch, inventory_first):
    gate = threading.Lock()
    entered, finish = threading.Event(), threading.Event()
    def busy(*a, **k):
        entered.set()
        assert finish.wait(5)
        return True
    r = _runtime(tmp_path, cycle_gate=gate)
    monkeypatch.setattr(r, '_run_once_guarded', busy)
    invoke, ns = child(gate, busy)
    owner = threading.Thread(target=(lambda: invoke([], {})) if inventory_first else r._run_once)
    owner.start()
    try:
        assert entered.wait(5)
        assert (r._run_once() is False) if inventory_first else (invoke([], {}) is None)
    finally:
        finish.set(); owner.join(5)
    assert not owner.is_alive()
    assert gate.acquire(blocking=False)
    gate.release()
    if inventory_first:
        assert ns['_data_sync_async_inventory']['worker_active'] is False


def test_inventory_exception_releases_gate_and_flag():
    gate = threading.Lock()
    def fail(*a, **k):
        raise RuntimeError('injected')
    invoke, ns = child(gate, fail)
    with pytest.raises(RuntimeError, match='injected'):
        invoke([], {})
    assert ns['_data_sync_async_inventory']['worker_active'] is False
    assert gate.acquire(False)
    gate.release()


def test_start_stop_while_inventory_holds_gate(tmp_path):
    gate = threading.Lock()
    gate.acquire()
    r = _runtime(tmp_path, cycle_gate=gate)
    try:
        assert r.start()
        assert r.stop(timeout=1)
        assert not r.acquire_cleanup_lease(timeout=0)
    finally:
        gate.release()


def test_stale_flag_is_not_admission_authority():
    gate = threading.Lock()
    invoke, ns = child(gate, lambda *a, **k: 'completed')
    assert ns['_data_sync_async_inventory']['worker_active'] is True
    assert invoke([], {}) == 'completed'
    assert ns['_data_sync_async_inventory']['worker_active'] is False


def test_bookkeeping_failure_cannot_leak_gate():
    gate = threading.Lock()
    invoke, ns = child(gate, lambda *a, **k: True)
    class BrokenCondition:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def notify_all(self): raise RuntimeError('notify failed')
    ns['_data_sync_inventory_cache_condition'] = BrokenCondition()
    with pytest.raises(RuntimeError, match='notify failed'):
        invoke([], {})
    assert gate.acquire(False)
    gate.release()
