import hashlib
from types import SimpleNamespace
from test_raw_generation_cleanup_owner import canonical, persisted_authority, owner
import raw_generation_cleanup_owner as module


def test_owner_does_not_replace_signed_transaction_fields(tmp_path, monkeypatch):
    _, identity = persisted_authority(tmp_path)
    service = owner(tmp_path, identity)
    monkeypatch.setattr(service, '_authority', lambda *a, **k: None)
    receipt = {'schema': 'raw_generation_purge_receipt_v1', 'state': 'PURGED',
               'generation_id': 'V3:decision:1', 'freed_bytes': 10,
               'free_bytes_before': 100, 'free_bytes_after': 110, 'free_bytes_delta': 10}
    receipt['receipt_sha256'] = hashlib.sha256(canonical(receipt)).hexdigest()
    monkeypatch.setattr(service.tx, 'purge', lambda *a, **k: dict(receipt))
    values = iter([200, 205])
    monkeypatch.setattr(module.shutil, 'disk_usage', lambda _: SimpleNamespace(free=next(values)))
    result = service.purge('V3:decision:1')
    assert {key: result[key] for key in receipt} == receipt
    assert result['owner_space_observation']['free_bytes_delta'] == 5
    signed = {k: v for k, v in result.items() if k not in {'receipt_sha256', 'owner_space_observation'}}
    assert hashlib.sha256(canonical(signed)).hexdigest() == result['receipt_sha256']
