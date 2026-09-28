from pathlib import Path
import pytest
import data_sync_inventory_worker as worker
from test_data_sync_quarantine_receipt import fixture


def request(root):
    return {'_runtime': root, '_volume': root, 'extensions': ['.json', '.sqlite3']}


@pytest.mark.parametrize('suffix', ['', '-wal', '-shm'])
def test_original_components_strict_and_bound(tmp_path, suffix, monkeypatch):
    import sqlite3
    monkeypatch.setattr(sqlite3, 'connect', lambda *a, **k: pytest.fail('online backup forbidden'))
    directory = fixture(tmp_path)
    row = worker._row(directory / ('lifecycle_index.sqlite3' + suffix), request(tmp_path))
    assert row['consistency_mode'] == 'strict_generation_v1'
    assert row['forensic_component']['payload_hash_verified'] is False
    assert row['forensic_component']['size'] == row['size']


def test_active_database_still_snapshot_and_unbound_sidecars_excluded(tmp_path):
    active = tmp_path / 'lifecycle_index.sqlite3'
    active.write_bytes(b'active')
    Path(str(active) + '-wal').write_bytes(b'wal')
    assert worker._row(active, request(tmp_path))['consistency_mode'] == 'sqlite_snapshot_v1'
    assert worker._row(Path(str(active) + '-wal'), request(tmp_path)) is None


def test_invalid_receipt_never_gets_strict_exemption(tmp_path):
    directory = fixture(tmp_path)
    (directory / 'receipt.json').write_text('{}')
    for name in ('lifecycle_index.sqlite3', 'lifecycle_index.sqlite3-wal'):
        with pytest.raises(RuntimeError, match='QUARANTINE_COMPONENT_BINDING_INVALID'):
            worker._row(directory / name, request(tmp_path))


def test_fingerprint_declares_new_component_admission_contract(tmp_path):
    settings = {**request(tmp_path), '_roots': [tmp_path]}
    assert worker._stable_request(settings)['quarantine_component_contract'] == 'receipt_bound_original_components_v1'
