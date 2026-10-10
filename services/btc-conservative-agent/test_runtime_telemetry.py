"""runtime_telemetry_v1: cheap per-minute Fly resource rows, shipped and evaluated."""

import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import research_segment_selection as rss
import runtime_telemetry as rt


class _Clock:
    def __init__(self, t=1_800_000_000.0, m=1000.0):
        self.t, self.m = t, m

    def advance(self, sec):
        self.t += sec
        self.m += sec


def _lock_counters(**over):
    base = {"name": "trade_lock", "holds": 100, "hold_over_budget": 0, "timeout_count": 10,
            "probe_busy_count": 10, "timed_timeout_count": 0, "waits": 4,
            "wait_total_ms": 40.0, "wait_over_budget": 0, "interval_hold_max_ms": 30.0,
            "interval_hold_max_site": "_build_relay_execution_state_snapshot:1",
            "interval_wait_max_ms": 15.0, "interval_wait_max_site": "_commit:1",
            "top_wait_sites": [], "top_timeout_sites": []}
    base.update(over)
    return base


def _sampler(tmp_path, clock, lock_seq, cpu_seq, crash=None):
    locks = iter(lock_seq)
    cpus = iter(cpu_seq)
    return rt.RuntimeTelemetry(
        str(tmp_path), boot_ts=clock.t - 100,
        pressure_fn=lambda: {"cpu_count": 1, "rss_bytes": 700_000_000, "load_1m": 3.1,
                             "load_1m_per_cpu": 3.1, "disk_used_pct": 19.4,
                             "disk_free_bytes": 40_000_000_000},
        handlers_fn=lambda: {"active_total": 1, "by_cap": {}},
        lock_fn=lambda: next(locks),
        revision_fn=lambda: "abc123",
        crash_dump_path=crash,
        clock=lambda: clock.t, mono=lambda: clock.m, process_cpu=lambda: next(cpus),
    )


def test_sample_rows_are_appended_with_interval_deltas(tmp_path):
    clock = _Clock()
    s = _sampler(tmp_path, clock,
                 [_lock_counters(),
                  _lock_counters(holds=160, probe_busy_count=13, timeout_count=14,
                                 timed_timeout_count=1, waits=6, wait_total_ms=100.0)],
                 [10.0, 40.0, 70.0])
    clock.advance(60)
    s.sample_and_write()
    clock.advance(60)
    row = s.sample_and_write()
    lines = (tmp_path / rt.FILE_NAME).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    stored = json.loads(lines[-1])
    assert stored == json.loads(json.dumps(row))
    assert stored["schema"] == rt.SCHEMA and stored["seq"] == 1
    assert stored["cpu"]["process_pct"] == 50.0
    assert stored["memory"]["rss_bytes"] == 700_000_000
    assert stored["memory"]["rss_delta_bytes"] == 0
    lock = stored["trade_lock"]
    assert lock["holds"] == 60 and lock["probe_busy"] == 3 and lock["timed_timeouts"] == 1
    assert lock["waits"] == 2 and lock["wait_mean_ms"] == 30.0
    assert stored["threads"]["python"] >= 1
    assert stored["revision"] == "abc123"
    assert str(tmp_path) not in lines[-1]


def test_crash_dump_watcher_counts_only_appended_rows(tmp_path):
    path = tmp_path / "crash_dump.json"
    path.write_text('{"a":1}\n{"a":2}\n', encoding="utf-8")
    w = rt.CrashDumpWatcher(str(path))
    assert w.poll()["new_rows"] == 0
    with open(path, "a", encoding="utf-8") as fh:
        fh.write('{"a":3}\n')
    assert w.poll()["new_rows"] == 1
    assert w.poll()["new_rows"] == 0
    path.write_text('{"a":9}\n', encoding="utf-8")
    assert w.poll()["new_rows"] == 0
    assert rt.CrashDumpWatcher(str(tmp_path / "missing")).poll() == {"available": False}


def test_lag_probe_percentiles_and_drain():
    probe = rt.LagProbe()
    for v in [1, 2, 3, 4, 100]:
        probe.record(v)
    out = probe.drain()
    assert out == {"n": 5, "p50_ms": 3.0, "p95_ms": 100.0, "max_ms": 100.0}
    assert probe.drain()["n"] == 0


def _compact_rows(n, now, **over):
    rows = []
    for i in range(n):
        row = {"ts": now - (n - 1 - i) * 60, "rss": 700_000_000, "lag_p95": 5.0, "lag_max": 20.0,
               "cpu_pct": 40.0, "timed_timeouts": 0, "wait_max": 30.0, "wait_over": 0,
               "crash_new": 0}
        row.update(over)
        rows.append(row)
    return rows


