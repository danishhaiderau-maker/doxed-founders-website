"""Indicator engine sidecar: closed-bar rows, identity, restart continuity, rotation, live state."""

import json
import math
import os

import pytest

import cross_venue_tape as cvt
import indicator_edge_spec as spec
import indicator_engine as ie

T0 = 1790985600  # UTC midnight, bar boundary
TAPE = "market_microstructure_1s.jsonl"


def _price(sec):
    return 60000.0 + 40.0 * math.sin((sec - T0) / 900.0) + 0.002 * (sec - T0)


def _tape_line(sec):
    p = _price(sec)
    row = {"schema": "market_microstructure_1s_v1", "bucket_ts": sec, "fresh": True, "valid_bbo": True,
           "bid": round(p - 0.5, 2), "ask": round(p + 0.5, 2), "bid_qty": 1.0 + (sec % 3), "ask_qty": 1.5,
           "last": round(p, 2) if sec % 4 == 0 else None, "buy_qty": 0.01 * (sec % 5), "sell_qty": 0.01 * (sec % 3),
           "trade_count": 1 if sec % 4 == 0 else 0}
    return json.dumps(row) + "\n"


def _xv_line(minute):
    samples = {v: [{"sec": minute + i, "mid": _price(minute + i) + 5.0, "last": None, "buy": 0.01, "sell": 0.0,
                    "up": True, "imb5": 0.1, "imb20": -0.1} for i in range(60)] for v in ("binance", "bybit")}
    return json.dumps(cvt.encode_minute(minute, samples, [_price(minute + i) for i in range(60)])) + "\n"


class Feed:
    """Appends tape rows (visible at second + 1) and optional cross-venue rows (minute + 63)."""

    def __init__(self, root, with_xv=False):
        self.root = str(root)
        self.with_xv = with_xv
        self.tape_upto = None
        self.xv_upto = None

    def advance(self, now):
        start = T0 if self.tape_upto is None else self.tape_upto
        end = int(now) - 1
        if end > start:
            with open(os.path.join(self.root, TAPE), "a") as h:
                h.write("".join(_tape_line(s) for s in range(start, end)))
            self.tape_upto = end
        if self.with_xv:
            m = T0 if self.xv_upto is None else self.xv_upto
            with open(os.path.join(self.root, cvt.FILE_NAME), "a") as h:
                while m + 63 <= now:
                    h.write(_xv_line(m))
                    m += 60
            self.xv_upto = m


def run(engine, feed, start, end, step=2.0):
    now = start
    while now < end:
        now += step
        feed.advance(now)
        engine.clock = lambda now=now: now
        engine.tick(now)
    return now


def rows(root):
    path = os.path.join(str(root), spec.BAR_FILE)
    if not os.path.exists(path):
        return []
    with open(path) as h:
        return [json.loads(line) for line in h if line.strip()]


def start_engine(root, feed, at):
    feed.advance(at)
    engine = ie.Engine(str(root), clock=lambda: at)
    engine.warm_start()
    return engine


def test_engine_writes_one_identity_stamped_row_per_closed_bar(tmp_path):
    feed = Feed(tmp_path)
    warm_at = T0 + 3 * 3600 - 30
    engine = start_engine(tmp_path, feed, warm_at)
    assert engine.stats["warm"]["history_bars"] >= 55
    run(engine, feed, warm_at, warm_at + 1800)
    out = rows(tmp_path)
    assert len(out) == 10
    ts = [r["bar_ts"] for r in out]
    assert ts == list(range(ts[0], ts[0] + 10 * 180, 180)) and ts[0] % 180 == 0
    first = out[0]
    assert first["schema"] == spec.BAR_SCHEMA
    assert first["feature_set_version"] == spec.FEATURE_SET_VERSION
    assert first["feature_set_sha"] == spec.feature_set_sha()
    assert first["boot_id"] == engine.boot_id and first["boot_id"].startswith("ie-")
    assert first["bar_close_ts"] == first["bar_ts"] + 180
    assert list(first["f"]) == spec.feature_ids()
    assert first["late"] is False and first["bar"]["tape"] == "OK" and first["bar"]["fresh_sec"] == 180
    # Cross-venue / market-context streams are absent: they are not waited for.
    assert all(r["emitted_lag_sec"] <= ie.CLOSE_LAG_SEC + 2 * ie.POLL_SEC for r in out)
    assert first["health"]["xvenue"] == "MISSING"
    assert set(first["status_counts"]) == {"AVAILABLE", "WARMING_UP", "UNAVAILABLE"}
    for r in out:
        assert r["bar_close_ts"] <= r["ts"]  # never printed before the bar closed


def test_engine_waits_for_live_cross_venue_minutes_but_not_forever(tmp_path):
    feed = Feed(tmp_path, with_xv=True)
    warm_at = T0 + 2 * 3600 - 30
    engine = start_engine(tmp_path, feed, warm_at)
    run(engine, feed, warm_at, warm_at + 900)
    out = rows(tmp_path)
    assert out
    for r in out:
        # Last minute row of the bar lands at close + 3 s; the bar closes after it.
        assert ie.CLOSE_LAG_SEC <= r["emitted_lag_sec"] <= ie.CLOSE_LAG_SEC + 2 * ie.POLL_SEC + 1
        assert r["bar"]["xv"]["minutes"] == 3 and r["bar"]["xv"]["imb5"] == pytest.approx(0.1)
    assert engine.builder.xv_imb_seen == {"imb5": True, "imb20": True}
    assert out[-1]["f"]["BOOK_IMBALANCE@BN5:TREND"][3] == "A"


