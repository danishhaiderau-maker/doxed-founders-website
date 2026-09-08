import json
import pytest
from types import SimpleNamespace
import lifecycle_pipeline as pipeline
from test_lifecycle_pipeline import _append, _row, _ready_rows, _patch_provenance, NOW

def test_continuous_append_ready_evidence_completes(tmp_path, monkeypatch):
    _patch_provenance(monkeypatch)
    for row in _ready_rows(): _append(tmp_path, row)
    completed = 0
    bundles = 0
    for index in range(3):
        for ledger in ('opportunity', 'decision'):
            _append(tmp_path, _row(ledger, f'{ledger}-{index}', episode_id='episode-noise'))
        report = pipeline.process_incremental_lifecycle_pipeline(tmp_path, now=NOW)
        completed += report['completion_appended_count']
        bundles += report['bundle_count']
        assert report['scan']['rows_scanned'] <= report['scan']['row_limit']
        assert report['scan']['bytes_indexed'] <= report['scan']['byte_limit']
    assert completed == 1
    assert bundles == 1

def test_insufficient_rows_stays_unevaluated(tmp_path, monkeypatch):
    _patch_provenance(monkeypatch)
    for row in _ready_rows(): _append(tmp_path, row)
    report = pipeline.process_incremental_lifecycle_pipeline(tmp_path, now=NOW, max_scan_rows=1)
    assert report['scan']['rows_scanned'] == 1
    assert report['scan']['caught_up'] is False
    assert report['candidate_count'] == report['completion_appended_count'] == 0

def test_residual_split_preserves_next_cursor_then_progresses(tmp_path):
    ledgers = tmp_path / 'v3' / 'ledgers'; ledgers.mkdir(parents=True)
    first = json.dumps({'padding':'x'*150}).encode()+b'\n'
    second = json.dumps({'padding':'y'*150}).encode()+b'\n'
    (ledgers/'opportunity.jsonl').write_bytes(first)
    (ledgers/'decision.jsonl').write_bytes(second)
    report = pipeline.process_incremental_lifecycle_pipeline(tmp_path, max_scan_bytes=len(first)+10)
    assert report['scan']['bytes_indexed'] == len(first)
    assert not report['scan']['caught_up']
    connection = pipeline._open_incremental_index(tmp_path)
    try:
        assert connection.execute("SELECT byte_offset FROM ledger_cursor WHERE ledger='decision'").fetchone() is None
    finally: connection.close()
    report = pipeline.process_incremental_lifecycle_pipeline(tmp_path, max_scan_bytes=len(first)+10)
    assert report['scan']['bytes_indexed'] == len(second)
    assert report['scan']['caught_up']

def test_corrupt_tail_after_progress_is_not_deferred(tmp_path):
    ledgers = tmp_path / 'v3' / 'ledgers'; ledgers.mkdir(parents=True)
    (ledgers/'opportunity.jsonl').write_bytes(b'{}\n')
    (ledgers/'decision.jsonl').write_bytes(b'{"broken":')
    with pytest.raises(ValueError, match='TRUNCATED_JSONL_LINE'):
        pipeline.process_incremental_lifecycle_pipeline(tmp_path, max_scan_bytes=100)

def test_full_budget_split_still_errors(tmp_path):
    ledgers = tmp_path / 'v3' / 'ledgers'; ledgers.mkdir(parents=True)
    (ledgers/'opportunity.jsonl').write_bytes(b'{"long":"'+b'x'*100+b'"}\n')
    with pytest.raises(ValueError, match='SCAN_BYTE_LIMIT_SPLITS_RECORD'):
        pipeline.process_incremental_lifecycle_pipeline(tmp_path, max_scan_bytes=10)

def test_time_budget_between_ledgers_preserves_next_cursor(tmp_path, monkeypatch):
    ledgers = tmp_path / 'v3' / 'ledgers'; ledgers.mkdir(parents=True)
    for name in ('opportunity', 'decision'):
        (ledgers/f'{name}.jsonl').write_bytes(b'{}\n')
    elapsed = [0.0]
    original = pipeline._index_ledger_chunk
    def index(*args, **kwargs):
        result = original(*args, **kwargs)
        elapsed[0] = 2.0
        return result
    monkeypatch.setattr(pipeline, 'time', SimpleNamespace(monotonic=lambda: elapsed[0], time=pipeline.time.time))
    monkeypatch.setattr(pipeline, '_index_ledger_chunk', index)
    report = pipeline.process_incremental_lifecycle_pipeline(tmp_path, max_runtime_sec=1)
    assert tuple(report['scan']['ledgers']) == ('opportunity',)
    assert report['candidate_count'] == 0
    assert not report['scan']['caught_up']
    connection = pipeline._open_incremental_index(tmp_path)
    try:
        assert connection.execute("SELECT byte_offset FROM ledger_cursor WHERE ledger='decision'").fetchone() is None
    finally: connection.close()
