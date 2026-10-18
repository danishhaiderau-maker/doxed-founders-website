"""memory_structures: read-only, bounded snapshot that names the growing in-process structure."""
import json
import sys
import types

import memory_structures as ms


def test_snapshot_names_the_growing_container_and_writes_atomically(tmp_path):
    mod = types.ModuleType("fake_growth_mod")
    mod.BIG = {i: i for i in range(3_000_000)}  # larger than every container in the test process
    mod.SMALL = [1, 2, 3]
    sys.modules["fake_growth_mod"] = mod
    try:
        t = ms.Tracker(tmp_path / "m.json", interval_sec=0)
        t.run(now=1000.0)
        mod.BIG.update({i: i for i in range(3_000_000, 3_000_300)})
        doc = t.run(now=1060.0)
    finally:
        del sys.modules["fake_growth_mod"]
    top = doc["container_growth"][0]
    assert top["name"] == "fake_growth_mod.BIG" and top["len_growth"] == 300 and top["len_now"] == 3_000_300
    assert all(r["name"] != "fake_growth_mod.SMALL" for r in doc["latest"]["containers"])  # < 50 items: not listed
    assert doc["samples"] == 2 and doc["window_sec"] == 60.0
    assert json.loads((tmp_path / "m.json").read_text())["schema"] == "memory_structures_v1"
    assert not list(tmp_path.glob(".*tmp"))


def test_due_respects_interval_and_history_is_bounded(tmp_path):
    t = ms.Tracker(tmp_path / "m.json", interval_sec=600)
    assert t.due(1000.0)
    t.run(now=1000.0)
    assert not t.due(1599.0) and t.due(1600.0)
    for i in range(ms.KEEP + 5):
        t.run(now=2000.0 + i)
    assert len(t.history) == ms.KEEP


def test_bot_runs_the_tracker_from_the_uptime_loop():
    from pathlib import Path
    src = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
    assert "memory_structures.json" in src and "_mem_tracker.run(now)" in src


def test_process_detail_reads_anon_file_threads_fds_and_top_mappings(tmp_path):
    smaps = tmp_path / "smaps"
    smaps.write_text(
        "00400000-00500000 r-xp 00000000 08:01 1 /usr/bin/python3\nRss: 100 kB\nAnonymous: 0 kB\n"
        "7f0000000000-7f0000900000 rw-p 00000000 00:00 0 \nRss: 9000 kB\nAnonymous: 9000 kB\n"
        "7f1000000000-7f1000100000 r--s 00000000 08:01 2 /app/data/x.duckdb\nRss: 500 kB\nAnonymous: 0 kB\n")
    d = ms.process_detail(top=2, smaps_path=str(smaps))
    assert [m["rss_kb"] for m in d["top_mappings"]] == [9000, 500] and d["top_mappings"][0]["anon_kb"] == 9000
    assert d["top_mappings"][0]["name"] == "[anon]" and d["mapping_count"] == 3
    assert isinstance(d["open_fds"], int) and d["open_fds"] > 0 and d["threads"] >= 1
    assert {"anon_kb", "file_kb", "duckdb_memory_bytes"} <= set(d)


def test_oom_eta_status_from_linear_rss_growth():
    hist = [{"ts": 0, "rss_kb": 1_000_000}, {"ts": 7200, "rss_kb": 1_000_000 + 2 * 180 * 1024}]
    red = ms.oom_eta(hist, limit_kb=1_400_000)          # ~3.3 h of headroom at 180 MB/h
    assert red["status"] == "RED" and red["rss_growth_mb_per_h"] == 180.0 and red["hours_to_oom"] < 6
    assert ms.oom_eta(hist, limit_kb=4_000_000)["status"] == "AMBER"       # ~ 16 h
    assert ms.oom_eta(hist, limit_kb=40_000_000)["status"] == "GREEN"
    flat = [{"ts": 0, "rss_kb": 5}, {"ts": 7200, "rss_kb": 5}]
    assert ms.oom_eta(flat, 100)["status"] == "GREEN" and ms.oom_eta(flat, 100)["hours_to_oom"] is None
    assert ms.oom_eta(hist[:1], 100)["status"] == "PENDING" and ms.oom_eta(hist, None)["status"] == "PENDING"
    assert ms.oom_eta([{"ts": 0, "rss_kb": 1}, {"ts": 60, "rss_kb": 9}], 100)["status"] == "PENDING"  # window too short


def test_tracker_doc_carries_proc_and_oom_eta(tmp_path):
    t = ms.Tracker(tmp_path / "m.json")
    doc = t.run(now=1000.0)
    assert "oom_eta" in doc and doc["latest"]["proc"]["open_fds"] and doc["rss_series"][0][0] == 1000


def test_health_check_surfaces_red_when_oom_is_near(tmp_path, monkeypatch):
    import bot
    monkeypatch.setattr(bot, "_data_sync_volume_root", lambda: tmp_path)
    assert bot._memory_oom_check(100.0)["status"] == "AMBER"  # no sample yet is visible, not silent
    doc = {"latest": {"ts": 90.0}, "oom_eta": {"status": "RED", "hours_to_oom": 3.1, "rss_growth_mb_per_h": 180.0,
                                                "rss_mb": 1300, "limit_mb": 1900}}
    (tmp_path / "memory_structures.json").write_text(json.dumps(doc))
    c = bot._memory_oom_check(100.0)
    assert c["id"] == "fly.memory_oom_eta" and c["status"] == "RED" and "oom_in=3.1h" in c["observed"]
    assert bot._memory_oom_check(100.0 + 7200)["status"] == "AMBER"  # stale sampler


def test_limit_prefers_fly_vm_memory_and_counts_include_self_checks(monkeypatch, tmp_path):
    monkeypatch.setenv("FLY_VM_MEMORY_MB", "2048")
    assert ms.memory_limit_kb() == 2048 * 1024
    import bot
    monkeypatch.setattr(bot, "_data_sync_volume_root", lambda: tmp_path)
    (tmp_path / "memory_structures.json").write_text(json.dumps(
        {"latest": {"ts": __import__("time").time()}, "oom_eta": {"status": "RED", "hours_to_oom": 2.0}}))
    monkeypatch.setattr(bot, "_DASHBOARD_BOOTSTRAP_COMPLETE", True)
    out = bot.app.test_client().get("/api/system-health").get_json()
    assert out["counts"].get("RED", 0) >= 1 and out["verdict"] == "RED"
    assert any(c["id"] == "fly.memory_oom_eta" for c in out["failing"])


def test_pending_state_still_reports_rss_and_limit():
    hist = [{"ts": 0, "rss_kb": 512 * 1024}, {"ts": 60, "rss_kb": 600 * 1024}]
    out = ms.oom_eta(hist, limit_kb=8192 * 1024)
    assert out["status"] == "PENDING" and out["rss_mb"] == 600 and out["limit_mb"] == 8192
    assert ms.oom_eta([], 100)["rss_mb"] is None
