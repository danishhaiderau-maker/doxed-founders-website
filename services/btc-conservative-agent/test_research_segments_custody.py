"""Fly-retired custody copies stay on the laptop but leave the analyzer view (#420)."""

from __future__ import annotations

import json

import research_segment_format as fmt
import research_segment_promotion as promotion
import segment_custody
from test_research_segments import Env, _rows
from test_research_segments_promotion import _head, _migration_module, _resync, _synced


def _state(env) -> dict:
    return json.loads((env.shadow / ".puller" / "state.json").read_text())


def test_update_tombstoned_tracks_retire_and_recreate():
    tomb: dict = {}
    segment_custody.update_tombstoned(tomb, 3, {"kind": "TOMBSTONE", "path": "a.jsonl"})
    segment_custody.update_tombstoned(tomb, 3, {"kind": "TOMBSTONE", "path": "b.jsonl.4"})
    assert tomb == {"a.jsonl": 3, "b.jsonl.4": 3}
    segment_custody.update_tombstoned(tomb, 4, {"kind": "REWRITE", "path": "a.jsonl"})
    segment_custody.update_tombstoned(tomb, 5, {"kind": "SEAL", "path": "b.jsonl.4", "source_path": "b.jsonl"})
    assert tomb == {}
    rebuilt = segment_custody.rebuild_tombstoned([(2, {"members": [{"kind": "APPEND", "path": "c"}]}),
                                                  (1, {"members": [{"kind": "TOMBSTONE", "path": "c"}]})])
    assert rebuilt == {}


def test_tombstoned_file_keeps_its_bytes_but_leaves_the_view(tmp_path):
    env, head, health = _synced(tmp_path)
    env.write("post_exit_replay.jsonl", _rows(0, 5, "pre-epoch"))
    head = _resync(env)
    (env.runtime / "post_exit_replay.jsonl").unlink()  # the clean-epoch boundary reset deletes it on Fly
    head = _resync(env)
    tree = env.shadow / "tree"
    assert (tree / "post_exit_replay.jsonl").read_bytes() == _rows(0, 5, "pre-epoch")  # custody copy kept
    assert "post_exit_replay.jsonl" in _state(env)["tombstoned"]
    view = tmp_path / "view"
    receipt = promotion.stage_view(shadow_root=env.shadow, view_root=view, head=head, health=health)
    assert receipt["files_retired_custody"] == 1
    assert receipt["retired_custody_sample"] == ["post_exit_replay.jsonl"]
    assert not (view / "post_exit_replay.jsonl").exists()
    heartbeat = json.loads((view / promotion.HEARTBEAT_NAME).read_text())
    assert heartbeat["retiredCustodyPaths"] == 1
    # Fly writes the stream again in the new epoch: it is current data and flows normally.
    env.write("post_exit_replay.jsonl", _rows(100, 2, "current"))
    head = _resync(env)
    assert "post_exit_replay.jsonl" not in _state(env)["tombstoned"]
    receipt = promotion.stage_view(shadow_root=env.shadow, view_root=view, head=head, health=health)
    assert receipt["files_retired_custody"] == 0
    assert (view / "post_exit_replay.jsonl").read_bytes() == _rows(100, 2, "current")


def test_custody_pruned_rotation_stays_evidence(tmp_path):
    env, head, health = _synced(tmp_path)
    env.write("signal_replay.jsonl.7", _rows(0, 3))
    _resync(env)
    (env.runtime / "signal_replay.jsonl.7").unlink()
    env.write("retention/prune_ledger.jsonl", json.dumps(
        {"kind": "runtime", "relpath": "signal_replay.jsonl.7", "deleted": True}).encode() + b"\n")
    head = _resync(env)
    assert "signal_replay.jsonl.7" in _state(env)["tombstoned"]
    assert segment_custody.retired_custody_paths(_state(env), env.shadow / "tree") == {}
    view = tmp_path / "view"
    receipt = promotion.stage_view(shadow_root=env.shadow, view_root=view, head=head, health=health)
    assert receipt["files_retired_custody"] == 0
    assert (view / "signal_replay.jsonl.7").read_bytes() == _rows(0, 3)


def _tree_applied_by_old_puller(env):
    """Simulate a shadow tree applied before the ``tombstoned`` map existed."""
    state_path = env.shadow / ".puller" / "state.json"
    state = json.loads(state_path.read_text())
    state.pop("tombstoned", None)
    state.pop("tombstoned_backfill", None)
    state_path.write_text(json.dumps(state))


