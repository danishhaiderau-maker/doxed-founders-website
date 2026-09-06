import hashlib
import json
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'services/btc-conservative-agent'))
import fly_bundle_retire as wrapper
import fly_bundle_retire_dispatch as dispatch
from test_data_sync_bundle_runtime import _status_fixture

REV = 'd' * 40
CURRENT = 'b' * 64


def fixture(tmp_path, monkeypatch):
    meta, source, output, directory = _status_fixture(tmp_path)
    volume = tmp_path / 'volume'
    volume.mkdir()
    source.rename(volume / 'runtime')
    (volume / '.data-sync-snapshots').mkdir()
    output.rename(volume / '.data-sync-snapshots/transport-bundles')
    state = volume / '.data-sync-snapshots/transport-bundles' / directory.name / 'bundle-worker-state.json'
    parsed = json.loads(state.read_text())
    parsed['generation']['source_git_rev'] = 'a' * 12
    state.write_text(json.dumps(parsed))
    monkeypatch.setenv('SOURCE_GIT_REV', REV)
    def request(path):
        if path == '/api/status': return 200, {'source_git_rev': REV[:12], 'force_paper_mode': True, 'live_armed': False, 'bitfinex_live_enabled': False, 'process_alive': True, 'dashboard_owner': True}
        if path.startswith('/api/data-sync/manifest'): return 200, {'inventory_status': 'CURRENT', 'inventory_build_status': 'BUILDING', 'inventory_authoritative': True, 'inventory_ack_eligible': True, 'inventory_generation_id': CURRENT, 'inventory_sha256': CURRENT, 'source_git_rev': REV[:12], 'collection_epoch_id': 'epoch', 'tile_registry_signature': 'tile'}
        return 409, {'error': 'GENERATION_NOT_RETAINED_OR_ACK_ELIGIBLE'}
    return volume, state, meta['generation_id'], request


def test_inspection_is_default_no_retirement(tmp_path, monkeypatch):
    volume, state, old, request = fixture(tmp_path, monkeypatch)
    result = wrapper.run(REV, old, hashlib.sha256(state.read_bytes()).hexdigest(), CURRENT, request, volume=volume)
    assert result['status'] == 'INSPECTED' and result['retirement_performed'] is False
    assert state.exists() and not list(volume.glob('transport-retirement-*'))


@pytest.mark.parametrize('defect', ['generic404', 'active', 'same_source', 'wrong_rev'])
def test_unproven_authority_refused(tmp_path, monkeypatch, defect):
    volume, state, old, request = fixture(tmp_path, monkeypatch)
    if defect == 'same_source':
        value = json.loads(state.read_text()); value['generation']['source_git_rev'] = REV[:12]; state.write_text(json.dumps(value))
    if defect == 'wrong_rev': monkeypatch.setenv('SOURCE_GIT_REV', 'c' * 40)
    def responses(path):
        if path.startswith('/api/data-sync/bundles'):
            if defect == 'generic404': return 404, {'error': 'PACKAGE_NOT_BUILT_OR_RETAINED'}
            if defect == 'active': return 200, {}
        return request(path)
    with pytest.raises(ValueError): wrapper.run(REV, old, hashlib.sha256(state.read_bytes()).hexdigest(), CURRENT, responses, volume=volume, inspect_only=False)
    assert state.exists()


def test_execute_and_idempotent_receipt_resume(tmp_path, monkeypatch):
    volume, state, old, request = fixture(tmp_path, monkeypatch)
    digest = hashlib.sha256(state.read_bytes()).hexdigest()
    first = wrapper.run(REV, old, digest, CURRENT, request, volume=volume, inspect_only=False)
    assert first['status'] == 'COMPLETE' and not state.exists()
    assert wrapper.run(REV, old, digest, CURRENT, request, volume=volume, inspect_only=False)['status'] == 'COMPLETE'


def test_reviewed_dispatch_payload_is_bounded_and_inspect_default():
    root = Path(__file__).resolve().parents[1]
    sources = {name: (root / 'services/btc-conservative-agent' / (name + '.py')).read_text() for name in ('data_sync_bundle_storage', 'data_sync_bundle_retirement')}
    sources['fly_bundle_retire'] = Path(wrapper.__file__).read_text()
    command = dispatch.remote_command(sources, REV, 'a' * 64, 'e' * 64, CURRENT)
    assert len(command.encode()) < 24 * 1024 and command.startswith('python -c')
