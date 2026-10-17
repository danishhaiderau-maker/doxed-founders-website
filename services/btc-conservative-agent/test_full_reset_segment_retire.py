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
    (root / "segment-shipper-v3-w1792300000/status.json").write_text(json.dumps({"prefix": "v3-w1792300000"}))


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
    # the live prefix + its state, archive prefixes v1/v2, the base shipper dir and runtime data are untouched
    for keep in ("segment-store/v3-w1792300000/seg/000000000001.tar.gz", "segment-shipper-v3-w1792300000/status.json",
                 "segment-store/v1/seg/1.tar.gz", "segment-store/v2/seg/1.tar.gz", "segment-shipper/status.json",
                 "runtime/trades_3factor.csv", "fresh_start_epoch.json"):
        assert (root / keep).exists(), keep
    names = sorted(os.listdir(root / "runtime/full-reset-receipts"))
    assert [n.startswith("segment-retire-complete-") for n in names] == [True, False] and names[1].startswith("segment-retire-intent-")


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


def _mk(root, rel):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True) if not rel.endswith("/") else None
    p.mkdir(parents=True, exist_ok=True) if rel.endswith("/") else p.write_text("x")


@pytest.mark.parametrize("name", ["v3-wabc", "v30", "v3-w", "v3-w12x", "xv3", "v3-w1-extra", "v3\n"])
def test_lookalike_names_are_never_candidates(tmp_path, name):
    root = _vol(tmp_path); _rotate(root)
    _mk(root, f"segment-store/{name}/seg/1.tar.gz")
    _mk(root, f"segment-shipper-{name}/status.json")
    doc = retire.plan(root, _env(root))
    assert all(name not in t["path"].split("/")[-1] for t in doc["targets"]), doc["targets"]
    retire.execute(root, _env(root), receipts_dir=root / "runtime/full-reset-receipts")
    assert (root / f"segment-store/{name}/seg/1.tar.gz").exists()
    assert (root / f"segment-shipper-{name}/status.json").exists()


def test_an_older_per_wipe_prefix_and_its_state_are_deleted(tmp_path):
    root = _vol(tmp_path); _rotate(root)
    _mk(root, "segment-store/v3-w1792200000/seg/000000000001.tar.gz")
    _mk(root, "segment-shipper-v3-w1792200000/status.json")
    doc = retire.execute(root, _env(root), receipts_dir=root / "runtime/full-reset-receipts")
    assert not (root / "segment-store/v3-w1792200000").exists()
    assert not (root / "segment-shipper-v3-w1792200000").exists()
    assert (root / "segment-store/v3-w1792300000/seg/000000000001.tar.gz").exists()  # live
    assert doc["deleted_files"] == 6


def test_symlinked_top_level_target_is_refused_and_nothing_is_deleted(tmp_path):
    root = _vol(tmp_path); _rotate(root)
    outside = tmp_path / "outside"; outside.mkdir(); (outside / "keep.txt").write_text("k")
    os.symlink(outside, root / "segment-store/v3-w1792200000")
    with pytest.raises(retire.SegmentRetireRefused, match="symlink"):
        retire.plan(root, _env(root))
    assert (root / "segment-store/v3/seg/000000000001.tar.gz").exists() and (outside / "keep.txt").exists()
    os.unlink(root / "segment-store/v3-w1792200000")
    os.symlink(outside, root / "segment-shipper-v3-w1792200000")
    with pytest.raises(retire.SegmentRetireRefused, match="symlink"):
        retire.plan(root, _env(root))
    assert (outside / "keep.txt").exists()


def test_live_status_must_exist_be_fresh_and_name_the_live_prefix(tmp_path):
    root = _vol(tmp_path); _rotate(root)
    status = root / "segment-shipper-v3-w1792300000/status.json"
    st_mtime = status.stat().st_mtime
    with pytest.raises(retire.SegmentRetireRefused, match="stale"):
        retire.plan(root, _env(root), now=st_mtime + retire.STATUS_MAX_AGE_SEC + 5)
    status.write_text(json.dumps({"prefix": "v3"}))  # the OLD prefix: not the live one
    with pytest.raises(retire.SegmentRetireRefused, match="names prefix"):
        retire.plan(root, _env(root))
    status.unlink()  # a directory alone is not proof the shipper rotated
    with pytest.raises(retire.SegmentRetireRefused, match="missing"):
        retire.plan(root, _env(root))
    assert (root / "segment-store/v3/seg/000000000001.tar.gz").exists()


def test_plan_over_the_size_cap_is_refused(tmp_path, monkeypatch):
    root = _vol(tmp_path); _rotate(root)
    monkeypatch.setattr(retire, "MAX_PLAN_BYTES", 2)  # the fixture's three 1-byte store files + state exceed 2
    with pytest.raises(retire.SegmentRetireRefused, match="cap"):
        retire.execute(root, _env(root), receipts_dir=root / "runtime/full-reset-receipts")
    assert (root / "segment-store/v3/seg/000000000001.tar.gz").exists()
    assert not (root / "runtime/full-reset-receipts").exists()
