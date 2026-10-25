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
    top = next(r for r in doc["container_growth"] if r["name"] == "fake_growth_mod.BIG")
    assert top["len_growth"] == 300 and top["len_now"] == 3_000_300
    assert doc["latest"]["containers"][0]["name"] == "fake_growth_mod.BIG"  # ranked by bytes
    assert doc["leak_attribution"]["structure"]  # named or UNATTRIBUTED_ANON_OR_NATIVE, never empty
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
    assert ms.oom_status([], LIMIT, boot, now, now, int(0.18 * LIMIT))["status"] == "PENDING"   # 1.4 GB; >2 GB in a young boot is RED since the interim alert
    r = ms.oom_status([], LIMIT, boot, now, now, int(0.61 * LIMIT))
    assert r["status"] == "RED" and r["reason"] in ("RSS_OVER_3GB", "RSS_OVER_2GB_UNDER_12H_BOOT", "RSS_OVER_60PCT_DURING_WARMUP") and r["hours_to_oom"] is None
    late = boot + 30 * H
    assert ms.oom_status([], LIMIT, boot, late, late, int(0.81 * LIMIT))["status"] == "RED"
    assert ms.oom_status(_series(boot, 2, 8, 10, 1000, 0), LIMIT, boot, late, late, int(0.3 * LIMIT))["status"] != "RED"


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
    assert c["id"] == "fly.memory_oom_eta" and c["status"] == "RED" and ("RSS_OVER_60PCT" in c["observed"] or "RSS_OVER_2GB" in c["observed"] or "RSS_OVER_3GB" in c["observed"])
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
    assert r["status"] == "RED" and r["reason"] in ("RSS_OVER_3GB", "RSS_OVER_2GB_UNDER_12H_BOOT", "RSS_OVER_60PCT_DURING_WARMUP")


def test_interim_alert_3gb_or_2gb_in_young_boot_is_red_and_sums_the_shipper():
    now, boot = 1_000_000.0, 1_000_000.0 - 3 * 3600
    kb = lambda mb: mb * 1024
    ok = ms.oom_status([], kb(8192), boot, now, now, kb(1500))
    assert ok["status"] != "RED"
    young = ms.oom_status([], kb(16384), boot, now, now, kb(2100))
    assert young["status"] == "RED" and young["reason"] == "RSS_OVER_2GB_UNDER_12H_BOOT"
    old_boot = now - 20 * 3600
    assert ms.oom_status([], kb(16384), old_boot, now, now, kb(2100))["status"] != "RED"      # >12 h: 2 GB alone is fine
    big = ms.oom_status([], kb(16384), old_boot, now, now, kb(3100))
    assert big["status"] == "RED" and big["reason"] == "RSS_OVER_3GB"
    summed = ms.oom_status([], kb(16384), old_boot, now, now, kb(2000), extra_rss_kb=kb(1100))   # 2000 + shipper 1100
    assert summed["status"] == "RED" and summed["shipper_rss_mb"] == 1100


def test_stairs_and_loop_start_end_are_recorded(tmp_path):
    series = [(1000.0 + 60 * i, kb) for i, kb in enumerate([900_000, 905_000, 1_020_000, 1_025_000, 1_140_000])]
    st = ms.detect_stairs(series)
    assert [s["delta_mb"] for s in st] == [112.3, 112.3]
    t = ms.Tracker(tmp_path / "m.json", interval_sec=0)
    t.record_loop("analytics_loop", 100.0, 103.5)
    doc = t.run(now=2000.0)
    assert doc["loops"] == [{"name": "analytics_loop", "start": 100.0, "end": 103.5, "body_sec": 3.5}]
    assert "stairs" in doc and "shipper_rss_kb" in doc


def test_shipper_match_is_the_script_not_any_cmdline_containing_shipper():
    yes = [b"python\0/app/research_segment_shipper.py\0", b"nice\0-n\x0010\0python\0/app/research_segment_shipper.py\0"]
    no = [b"python\0scripts/deploy_gate_open_trades.py\0--shipper-check\0",
          b"node\0scripts/verify-shipper-lag.mjs\0", b"python\0/app/monitor_shipper_lag.py\0",
          b"grep\0shipper\0", b"python\0/app/research_segment_shipper_test.py\0", b"python\0-c\0print('research_segment_shipper.py')\0",
          b"python\0/app/research_segment_server.py\0", b""]
    assert all(ms.cmdline_runs_script(c) for c in yes)
    assert not any(ms.cmdline_runs_script(c) for c in no)


def test_process_rss_ignores_a_non_shipper_process_that_mentions_shipper(tmp_path, monkeypatch):
    import subprocess, sys
    decoy = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", "shipper", "research_segment_shipper"])
    try:
        assert ms.process_rss_kb() == 0 or ms.process_rss_kb() >= 0
        # the decoy's own cmdline contains 'shipper' twice but no argv token is the script: it must not be counted
        assert ms.cmdline_runs_script(open(f"/proc/{decoy.pid}/cmdline", "rb").read()) is False
    finally:
        decoy.kill()


