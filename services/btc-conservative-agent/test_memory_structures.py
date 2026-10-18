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
    assert "memory_structures.json" in src and "_mem_tracker.run(now, sample=" in src


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


LIMIT = 8192 * 1024  # kB
H = 3600.0


def _series(boot, start_h, n, step_min, base_mb, mb_per_h):
    return [(boot + start_h * H + i * step_min * 60, int((base_mb + mb_per_h * (i * step_min / 60.0)) * 1024)) for i in range(n)]


def test_low_rss_after_deploy_is_pending_without_projection():
    boot, now = 1_000_000.0, 1_000_000.0 + 1800
    r = ms.oom_status([(now, 900 * 1024)], LIMIT, boot, now, now, 900 * 1024)
    assert r["status"] == "PENDING" and r["rss_mb"] == 900 and r["hours_to_oom"] is None and r["rss_growth_mb_per_h"] is None


def test_stale_or_missing_sample_is_unknown_then_amber_after_30_min():
    boot, now = 1_000_000.0, 1_000_000.0 + 5 * H
    assert ms.oom_status([], LIMIT, boot, now, None, None)["status"] == "UNKNOWN"
    assert ms.oom_status([], LIMIT, boot, now, now - 700, 1)["status"] == "UNKNOWN"
    r = ms.oom_status([], LIMIT, boot, now, now - 1900, 1)
    assert r["status"] == "AMBER" and r["reason"] == "STALE_SAMPLE"


def test_runaway_during_warmup_is_red_at_60pct_and_80pct_any_time():
    boot, now = 1_000_000.0, 1_000_000.0 + 1800
    assert ms.oom_status([], LIMIT, boot, now, now, int(0.59 * LIMIT))["status"] == "PENDING"
    r = ms.oom_status([], LIMIT, boot, now, now, int(0.61 * LIMIT))
    assert r["status"] == "RED" and r["reason"] == "RSS_OVER_60PCT_DURING_WARMUP" and r["hours_to_oom"] is None
    late = boot + 30 * H
    assert ms.oom_status([], LIMIT, boot, late, late, int(0.81 * LIMIT))["status"] == "RED"
    assert ms.oom_status(_series(boot, 2, 8, 10, 1000, 0), LIMIT, boot, late, late, int(0.7 * LIMIT))["status"] != "RED"


def test_steady_60mb_per_h_at_3h_uptime_is_a_named_leak():
    boot = 1_000_000.0
    ser = _series(boot, 2.0, 7, 10, 1000, 60.0)  # 2h00..3h00 post-warm-up, 7 samples
    now = ser[-1][0]
    r = ms.oom_status(ser, LIMIT, boot, now, now, ser[-1][1], leak_structure="container:bot._FOO")
    assert r["leak"] is True and r["leak_structure"] == "container:bot._FOO" and 55 <= r["rss_growth_mb_per_h"] <= 65
    assert r["status"] in ("AMBER", "RED") and r["reason"] == "LEAK_OVER_50MB_PER_H"
    flat = ms.oom_status(_series(boot, 2.0, 7, 10, 1000, 10.0), LIMIT, boot, now, now, 1010 * 1024)
    assert flat["leak"] is False and flat["status"] == "GREEN"


def test_slope_ignores_warmup_samples_and_needs_six_post_warmup():
    boot = 1_000_000.0
    warm = [(boot + i * 600, int((500 + i * 100) * 1024)) for i in range(12)]  # steep growth in the first 2 h
    post5 = _series(boot, 2.0, 5, 10, 1700, 0)
    now = post5[-1][0]
    assert ms.oom_status(warm + post5, LIMIT, boot, now, now, post5[-1][1])["status"] == "PENDING"
    post7 = _series(boot, 2.0, 7, 10, 1700, 0)
    now = post7[-1][0]
    r = ms.oom_status(warm + post7, LIMIT, boot, now, now, post7[-1][1])
    assert r["status"] == "GREEN" and abs(r["rss_growth_mb_per_h"]) < 1