def test_evaluate_green_baseline_and_probe_noise_is_ignored():
    now = 1_800_000_000.0
    rows = _compact_rows(60, now)
    assert rt.evaluate(rows, now) == {"status": "GREEN", "reasons": []}
    assert rt.evaluate([], now)["status"] == "UNKNOWN"


def test_evaluate_amber_on_lock_timeouts_waits_and_crash_rows():
    now = 1_800_000_000.0
    rows = _compact_rows(60, now, timed_timeouts=1)
    out = rt.evaluate(rows, now)
    assert out["status"] == "AMBER" and "TRADE_LOCK_TIMED_TIMEOUTS_1H" in out["reasons"]
    rows = _compact_rows(60, now)
    rows[-5]["wait_max"] = 700.0
    rows[-4]["crash_new"] = 1
    reasons = rt.evaluate(rows, now)["reasons"]
    assert "TRADE_LOCK_WAIT_MAX_1H" in reasons and "CRASH_DUMP_NEW_ROWS_1H" in reasons


def test_evaluate_red_on_sustained_lag_or_stale_rows():
    now = 1_800_000_000.0
    rows = _compact_rows(10, now)
    for r in rows[-3:]:
        r["lag_max"] = 2500.0
    assert rt.evaluate(rows, now)["status"] == "RED"
    assert "TELEMETRY_STALE" in rt.evaluate(_compact_rows(5, now - 600), now)["reasons"]


def test_evaluate_amber_on_rss_growth_over_six_hours():
    now = 1_800_000_000.0
    rows = _compact_rows(360, now)
    rows[-1]["rss"] = 900_000_000
    assert "RSS_GROWTH_6H" in rt.evaluate(rows, now)["reasons"]


def test_status_summarises_last_hour(tmp_path):
    clock = _Clock()
    s = _sampler(tmp_path, clock, [_lock_counters()] * 3, [1.0, 2.0, 3.0, 4.0])
    assert s.status()["health"]["status"] == "UNKNOWN"
    for _ in range(3):
        clock.advance(60)
        s.sample_and_write()
    status = s.status()
    assert status["schema"] == rt.STATUS_SCHEMA and status["file"] == rt.FILE_NAME
    assert status["rows_this_process"] == 3 and status["last_hour"]["rows"] == 3
    assert status["latest"]["seq"] == 2
    assert status["health"]["status"] == "GREEN"


def test_rotation_never_deletes(tmp_path, monkeypatch):
    monkeypatch.setattr(rt, "ROTATE_BYTES", 10)
    clock = _Clock()
    s = _sampler(tmp_path, clock, [_lock_counters()] * 3, [1.0] * 4)
    for _ in range(3):
        clock.advance(60)
        s.sample_and_write()
    names = sorted(os.listdir(tmp_path))
    assert rt.FILE_NAME in names and f"{rt.FILE_NAME}.1" in names
    total = sum(1 for n in names for _ in open(tmp_path / n, encoding="utf-8"))
    assert total == 3


def test_thread_loop_writes_rows_and_stops(tmp_path):
    stop = threading.Event()
    s = rt.RuntimeTelemetry(str(tmp_path), boot_ts=time.time(), stop_event=stop,
                            interval_sec=0.05, lag_tick_sec=0.01)
    s.start()
    deadline = time.time() + 5
    while s.seq < 2 and time.time() < deadline:
        time.sleep(0.02)
    stop.set()
    s.join(2)
    assert not s.is_alive()
    rows = [json.loads(l) for l in open(tmp_path / rt.FILE_NAME, encoding="utf-8")]
    assert len(rows) >= 2 and rows[-1]["scheduler_lag"]["n"] >= 1


def test_telemetry_file_is_shipped_by_the_segment_shipper():
    assert rt.FILE_NAME not in rss.EXCLUDED_NAMES
    assert os.path.splitext(rt.FILE_NAME)[1] in rss.EXTENSIONS


def test_external_clock_offset_uses_rtt_midpoint(monkeypatch):
    import io, urllib.request
    import runtime_telemetry as rt

    class Resp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    clock = iter([100.0, 100.2, 100.3])
    monkeypatch.setattr(rt.time, "time", lambda: next(clock))
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: Resp(b"h=x\nts=100.05\n"))
    out = rt._probe_clock_offset_once(samples=1)
    assert out["offset_ms"] == 50.0 and out["rtt_ms"] == 200.0