def test_shipper_term_is_a_30_min_rolling_median_but_3gib_ceiling_stays_unsmoothed():
    t = ms.Tracker("/tmp/_unused_ms2.json")
    # six samples 10 min apart: a steady ~400 MB shipper with one 1326 MB spike -> median ignores the spike
    vals = [400, 410, 1326, 405, 400, 395]
    out = [t.smooth_shipper(i * 600.0, v * 1024) for i, v in enumerate(vals)]
    assert out[2] <= 410 * 1024 and out[-1] <= 405 * 1024
    # samples older than 30 min fall out of the window
    assert len(t.shipper_hist) <= 4
    boot, now = 0.0, 3600.0
    series = [(now, 600 * 1024)]
    # young boot, bot 600 MB + smoothed shipper 400 MB = 1.0 GB < 2 GB: not RED even though the raw spike is 1326 MB
    ok = ms.oom_status(series, 8 * 1024 * 1024, boot, now, now, 600 * 1024, extra_rss_kb=400 * 1024, extra_rss_raw_kb=1326 * 1024)
    assert ok["status"] != "RED" and ok["shipper_rss_raw_mb"] == 1326 and ok["shipper_rss_mb"] == 400
    # but an UNSMOOTHED total >= 3 GiB is RED regardless of the median
    red = ms.oom_status(series, 8 * 1024 * 1024, boot, now, now, 1800 * 1024, extra_rss_kb=400 * 1024, extra_rss_raw_kb=1300 * 1024)
    assert red["status"] == "RED" and red["reason"] == "RSS_OVER_3GB"
    # the bot's own RSS is never smoothed: bot alone over 2 GB in a young boot is RED
    bot_only = ms.oom_status(series, 8 * 1024 * 1024, boot, now, now, 2100 * 1024, extra_rss_kb=0)
    assert bot_only["status"] == "RED"


def test_record_loop_keeps_per_step_rss_delta_and_timed_step_records_it():
    import bot
    t = ms.Tracker("/tmp/_unused_ms.json")
    steps = []
    bot._timed_step(steps, "a", lambda: [0] * 1000)
    assert steps and steps[0]["step"] == "a" and set(steps[0]) == {"step", "sec", "rss_delta_kb"}
    t.record_loop("analytics_loop", 1.0, 2.0, steps=steps)
    assert t.loops[-1]["steps"][0]["step"] == "a"
    t.record_loop("analytics_loop", 3.0, 4.0)
    assert "steps" not in t.loops[-1]
    try:
        bot._timed_step([], "boom", lambda: (_ for _ in ()).throw(ValueError("x")))
    except ValueError:
        pass
    else:
        raise AssertionError("step exceptions must propagate")


def test_mem_tracker_is_a_module_global_so_analytics_loop_can_record_loops():
    import re
    src = open(__file__.replace("test_memory_structures.py", "bot.py"), encoding="utf-8").read()
    assert re.search(r"^_mem_tracker = None", src, re.M)
    body = src[src.index("def runtime_uptime_loop():"):][:400]
    assert "global _mem_tracker" in body


def test_attribution_ranks_by_bytes_not_element_count():
    # many tiny entries vs few huge: by bytes the second wins
    base = {"m.COUNT": 1000, "m.BYTES": 1000}
    cont = [{"name": "m.COUNT", "len": 100_000, "shallow_bytes": 1000 + 2 * (1 << 20)},
            {"name": "m.BYTES", "len": 60, "shallow_bytes": 1000 + 40 * (1 << 20)}]
    a = ms.attribute_growth(cont, [], base, {}, rss_growth_kb=100 * 1024)
    assert a["structure"] == "container:m.BYTES" and a["bytes_growth"] == 40 * (1 << 20)
    assert a["fraction_of_rss_growth"] == 0.4


def test_attribution_skips_capped_deque_still_filling():
    base = {"m.RING": 1000}
    cont = [{"name": "m.RING", "len": 500, "shallow_bytes": 1000 + 50 * (1 << 20), "maxlen": 1000, "capped_filling": True}]
    a = ms.attribute_growth(cont, [], base, {}, rss_growth_kb=100 * 1024)
    assert a["structure"] == ms.UNATTRIBUTED and a["best_candidate"] is None


def test_attribution_reports_unattributed_when_nothing_explains_the_rss_growth():
    base = {"m.D": 1000}
    cont = [{"name": "m.D", "len": 900, "shallow_bytes": 1000 + (2 << 20)}]  # 2 MiB of a 500 MiB RSS rise
    a = ms.attribute_growth(cont, [{"type": "dict", "shallow_bytes": 3 << 20}], base, {"dict": 1 << 20},
                            rss_growth_kb=500 * 1024)
    assert a["structure"] == "UNATTRIBUTED_ANON_OR_NATIVE" and a["bytes_growth"] is None
    assert a["best_candidate"] in {"container:m.D", "type:dict"}


def test_module_containers_marks_capped_deques_and_ranks_by_bytes():
    import collections as _c
    mod = types.ModuleType("fake_deque_mod")
    mod.RING = _c.deque(range(60), maxlen=100)
    mod.FULL = _c.deque(range(100), maxlen=100)
    sys.modules["fake_deque_mod"] = mod
    try:
        rows = {r["name"]: r for r in ms.module_containers(modules={"fake_deque_mod": mod})}
    finally:
        del sys.modules["fake_deque_mod"]
    assert rows["fake_deque_mod.RING"]["capped_filling"] is True and rows["fake_deque_mod.RING"]["maxlen"] == 100
    assert rows["fake_deque_mod.FULL"]["capped_filling"] is False
    sizes = [r["shallow_bytes"] for r in ms.module_containers(modules={"fake_deque_mod": mod})]
    assert sizes == sorted(sizes, reverse=True)
