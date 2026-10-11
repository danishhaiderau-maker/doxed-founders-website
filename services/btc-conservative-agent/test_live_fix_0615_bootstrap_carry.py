from types import SimpleNamespace

from research_v3_store import V3EvidenceStore

carries = V3EvidenceStore._bootstrap_identity_carries


def _store(**ident):
    base = {"epoch_id": "epoch-1", "source_revision": "aaa", "deployed_revision": "aaa",
            "tile_config_signature": "tiles-1"}
    base.update(ident)
    return SimpleNamespace(_identity_binding=lambda: base)


def test_new_revision_same_epoch_and_tiles_carries():
    old = {"epoch_id": "epoch-1", "source_revision": "zzz", "deployed_revision": "zzz",
           "tile_config_signature": "tiles-1"}
    assert carries(_store(), old) is True


def test_new_epoch_or_tile_config_resets():
    old = {"epoch_id": "epoch-0", "source_revision": "aaa", "deployed_revision": "aaa",
           "tile_config_signature": "tiles-1"}
    assert carries(_store(), old) is False
    old2 = dict(old, epoch_id="epoch-1", tile_config_signature="tiles-0")
    assert carries(_store(), old2) is False
    assert carries(_store(), None) is False
    assert carries(_store(epoch_id=""), dict(old, epoch_id="", source_revision="zzz")) is False