def test_restart_reproduces_the_continuous_series_and_flags_late_rows(tmp_path):
    a_dir, b_dir = tmp_path / "a", tmp_path / "b"
    a_dir.mkdir()
    b_dir.mkdir()
    warm_at = T0 + 3 * 3600 - 30
    fa, fb = Feed(a_dir), Feed(b_dir)
    a = start_engine(a_dir, fa, warm_at)
    run(a, fa, warm_at, warm_at + 3600)
    b = start_engine(b_dir, fb, warm_at)
    stop_at = run(b, fb, warm_at, warm_at + 1200)
    restart_at = stop_at + 400  # down for two bar closes
    c = start_engine(b_dir, fb, restart_at)
    assert c.boot_id != b.boot_id and c.stats["warm"]["stored_bars"] > 0
    run(c, fb, restart_at, warm_at + 3600)
    ra = {r["bar_ts"]: r for r in rows(a_dir)}
    rb = {}
    for r in rows(b_dir):
        assert r["bar_ts"] not in rb, "a bar must never be printed twice"
        rb[r["bar_ts"]] = r
    assert set(ra) == set(rb)
    for ts in ra:
        assert rb[ts]["f"] == ra[ts]["f"], ts
        assert rb[ts]["bar"] == ra[ts]["bar"], ts
    late = [r for r in rb.values() if r["late"]]
    assert late and all(r["boot_id"] == c.boot_id for r in late)


def test_tape_rotation_loses_no_seconds(tmp_path):
    feed = Feed(tmp_path)
    warm_at = T0 + 2 * 3600 - 30
    engine = start_engine(tmp_path, feed, warm_at)
    now = run(engine, feed, warm_at, warm_at + 600)
    os.replace(os.path.join(tmp_path, TAPE), os.path.join(tmp_path, TAPE + ".1"))
    run(engine, feed, now, now + 900)
    out = rows(tmp_path)
    assert len(out) >= 7
    assert all(r["bar"]["fresh_sec"] == 180 for r in out)


def test_live_file_reports_progress_and_versions(tmp_path):
    feed = Feed(tmp_path)
    warm_at = T0 + 2 * 3600 - 30
    engine = start_engine(tmp_path, feed, warm_at)
    run(engine, feed, warm_at, warm_at + 400)
    with open(os.path.join(tmp_path, spec.LIVE_FILE)) as h:
        live = json.load(h)
    assert live["schema"] == spec.LIVE_SCHEMA
    assert live["feature_set_sha"] == spec.feature_set_sha()
    assert live["last_bar_ts"] == rows(tmp_path)[-1]["bar_ts"]
    assert live["stats"]["rows_written"] == len(rows(tmp_path))
    assert live["stats"]["compute_ms_last"] is not None


def test_file_tail_handles_partial_lines_and_rotation(tmp_path):
    path = tmp_path / "x.jsonl"
    path.write_bytes(b'{"a":1}\n{"a":')
    tail = ie.FileTail(str(path))
    assert tail.read_lines() == [b'{"a":1}']
    with open(path, "ab") as h:
        h.write(b'2}\n')
    assert tail.read_lines() == [b'{"a":2}']
    with open(path, "ab") as h:
        h.write(b'{"a":3}\n')
    os.replace(path, str(path) + ".1")
    path.write_bytes(b'{"a":4}\n')
    assert tail.read_lines() == [b'{"a":3}', b'{"a":4}']


def test_engine_and_core_import_no_bot_network_or_trading_module():
    import ast
    import indicator_engine_core as core
    allowed = {"__future__", "glob", "json", "os", "re", "signal", "sys", "threading", "time", "uuid", "math",
               "collections", "typing", "cross_venue_collector", "cross_venue_tape", "indicator_edge_spec",
               "indicator_engine_core", "data_epoch", "cross_venue_lead", "cross_venue_premium", "tape_minute_bars"}
    for module in (ie, core):
        with open(module.__file__, encoding="utf-8") as h:
            tree = ast.parse(h.read())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                names.add((node.module or "").split(".")[0])
        assert names <= allowed, (module.__name__, names - allowed)


def test_entrypoint_runs_the_engine_as_its_own_niced_single_thread_process():
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "fly-entrypoint.sh"), encoding="utf-8") as h:
        entry = h.read()
    block = entry[entry.index('if [ "${INDICATOR_ENGINE_ENABLED:-1}" = "1" ]'):]
    block = block[: block.index("\nfi\n")]
    assert "OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 nice -n 10 python /app/indicator_engine.py" in block
    assert "while true" in block and ") &" in block
    assert entry.index("indicator_engine.py") < entry.index("btc_conservative_agent.py on :7002")
