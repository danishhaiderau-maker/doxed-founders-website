import pytest
from research.local_holdout_producer import _read_manifest


def test_manifest_growth_remains_bounded(tmp_path):
    path = tmp_path / 'manifest.json'
    path.write_bytes(b'{}')
    assert _read_manifest(path) == {}
    path.write_bytes(b' ' * (1024 * 1024 + 1))
    with pytest.raises(ValueError, match='HOLDOUT_MANIFEST_READ_BUDGET'):
        _read_manifest(path)
