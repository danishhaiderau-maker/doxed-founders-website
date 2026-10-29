"""#808 tape_freeze: synthetic 1 s tape, monitoring only (no gating, nothing stored)."""
import json
import os

import monitor_streams as ms

NOW = 1_800_000_000.0
FILE = ms.STREAMS["book_tape_1s"]


def _row(ts, bid=80_000.0, ask=80_001.0, hi=None, lo=None, src=None, valid=True):
    return {"schema": "x", "bucket_ts": ts, "source_ts": ts - 0.3 if src is None else src, "bid": bid, "ask": ask,
            "valid_bbo": valid, "fresh": valid, "trade_high": hi, "trade_low": lo, "last": bid}


def _write(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_frozen_run_with_outside_prints_counted(tmp_path):
    t0 = NOW - 100
    rows = [_row(t0 + i, bid=80_000 + i, ask=80_001 + i) for i in range(10)]          # moving quote
    frozen_q = dict(bid=80_010.0, ask=80_011.0)
    out_hi = 80_011.0 * (1 + 3.0 / 1e4)                                              # 3 bp > 2.5 bp above ask
    rows += [_row(t0 + 10 + i, **frozen_q, hi=out_hi, src=t0 + 9.7) for i in range(5)]  # quote stuck, trades through
    _write(tmp_path / FILE, rows)
    r = ms.tape_freeze(tmp_path, NOW, window_s=200)
    assert r["reason"] is None and r["rows_seen"] == 15 and r["valid_rows"] == 15 and r["compared_pairs"] == 14
    assert r["outside_print_count"] == 5
    # first frozen-quote row vs the last moving row: quote changed, so 4 frozen seconds, run 4
    assert r["frozen_s"] == 4 and r["longest_frozen_run_s"] == 4 and r["longest_unchanged_run_s"] == 4
    assert r["unchanged_bbo_pct"] == round(100 * 4 / 14, 3)
    assert r["source_ts_unchanged_pct"] == round(100 * 4 / 14, 3)
    assert r["frozen_s_per_h"] == round(4 * 3600 / 14, 2)
    assert r["truncated"] is False and r["coverage"] == round(15 / 200, 4) and r["monitoring_only"] is True




def test_threshold_is_a_named_bp_constant(tmp_path):
    assert ms.TAPE_FREEZE_OUTSIDE_BP == 2.5
    t0 = NOW - 50
    inside = 80_001.0 * (1 + 2.0 / 1e4)
    _write(tmp_path / FILE, [_row(t0 + i, hi=inside, lo=80_000.0 * (1 - 2.0 / 1e4)) for i in range(5)])
    r = ms.tape_freeze(tmp_path, NOW, window_s=60)
    assert r["outside_print_count"] == 0 and r["frozen_s"] == 0 and r["unchanged_bbo_pct"] == 100.0
    assert r["longest_unchanged_run_s"] == 4 and r["longest_frozen_run_s"] == 0
    # source_ts moves every row -> ticker kept sending the same quote, not "no message"
    assert r["source_ts_unchanged_pct"] == 0.0


def test_changing_bbo_counts_zero(tmp_path):
    t0 = NOW - 30
    _write(tmp_path / FILE, [_row(t0 + i, bid=80_000 + i, ask=80_001 + i, hi=90_000.0) for i in range(20)])
    r = ms.tape_freeze(tmp_path, NOW, window_s=60)
    assert r["unchanged_bbo_pct"] == 0.0 and r["frozen_s"] == 0 and r["outside_print_count"] == 20


def test_invalid_bbo_rows_excluded_and_break_runs(tmp_path):
    t0 = NOW - 30
    rows = [_row(t0 + i, hi=90_000.0) for i in range(3)]
    rows += [_row(t0 + 3, valid=False, hi=90_000.0), _row(t0 + 4, bid=None, ask=None)]
    rows += [_row(t0 + 5 + i, hi=90_000.0) for i in range(3)]
    _write(tmp_path / FILE, rows)
    r = ms.tape_freeze(tmp_path, NOW, window_s=60)
    assert r["rows_seen"] == 8 and r["valid_rows"] == 6 and r["compared_pairs"] == 4
    assert r["frozen_s"] == 4 and r["longest_frozen_run_s"] == 2 and r["outside_print_count"] == 6


def test_gap_rows_are_not_compared(tmp_path):
    _write(tmp_path / FILE, [_row(NOW - 30), _row(NOW - 20), _row(NOW - 10)])
    r = ms.tape_freeze(tmp_path, NOW, window_s=60)
    assert r["valid_rows"] == 3 and r["compared_pairs"] == 0 and r["unchanged_bbo_pct"] is None
    assert r["reason"] == "NO_COMPARABLE_PAIRS"


def test_rotated_window_counted_across_parts(tmp_path):
    t0 = NOW - 40
    _write(tmp_path / (FILE + ".1"), [_row(t0 + i, hi=90_000.0) for i in range(10)])           # older part
    _write(tmp_path / FILE, [_row(t0 + 10 + i, hi=90_000.0) for i in range(10)])               # current part
    r = ms.tape_freeze(tmp_path, NOW, window_s=60, page_bytes=600)                              # several pages
    assert r["rows_seen"] == 20 and r["compared_pairs"] == 19 and r["frozen_s"] == 19
    assert r["longest_frozen_run_s"] == 19 and r["truncated"] is False


def test_rows_outside_window_ignored(tmp_path):
    _write(tmp_path / FILE, [_row(NOW - 500 + i) for i in range(5)] + [_row(NOW - 5 + i) for i in range(3)])
    r = ms.tape_freeze(tmp_path, NOW, window_s=60)
    assert r["rows_seen"] == 3


def test_byte_cap_sets_truncated(tmp_path):
    t0 = NOW - 3000
    _write(tmp_path / FILE, [_row(t0 + i) for i in range(2000)])
    size = os.path.getsize(tmp_path / FILE)
    r = ms.tape_freeze(tmp_path, NOW, window_s=3600, max_bytes=size // 4, page_bytes=4096)
    assert r["truncated"] is True and r["bytes_read"] <= size // 4 and 0 < r["rows_seen"] < 2000
    full = ms.tape_freeze(tmp_path, NOW, window_s=3600)
    assert full["truncated"] is False and full["rows_seen"] == 2000 and full["bytes_read"] == size


def test_missing_or_empty_tape_is_null_with_reason(tmp_path):
    keys = ("unchanged_bbo_pct", "source_ts_unchanged_pct", "frozen_s", "frozen_s_per_h", "longest_frozen_run_s",
            "outside_print_count")
    r = ms.tape_freeze(tmp_path, NOW)
    assert r["reason"] == "TAPE_MISSING" and all(r[k] is None for k in keys)
    (tmp_path / FILE).write_text("", encoding="utf-8")
    r = ms.tape_freeze(tmp_path, NOW)
    assert r["reason"] == "TAPE_EMPTY_IN_WINDOW" and all(r[k] is None for k in keys)
    _write(tmp_path / FILE, [_row(NOW - 5, valid=False)])
    r = ms.tape_freeze(tmp_path, NOW)
    assert r["reason"] == "NO_VALID_BBO_ROWS" and all(r[k] is None for k in keys)


def test_never_raises_and_stores_nothing(tmp_path, monkeypatch):
    _write(tmp_path / FILE, [_row(NOW - 5 + i) for i in range(3)])
    before = sorted(p.name for p in tmp_path.iterdir())
    ms.tape_freeze(tmp_path, NOW)
    assert sorted(p.name for p in tmp_path.iterdir()) == before

    def boom(*a, **k):
        raise OSError("disk")
    monkeypatch.setattr(ms, "read_range", boom)
    r = ms.tape_freeze(tmp_path, NOW)
    assert r["reason"] == "ERROR:OSError" and r["frozen_s"] is None


def test_health_includes_tape_freeze_and_accepts_cached_value(tmp_path):
    _write(tmp_path / FILE, [_row(NOW - 5 + i) for i in range(3)])
    h = ms.health(tmp_path, NOW)
    assert h["tape_freeze"]["schema"] == "tape_freeze_v1" and h["tape_freeze"]["rows_seen"] == 3
    sentinel = {"schema": "tape_freeze_v1", "cached": True}
    assert ms.health(tmp_path, NOW, tape_freeze_result=sentinel)["tape_freeze"] is sentinel


def test_route_caches_are_separate(tmp_path, monkeypatch):
    import bot
    _write(tmp_path / FILE, [_row(NOW - 5 + i) for i in range(3)])
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(bot, "_DASHBOARD_BOOTSTRAP_COMPLETE", True)
    monkeypatch.setattr(bot, "_API_RATE_MAX_PER_IP", 10_000)
    monkeypatch.setattr(bot, "monitor_integrity_authorized", lambda: True)
    monkeypatch.setattr(bot, "_STREAM_HEALTH_CACHE", {})
    monkeypatch.setattr(bot, "_TAPE_FREEZE_CACHE", {})
    calls = {"health": 0, "tape": 0}
    real_health, real_tape = ms.health, ms.tape_freeze

    def health(*a, **k):
        calls["health"] += 1
        return real_health(*a, **k)

    def tape(*a, **k):
        calls["tape"] += 1
        return real_tape(*a, **k)
    monkeypatch.setattr(ms, "health", health)
    monkeypatch.setattr(ms, "tape_freeze", tape)
    clock = {"t": NOW}
    monkeypatch.setattr(bot.time, "time", lambda: clock["t"])
    client = bot.app.test_client()
    body = client.get("/api/monitor/streams/health").get_json()
    assert "tape_freeze" in body and body["tape_freeze"]["cached_at"] == NOW and calls == {"health": 1, "tape": 1}
    clock["t"] = NOW + 10                                  # inside both caches
    client.get("/api/monitor/streams/health")
    assert calls == {"health": 1, "tape": 1}
    clock["t"] = NOW + 20                                  # 15 s health cache expired, 300 s tape cache still warm
    body = client.get("/api/monitor/streams/health").get_json()
    assert calls == {"health": 2, "tape": 1} and body["cached_at"] == NOW + 20 and body["tape_freeze"]["cached_at"] == NOW
    clock["t"] = NOW + 301                                 # tape cache expired too
    client.get("/api/monitor/streams/health")
    assert calls == {"health": 3, "tape": 2}


def test_tape_freeze_is_monitoring_only():
    """No trading module reads tape_freeze; it lives in monitor_streams and the health route only."""
    import pathlib
    here = pathlib.Path(__file__).parent
    users = sorted(p.name for p in here.glob("*.py")
                   if not p.name.startswith("test_") and "tape_freeze" in p.read_text(encoding="utf-8", errors="ignore"))
    assert users == ["bot.py", "monitor_streams.py"]
    src = (here / "bot.py").read_text(encoding="utf-8")
    assert src.count("tape_freeze") == src.count("tape_freeze", src.index("@app.route('/api/monitor/streams/health'"))
