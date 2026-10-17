"""Retiring the superseded segment prefix after a full reset (HM confirmation #1)."""
import json
import os
import pytest

import full_reset_segment_retire as retire

ENV = {"RESEARCH_SEGMENTS_PREFIX_PER_WIPE": "1", "RESEARCH_SEGMENTS_PREFIX": "v3",
       "RESEARCH_SEGMENTS_STATE_DIR": "{root}/segment-shipper-v3"}


def _env(root):
    return {k: v.format(root=root) for k, v in ENV.items()}


def _vol(tmp_path, stamp=True):
    root = tmp_path / "data"
    for rel in ("segment-store/v3/seg/000000000001.tar.gz", "segment-store/v3/man/000000000001.json",
                "segment-store/v3/acks/laptop/x.json", "segment-store/v1/seg/1.tar.gz", "segment-store/v2/seg/1.tar.gz",
                "segment-shipper-v3/status.json", "segment-shipper/status.json", "runtime/trades_3factor.csv"):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")
    if stamp:
        (root / "fresh_start_epoch.json").write_text(json.dumps({"epoch_ts": 1792300000.5}))
    return root


def _rotate(root):  # what the shipper does on the first start after the stamp
    (root / "segment-store/v3-w1792300000/seg").mkdir(parents=True)
    (root / "segment-store/v3-w1792300000/seg/000000000001.tar.gz").write_text("new")
    (root / "segment-shipper-v3-w1792300000").mkdir()
    (root / "segment-shipper-v3-w1792300000/status.json").write_text("{}")


def test_refuses_without_stamped_epoch(tmp_path):
    root = _vol(tmp_path, stamp=False)
    with pytest.raises(retire.SegmentRetireRefused, match="not stamped"):
        retire.plan(root, _env(root))
    assert (root / "segment-store/v3/seg/000000000001.tar.gz").exists()


def test_refuses_without_per_wipe(tmp_path):
    root = _vol(tmp_path); _rotate(root)
    env = {**_env(root), "RESEARCH_SEGMENTS_PREFIX_PER_WIPE": "0"}
    with pytest.raises(retire.SegmentRetireRefused, match="PER_WIPE"):
        retire.plan(root, env)


def test_refuses_before_shipper_rotated(tmp_path):
    root = _vol(tmp_path)
    with pytest.raises(retire.SegmentRetireRefused, match="restart the bot"):
        retire.plan(root, _env(root))
    assert (root / "segment-store/v3/seg/000000000001.tar.gz").exists()


def test_deletes_only_old_v3_prefix_and_state(tmp_path):
    root = _vol(tmp_path); _rotate(root)
    doc = retire.execute(root, _env(root), receipts_dir=root / "runtime/full-reset-receipts")
    assert doc["phase"] == "COMPLETE" and doc["deleted_files"] == 4  # 3 store files + old state status.json
    assert not (root / "segment-store/v3").exists() and not (root / "segment-shipper-v3").exists()
    # live prefix, archive prefixes, old-epoch shipper dirs, runtime data are untouched
    for keep in ("segment-store/v3-w1792300000/seg/000000000001.tar.gz", "segment-shipper-v3-w1792300000/status.json",
                 "segment-store/v1/seg/1.tar.gz", "segment-store/v2/seg/1.tar.gz", "segment-shipper/status.json",
                 "runtime/trades_3factor.csv", "fresh_start_epoch.json"):
        assert (root / keep).exists(), keep
    names = sorted(os.listdir(root / "runtime/full-reset-receipts"))
    assert [n.split("-2026")[0].split("-20")[0] for n in names] == ["segment-retire-complete", "segment-retire-intent"]


def test_symlink_refused_nothing_deleted(tmp_path):
    root = _vol(tmp_path); _rotate(root)
    os.symlink(root / "runtime", root / "segment-store/v3/link")
    with pytest.raises(retire.SegmentRetireRefused, match="symlink"):
        retire.plan(root, _env(root))
    assert (root / "segment-store/v3/seg/000000000001.tar.gz").exists()


def test_cli_requires_expected_live_prefix(tmp_path, monkeypatch, capsys):
    root = _vol(tmp_path); _rotate(root)
    for k, v in _env(root).items():
        monkeypatch.setenv(k, v)
    assert retire.main(["execute", "--data-root", str(root)]) == 8
    assert (root / "segment-store/v3").exists()
    assert retire.main(["execute", "--data-root", str(root), "--expect-live-prefix", "v3-w1792300000"]) == 0
    assert not (root / "segment-store/v3").exists()
