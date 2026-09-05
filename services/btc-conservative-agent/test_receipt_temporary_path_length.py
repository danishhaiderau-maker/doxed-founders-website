import json
from pathlib import Path
import pytest
from research_v3_store import V3EvidenceStore


@pytest.mark.parametrize("method", ["_write_immutable_receipt", "_atomic_json_receipt"])
def test_long_valid_destination_does_not_expand_temporary_name(tmp_path, monkeypatch, method):
    store = V3EvidenceStore(tmp_path, epoch_id="epoch-test")
    filename = "a" * 64 + ".PREPARED.json"
    # Destination is valid under classic Windows MAX_PATH; old temp was not.
    padding = 246 - len(str(tmp_path)) - len(filename) - 2
    assert padding > 0
    parent = tmp_path / ("p" * padding)
    destination = parent / filename
    assert len(str(destination)) == 246
    assert len(str(destination.with_name("." + filename + "." + "b"*32 + ".tmp"))) > 260
    observed = []
    original_open = Path.open
    def bounded_open(path, *args, **kwargs):
        if path.suffix == ".tmp":
            observed.append(path)
            assert path.parent == parent
            assert len(str(path)) < 260
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", bounded_open)
    if method == "_atomic_json_receipt":
        import research_v3_store as module
        original = module.tempfile.mkstemp
        def bounded_mkstemp(*args, **kwargs):
            fd, candidate = original(*args, **kwargs)
            observed.append(Path(candidate))
            assert len(candidate) < 260
            return fd, candidate
        monkeypatch.setattr(module.tempfile, "mkstemp", bounded_mkstemp)
    getattr(store, method)(destination, {"ok": True})
    assert json.loads(destination.read_text()) == {"ok": True}
    assert observed and all(not p.exists() for p in observed)