def test_backfill_replays_archived_manifests(tmp_path):
    env = Env(tmp_path)
    env.write("keep.jsonl", _rows(0, 2))
    env.write("gone.jsonl", _rows(0, 2))
    env.write("back.jsonl", _rows(0, 2))
    env.ship_all()
    env.puller().pull_once()
    (env.runtime / "gone.jsonl").unlink()
    (env.runtime / "back.jsonl").unlink()
    env.ship_all()
    env.write("back.jsonl", _rows(5, 1))
    env.ship_all()
    env.puller().pull_once()
    assert set(_state(env)["tombstoned"]) == {"gone.jsonl"}
    _tree_applied_by_old_puller(env)
    env.puller().pull_once()
    state = _state(env)
    assert state["tombstoned"] == {"gone.jsonl": 2} and state["tombstoned_backfill"] == "ARCHIVED_MANIFESTS"


def test_backfill_falls_back_to_markers_and_tree_mtime(tmp_path):
    env = Env(tmp_path)
    env.write("gone.jsonl", _rows(0, 2))
    env.write("back.jsonl", _rows(0, 2))
    env.ship_all()
    env.puller().pull_once()
    (env.runtime / "gone.jsonl").unlink()
    (env.runtime / "back.jsonl").unlink()
    env.ship_all()
    env.puller().pull_once()
    _tree_applied_by_old_puller(env)
    archived = env.archive / "v1" / "man" / fmt.manifest_key("v1", 1).rsplit("/", 1)[-1]
    archived.unlink()  # one archived manifest missing -> replay impossible
    back = env.shadow / "tree" / "back.jsonl"
    back.write_bytes(_rows(7, 1))  # re-created after its tombstone marker
    marker_ns = max(p.stat().st_mtime_ns for p in (env.shadow / "tombstones").glob("*.json"))
    import os
    os.utime(back, ns=(marker_ns + 10**9, marker_ns + 10**9))
    puller = env.puller()
    state = puller.load_state()
    assert puller.backfill_tombstoned(state) == {"gone.jsonl": 2}
    assert state["tombstoned_backfill"] == "MARKERS_MTIME"


def test_migration_moves_files_that_left_the_view_and_never_deletes(tmp_path, monkeypatch):
    env, head, health = _synced(tmp_path)
    env.write("xvp_shadow_signals.jsonl", _rows(0, 4, "retired-stream"))
    head = _resync(env)
    view = tmp_path / "view"
    project = tmp_path / "project"
    migration = _migration_module()
    monkeypatch.setattr(migration, "REPO_ROOT", project)
    store = project / "services" / "btc-conservative-agent" / "canonical-research-data"
    promotion.stage_view(shadow_root=env.shadow, view_root=view, head=head, health=health)
    migration.migrate(view, store, view / promotion.HEARTBEAT_NAME, now=1000.0)
    assert (store / "xvp_shadow_signals.jsonl").is_file()
    (env.runtime / "xvp_shadow_signals.jsonl").unlink()
    head = _resync(env)
    promotion.stage_view(shadow_root=env.shadow, view_root=view, head=head, health=health)
    result = migration.migrate(view, store, view / promotion.HEARTBEAT_NAME, now=2000.0)
    assert result["files_retired"] == 1 and result["retired_sample"] == ["xvp_shadow_signals.jsonl"]
    assert not (store / "xvp_shadow_signals.jsonl").exists()
    moved = list((store / "migration" / "retired").rglob("xvp_shadow_signals.jsonl"))
    assert len(moved) == 1 and moved[0].read_bytes() == _rows(0, 4, "retired-stream")
    ledger = [json.loads(line) for line in (store / migration.RETIRED_LEDGER).read_text().splitlines()]
    assert ledger[0]["schema"] == migration.RETIRED_SCHEMA and ledger[0]["relpath"] == "xvp_shadow_signals.jsonl"
    assert (env.shadow / "tree" / "xvp_shadow_signals.jsonl").is_file()  # laptop custody copy untouched
    again = migration.migrate(view, store, view / promotion.HEARTBEAT_NAME, now=3000.0)
    assert again["files_retired"] == 0  # idempotent
    assert len((store / migration.RETIRED_LEDGER).read_text().splitlines()) == 1
