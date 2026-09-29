import hashlib
import json
import pytest
from data_sync_quarantine_receipt import original_component_binding, SCHEMA, MAX_RECEIPT_BYTES
from data_sync_quarantine_receipt import verify_original_component_group


def fixture(root, mutate=lambda row: None):
    directory = root / 'v3/lifecycle_bundle_index/recovery-quarantine' / ('a' * 16)
    directory.mkdir(parents=True)
    rows = []
    for suffix in ('', '-wal', '-shm'):
        name = 'lifecycle_index.sqlite3' + suffix
        payload = ('original' + suffix).encode()
        (directory / name).write_bytes(payload)
        rows.append({'name': name, 'size': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()})
    receipt = {'schema': SCHEMA, 'status': 'QUARANTINED', 'recovery_id': 'a' * 64,
               'components': rows, 'sources': [], 'trigger': 'SOURCE_LEDGER_ROTATED:opportunity.jsonl'}
    mutate(receipt)
    receipt['receipt_sha256'] = hashlib.sha256(json.dumps(receipt, separators=(',', ':'), sort_keys=True).encode()).hexdigest()
    (directory / 'receipt.json').write_text(json.dumps(receipt), encoding='utf-8')
    return directory


@pytest.mark.parametrize('suffix', ['', '-wal', '-shm'])
def test_original_components_bound_without_sqlite_open(tmp_path, suffix):
    directory = fixture(tmp_path)
    path = directory / ('lifecycle_index.sqlite3' + suffix)
    binding = original_component_binding(tmp_path, path.relative_to(tmp_path).as_posix())
    assert binding['expected_sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert binding['payload_hash_verified'] is False
    assert len(binding['required_components']) == 3
    assert binding['consistency_mode'] == 'strict_generation_v1'


@pytest.mark.parametrize('change', [
    lambda r: r.update(schema='wrong'), lambda r: r.update(status='COMPLETE'),
    lambda r: r.update(recovery_id='b' * 64),
    lambda r: r['components'].__setitem__(1, r['components'][0]),
    lambda r: r['components'][0].update(size=True),
    lambda r: r['components'][0].update(name='../outside'),
])
def test_rejects_invalid_bound_receipt(tmp_path, change):
    directory = fixture(tmp_path, change)
    with pytest.raises(ValueError):
        original_component_binding(tmp_path, (directory / 'lifecycle_index.sqlite3').relative_to(tmp_path).as_posix())


@pytest.mark.parametrize('kind', ['hash', 'oversize', 'size', 'duplicate'])
def test_tampering(tmp_path, kind):
    directory = fixture(tmp_path)
    receipt = directory / 'receipt.json'
    if kind == 'hash': receipt.write_text(receipt.read_text().replace('QUARANTINED', 'CHANGED'))
    elif kind == 'oversize': receipt.write_bytes(b' ' * (MAX_RECEIPT_BYTES + 1))
    elif kind == 'size': (directory / 'lifecycle_index.sqlite3').write_bytes(b'x')
    else: receipt.write_text('{"schema":1,"schema":2}')
    with pytest.raises(ValueError):
        original_component_binding(tmp_path, (directory / 'lifecycle_index.sqlite3').relative_to(tmp_path).as_posix())


def test_no_blanket_exemption(tmp_path):
    assert original_component_binding(tmp_path, 'v3/lifecycle_bundle_index/lifecycle_index.sqlite3') is None
    assert original_component_binding(tmp_path, 'v3/lifecycle_bundle_index/recovery-quarantine/' + 'a'*16 + '/retired-active.sqlite3') is None
    with pytest.raises(ValueError): original_component_binding(tmp_path, '../outside')


def test_reparse_attribute_refused_without_symlink_privilege(tmp_path, monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace
    directory = fixture(tmp_path)
    target = directory / 'lifecycle_index.sqlite3'
    original = Path.lstat
    def marked(path):
        info = original(path)
        if path == target:
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
        return info
    monkeypatch.setattr(Path, 'lstat', marked)
    with pytest.raises(ValueError, match='LINK_REFUSED'):
        original_component_binding(tmp_path, target.relative_to(tmp_path).as_posix())


def test_same_size_payload_is_not_claimed_verified(tmp_path):
    directory = fixture(tmp_path)
    target = directory / 'lifecycle_index.sqlite3'
    target.write_bytes(b'X' * target.stat().st_size)
    binding = original_component_binding(tmp_path, target.relative_to(tmp_path).as_posix())
    assert binding['payload_hash_verified'] is False
    assert binding['expected_sha256'] != hashlib.sha256(target.read_bytes()).hexdigest()


@pytest.mark.parametrize('name', ['receipt.json', 'lifecycle_index.sqlite3'])
def test_links_refused(tmp_path, name):
    directory = fixture(tmp_path)
    path = directory / name
    outside = tmp_path / 'outside'
    path.rename(outside)
    try: path.symlink_to(outside)
    except OSError: pytest.skip('symlink privilege unavailable')
    with pytest.raises(ValueError, match='LINK'):
        original_component_binding(tmp_path, (directory / 'lifecycle_index.sqlite3').relative_to(tmp_path).as_posix())


def test_complete_group_hashes_original_bytes_without_sqlite(tmp_path, monkeypatch):
    import sqlite3
    monkeypatch.setattr(sqlite3, 'connect', lambda *a, **k: pytest.fail('must not open SQLite'))
    directory = fixture(tmp_path)
    relative = (directory / 'lifecycle_index.sqlite3').relative_to(tmp_path).as_posix()
    result = verify_original_component_group(tmp_path, relative)
    assert result['group_complete'] and result['payload_hash_verified']
    assert len(result['components']) == 3
    assert result['verified_bytes'] == sum(p.stat().st_size for p in directory.iterdir() if p.name != 'receipt.json')
    assert result['cleanup_authorized'] is False


@pytest.mark.parametrize('damage', ['missing', 'same_size', 'budget'])
def test_group_never_certifies_incomplete_or_changed_bytes(tmp_path, damage):
    directory = fixture(tmp_path)
    sidecar = directory / 'lifecycle_index.sqlite3-wal'
    if damage == 'missing': sidecar.unlink()
    elif damage == 'same_size': sidecar.write_bytes(b'X' * sidecar.stat().st_size)
    with pytest.raises((ValueError, OSError)):
        verify_original_component_group(tmp_path,
            (directory / 'lifecycle_index.sqlite3').relative_to(tmp_path).as_posix(),
            maximum_bytes=1 if damage == 'budget' else 1024)


def test_generation_change_during_verification_refused(tmp_path, monkeypatch):
    import data_sync_quarantine_receipt as module
    directory = fixture(tmp_path)
    target = directory / 'lifecycle_index.sqlite3'
    actual = module._regular
    calls = 0
    def changed(path):
        nonlocal calls
        if path == target:
            calls += 1
            if calls == 6:
                path.write_bytes(b'X' * path.stat().st_size)
        return actual(path)
    monkeypatch.setattr(module, '_regular', changed)
    with pytest.raises(ValueError, match='CHANGED|HASH_MISMATCH'):
        verify_original_component_group(tmp_path, target.relative_to(tmp_path).as_posix())


def test_deadline_refused(tmp_path, monkeypatch):
    import data_sync_quarantine_receipt as module
    directory = fixture(tmp_path)
    values = iter([0, 2])
    monkeypatch.setattr(module.time, 'monotonic', lambda: next(values, 2))
    with pytest.raises(TimeoutError):
        verify_original_component_group(tmp_path,
            (directory / 'lifecycle_index.sqlite3').relative_to(tmp_path).as_posix(), timeout_seconds=1)
