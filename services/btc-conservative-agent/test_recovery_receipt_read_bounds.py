from pathlib import Path
import os

import pytest
from research_v3_store import V3EvidenceStore


@pytest.mark.parametrize('linked_parent', [False, True])
def test_real_receipt_symlink_is_rejected(tmp_path, monkeypatch, linked_parent):
    store = V3EvidenceStore(tmp_path / 'store', epoch_id='e1')
    target_dir = store.root / 'target'
    target_dir.mkdir()
    target = target_dir / 'receipt.json'
    target.write_text('{}')
    link = store.root / 'link'
    try:
        link.symlink_to(target_dir if linked_parent else target, target_is_directory=linked_parent)
    except OSError as exc:
        pytest.skip(f'Host cannot create symlink: {exc.winerror if hasattr(exc, "winerror") else exc.errno}')
    receipt = link / 'receipt.json' if linked_parent else link
    monkeypatch.setattr(store, '_record_receipt_path', lambda *a: receipt)
    with pytest.raises(ValueError, match='RECEIPT_LINKED'):
        store._paper_close_recovery_duplicate('execution', {'record_id': 'x'})


@pytest.mark.parametrize('linked_parent', [False, True])
def test_recovery_rejects_linked_receipt_components(tmp_path, monkeypatch, linked_parent):
    store = V3EvidenceStore(tmp_path, epoch_id='e1')
    directory = store.root / 'receipts-test'
    directory.mkdir()
    receipt = directory / 'receipt.json'
    receipt.write_text('{}')
    monkeypatch.setattr(store, '_record_receipt_path', lambda *a: receipt)
    original = Path.is_symlink
    linked = directory if linked_parent else receipt
    monkeypatch.setattr(Path, 'is_symlink', lambda path: path == linked or original(path))
    with pytest.raises(ValueError, match='RECEIPT_LINKED'):
        store._paper_close_recovery_duplicate('execution', {'record_id': 'x'})


def test_recovery_rejects_outside_receipt_before_read(tmp_path, monkeypatch):
    store = V3EvidenceStore(tmp_path / 'data', epoch_id='e1')
    outside = tmp_path / 'outside.json'
    outside.write_text('{}')
    monkeypatch.setattr(store, '_record_receipt_path', lambda *a: outside)
    with pytest.raises(ValueError, match='OUTSIDE_ROOT'):
        store._paper_close_recovery_duplicate('execution', {'record_id': 'x'})


def test_recovery_bounds_growth_after_stat(tmp_path, monkeypatch):
    store = V3EvidenceStore(tmp_path, epoch_id='e1')
    receipt = store.root / 'growth.json'
    receipt.write_text('{}')
    monkeypatch.setattr(store, '_record_receipt_path', lambda *a: receipt)
    original = Path.open
    reads = []

    class Reader:
        def __init__(self, handle): self.handle = handle
        def __enter__(self): return self
        def __exit__(self, *args): self.handle.close()
        def fileno(self): return self.handle.fileno()
        def read(self, size):
            reads.append(size)
            return self.handle.read(size)

    def raced_open(path, mode='r', *args, **kwargs):
        if path == receipt and mode == 'rb':
            with original(path, 'wb') as handle:
                handle.write(b' ' * (128 * 1024))
            return Reader(original(path, mode, *args, **kwargs))
        return original(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', raced_open)
    with pytest.raises(ValueError, match='RECEIPT_OVERSIZED'):
        store._paper_close_recovery_duplicate('execution', {'record_id': 'x'})
    assert reads == [64 * 1024 + 1]


def test_recovery_rejects_receipt_replaced_after_read(tmp_path, monkeypatch):
    store = V3EvidenceStore(tmp_path, epoch_id='e1')
    receipt = store.root / 'receipt.json'
    receipt.write_text('{}')
    replacement = store.root / 'replacement.json'
    replacement.write_text('{}')
    monkeypatch.setattr(store, '_record_receipt_path', lambda *a: receipt)
    original = Path.open

    class Reader:
        def __init__(self, handle): self.handle = handle
        def __enter__(self): return self
        def __exit__(self, *args):
            self.handle.close()
            os.replace(replacement, receipt)
        def fileno(self): return self.handle.fileno()
        def read(self, size): return self.handle.read(size)

    def raced_open(path, mode='r', *args, **kwargs):
        handle = original(path, mode, *args, **kwargs)
        return Reader(handle) if path == receipt and mode == 'rb' else handle

    monkeypatch.setattr(Path, 'open', raced_open)
    with pytest.raises(ValueError, match='RECEIPT_CHANGED'):
        store._paper_close_recovery_duplicate('execution', {'record_id': 'x'})