def test_restart_resets_series_but_keeps_previous_boot_final_rss_and_slope(tmp_path):
    p = tmp_path / "m.json"
    boot1 = 1_000_000.0
    t = ms.Tracker(p, interval_sec=0)
    row = lambda ts, boot, mb: {"ts": ts, "boot_ts": boot, "memory": {"rss_bytes": mb * 1024 * 1024}}
    for ts, kb in _series(boot1, 2.0, 8, 10, 1000, 20.0):
        t.run(now=ts, sample=row(ts, boot1, kb // 1024))
    boot2 = boot1 + 5 * H
    t2 = ms.Tracker(p, interval_sec=0)
    doc = t2.run(now=boot2 + 60, sample=row(boot2 + 60, boot2, 800))
    assert [x[1] for x in doc["rss_series_full"]] == [800 * 1024]
    pb = doc["prev_boot"]
    assert pb["boot_ts"] == boot1 and pb["final_rss_mb"] is not None and 15 <= pb["slope_mb_per_h"] <= 25
    assert doc["oom_eta"]["prev_boot"] == pb and doc["oom_eta"]["status"] == "PENDING"


def test_health_check_reads_same_telemetry_sample_and_surfaces_states(tmp_path, monkeypatch):
    import bot
    monkeypatch.setenv("FLY_VM_MEMORY_MB", "8192")
    monkeypatch.setattr(bot, "_data_sync_volume_root", lambda: tmp_path)
    assert bot._memory_oom_check(100.0)["status"] == "UNKNOWN"  # no tracker file yet is visible, not silent
    now = 1_000_000.0 + 1800
    (tmp_path / "memory_structures.json").write_text(json.dumps({"boot_ts": 1_000_000.0, "rss_series_full": [], "oom_eta": {}}))
    sample = {"ts": now - 5, "boot_ts": 1_000_000.0, "memory": {"rss_bytes": int(0.65 * 8192) * 1024 * 1024}}
    monkeypatch.setattr(bot, "_runtime_telemetry_status", lambda n=None: {"latest": sample})
    c = bot._memory_oom_check(now)
    assert c["id"] == "fly.memory_oom_eta" and c["status"] == "RED" and "RSS_OVER_60PCT" in c["observed"]
    sample["ts"] = now - 4000
    assert bot._memory_oom_check(now)["status"] == "AMBER"  # stale > 30 min


def test_limit_prefers_fly_vm_memory_and_counts_include_self_checks(monkeypatch, tmp_path):
    monkeypatch.setenv("FLY_VM_MEMORY_MB", "2048")
    assert ms.memory_limit_kb() == 2048 * 1024
    import bot
    monkeypatch.setattr(bot, "_data_sync_volume_root", lambda: tmp_path)
    import time as _t
    nowt = _t.time()
    (tmp_path / "memory_structures.json").write_text(json.dumps({"boot_ts": nowt - 100, "rss_rows": [], "oom_eta": {}}))
    monkeypatch.setattr(bot, "_runtime_telemetry_status", lambda n=None: {"latest": {
        "ts": nowt, "boot_ts": nowt - 100, "memory": {"rss_bytes": int(0.9 * 2048) * 1024 * 1024}}})
    monkeypatch.setattr(bot, "_DASHBOARD_BOOTSTRAP_COMPLETE", True)
    out = bot.app.test_client().get("/api/system-health").get_json()
    assert out["counts"].get("RED", 0) >= 1 and out["verdict"] == "RED"
    assert any(c["id"] == "fly.memory_oom_eta" for c in out["failing"])


def test_pending_state_still_reports_rss_and_limit():
    hist = [{"ts": 0, "rss_kb": 512 * 1024}, {"ts": 60, "rss_kb": 600 * 1024}]
    out = ms.oom_eta(hist, limit_kb=8192 * 1024)
    assert out["status"] == "PENDING" and out["rss_mb"] == 600 and out["limit_mb"] == 8192
    assert ms.oom_eta([], 100)["rss_mb"] is None


def _health(monkeypatch, tmp_path, nowt, rss_frac, sample_age=0.0, boot_age=100.0, have_sample=True):
    import bot
    monkeypatch.setenv("FLY_VM_MEMORY_MB", "8192")
    monkeypatch.setattr(bot, "_data_sync_volume_root", lambda: tmp_path)
    monkeypatch.setattr(bot, "_DASHBOARD_BOOTSTRAP_COMPLETE", True)
    (tmp_path / "memory_structures.json").write_text(json.dumps({"boot_ts": nowt - boot_age, "rss_series_full": [], "oom_eta": {}}))
    latest = {"ts": nowt - sample_age, "boot_ts": nowt - boot_age, "memory": {"rss_bytes": int(rss_frac * 8192) * 1024 * 1024}} if have_sample else None
    monkeypatch.setattr(bot, "_runtime_telemetry_status", lambda n=None: {"latest": latest})
    return bot


def test_unknown_is_rank0_for_30_min_then_amber_colours_the_verdict(tmp_path, monkeypatch):
    import time as _t
    nowt = _t.time()
    bot = _health(monkeypatch, tmp_path, nowt, 0.1, have_sample=False)
    bot._MEMORY_UNKNOWN_SINCE["t"] = None
    assert bot._memory_oom_check(nowt)["status"] == "UNKNOWN"
    assert bot._memory_oom_check(nowt + 1700)["status"] == "UNKNOWN"
    assert bot._memory_oom_check(nowt + 1900)["status"] == "AMBER"
    bot._MEMORY_UNKNOWN_SINCE["t"] = nowt - 2000   # UNKNOWN has now persisted > 30 min
    out = bot.app.test_client().get("/api/system-health").get_json()
    assert out["verdict"] in ("AMBER", "RED") and any(c["id"] == "fly.memory_oom_eta" for c in out["failing"])


def test_pending_is_rank0_only_below_the_absolute_ceilings(tmp_path, monkeypatch):
    import time as _t
    nowt = _t.time()
    bot = _health(monkeypatch, tmp_path, nowt, 0.2)
    c = bot._memory_oom_check(nowt)
    assert c["status"] == "PENDING"
    out = bot.app.test_client().get("/api/system-health").get_json()
    mem = [c for c in out["fly_self_checks"] if c["id"] == "fly.memory_oom_eta"][0]
    assert mem["status"] == "PENDING"           # visible, but contributes nothing to the RED/AMBER counts
    bot = _health(monkeypatch, tmp_path, nowt, 0.65)      # PENDING window, over 60%
    assert bot._memory_oom_check(nowt)["status"] == "RED"
    out = bot.app.test_client().get("/api/system-health").get_json()
    assert out["verdict"] == "RED"
    assert [c for c in out["fly_self_checks"] if c["id"] == "fly.memory_oom_eta"][0]["status"] == "RED"
    bot = _health(monkeypatch, tmp_path, nowt, 0.85, boot_age=10 * 3600)   # any time, over 80%
    assert bot._memory_oom_check(nowt)["status"] == "RED"


def test_absolute_ceiling_applies_even_if_limit_unreadable():
    boot, now = 1_000_000.0, 1_000_000.0 + 1800
    r = ms.oom_status([], None, boot, now, now, int(0.7 * 8192 * 1024))
    assert r["status"] == "RED" and r["reason"] == "RSS_OVER_60PCT_DURING_WARMUP"
