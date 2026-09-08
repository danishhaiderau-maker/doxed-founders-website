import ast
import json
from pathlib import Path
import threading
import os

import pytest
from research import derived_report_snapshot as reports


def invoke(root, compute, **overrides):
    options = dict(gate=threading.RLock(), reset_active=lambda: False, identity=lambda: 0,
                   root=root, inputs=('input.jsonl',), compute=compute)
    options.update(overrides)
    return reports.run_snapshot_report(**options)


def computed(snapshot):
    return {'done': True}, {'report.json': {'rows': snapshot['input.jsonl'].decode()}}


def test_compute_releases_gate_and_uses_captured_bytes(tmp_path):
    source = tmp_path/'input.jsonl'
    source.write_bytes(b'{"n":1}\n')
    gate = threading.RLock()
    def compute(snapshot):
        def append():
            with gate:
                with source.open('ab') as stream:
                    stream.write(b'{"n":2}\n')
        thread = threading.Thread(target=append, daemon=True)
        thread.start(); thread.join(1)
        assert not thread.is_alive(), 'aggregation still holds writer gate'
        assert snapshot['input.jsonl'] == b'{"n":1}\n'
        return computed(snapshot)
    assert invoke(tmp_path, compute, gate=gate) == {'done': True}
    assert json.loads((tmp_path/'report.json').read_text())['rows'] == '{"n":1}\n'


@pytest.mark.parametrize('change', ['reset_active', 'failed_same_epoch', 'identity'])
def test_reset_and_identity_changes_preserve_old_publication(tmp_path, change):
    (tmp_path/'report.json').write_text('{"old":true}')
    state = {'generation': 1, 'active': False}
    def compute(snapshot):
        if change == 'reset_active':
            state['active'] = True
        else:
            # Completed or failed reset can retain epoch; generation must still move.
            state['generation'] += 1
        return computed(snapshot)
    result = invoke(tmp_path, compute, identity=lambda: state['generation'],
                    reset_active=lambda: state['active'])
    assert result['refresh_status'] == 'SKIPPED'
    assert (tmp_path/'report.json').read_text() == '{"old":true}'
    assert not list(tmp_path.glob('.derived-report-*'))


def test_snapshot_byte_and_time_budget_never_compute_partial(tmp_path):
    (tmp_path/'input.jsonl').write_bytes(b'{}\n'*10)
    def forbidden(_):
        pytest.fail('partial snapshot used')
    assert invoke(tmp_path, forbidden, max_bytes=5)['reason_code'] == 'REPORT_SNAPSHOT_BYTE_BUDGET'
    values = iter([0, 1])
    with pytest.raises(reports.SnapshotUnavailable, match='TIME_BUDGET'):
        reports.capture_files(tmp_path, ('input.jsonl',), clock=lambda: next(values))


def test_partial_record_and_source_change_are_not_zero_data(tmp_path, monkeypatch):
    (tmp_path/'input.jsonl').write_bytes(b'{')
    assert invoke(tmp_path, computed)['reason_code'] == 'REPORT_SOURCE_INCOMPLETE_RECORD'
    (tmp_path/'input.jsonl').write_bytes(b'{}\n')
    original = reports.file_identity
    count = 0
    def changed(path):
        nonlocal count
        count += 1
        result = original(path)
        return result if count == 1 else (*result[:-1], result[-1]+1)
    monkeypatch.setattr(reports, 'file_identity', changed)
    assert invoke(tmp_path, computed)['reason_code'] == 'REPORT_SOURCE_CHANGED'


@pytest.mark.parametrize('failure', ['compute', 'serialize', 'fsync', 'replace'])
def test_failure_before_first_publication_keeps_prior_output(tmp_path, monkeypatch, failure):
    public = tmp_path/'report.json'; public.write_text('{"old":true}')
    def compute(snapshot):
        if failure == 'compute':
            raise ValueError('injected')
        if failure == 'serialize':
            return {}, {'report.json': {'bad': float('nan')}}
        return computed(snapshot)
    def fail(*args):
        raise OSError('injected')
    if failure in ('fsync', 'replace'):
        monkeypatch.setattr(reports.os, failure, fail)
    if failure == 'replace':
        result = invoke(tmp_path, compute)
        assert result['published_reports'] == [] and result['refresh_status'] == 'FAILED'
    else:
        with pytest.raises((ValueError, OSError)):
            invoke(tmp_path, compute)
    assert public.read_text() == '{"old":true}'
    assert not list(tmp_path.glob('.derived-report-*'))


def test_later_replace_failure_explicitly_reports_per_file_atomicity(tmp_path, monkeypatch):
    for name in ('one.json', 'two.json'):
        (tmp_path/name).write_text('{"old":true}')
    real_replace = reports.os.replace
    def replace(source, target):
        if Path(target).name == 'two.json':
            raise OSError('injected')
        real_replace(source, target)
    monkeypatch.setattr(reports.os, 'replace', replace)
    result = invoke(tmp_path, lambda _: ({}, {'one.json': {'new': True}, 'two.json': {'new': True}}))
    assert result == {'refresh_status': 'FAILED', 'reason_code': 'REPORT_PUBLICATION_FAILED',
                      'published_reports': ['one.json'], 'atomicity': 'PER_FILE_ONLY'}
    assert json.loads((tmp_path/'one.json').read_text()) == {'new': True}
    assert json.loads((tmp_path/'two.json').read_text()) == {'old': True}


