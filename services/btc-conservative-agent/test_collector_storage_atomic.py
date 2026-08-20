import json
from pathlib import Path

import collector_storage


def test_storage_receipt_is_complete_and_atomically_replaced(tmp_path, monkeypatch):
    monkeypatch.setattr(collector_storage, "disk_usage_fraction", lambda _path=None: 0.25)
    target = tmp_path / collector_storage.STORAGE_STATE_FILE
    target.write_text('{"schema":"collector_storage_v1","pressure":true}\n', encoding="utf-8")

    receipt = collector_storage.storage_state(str(tmp_path))

    assert json.loads(target.read_text(encoding="utf-8")) == receipt
    assert target.read_bytes().endswith(b"\n")
    assert list(Path(tmp_path).glob(f".{collector_storage.STORAGE_STATE_FILE}.*.tmp")) == []


def test_failed_replace_preserves_previous_complete_receipt(tmp_path, monkeypatch):
    monkeypatch.setattr(collector_storage, "disk_usage_fraction", lambda _path=None: 0.25)
    target = tmp_path / collector_storage.STORAGE_STATE_FILE
    previous = '{"schema":"collector_storage_v1","pressure":false}\n'
    target.write_text(previous, encoding="utf-8")
    monkeypatch.setattr(collector_storage.os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("busy")))

    collector_storage.storage_state(str(tmp_path))

    assert target.read_text(encoding="utf-8") == previous
    assert list(Path(tmp_path).glob(f".{collector_storage.STORAGE_STATE_FILE}.*.tmp")) == []
