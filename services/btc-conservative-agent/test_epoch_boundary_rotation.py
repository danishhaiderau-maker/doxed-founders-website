"""Clean-epoch writer cutover: sidecar stamping, bypass writers, V3 material stamp, v22 head seal, wipe reach."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

import clean_epoch_wipe
import data_epoch
import epoch_boundary_rotation as ebr

EPOCH = "ce-20261004-v31-clean"


@pytest.fixture(autouse=True)
def _reset_active_epoch():
    data_epoch.activate(None)
    yield
    data_epoch.activate(None)


def _manifest(root: Path, started: float) -> dict:
    return data_epoch.ensure_runtime_manifest(root, EPOCH, now=started)


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_stamp_active_only_when_activated():
    assert data_epoch.stamp_active({"ts": 1}) == {"ts": 1}
    data_epoch.activate({"schema": data_epoch.MANIFEST_SCHEMA, "epoch_id": EPOCH, "started_at_ts": 1.8e9,
                         "started_at_utc": "x"})
    assert data_epoch.stamp_active({"ts": 1})["data_epoch_id"] == EPOCH
    assert "data_epoch_id" not in data_epoch.stamp_active({"ts": 1, "row_sha256": "x"})
    data_epoch.activate({"epoch_id": "bad"})
    assert data_epoch.active_epoch_id() is None


def test_sidecar_adopts_bot_manifest_from_env_and_never_opens_one(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_EPOCH_ID", EPOCH)
    data_epoch.activate_from_env(tmp_path)
    assert data_epoch.active_epoch_id() is None
    assert not (tmp_path / data_epoch.MANIFEST_NAME).exists()
    _manifest(tmp_path, time.time())
    monkeypatch.setattr(data_epoch, "_ENV_NEXT_TRY", 0.0)
    assert data_epoch.active_epoch_id() == EPOCH
    monkeypatch.setenv("DATA_EPOCH_ID", "ce-20261005-other")
    data_epoch.activate_from_env(tmp_path)
    data_epoch._ACTIVE = None
    assert data_epoch.active_epoch_id() is None


def test_bypass_writers_stamp_rows(tmp_path):
    import execution_funnel
    import opportunity_capture_v21
    import order_multiverse
    data_epoch.activate(_manifest(tmp_path, time.time()))
    funnel = tmp_path / "execution_funnel.jsonl"
    execution_funnel._append_jsonl(str(funnel), {"stage": "ORDER", "trade_id": "t1"})
    assert execution_funnel._append_close_once(str(funnel), {"stage": "CLOSED", "trade_id": "t1"})
    capture = tmp_path / "opportunity_capture.jsonl"
    line = opportunity_capture_v21.write_opportunity_capture(str(capture), {"signal_ts": 1})
    multiverse = tmp_path / "order_multiverse.jsonl"
    order_multiverse.write_order_multiverse(str(multiverse), {"signal_ts": 1})
    for path in (funnel, capture, multiverse):
        assert all(row["data_epoch_id"] == EPOCH for row in _rows(path)), path.name
    assert json.loads(line)["data_epoch_id"] == EPOCH
    assert data_epoch.line_version(funnel.read_bytes().splitlines()[0]) == f"data_epoch_id={EPOCH}"


def test_collectors_stamp_minute_tapes(tmp_path):
    import cross_venue_collector
    data_epoch.activate(_manifest(tmp_path, time.time()))
    collector = cross_venue_collector.Collector(str(tmp_path), [])
    assert collector._append({"minute_ts": 1_800_000_000, "venue": "x"})
    rows = _rows(Path(collector.tape_path))
    assert rows[-1]["data_epoch_id"] == EPOCH


def test_v3_append_stamps_inside_hashed_material(tmp_path):
    from research_v3_store import V3EvidenceStore
    data_epoch.activate(_manifest(tmp_path, time.time()))
    store = V3EvidenceStore(tmp_path, epoch_id="epoch-test")
    store.append("decision", {"record_id": "r1", "ts": 1})
    row = _rows(tmp_path / "v3" / "ledgers" / "decision.jsonl")[-1]
    assert row["data_epoch_id"] == EPOCH
    assert row["epoch_id"] == "epoch-test"


def test_worker_environment_carries_only_a_valid_epoch_id(monkeypatch):
    from lifecycle_pipeline_runtime import _minimal_worker_environment
    monkeypatch.setenv("DATA_EPOCH_ID", EPOCH)
    assert _minimal_worker_environment()["DATA_EPOCH_ID"] == EPOCH
    monkeypatch.setenv("DATA_EPOCH_ID", "not an epoch; rm -rf")
    assert "DATA_EPOCH_ID" not in _minimal_worker_environment()


def _write_events(root: Path, ts: float, n: int = 3) -> Path:
    path = root / ebr.RESEARCH_EVENTS_FILE
    with path.open("a", encoding="utf-8") as handle:
        for i in range(n):
            handle.write(json.dumps({"event_id": f"e{ts}-{i}", "ts": ts + i}) + "\n")
    return path


def test_v22_head_with_pre_epoch_rows_is_sealed_once(tmp_path):
    started = time.time()
    _write_events(tmp_path, started - 7200)
    manifest = _manifest(tmp_path, started)
    calls = []

    def fake_rotate(data_dir):
        calls.append(data_dir)
        os.replace(Path(data_dir) / ebr.RESEARCH_EVENTS_FILE, Path(data_dir) / f"{ebr.RESEARCH_EVENTS_FILE}.1")
        return {"generation": 1, "relative_path": f"{ebr.RESEARCH_EVENTS_FILE}.1", "sha256": "ab"}

    receipt = ebr.rotate_research_events_at_boundary(tmp_path, manifest, rotate=fake_rotate)
    assert receipt["status"] == "ROTATED" and receipt["sealed_generation"] == 1
    assert receipt["deletion_invoked"] is False
    again = ebr.rotate_research_events_at_boundary(tmp_path, manifest, rotate=fake_rotate)
    assert again == receipt and len(calls) == 1
    assert (tmp_path / "data_epoch_boundary" / f"{EPOCH}.research_events_v22.json").is_file()


def test_v22_head_already_current_or_failing_is_kept(tmp_path):
    started = time.time() - 60
    _write_events(tmp_path, started + 10)
    manifest = _manifest(tmp_path, started)
    receipt = ebr.rotate_research_events_at_boundary(tmp_path, manifest, rotate=lambda **_: pytest.fail("rotated"))
    assert receipt["status"] == "NOT_ROTATED" and receipt["head_decision"] == "ALREADY_CURRENT"

    other = tmp_path / "other"
    other.mkdir()
    _write_events(other, started - 7200)
    manifest2 = _manifest(other, started)

    def boom(**_):
        raise RuntimeError("V22_ROTATION_ACTIVE_TAIL_INVALID")

    blocked = ebr.rotate_research_events_at_boundary(other, manifest2, rotate=boom)
    assert blocked["status"] == "ROTATION_BLOCKED" and "TAIL_INVALID" in blocked["reason"]
    assert (other / ebr.RESEARCH_EVENTS_FILE).is_file()


def test_real_v22_rotation_seals_head(tmp_path):
    from collector_v22 import rotate_research_events
    started = time.time()
    _write_events(tmp_path, started - 7200)
    manifest = _manifest(tmp_path, started)
    receipt = ebr.rotate_research_events_at_boundary(tmp_path, manifest, rotate=rotate_research_events)
    assert receipt["status"] == "ROTATED", receipt
    assert (tmp_path / receipt["sealed_relative_path"]).is_file()
    assert (tmp_path / "research_events_v22.seals").is_dir()


def test_v3_ledgers_are_assessed_never_mutated(tmp_path):
    started = time.time()
    ledgers = tmp_path / "v3" / "ledgers"
    ledgers.mkdir(parents=True)
    for name in ("decision", "order_intent"):
        (ledgers / f"{name}.jsonl").write_text(json.dumps({"ts": started - 3600}) + "\n", encoding="utf-8")
    before = {p.name: p.read_bytes() for p in ledgers.iterdir()}
    receipt = ebr.assess_v3_ledgers(tmp_path, _manifest(tmp_path, started))
    assert {p.name: p.read_bytes() for p in ledgers.iterdir()} == before
    assert receipt["ledgers"]["decision"]["reason"] == "GENERATION_POINTER_BOUND_TO_DEPLOYED_REVISION"
    assert receipt["ledgers"]["order_intent"]["reason"] == "RELAY_OR_RESTART_RECOVERY_LEDGER"
    assert receipt["ledgers"]["execution"]["head_decision"] == "ABSENT"


def test_wipe_plan_keeps_a_receipt_bound_v22_generation_and_its_seal(tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    started = time.time()
    sealed = runtime / f"{ebr.RESEARCH_EVENTS_FILE}.1"
    sealed.write_text("{}\n", encoding="utf-8")
    os.utime(sealed, (started - 3600, started - 3600))
    seals = runtime / "research_events_v22.seals"
    seals.mkdir()
    (seals / "generation-1.json").write_text("{}", encoding="utf-8")
    (runtime / "research_events_v22.provisional.json").write_text("{}", encoding="utf-8")
    boundary = runtime / "data_epoch_boundary"
    boundary.mkdir()
    (boundary / f"{EPOCH}.research_events_v22.json").write_text("{}", encoding="utf-8")
    plan = clean_epoch_wipe.Plan("fly", EPOCH, started, False)
    clean_epoch_wipe.plan_fly(plan, tmp_path, now=started + 7200)
    deleted = {c["rel"]: c["reason"] for c in plan.candidates}
    assert sealed.name not in deleted
    assert plan.kept[clean_epoch_wipe.SEALED_GENERATION_KEEP]["files"] == 1
    assert not any(rel.startswith(("research_events_v22.seals", "data_epoch_boundary")) for rel in deleted)
    assert "research_events_v22.provisional.json" not in deleted


def test_wipe_plan_still_reaches_an_unsealed_numbered_v22_file(tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    started = time.time()
    stray = runtime / f"{ebr.RESEARCH_EVENTS_FILE}.4"
    stray.write_text("{}\n", encoding="utf-8")
    os.utime(stray, (started - 3600, started - 3600))
    plan = clean_epoch_wipe.Plan("fly", EPOCH, started, False)
    clean_epoch_wipe.plan_fly(plan, tmp_path, now=started + 7200)
    deleted = {c["rel"]: c["reason"] for c in plan.candidates}
    assert deleted.get(stray.name, "").startswith("PRE_EPOCH_SEALED_ROTATION")