def test_actual_execution_builders_reuse_same_funnel_snapshot(tmp_path, monkeypatch):
    import execution_funnel as funnel
    snapshot = {funnel.FUNNEL_FILE: b'{"trade_id":"a","stage":"APPROVE"}\n',
                'shadow_outcome.jsonl': b'', 'trades_3factor.csv': b''}
    def forbidden(*a):
        pytest.fail('builder reopened live input')
    monkeypatch.setattr(funnel, '_load_funnel_rows', forbidden)
    monkeypatch.setattr(funnel, '_load_jsonl', forbidden)
    monkeypatch.setattr(funnel, '_load_csv_trades', forbidden)
    seen = []
    original = funnel.build_funnel_summary
    def summary(*a, **kw):
        seen.append(kw['rows'])
        return original(*a, **kw)
    original_quality = funnel.build_fill_quality_report
    def quality(*a, **kw):
        assert kw['rows'] is seen[0]
        return original_quality(*a, **kw)
    monkeypatch.setattr(funnel, 'build_funnel_summary', summary)
    monkeypatch.setattr(funnel, 'build_fill_quality_report', quality)
    result = funnel.refresh_all_execution_reports(str(tmp_path), snapshot=snapshot, publish=False)
    assert result['funnel_summary']['approve_count'] == result['approval_ev']['AI_APPROVE_EV']['n'] == 1
    assert not list(tmp_path.iterdir())


def test_snapshot_builders_match_existing_report_values(tmp_path, monkeypatch):
    import execution_funnel as funnel
    monkeypatch.setattr(funnel, '_utc_iso', lambda: 'fixed-time')
    snapshot = {funnel.FUNNEL_FILE: b'{"trade_id":"a","stage":"APPROVE"}\n'
                b'{"trade_id":"a","stage":"FILLED","filled":true,"order_submitted":true}\n',
                'shadow_outcome.jsonl': b'{"trade_id":"b","net_pnl_usd":-1}\n',
                'trades_3factor.csv': b'trade_id,net_pnl_usd\na,2\n'}
    for name, raw in snapshot.items():
        (tmp_path/name).write_bytes(raw)
    old = funnel.refresh_all_execution_reports(str(tmp_path))
    assert funnel.refresh_all_execution_reports(str(tmp_path), snapshot=snapshot, publish=False) == old


def test_capture_oserror_releases_gate_and_output_budget_keeps_old(tmp_path, monkeypatch):
    gate = threading.RLock()
    original = reports.file_identity
    def fail(_):
        raise PermissionError('injected')
    monkeypatch.setattr(reports, 'file_identity', fail)
    with pytest.raises(PermissionError):
        invoke(tmp_path, computed, gate=gate)
    acquired = []
    def probe():
        if gate.acquire(timeout=1):
            acquired.append(True); gate.release()
    thread = threading.Thread(target=probe, daemon=True)
    thread.start(); thread.join(2)
    assert acquired == [True]
    monkeypatch.setattr(reports, 'file_identity', original)
    (tmp_path/'report.json').write_text('{"old":true}')
    with pytest.raises(reports.SnapshotUnavailable, match='OUTPUT_BYTE_BUDGET'):
        invoke(tmp_path, lambda _: ({}, {'report.json': {'large': 'x'*(4*1024*1024)}}))
    assert (tmp_path/'report.json').read_text() == '{"old":true}'


def test_actual_reset_entry_fences_before_work_and_on_early_failure():
    tree = ast.parse(Path(__file__).with_name('bot.py').read_text(encoding='utf-8'))
    reset = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_perform_fresh_collection_reset_quiesced')
    prefix = []
    for node in reset.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            break
        prefix.append(node)
    # Run actual reset prefix, then inject a failure before its first operational step.
    prefix.append(ast.Raise(exc=ast.Call(func=ast.Name(id='RuntimeError', ctx=ast.Load()), args=[], keywords=[])))
    fn = ast.FunctionDef(name='reset_entry', args=reset.args, body=prefix, decorator_list=[])
    env = {'_research_report_reset_generation': 0}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[])), 'bot.py', 'exec'), env)
    for expected in (1, 2):
        with pytest.raises(RuntimeError):
            env['reset_entry']()
        assert env['_research_report_reset_generation'] == expected
    callers = [n.name for n in tree.body if isinstance(n, ast.FunctionDef) and any(
        isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id == '_perform_fresh_collection_reset_quiesced'
        for c in ast.walk(n))]
    assert callers == ['_perform_fresh_collection_reset_locked']


def test_actual_wrapper_single_flight_reset_fence_and_exception_cleanup(tmp_path):
    tree = ast.parse(Path(__file__).with_name('bot.py').read_text(encoding='utf-8'))
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_run_reset_guarded_report')
    env = dict(os=os, _research_write_gate=threading.RLock(), _fresh_collection_lock=threading.Lock(),
        _research_report_compute_lock=threading.Lock(), _research_report_reset_generation=0,
        RESEARCH_SESSION_FILE=str(tmp_path/'research_session.json'), _data_sync_runtime_root=lambda: tmp_path)
    exec(compile(ast.Module(body=[function], type_ignores=[]), 'bot.py', 'exec'), env)
    run = env['_run_reset_guarded_report']
    def compute(snapshot):
        assert run(computed, cwd=tmp_path, inputs=('input.jsonl',))['reason_code'] == 'REPORT_COMPUTE_BUSY'
        # The actual wrapper must re-read the generation, not capture it in its closure.
        env['_research_report_reset_generation'] += 1
        return computed(snapshot)
    assert run(compute, cwd=tmp_path, inputs=('input.jsonl',))['reason_code'] == 'REPORT_RESET_GENERATION_CHANGED'
    assert not (tmp_path/'report.json').exists()
    def fail(_):
        raise RuntimeError('compute')
    with pytest.raises(RuntimeError):
        run(fail, cwd=tmp_path, inputs=('input.jsonl',))
    assert run(computed, cwd=tmp_path, inputs=('input.jsonl',)) == {'done': True}
