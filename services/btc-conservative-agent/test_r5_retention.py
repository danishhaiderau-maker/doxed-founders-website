"""R5 retention: segment/bundle caps, locks wipe, v22 seal/drain/discard."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

import pytest

import disk_alert
import r5_retention as r5
import rotation_cap


@pytest.fixture(autouse=True)
def _clear_red():
    for row in disk_alert.red_conditions():
        disk_alert.clear_red(row["reason"])
    yield
    for row in disk_alert.red_conditions():
        disk_alert.clear_red(row["reason"])


def _seg(root, i, size, mtime):
    sha = f"{i:064x}"
    d = root / "v3" / "market_segments" / sha[:2]
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{sha}.json"
    p.write_bytes(b"s" * size)
    os.utime(p, (mtime, mtime))
    return sha


def test_segment_cap_oldest_first_and_count(tmp_path):
    shas = [_seg(tmp_path, i, 100, 1000 + i) for i in range(10)]
    (tmp_path / "v3" / "market_segments" / "zz").mkdir()
    (tmp_path / "v3" / "market_segments" / "zz" / "notasegment.json").write_text("x")
    assert r5.count_market_segments(tmp_path) == 10
    res = r5.cap_market_segments(tmp_path, max_bytes=500, now=5.0)
    assert res["deleted"] == 5 and res["referenced_deleted"] == 0
    left = sorted(p.stem for p in (tmp_path / "v3" / "market_segments").rglob("*.json") if len(p.stem) == 64)
    assert left == sorted(shas[5:])
    assert disk_alert.red_conditions() == []
    assert (tmp_path / rotation_cap.CAP_RECEIPTS_FILE).exists()


def test_referenced_segments_go_last_but_still_capped_with_red(tmp_path):
    shas = [_seg(tmp_path, i, 100, 1000 + i) for i in range(4)]
    res = r5.cap_market_segments(tmp_path, max_bytes=100, referenced=[shas[0]], now=time.time())
    assert res["deleted"] == 3 and res["referenced_deleted"] == 0
    res = r5.cap_market_segments(tmp_path, max_bytes=0, referenced=[shas[0]], now=time.time())
    assert res["referenced_deleted"] == 1
    assert r5.count_market_segments(tmp_path) == 0
    assert "SEGMENT_CAP_DELETED_REFERENCED" in {r["reason"] for r in disk_alert.red_conditions()}
    assert disk_alert.compute_disk_alert({"total_bytes": 100, "used_bytes": 1})["level"] == "RED"


def _bundle(root, kind, prefix, name, size, mtime):
    d = root / "v3" / kind / "aa" / f"{prefix}{name}"
    d.mkdir(parents=True)
    (d / "payload.bin").write_bytes(b"b" * size)
    os.utime(d, (mtime, mtime))
    return d


def test_bundles_capped_regardless_of_ack(tmp_path):
    now = 10 * 86400.0
    old_acked = _bundle(tmp_path, "lifecycle_bundles", "lifecycle-", "a1", 10, now - 8 * 86400)
    old_unacked = _bundle(tmp_path, "lifecycle_transfer_bundles", "transfer-", "b2", 10, now - 9 * 86400)
    fresh = _bundle(tmp_path, "lifecycle_bundles", "lifecycle-", "c3", 10, now - 60)
    acks = tmp_path / "v3" / "lifecycle_cleanup_acks"
    acks.mkdir(parents=True)
    (acks / "lifecycle-a1.json").write_text("{}")
    res = r5.cap_lifecycle_bundles(tmp_path, max_bytes=10 ** 9, now=now)
    assert res["removed"] == 2 and res["unacked_removed"] == 1
    assert not old_acked.exists() and not old_unacked.exists() and fresh.exists()
    assert "BUNDLE_CAP_REMOVED_UNACKED" in {r["reason"] for r in disk_alert.red_conditions(now=now)}
    res = r5.cap_lifecycle_bundles(tmp_path, max_bytes=0, now=now)  # byte cap removes the fresh one too
    assert res["removed"] == 1 and not fresh.exists()


def test_locks_wipe_keeps_recent(tmp_path):
    locks = tmp_path / "v3" / ".locks"
    locks.mkdir(parents=True)
    now = 100_000.0
    old = locks / "old.lock"; old.write_text("")
    os.utime(old, (now - 7200, now - 7200))
    recent = locks / "recent.lock"; recent.write_text("")
    os.utime(recent, (now - 600, now - 600))
    pre_boot_recent = locks / "preboot.lock"; pre_boot_recent.write_text("")
    os.utime(pre_boot_recent, (now - 1800, now - 1800))
    other = locks / "keep.txt"; other.write_text("")
    os.utime(other, (now - 9999, now - 9999))
    res = r5.wipe_stale_locks(tmp_path, boot_ts=now - 1000, now=now)
    assert res["deleted"] == 1
    assert not old.exists() and recent.exists() and pre_boot_recent.exists() and other.exists()


def _event(event_id):
    from collector_v22_schema import OBS_DATA_ERROR
    return {"event_id": event_id, "trade_id": event_id, "collector_version": "collector_v2.2",
            "observation_status": OBS_DATA_ERROR, "primary_outcome": "REJECTED",
            "envelope": {"signal_ts": 1700000000.0}}


def test_v22_below_threshold_is_noop(tmp_path):
    from collector_v22 import write_research_event_once
    write_research_event_once(_event("e1"), data_dir=str(tmp_path))
    assert r5.seal_drain_discard_v22(tmp_path, drain=lambda: [], min_bytes=10 ** 9)["status"] == "BELOW_THRESHOLD"


def test_v22_seal_drain_discard_with_pending_raises_red_and_keeps_index(tmp_path):
    import collector_v22
    from collector_v22 import research_event_generation_paths, write_research_event_once
    from collector_v22_schema import EVENT_SQLITE_INDEX_FILE, RESEARCH_EVENTS_FILE
    for i in range(3):
        assert write_research_event_once(_event(f"e{i}"), data_dir=str(tmp_path))[0]
    t = [0.0]

    def clock():
        return t[0]

    def sleep(sec):
        t[0] += sec
    pending = [f"evt-{i:06d}-" + "p" * 40 for i in range(5000)]  # forces receipt truncation
    calls = []

    def drain():
        calls.append(1)
        return pending
    res = r5.seal_drain_discard_v22(tmp_path, drain=drain, min_bytes=1, max_drain_sec=90,
                                    poll_sec=30, sleep=sleep, clock=clock)
    assert res["status"] == "DISCARDED" and res["pending_count"] == 5000
    assert len(calls) == 4  # t=0,30,60,90 -> deadline, never blocks
    assert not (tmp_path / f"{RESEARCH_EVENTS_FILE}.1").exists()
    assert (tmp_path / EVENT_SQLITE_INDEX_FILE).exists()
    receipt = (tmp_path / r5.V22_RECEIPT_FILE).read_text().splitlines()
    assert len(receipt) == 1 and len(receipt[0].encode()) + 1 <= r5.V22_RECEIPT_MAX_BYTES
    row = json.loads(receipt[0])
    assert row["pending_event_ids_truncated"] is True and row["pending_count"] == 5000
    assert "V22_DISCARDED_WITH_PENDING" in {r["reason"] for r in disk_alert.red_conditions(now=90.0)}
    # Collector reads/writes stay valid after the discard.
    paths = research_event_generation_paths(str(tmp_path))
    assert all(os.path.exists(p) for p in paths)
    assert write_research_event_once(_event("after"), data_dir=str(tmp_path))[0]
    assert collector_v22.plan_orphan_event_seals(str(tmp_path))["plan_sha256"]


def test_v22_drained_clean_no_red(tmp_path):
    from collector_v22 import write_research_event_once
    write_research_event_once(_event("e1"), data_dir=str(tmp_path))
    res = r5.seal_drain_discard_v22(tmp_path, drain=lambda: [], min_bytes=1, sleep=lambda s: None)
    assert res["status"] == "DISCARDED" and res["pending_count"] == 0
    assert disk_alert.red_conditions() == []


def test_v22_broken_drain_never_blocks(tmp_path):
    from collector_v22 import write_research_event_once
    write_research_event_once(_event("e1"), data_dir=str(tmp_path))

    def drain():
        raise RuntimeError("down")
    t = [0.0]
    res = r5.seal_drain_discard_v22(tmp_path, drain=drain, min_bytes=1, max_drain_sec=10, poll_sec=5,
                                    sleep=lambda s: t.__setitem__(0, t[0] + s), clock=lambda: t[0])
    assert res["status"] == "DISCARDED" and res["pending_count"] == 1


def test_reset_asserts_zero_market_segments():
    src = (Path(__file__).resolve().parent / "bot.py").read_text("utf-8")
    i = src.index('stage = "V22_SEAL_RETIREMENT"')
    j = src.index('stage = "GENOME_RESET"', i)
    block = src[i:j]
    assert "r5_retention.count_market_segments(root)" in block
    assert 'disk_alert.raise_red("RESET_LEFT_MARKET_SEGMENTS"' in block


def test_bundles_rk_1810_keep_needed_unshipped_below_cap_drop_oldest_at_cap(tmp_path):
    now = 10 * 86400.0
    needed = _bundle(tmp_path, "lifecycle_bundles", "lifecycle-", "n1", 10, now - 9 * 86400)
    needed_acked = _bundle(tmp_path, "lifecycle_bundles", "lifecycle-", "n2", 10, now - 9 * 86400)
    aged = _bundle(tmp_path, "lifecycle_bundles", "lifecycle-", "x9", 10, now - 8 * 86400)
    fresh = _bundle(tmp_path, "lifecycle_bundles", "lifecycle-", "f1", 10, now - 60)
    acks = tmp_path / "v3" / "lifecycle_cleanup_acks"
    acks.mkdir(parents=True)
    (acks / "lifecycle-n2.json").write_text("{}")
    res = r5.cap_lifecycle_bundles(tmp_path, max_bytes=10 ** 9, keep=["n1", "n2"], now=now)
    # Below the cap: the needed unshipped bundle stays; a needed but shipped one ages out.
    assert needed.exists() and fresh.exists()
    assert not needed_acked.exists() and not aged.exists()
    assert res["kept_needed_unshipped"] == 1 and res["cap_removed"] == 0
    assert "BUNDLE_CAP_REACHED" not in {r["reason"] for r in disk_alert.red_conditions(now=now)}
    # At the cap: oldest first regardless of ship state or keep; ids logged; RED.
    res = r5.cap_lifecycle_bundles(tmp_path, max_bytes=10, keep=["n1"], now=now)
    assert not needed.exists() and fresh.exists()
    assert res["cap_removed"] == 1 and res["over_cap"] is False
    assert "BUNDLE_CAP_REACHED" in {r["reason"] for r in disk_alert.red_conditions(now=now)}
    receipts = tmp_path / "cap_receipts.jsonl"
    assert receipts.exists() and "lifecycle-n1" in receipts.read_text()
