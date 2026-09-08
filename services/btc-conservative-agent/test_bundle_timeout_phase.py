import json
from pathlib import Path
import subprocess
import sys

import pytest

import data_sync_bundle_runtime as runtime
from test_data_sync_bundle_worker import _fixture, _row


@pytest.mark.parametrize('mode', ['valid', 'nonce', 'identity', 'oversized', 'phase', 'partial'])
def test_timeout_phase_binding_and_owned_cleanup(mode):
    paths = []
    def runner(command, **kwargs):
        payload = json.loads(kwargs['input'])
        path = Path(payload['phase_path']); paths.append(path)
        value = {'nonce': payload['nonce'], 'identity': runtime._identity(payload['generation']), 'phase': 'ADMISSION'}
        if mode == 'nonce': value['nonce'] = 'stale'
        if mode == 'identity': value['identity'] = {'other': 'generation'}
        if mode == 'phase': value['phase'] = '/secret/path'
        path.write_text('x' * 4097 if mode == 'oversized' else '{' if mode == 'partial' else json.dumps(value))
        raise subprocess.TimeoutExpired(command, kwargs['timeout'])
    result = runtime.run_slice({}, 'source', 'out', runner=runner)
    assert result['timeout_phase'] == ('ADMISSION' if mode == 'valid' else 'UNKNOWN')
    assert result['error'] == 'BUNDLE_SLICE_TIMEOUT'
    assert all(not path.exists() for path in paths)
    assert '/secret/path' not in json.dumps(result)


def test_actual_child_timeout_worker_phase(tmp_path):
    source = tmp_path / 'source'
    rows = [_row(source, 'v3/market_segments/11/' + '1' * 64 + '.json', b'sample')]
    metadata = _fixture(tmp_path, rows)
    paths = []
    def runner(command, **kwargs):
        paths.append(Path(json.loads(kwargs['input'])['phase_path']))
        # Run the actual child protocol and admission, injecting a blocking
        # worker operation. subprocess.run kills and waits on timeout.
        code = ('import time; import data_sync_bundle_runtime as r; '
                'import data_sync_bundle_worker as w; '
                'w.run_bundle_worker=lambda *a,**k: (k["phase_callback"]("CHECKPOINT"),time.sleep(30)); '
                'r._child()')
        return subprocess.run([sys.executable, '-c', code], **kwargs)
    result = runtime.run_slice(metadata, source, tmp_path/'out', timeout=1, runner=runner)
    assert result == {'status': 'FAILED', 'error': 'BUNDLE_SLICE_TIMEOUT', 'timeout_phase': 'CHECKPOINT'}
    assert all(not path.exists() for path in paths)
    assert not list((tmp_path/'out').rglob('bundle-worker-state.json'))


def test_actual_worker_phase_order_and_identical_bundle(tmp_path):
    from data_sync_bundle_worker import run_bundle_worker
    source = tmp_path/'source'
    rows = [_row(source, 'v3/market_segments/11/' + '1'*64 + '.json', b'sample')]
    metadata = _fixture(tmp_path, rows)
    phases = []
    baseline = run_bundle_worker(metadata, source, tmp_path/'base')
    observed = run_bundle_worker(metadata, source, tmp_path/'observed', phase_callback=phases.append)
    assert phases == ['LEASE_STATE', 'BUILD', 'CHECKPOINT']
    assert observed['cursor'] == baseline['cursor']
    assert observed['package']['package_sha256'] == baseline['package']['package_sha256']


def test_unavailable_diagnostic_storage_does_not_change_timeout(monkeypatch):
    def fail(**kwargs): raise OSError('disk unavailable')
    monkeypatch.setattr(runtime.tempfile, 'mkstemp', fail)
    def runner(command, **kwargs): raise subprocess.TimeoutExpired(command, 12)
    assert runtime.run_slice({}, 'source', 'out', runner=runner)['timeout_phase'] == 'UNKNOWN'
