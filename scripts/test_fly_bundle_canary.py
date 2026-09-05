import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('canary', HERE / 'fly_bundle_canary.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)
sys.path.insert(0, str(HERE.parent / 'services/btc-conservative-agent'))
from data_sync_bundle_runtime import run_slice

REV = 'c' * 12
canonical = lambda value: json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def fixture(volume):
    source = volume / 'runtime'
    relative = 'v3/market_segments/11/' + '1' * 64 + '.json'
    path = source / relative
    path.parent.mkdir(parents=True)
    path.write_bytes(b'sample')
    info = path.stat()
    rows = [{'path': relative, 'size': 6, 'mtime_ns': info.st_mtime_ns,
             'inode': info.st_ino, 'consistency_mode': 'strict_generation_v1'}]
    page = canonical({'schema': 'fly_runtime_inventory_page_v1', 'page_index': 0,
                      'file_count': 1, 'total_bytes': 6, 'rows': rows,
                      'rows_sha256': hashlib.sha256(canonical(rows)).hexdigest()})
    sha = hashlib.sha256(page).hexdigest()
    name = f'p00000000-{sha[:24]}.json'
    index = canonical({'page_index': 0, 'file_count': 1, 'total_bytes': 6,
                       'page_sha256': sha, 'file_name': name}) + b'\n'
    digest = hashlib.sha256(b'fly_runtime_inventory_generation_v2\n' + f'0:1:6:{sha}\n'.encode()).hexdigest()
    directory = volume / '.data-sync-snapshots/inventory-generations' / digest
    directory.mkdir(parents=True)
    (directory / name).write_bytes(page)
    (directory / 'page-index.jsonl').write_bytes(index)
    identity = {'source_git_rev': REV, 'collection_epoch_id': 'epoch-test', 'tile_registry_signature': 'f' * 64}
    snapshot = {'schema': 'fly_runtime_inventory_snapshot_v2', 'source_git_rev': REV,
                'generation': {'storage': 'disk_pages_v2', 'generation_id': digest,
                    'generation_dir': str(directory), 'page_index_path': str(directory / 'page-index.jsonl'),
                    'page_index_sha256': hashlib.sha256(index).hexdigest(), 'file_count': 1,
                    'total_bytes': 6, 'page_count': 1, 'page_size': 1, 'bundle_identity': identity}}
    (volume / 'sync_inventory_current.json').write_bytes(canonical(snapshot))
    bootstrap = {'complete': True, 'blocked': False}
    manifest = {'schema': 'fly_runtime_incremental_sync_v1', **identity,
                'inventory_generation_id': digest, 'inventory_sha256': digest,
                'inventory_status': 'CURRENT', 'inventory_authoritative': True,
                'inventory_ack_eligible': True, 'inventory_build_status': 'IDLE',
                'receipt_bootstrap': bootstrap, 'file_count': 1, 'total_bytes': 6, 'manifest_page_count': 1}
    status = {'source_git_rev': REV, 'force_paper_mode': True, 'live_armed': False,
              'dashboard_owner': True, 'dashboard_pid': 123,
              'bitfinex_live_enabled': False, 'process_alive': True,
              'lifecycle_pipeline': {'owner': True, 'running': True, 'source_revision_match': True,
                  'active': False, 'last_outcome': 'SUCCESS', 'last_success_age_sec': 1,
                  'next_run_in_sec': 50, 'pressure': False, 'emergency': False,
                  'overlap_code': None, 'receipt_bootstrap': bootstrap}}
    return digest, manifest, status


def test_actual_existing_child_one_slice(tmp_path):
    digest, manifest, status = fixture(tmp_path)
    calls = []
    def request(url):
        calls.append(url)
        return copy.deepcopy(status if url == '/api/status' else manifest)
    result = c.run_canary(REV, digest, request, run_slice, lambda: None, volume=tmp_path)
    assert result['status'] == 'SLICE_VERIFIED'
    assert result['slice']['status'] == 'COMPLETE'
    assert result['slice']['package_index_count'] == 1
    assert result['managed_coordinator_started'] is False
    assert result['ack_performed'] is result['cleanup_performed'] is False
    assert all(url == '/api/status' or '?paged=1&generation_id=' in url for url in calls)
    assert (tmp_path / 'runtime/v3/market_segments/11' / ('1' * 64 + '.json')).read_bytes() == b'sample'


@pytest.mark.parametrize('mutation', [
    lambda m, s: m.update(inventory_status='BUILDING'),
    lambda m, s: m.update(inventory_ack_eligible=False),
    lambda m, s: m.update(inventory_authoritative=False),
    lambda m, s: m.update(inventory_build_status='BUILDING'),
    lambda m, s: m.update(source_git_rev='a' * 12),
    lambda m, s: s.update(live_armed=True),
    lambda m, s: s['lifecycle_pipeline'].update(last_success_age_sec=100),
    lambda m, s: s['lifecycle_pipeline'].update(next_run_in_sec=2),
    lambda m, s: s['lifecycle_pipeline'].update(active=True),
    lambda m, s: s['lifecycle_pipeline'].update(overlap_code='SQLITE_SNAPSHOT_BUILDING'),
    lambda m, s: s['lifecycle_pipeline'].pop('pressure'),
    lambda m, s: s['lifecycle_pipeline'].update(pressure=True),
    lambda m, s: s['lifecycle_pipeline']['receipt_bootstrap'].update(complete=False),
])
def test_refuses_before_slice(tmp_path, mutation):
    digest, manifest, status = fixture(tmp_path)
    mutation(manifest, status)
    calls = []
    with pytest.raises(c.Blocked):
        c.run_canary(REV, digest, lambda u: status if u == '/api/status' else manifest,
                     lambda *a, **k: calls.append(1), lambda: None, volume=tmp_path)
    assert calls == []


def test_post_slice_snapshot_mutation_not_certified(tmp_path):
    digest, manifest, status = fixture(tmp_path)
    def slice_(*a, **kw):
        (tmp_path / 'sync_inventory_current.json').write_bytes(b'{}')
        return {'status': 'BUILDING'}
    with pytest.raises(c.Blocked, match='SNAPSHOT_CHANGED_AFTER_SLICE'):
        c.run_canary(REV, digest, lambda u: status if u == '/api/status' else manifest,
                     slice_, lambda: None, volume=tmp_path)


def test_index_content_cannot_be_rebound_by_only_snapshot_hash(tmp_path):
    digest, manifest, status = fixture(tmp_path)
    path = tmp_path / 'sync_inventory_current.json'
    value = json.loads(path.read_bytes())
    index_path = Path(value['generation']['page_index_path'])
    row = json.loads(index_path.read_bytes())
    row['page_sha256'] = '0' * 64
    row['file_name'] = 'p00000000-' + '0' * 24 + '.json'
    raw = canonical(row) + b'\n'
    index_path.write_bytes(raw)
    value['generation']['page_index_sha256'] = hashlib.sha256(raw).hexdigest()
    with pytest.raises(c.Blocked, match='INDEX_MANIFEST_BINDING'):
        c.bind_snapshot(canonical(value), manifest, tmp_path)


def test_inspection_allows_stale_and_reads_only_exact_progress(tmp_path, monkeypatch):
    digest, manifest, status = fixture(tmp_path)
    manifest.update(inventory_status='BUILDING', inventory_sha256=None)
    fingerprint = '9' * 64
    work = tmp_path / '.data-sync-snapshots'
    (work / f'inventory-worker-v2-{fingerprint[:32]}.progress.json').write_bytes(
        canonical({'request_fingerprint': fingerprint, 'phase': 'SCAN', 'files_seen': 7,
                   'cpu_seconds': 4.2, 'invocation_files_seen': 10, 'invocation_dirs_seen': 2,
                   'file_budget': 500, 'elapsed_budget_seconds': 4,
                   'current_directory_files_remaining': 99, 'peak_rss_bytes': 123456}))
    monkeypatch.setattr(c.os, 'scandir', lambda *a: pytest.fail('no directory enumeration'))
    paths = []
    def request(url):
        paths.append(url)
        return status if url == '/api/status' else manifest
    result = c.inspect_only(REV, digest, request, volume=tmp_path, inventory_fingerprint=fingerprint)
    assert result['slice_invoked'] is False
    assert result['progress_receipts'][0]['files_seen'] == 7
    for key, value in {'cpu_seconds': 4.2, 'invocation_files_seen': 10, 'invocation_dirs_seen': 2,
                       'file_budget': 500, 'elapsed_budget_seconds': 4,
                       'current_directory_files_remaining': 99, 'peak_rss_bytes': 123456}.items():
        assert result['progress_receipts'][0][key] == value
    assert 'NOT_CURRENT_INVENTORY_OR_CANARY_PROOF' in result['inspection_authority']
    assert paths == ['/api/status', '/api/data-sync/manifest?identity_only=1']


def test_bounded_refuses_oversize_and_path_link(tmp_path):
    path = tmp_path / 'big'
    path.write_bytes(b'123')
    with pytest.raises(c.Blocked, match='OBJECT_SIZE_LIMIT'):
        c.bounded(path, 2)


def test_source_has_no_mutating_api_or_coordinator():
    text = (HERE / 'fly_bundle_canary.py').read_text()
    assert 'run_managed_generation' not in text
    assert 'import bot' not in text
    assert '127.0.0.1:7002' in text
    assert 'os.scandir' not in text
