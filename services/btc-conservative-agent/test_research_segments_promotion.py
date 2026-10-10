"""Segment shadow -> canonical promotion inputs accepted by the real migration."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest

import research_segment_promotion as promotion
from research.canonical_data_store import current_analyzer_dataset_identity
from research.mirror_coherence import assert_mirror_coherent
from test_research_segments import Env, _rows

REPO_ROOT = Path(__file__).resolve().parents[2]
SIGNATURE = "524acf4949ae234e39a3902882a20872dadfe33d1235453696256348bf0c9335"


def _migration_module():
    spec = importlib.util.spec_from_file_location(
        "migrate_canonical_research_store", REPO_ROOT / "scripts" / "migrate_canonical_research_store.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _synced(tmp_path):
    env = Env(tmp_path)
    env.write("research_session.json", json.dumps({"collector_v22_epoch_id": "epoch-abc"}).encode())
    env.write("v3/ledgers/opportunity.jsonl", _rows(0, 4))
    env.write("v3/ledgers/decision.jsonl", _rows(0, 2))
    env.write("research.db", b"sqlite-bytes" * 50)
    env.ship_all()
    env.puller().pull_once()
    status = json.loads((env.state_dir / "status.json").read_text())
    head = {"published_seq": status["shipped_seq"], "unshipped_bytes": status["unshipped_bytes"],
            "last_manifest_sha256": status["last_manifest_sha256"], "oversized_paths": [],
            "racing_paths": [], "shipper_last_error": None, "throttled_snapshots": []}
    health = {"source_git_rev": "abc123def456", "tile_registry_signature": SIGNATURE}
    return env, head, health


def test_complete_shadow_is_promoted_by_the_existing_migration(tmp_path, monkeypatch):
    env, head, health = _synced(tmp_path)
    view = tmp_path / "view"
    receipt = promotion.stage_view(shadow_root=env.shadow, view_root=view, head=head, health=health)
    assert receipt["files"] == 4 and receipt["applied_seq"] == head["published_seq"]
    heartbeat = view / promotion.HEARTBEAT_NAME

    project = tmp_path / "project"
    migration = _migration_module()
    monkeypatch.setattr(migration, "REPO_ROOT", project)
    store = project / "services" / "btc-conservative-agent" / "canonical-research-data"
    result = migration.migrate(view, store, heartbeat)
    assert result["files_verified"] == 4 and result["source_deleted"] is False
    identity = current_analyzer_dataset_identity(store)
    assert identity["dataset_epoch"] == "epoch-abc"
    assert identity["source_revision"] == "abc123def456"
    assert identity["tile_config_signature"] == SIGNATURE
    token = assert_mirror_coherent(repo_root=project, data_root=store, expected_revision="abc123def456",
                                   max_age_seconds=10 ** 9, require_canonical_manifest=True)
    assert token.manifest_entry_hash
    assert (store / "v3" / "ledgers" / "opportunity.jsonl").read_bytes() == _rows(0, 4)
    assert not (env.shadow / "tree" / promotion.SYNC_STATE_NAME).exists()


@pytest.mark.parametrize("change, reason", [
    ({"published_seq": 999}, "SHADOW_BEHIND_PUBLISHED"),
    ({"unshipped_bytes": promotion.DEFAULT_MAX_UNSHIPPED_BYTES + 1}, "FLY_UNSHIPPED_BYTES"),
    ({"racing_paths": [{"path": "never_shipped.json"}]}, "FLY_RACING_PATH_NEVER_SHIPPED"),
    ({"oversized_paths": ["big.db"]}, "FLY_OVERSIZED_PATHS"),
    ({"shipper_last_error": "OSError: disk"}, "FLY_SHIPPER_ERROR"),
    ({"last_manifest_sha256": "0" * 64}, "HEAD_MANIFEST_MISMATCH"),
])
def test_incomplete_or_unhealthy_shadow_is_refused(tmp_path, change, reason):
    env, head, health = _synced(tmp_path)
    view = tmp_path / "view"
    with pytest.raises(promotion.PromotionRefused) as refused:
        promotion.stage_view(shadow_root=env.shadow, view_root=view, head={**head, **change}, health=health)
    assert any(item.startswith(reason) for item in refused.value.reasons)
    assert not view.exists() or not any(view.iterdir())


def test_live_tail_and_racing_shipped_snapshot_are_promoted_and_recorded(tmp_path):
    env, head, health = _synced(tmp_path)
    live = {**head, "unshipped_bytes": 4096,
            "racing_paths": [{"path": "research.db", "races": 3},
                             {"path": "never.jsonl.validation.json", "races": 9}],
            "shipper_last_error": "PLAN_RACE: research.db changed identity"}
    view = tmp_path / "view"
    promotion.stage_view(shadow_root=env.shadow, view_root=view, head=live, health=health)
    heartbeat = json.loads((view / promotion.HEARTBEAT_NAME).read_text())
    assert heartbeat["unshippedBytesAtPromotion"] == 4096
    assert heartbeat["racingPaths"] == ["research.db", "never.jsonl.validation.json"]


def _oversized_research_db(head, now, **change):
    return {**head, "oversized_paths": ["research.db"], "shipper_last_segment_at": now - 120,
            "unshipped_bytes": 560 * 1024 * 1024, **change}


def test_oversized_research_db_alone_promotes_as_disclosed_amber(tmp_path, monkeypatch):
    env, head, health = _synced(tmp_path)
    now = 1_790_990_000.0
    view = tmp_path / "view"
    receipt = promotion.stage_view(shadow_root=env.shadow, view_root=view, head=_oversized_research_db(head, now),
                                   health=health, now=now)
    expected = ["FLY_OVERSIZED_SQLITE_SNAPSHOT:research.db",
                f"FLY_UNSHIPPED_BYTES_INCLUDE_OVERSIZED_SNAPSHOT:{560 * 1024 * 1024}"]
    assert receipt["promotion_level"] == "AMBER" and receipt["promotion_warnings"] == expected
    heartbeat = json.loads((view / promotion.HEARTBEAT_NAME).read_text())
    assert heartbeat["promotionLevel"] == "AMBER" and heartbeat["promotionWarnings"] == expected
    assert heartbeat["oversizedPaths"] == ["research.db"]
    assert (view / "research.db").read_bytes() == (env.shadow / "tree" / "research.db").read_bytes()

    project = tmp_path / "project"
    migration = _migration_module()
    monkeypatch.setattr(migration, "REPO_ROOT", project)
    store = project / "services" / "btc-conservative-agent" / "canonical-research-data"
    migrated = migration.migrate(view, store, view / promotion.HEARTBEAT_NAME)
    assert migrated["promotion_level"] == "AMBER" and migrated["promotion_warnings"] == expected

    from research.input_blockers import segment_promotion_item
    item = segment_promotion_item(store)
    assert item["status"] == "DEGRADED" and item["reason_code"] == "FLY_OVERSIZED_SQLITE_SNAPSHOT"
    assert "research.db" in item["reason"] and item["side"] == "COLLECTION"


def test_clean_promotion_is_green_without_warnings(tmp_path):
    env, head, health = _synced(tmp_path)
    receipt = promotion.stage_view(shadow_root=env.shadow, view_root=tmp_path / "view", head=head, health=health)
    assert receipt["promotion_level"] == "GREEN" and receipt["promotion_warnings"] == []
    from research.input_blockers import segment_promotion_item
    assert segment_promotion_item(tmp_path / "no-store") is None


@pytest.mark.parametrize("change, reason", [
    ({"oversized_paths": ["research.db", "v3/other.sqlite3"]}, "FLY_OVERSIZED_PATHS:2"),
    ({"shipper_last_segment_at": None}, "FLY_OVERSIZED_PATHS:1:no segment shipped for unknown"),
    ({"shipper_last_segment_at": 1_790_990_000.0 - promotion.OVERSIZED_SNAPSHOT_MAX_SEGMENT_AGE_SEC - 1},
     "FLY_OVERSIZED_PATHS:1:no segment shipped"),
    ({"unshipped_bytes": promotion.DEFAULT_MAX_UNSHIPPED_BYTES + promotion.OVERSIZED_SNAPSHOT_ALLOWANCE_BYTES + 1},
     "FLY_UNSHIPPED_BYTES"),
])
def test_oversized_research_db_tolerance_is_bounded(tmp_path, change, reason):
    env, head, health = _synced(tmp_path)
    now = 1_790_990_000.0
    with pytest.raises(promotion.PromotionRefused) as refused:
        promotion.stage_view(shadow_root=env.shadow, view_root=tmp_path / "view",
                             head=_oversized_research_db(head, now, **change), health=health, now=now)
    assert any(item.startswith(reason) for item in refused.value.reasons), refused.value.reasons


def test_oversized_research_db_never_shipped_is_refused(tmp_path):
    env, head, health = _synced(tmp_path)
    (env.shadow / "tree" / "research.db").unlink()
    now = 1_790_990_000.0
    with pytest.raises(promotion.PromotionRefused) as refused:
        promotion.stage_view(shadow_root=env.shadow, view_root=tmp_path / "view",
                             head=_oversized_research_db(head, now), health=health, now=now)
    assert "FLY_OVERSIZED_PATH_NEVER_SHIPPED:research.db" in refused.value.reasons


def test_revision_drift_and_missing_session_are_refused(tmp_path):
    env, head, health = _synced(tmp_path)
    (env.shadow / "tree" / "research_session.json").unlink()
    with pytest.raises(promotion.PromotionRefused) as refused:
        promotion.stage_view(shadow_root=env.shadow, view_root=tmp_path / "view", head=head,
                             health={**health, "source_git_rev": "fff000"})
    reasons = refused.value.reasons
    assert any(item.startswith("REVISION_MISMATCH") for item in reasons)
    assert "RESEARCH_SESSION_MISSING" in reasons


def test_view_must_be_empty_and_outside_the_shadow_tree(tmp_path):
    env, head, health = _synced(tmp_path)
    with pytest.raises(promotion.PromotionRefused, match="VIEW_INSIDE_SHADOW_TREE"):
        promotion.stage_view(shadow_root=env.shadow, view_root=env.shadow / "tree" / "x",
                             head=head, health=health)
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "keep.txt").write_text("evidence")
    with pytest.raises(promotion.PromotionRefused, match="VIEW_NOT_EMPTY"):
        promotion.stage_view(shadow_root=env.shadow, view_root=occupied, head=head, health=health)
    assert (occupied / "keep.txt").read_text() == "evidence"

def test_synced_at_is_the_head_verification_not_the_last_applied_segment(tmp_path, monkeypatch):
    # 10-02 14:10Z: no segment applied for 31 min (parity held the lock, then the
    # head was idle), promotion verified shadow == published head, yet the
    # analyzer refused MIRROR_SYNC_RECEIPT_STALE off last_applied_at.
    env, head, health = _synced(tmp_path)
    state_path = env.shadow / ".puller" / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["last_applied_at"] = "2026-10-02T13:39:06Z"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(promotion, "_utc_now", lambda: "2026-10-02T13:55:21Z")
    view = tmp_path / "view"
    promotion.stage_view(shadow_root=env.shadow, view_root=view, head=head, health=health)
    heartbeat = json.loads((view / promotion.HEARTBEAT_NAME).read_text(encoding="utf-8"))
    assert heartbeat["syncedAt"] == "2026-10-02T13:55:21Z"
    assert heartbeat["segmentLastAppliedAt"] == "2026-10-02T13:39:06Z"


def test_promotion_heartbeat_carries_v2_genesis(tmp_path):
    env, head, health = _synced(tmp_path)
    view = tmp_path / "view"
    promotion.stage_view(shadow_root=env.shadow, view_root=view, head=head, health=health, genesis_at=1790735978.5)
    heartbeat = json.loads((view / promotion.HEARTBEAT_NAME).read_text(encoding="utf-8"))
    assert heartbeat["segmentGenesisAt"] == 1790735978.5


def _head(env):
    status = json.loads((env.state_dir / "status.json").read_text())
    return {"published_seq": status["shipped_seq"], "unshipped_bytes": status["unshipped_bytes"],
            "last_manifest_sha256": status["last_manifest_sha256"], "oversized_paths": [],
            "racing_paths": [], "shipper_last_error": None, "throttled_snapshots": []}


def _resync(env):
    env.ship_all()
    env.puller().pull_once()
    return _head(env)


def _tree_bytes(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*")
            if p.is_file() and p.name not in promotion.VIEW_CONTROL_NAMES}


def test_incremental_view_reuses_appends_recopies_and_removes(tmp_path):
    env, head, health = _synced(tmp_path)
    view = tmp_path / "view"
    first = promotion.stage_view(shadow_root=env.shadow, view_root=view, head=head, health=health)
    assert first["mode"] == "FULL_REBUILD" and first["files_copied"] == 4
    env.write("v3/ledgers/opportunity.jsonl", _rows(4, 3), append=True)
    env.write("research.db", b"rewritten-db" * 50)
    env.write("v3/ledgers/new.jsonl", _rows(0, 1))
    second = promotion.stage_view(shadow_root=env.shadow, view_root=view, head=_resync(env), health=health)
    assert second["mode"] == "INCREMENTAL"
    assert second["files_appended"] == 1 and second["files_copied"] == 2 and second["files_reused"] >= 2
    tree = env.shadow / "tree"
    assert _tree_bytes(view) == _tree_bytes(tree)
    state = json.loads((view / promotion.SYNC_STATE_NAME).read_text())
    assert all(state[rel]["sha256"] == promotion._sha256_file(tree / rel) for rel in state)
    assert (view / promotion.HEARTBEAT_NAME).is_file()


def test_incremental_view_recopies_a_tampered_file_and_verify_catches_same_stat_tamper(tmp_path):
    env, head, health = _synced(tmp_path)
    view = tmp_path / "view"
    promotion.stage_view(shadow_root=env.shadow, view_root=view, head=head, health=health, now=1000.0)
    target = view / "v3" / "ledgers" / "decision.jsonl"
    original = target.read_bytes()
    target.write_bytes(b"X" * len(original))
    receipt = promotion.stage_view(shadow_root=env.shadow, view_root=view, head=head, health=health, now=1001.0)
    assert receipt["files_copied"] == 1 and target.read_bytes() == original
    stat = target.stat()
    target.write_bytes(b"Y" * len(original))
    os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    receipt = promotion.stage_view(shadow_root=env.shadow, view_root=view, head=head, health=health,
                                   now=1002.0, verify_interval_sec=0)
    assert receipt["mode"] == "INCREMENTAL_VERIFIED" and receipt["files_copied"] == 1
    assert target.read_bytes() == original


def test_view_without_index_is_refused_and_full_flag_rebuilds(tmp_path):
    env, head, health = _synced(tmp_path)
    view = tmp_path / "view"
    promotion.stage_view(shadow_root=env.shadow, view_root=view, head=head, health=health)
    (view / promotion.INDEX_NAME).unlink()
    with pytest.raises(promotion.PromotionRefused, match="VIEW_NOT_EMPTY"):
        promotion.stage_view(shadow_root=env.shadow, view_root=view, head=head, health=health)
    view.joinpath(promotion.INDEX_NAME).write_text(json.dumps({"schema": promotion.INDEX_SCHEMA, "files": {}}))
    (view / "orphan.txt").write_text("stale")
    receipt = promotion.stage_view(shadow_root=env.shadow, view_root=view, head=head, health=health, full=True)
    assert receipt["mode"] == "FULL_REBUILD" and not (view / "orphan.txt").exists()
    assert _tree_bytes(view) == _tree_bytes(env.shadow / "tree")


def test_refused_promotion_leaves_the_previous_view_unconsumable_only_after_staging_starts(tmp_path):
    env, head, health = _synced(tmp_path)
    view = tmp_path / "view"
    promotion.stage_view(shadow_root=env.shadow, view_root=view, head=head, health=health)
    with pytest.raises(promotion.PromotionRefused):
        promotion.stage_view(shadow_root=env.shadow, view_root=view, head={**head, "published_seq": 999}, health=health)
    assert (view / promotion.HEARTBEAT_NAME).is_file() and (view / promotion.INDEX_NAME).is_file()


def test_incremental_migration_reuses_appends_and_recopies(tmp_path, monkeypatch):
    env, head, health = _synced(tmp_path)
    view = tmp_path / "view"
    project = tmp_path / "project"
    migration = _migration_module()
    monkeypatch.setattr(migration, "REPO_ROOT", project)
    store = project / "services" / "btc-conservative-agent" / "canonical-research-data"
    promotion.stage_view(shadow_root=env.shadow, view_root=view, head=head, health=health)
    first = migration.migrate(view, store, view / promotion.HEARTBEAT_NAME, now=1000.0)
    assert first["mode"] == "FULL" and first["files_copied"] == 4
    env.write("v3/ledgers/opportunity.jsonl", _rows(4, 5), append=True)
    promotion.stage_view(shadow_root=env.shadow, view_root=view, head=_resync(env), health=health)
    second = migration.migrate(view, store, view / promotion.HEARTBEAT_NAME, now=1001.0)
    assert second["mode"] == "INCREMENTAL" and second["files_appended"] == 1
    assert second["files_reused"] == 3 and second["files_copied"] == 0 and second["files_verified"] == 4
    assert (store / "v3" / "ledgers" / "opportunity.jsonl").read_bytes() == _rows(0, 4) + _rows(4, 5)
    decision = store / "v3" / "ledgers" / "decision.jsonl"
    stat = decision.stat()
    decision.write_bytes(b"Z" * stat.st_size)
    os.utime(decision, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    third = migration.migrate(view, store, view / promotion.HEARTBEAT_NAME, now=1002.0, verify_interval_sec=0)
    assert third["mode"] == "INCREMENTAL_VERIFIED" and third["files_copied"] == 1
    assert decision.read_bytes() == _rows(0, 2)
    identity = current_analyzer_dataset_identity(store)
    assert identity["source_revision"] == "abc123def456"


def test_incremental_migration_refuses_a_source_changed_after_staging(tmp_path, monkeypatch):
    env, head, health = _synced(tmp_path)
    view = tmp_path / "view"
    project = tmp_path / "project"
    migration = _migration_module()
    monkeypatch.setattr(migration, "REPO_ROOT", project)
    store = project / "services" / "btc-conservative-agent" / "canonical-research-data"
    promotion.stage_view(shadow_root=env.shadow, view_root=view, head=head, health=health)
    migration.migrate(view, store, view / promotion.HEARTBEAT_NAME)
    staged = view / "v3" / "ledgers" / "decision.jsonl"
    staged.write_bytes(b"Q" * staged.stat().st_size)
    with pytest.raises(RuntimeError, match="Source checksum drift"):
        migration.migrate(view, store, view / promotion.HEARTBEAT_NAME)


def _receipt(i: int, revision: str) -> bytes:
    return json.dumps({"schema": "emergency_record_idempotency_v1", "state": "COMMITTED", "record_id": f"r{i:04d}",
                       "identity": {"deployed_revision": revision}}, sort_keys=True).encode() + b"\n"


def _receipt_env(tmp_path, count: int):
    env, head, health = _synced(tmp_path)
    for i in range(count):
        env.write(f"v3/receipts/idem/d/{i:04x}.json", _receipt(i, "aaaaaaaaaaaa"))
    return env, _resync(env), health


def test_mtime_only_source_rewrite_is_neither_recopied_nor_remigrated(tmp_path, monkeypatch):
    env, head, health = _receipt_env(tmp_path, 6)
    view = tmp_path / "view"
    project = tmp_path / "project"
    migration = _migration_module()
    monkeypatch.setattr(migration, "REPO_ROOT", project)
    store = project / "services" / "btc-conservative-agent" / "canonical-research-data"
    promotion.stage_view(shadow_root=env.shadow, view_root=view, head=head, health=health, now=1000.0)
    migration.migrate(view, store, view / promotion.HEARTBEAT_NAME, now=1000.0)
    tree = env.shadow / "tree"
    touched = sorted((tree / "v3" / "receipts").rglob("*.json"))
    view_mtimes = {p.name: (view / p.relative_to(tree)).stat().st_mtime_ns for p in touched}
    for path in touched:
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
    receipt = promotion.stage_view(shadow_root=env.shadow, view_root=view, head=head, health=health, now=1001.0)
    assert receipt["files_copied"] == 0 and receipt["files_rehashed_unchanged"] == len(touched)
    assert {p.name: (view / p.relative_to(tree)).stat().st_mtime_ns for p in touched} == view_mtimes
    migrated = migration.migrate(view, store, view / promotion.HEARTBEAT_NAME, now=1001.0)
    assert migrated["files_copied"] == 0 and migrated["files_reused"] == migrated["files_verified"]
    # The index now carries the new source mtime, so the next pass is a plain stat reuse.
    again = promotion.stage_view(shadow_root=env.shadow, view_root=view, head=head, health=health, now=1002.0)
    assert again["files_rehashed_unchanged"] == 0 and again["files_copied"] == 0


def test_post_deploy_receipt_rebinding_is_copied_in_parallel_and_byte_exact(tmp_path, monkeypatch):
    count = 40
    env, head, health = _receipt_env(tmp_path, count)
    view = tmp_path / "view"
    project = tmp_path / "project"
    migration = _migration_module()
    monkeypatch.setattr(migration, "REPO_ROOT", project)
    store = project / "services" / "btc-conservative-agent" / "canonical-research-data"
    promotion.stage_view(shadow_root=env.shadow, view_root=view, head=head, health=health, copy_workers=4)
    migration.migrate(view, store, view / promotion.HEARTBEAT_NAME, copy_workers=4)
    # A Fly boot re-binds every receipt to the new revision: same size, new bytes.
    for i in range(count):
        env.write(f"v3/receipts/idem/d/{i:04x}.json", _receipt(i, "bbbbbbbbbbbb"))
    head = _resync(env)
    staged = promotion.stage_view(shadow_root=env.shadow, view_root=view, head=head, health=health, copy_workers=4)
    assert staged["files_copied"] == count and staged["files_rehashed_unchanged"] == 0
    migrated = migration.migrate(view, store, view / promotion.HEARTBEAT_NAME, copy_workers=4)
    assert migrated["files_copied"] == count
    tree = env.shadow / "tree"
    assert _tree_bytes(view) == _tree_bytes(tree)
    receipts = {rel: data for rel, data in _tree_bytes(store).items() if rel.startswith("v3/receipts/")}
    assert receipts == {rel: data for rel, data in _tree_bytes(tree).items() if rel.startswith("v3/receipts/")}
    assert all(b"bbbbbbbbbbbb" in data for data in receipts.values()) and len(receipts) == count
    index = json.loads((store / migration.INCREMENTAL_INDEX).read_text(encoding="utf-8"))["files"]
    state = json.loads((view / promotion.SYNC_STATE_NAME).read_text(encoding="utf-8"))
    assert {k: v["sha256"] for k, v in index.items()} == {k: v["sha256"] for k, v in state.items()}
    leftovers = [p for p in store.rglob("*.migration")]
    assert leftovers == []

def test_settled_files_are_hardlinked_across_layers_and_writers_copy_on_write(tmp_path, monkeypatch):
    import time as _time

    import storage_links

    env, head, health = _synced(tmp_path)
    tree = env.shadow / "tree"
    old = _time.time() - 8 * 3600
    for rel in ("v3/ledgers/decision.jsonl", "v3/ledgers/opportunity.jsonl", "research.db"):
        os.utime(tree / rel, (old, old))
    view = tmp_path / "view"
    receipt = promotion.stage_view(shadow_root=env.shadow, view_root=view, head=head, health=health,
                                   link_settle_sec=3600)
    assert receipt["files_linked"] == 2 and receipt["files_link_fallback_copied"] == 0
    linked = tree / "v3/ledgers/opportunity.jsonl"
    assert storage_links.same_file(linked, view / "v3/ledgers/opportunity.jsonl")
    assert not storage_links.same_file(tree / "research.db", view / "research.db")
    assert not storage_links.same_file(tree / "research_session.json", view / "research_session.json")

    project = tmp_path / "project"
    migration = _migration_module()
    monkeypatch.setattr(migration, "REPO_ROOT", project)
    store = project / "services" / "btc-conservative-agent" / "canonical-research-data"
    result = migration.migrate(view, store, view / promotion.HEARTBEAT_NAME, link_settle_sec=3600)
    assert result["files_verified"] == 4 and result["files_linked"] == 2
    assert storage_links.same_file(linked, store / "v3/ledgers/opportunity.jsonl")

    before = (view / "v3/ledgers/opportunity.jsonl").read_bytes()
    env.write("v3/ledgers/opportunity.jsonl", _rows(4, 3), append=True)
    _resync(env)
    assert linked.read_bytes().startswith(before) and len(linked.read_bytes()) > len(before)
    assert (view / "v3/ledgers/opportunity.jsonl").read_bytes() == before
    assert (store / "v3/ledgers/opportunity.jsonl").read_bytes() == before
    assert not storage_links.same_file(linked, view / "v3/ledgers/opportunity.jsonl")


def test_reported_oversized_bytes_replace_the_fixed_allowance(tmp_path):
    """HM 10 Oct: research.db grew past the 1 GiB allowance and blocked promotion
    forever (MIRROR_SYNC_RECEIPT_STALE). Fly now reports its bytes separately."""
    env, head, health = _synced(tmp_path)
    now = 1_790_990_000.0
    db = 1_600 * 1024 * 1024
    receipt = promotion.stage_view(
        shadow_root=env.shadow, view_root=tmp_path / "view", health=health, now=now,
        head=_oversized_research_db(head, now, unshipped_bytes=db + 4096, oversized_bytes=db))
    assert receipt["promotion_level"] == "AMBER"
    assert f"FLY_OVERSIZED_SNAPSHOT_BYTES_EXCLUDED:{db}" in receipt["promotion_warnings"]


def test_reported_oversized_bytes_still_refuse_a_real_backlog(tmp_path):
    env, head, health = _synced(tmp_path)
    now = 1_790_990_000.0
    db = 1_600 * 1024 * 1024
    backlog = promotion.DEFAULT_MAX_UNSHIPPED_BYTES + 1
    with pytest.raises(promotion.PromotionRefused) as refused:
        promotion.stage_view(
            shadow_root=env.shadow, view_root=tmp_path / "view", health=health, now=now,
            head=_oversized_research_db(head, now, unshipped_bytes=db + backlog, oversized_bytes=db))
    assert any(item.startswith("FLY_UNSHIPPED_BYTES:") for item in refused.value.reasons)
