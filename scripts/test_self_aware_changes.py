"""/api/selfaware/changes: receipts, Fly runtime transitions, AI model/prompt sightings, epochs, filters."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("duckdb")
pd = pytest.importorskip("pandas")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from self_aware import changes  # noqa: E402
from self_aware.config import Paths  # noqa: E402
from self_aware.store import Store  # noqa: E402

TILES = ["FAMILY_XVENUE_LEAD_60S"]
RECEIPTS = {
    "deploys": [{"databaseId": 1, "status": "completed", "conclusion": "success", "headSha": "29742de53a5cacc9",
                 "createdAt": "2026-10-02T09:47:03Z", "updatedAt": "2026-10-02T10:35:08Z", "displayTitle": "feat"}],
    "auto_ff": [{"at": "2026-10-02T16:15:40Z", "outcome": "FAST_FORWARDED_LAPTOP_ONLY", "repoRoot": "v2c",
                 "from": "03ba129acf", "to": "253f27b3f8", "commits": 32}],
    "manual_interventions": [{"at": "2026-10-02T05:46:30Z", "action": "merged PR #283"}, {"at": None, "action": "x"}],
}


@pytest.fixture()
def store(tmp_path: Path):
    paths = Paths(home=tmp_path / "home", chain=tmp_path / "chain", mirror=tmp_path / "mirror",
                  mirror_archive=tmp_path / "am", puller=tmp_path / "p", exports=tmp_path / "e",
                  archive=tmp_path / "a", diagnostics=tmp_path / "d", analyzer_repo=tmp_path / "v2c",
                  retention=tmp_path / "r", segment_manifests=tmp_path / "sm", laptop_root=tmp_path)
    s = Store(paths, path=paths.home / "t.duckdb", threads=1, memory_limit="256MB")
    yield s
    s.close()


def _health(at, rev="29742de53a5c", paused=False, owner=None, tiles=TILES, armed=False):
    fly = {"git_rev": rev, "paused": paused, "pause_owner": owner, "active_tile_lanes": tiles, "live_armed": armed}
    return {"at": at, "kind": "HEALTH", "id": at, "fly": fly}


def test_runtime_transitions_report_pause_resume_revision_and_tiles_only_on_change(store):
    store.append("runtime_history", [
        _health("2026-10-02T10:00:00Z"),
        _health("2026-10-02T10:02:00Z"),
        {"at": "2026-10-02T10:03:00Z", "kind": "HEALTH", "id": "x", "fly": {"git_rev": None}},
        _health("2026-10-02T10:04:00Z", paused=True, owner="maintenance"),
        _health("2026-10-02T10:06:00Z", rev="aab9bd7a8000", paused=False, tiles=TILES + ["FAMILY_X"]),
    ], sources=["t"])
    ev = changes.runtime_transitions(store)
    kinds = [(e["at"], e["kind"]) for e in ev]
    assert ("2026-10-02T10:04:00Z", "PAUSE") in kinds and ("2026-10-02T10:04:00Z", "PAUSE_OWNER") in kinds
    assert ("2026-10-02T10:06:00Z", "RESUME") in kinds and ("2026-10-02T10:06:00Z", "FLY_REVISION") in kinds
    assert ("2026-10-02T10:06:00Z", "ACTIVE_TILES") in kinds
    assert not any(e["at"] in ("2026-10-02T10:02:00Z", "2026-10-02T10:03:00Z") for e in ev)


def test_snapshot_missing_a_field_is_not_a_change(store):
    store.append("runtime_history", [
        _health("2026-10-02T15:00:00Z"),
        _health("2026-10-02T15:27:00Z", tiles=None),
        _health("2026-10-02T15:31:00Z"),
        _health("2026-10-02T15:40:00Z", tiles=None),
        _health("2026-10-02T15:45:00Z", tiles=["FAMILY_Y"]),
    ], sources=["t"])
    ev = changes.runtime_transitions(store)
    assert [(e["at"], e["kind"]) for e in ev] == [("2026-10-02T15:45:00Z", "ACTIVE_TILES")]
    assert "FAMILY_XVENUE_LEAD_60S" in ev[0]["detail"]["before"]


def test_ai_sightings_and_epochs_and_receipts(store, tmp_path):
    store.publish("ai_calls", pd.DataFrame({
        "call_id": ["a", "b", "c"], "ts": [1790617329.5, 1790812473.2, 1790812500.0],
        "model_served": ["deepseek-v4-flash", "deepseek-v4-flash", "deepseek-v4-flash"],
        "prompt_id": ["unlogged", "abstain_v4_1", "abstain_v4_1"]}), sources=["t"])
    ai = changes.ai_switches(store)
    assert [e["detail"]["prompt"] for e in ai] == ["unlogged", "abstain_v4_1"] and ai[1]["detail"]["calls"] == 2
    (tmp_path / "mirror").mkdir(exist_ok=True)
    (tmp_path / "mirror" / "research_session.json").write_text(json.dumps({
        "bot_version": "v6", "bot_start_time": 1789983916.5, "collector_v22_epoch_ts": 1790624095.1,
        "collector_v22_epoch_id": "epoch-v22-x", "fresh_collection_mode": False}), encoding="utf-8")
    assert {e["kind"] for e in changes.epochs(tmp_path / "mirror")} == {"BOT_START", "EPOCH"}
    assert changes.epochs(tmp_path / "missing") == []
    assert [e["kind"] for e in changes.from_receipts(RECEIPTS)] == ["DEPLOY", "LAPTOP_FAST_FORWARD", "MANUAL"]


def test_timeline_merges_sorts_filters_and_isolates_broken_sources(store, tmp_path):
    store.append("runtime_history", [_health("2026-10-02T10:00:00Z"),
                                     _health("2026-10-02T11:00:00Z", paused=True)], sources=["t"])
    doc = changes.timeline(store, RECEIPTS, tmp_path / "none")
    ats = [e["at"] for e in doc["events"]]
    assert ats == sorted(ats, reverse=True) and doc["sources"]["ai_calls"] == {"status": "OK", "events": 0}
    assert all("ts" not in e for e in doc["events"])
    only = changes.timeline(store, RECEIPTS, tmp_path / "none", since="2026-10-02T09:00:00Z", kinds="pause,deploy")
    assert {e["kind"] for e in only["events"]} == {"PAUSE", "DEPLOY"}
    broken = changes.timeline(store, {"deploys": "not-a-list-of-dicts"}, tmp_path / "none", limit=1)
    assert broken["sources"]["receipts"]["status"] == "ERROR" and broken["returned"] == 1
