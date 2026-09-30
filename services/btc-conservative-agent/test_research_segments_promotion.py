"""Segment shadow -> canonical promotion inputs accepted by the real migration."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

import research_segment_promotion as promotion
from research.canonical_data_store import current_analyzer_dataset_identity
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
