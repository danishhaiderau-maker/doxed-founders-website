"""Exercise the actual crash collector without importing the trading runtime."""
import ast
import copy
import io
import json
from pathlib import Path
from types import SimpleNamespace


def load_snapshot(writer):
    tree = ast.parse(Path(__file__).with_name('bot.py').read_text(encoding='utf-8'))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'dump_system_state')
    def forbidden(*args, **kwargs):
        raise AssertionError('Crash diagnostics invoked trading work or a business lock')
    env = dict(state={'edge_threshold': 3}, open_positions=[{'id': 'p'}],
               pending_orders=[{'id': 'o'}], latest_candles=[], utc_iso=lambda: 'now',
               time=SimpleNamespace(time=lambda: 1), json=json, open=writer,
               get_active_signal_count=forbidden, get_edge_threshold=forbidden,
               logger=SimpleNamespace(critical=lambda *a: None, error=lambda *a: None),
               _watchdog_crash_context=lambda *a, **k: {'trigger': k['trigger']})
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'bot.py', 'exec'), env)
    return env


class Buffer(io.StringIO):
    def close(self):
        pass


def test_snapshot_is_nonmutating_and_explicitly_unknown(monkeypatch):
    output = Buffer()
    monkeypatch.setattr('crash_journal_writer.append_crash_snapshot',
                        lambda path, snapshot: output.write(json.dumps(snapshot)))
    env = load_snapshot(lambda *a: output)
    before = copy.deepcopy([env['state'], env['open_positions'], env['pending_orders']])
    env['dump_system_state']()
    result = json.loads(output.getvalue())
    assert result['active_signals'] is None
    assert result['active_signals_status'] == 'UNAVAILABLE_NONMUTATING_CRASH_SNAPSHOT'
    assert result['snapshot_consistency'] == 'ADVISORY_UNLOCKED'
    assert result['edge_threshold'] == 3
    assert before == [env['state'], env['open_positions'], env['pending_orders']]


def test_disk_full_does_not_escape_or_reconcile(monkeypatch):
    def disk_full(*args):
        raise OSError(28, 'No space left on device')
    monkeypatch.setattr('crash_journal_writer.append_crash_snapshot', disk_full)
    load_snapshot(disk_full)['dump_system_state']()


def test_watchdog_context_is_preserved(monkeypatch):
    output = Buffer()
    monkeypatch.setattr('crash_journal_writer.append_crash_snapshot',
                        lambda path, snapshot: output.write(json.dumps(snapshot)))
    env = load_snapshot(lambda *a: output)
    env['dump_system_state'](trigger='WATCHDOG')
    assert json.loads(output.getvalue())['watchdog'] == {'trigger': 'WATCHDOG'}
