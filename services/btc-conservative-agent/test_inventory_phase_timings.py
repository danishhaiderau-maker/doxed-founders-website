import pytest
from data_sync_inventory_worker import _InvocationTimings


def test_fixed_totals_preserve_result_and_failures_without_payloads():
    now = [0.0]
    timer = _InvocationTimings(lambda: now[0])
    def work():
        now[0] += 2
        return "private"
    assert timer.call("row_processing", work) == "private"
    assert timer.call("row_processing", work) == "private"
    def fail():
        now[0] += 3
        raise RuntimeError("sensitive")
    with pytest.raises(RuntimeError):
        timer.call("sql_batch", fail)
    assert timer.snapshot() == dict(row_processing=4, sql_batch=3, counts=0, checkpoint_publication=0)
    assert 'private' not in str(timer.snapshot())
    assert 'sensitive' not in str(timer.snapshot())
    with pytest.raises(ValueError):
        timer.call("raw/path", work)


def test_snapshot_detached_and_clock_regression_clamped():
    stamps = iter([4, 2])
    timer = _InvocationTimings(lambda: next(stamps))
    timer.call("counts", lambda: None)
    snapshot = timer.snapshot()
    snapshot['counts'] = 99
    assert timer.snapshot()['counts'] == 0


def test_setup_delay_is_measured_without_consuming_scan_budget(tmp_path, monkeypatch):
    import json
    import data_sync_inventory_worker as worker
    from test_data_sync_inventory_worker_contract import _request, _paths
    nonce = 'b' * 32
    request = _request(tmp_path, nonce)
    request['inventory_file_budget'] = 1
    (tmp_path / 'runtime' / 'a.json').write_text('{}')
    request_path, result_path = _paths(tmp_path, nonce)
    request_path.write_text(json.dumps(request))
    loaded = worker._load_request(request_path, result_path, nonce)
    original_open = worker._open_database
    original_clock = worker.time.monotonic
    offset = [0.0]
    monkeypatch.setattr(worker.time, 'monotonic', lambda: original_clock() + offset[0])
    def delayed_open(*args, **kwargs):
        result = original_open(*args, **kwargs)
        offset[0] += 1000.0
        return result
    monkeypatch.setattr(worker, '_open_database', delayed_open)
    _, receipt = worker._build_resumable(loaded, tmp_path / '.data-sync-snapshots')
    assert receipt['invocation_setup_seconds'] >= 1000.0
    assert receipt['invocation_elapsed_seconds'] < 1000.0
    assert receipt['invocation_files_seen'] == 1


@pytest.mark.parametrize('fail_checkpoint', [False, True])
def test_actual_resumable_progress_and_checkpoint_order(tmp_path, monkeypatch, fail_checkpoint):
    import json
    import data_sync_inventory_worker as worker
    from test_data_sync_inventory_worker_contract import _request, _paths
    nonce = 'a' * 32
    request = _request(tmp_path, nonce)
    request['inventory_file_budget'] = 1
    for name in ('a.json', 'b.json', 'c.json'):
        (tmp_path / 'runtime' / name).write_text('{}')
    request_path, result_path = _paths(tmp_path, nonce)
    request_path.write_text(json.dumps(request))
    loaded = worker._load_request(request_path, result_path, nonce)
    work = tmp_path / '.data-sync-snapshots'
    checkpoint_path, progress_path, _, _ = worker._state_paths(work, worker._request_fingerprint(loaded))
    original = worker._atomic_json
    publications = []
    def publish(path, body):
        if path == checkpoint_path:
            publications.append('checkpoint')
            if fail_checkpoint:
                raise OSError('INJECTED_CHECKPOINT_FAILURE')
        if path == progress_path:
            publications.append('progress')
        return original(path, body)
    monkeypatch.setattr(worker, '_atomic_json', publish)
    if fail_checkpoint:
        with pytest.raises(OSError, match='INJECTED_CHECKPOINT_FAILURE'):
            worker._build_resumable(loaded, work)
        assert publications == ['checkpoint']
        assert not progress_path.exists()
    else:
        generation, receipt = worker._build_resumable(loaded, work)
        assert generation is None
        assert publications == ['checkpoint', 'progress']
        saved = json.loads(progress_path.read_text())
        assert saved['invocation_phase_seconds'] == receipt['invocation_phase_seconds']
        assert saved['invocation_setup_seconds'] >= 0
        assert saved['invocation_setup_seconds'] == receipt['invocation_setup_seconds']
        assert set(saved['invocation_phase_seconds']) == {'row_processing', 'sql_batch', 'counts', 'checkpoint_publication'}
        assert all(v >= 0 for v in saved['invocation_phase_seconds'].values())
        _, second = worker._build_resumable(loaded, work)
        assert second['files_seen'] > receipt['files_seen']
        assert second['invocations'] == receipt['invocations'] + 1
