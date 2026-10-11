"""API-GAP-SWEEP-20261012: pure helpers behind the new /api/monitor/* gap routes."""
import ast
import json
from pathlib import Path

import monitor_gaps as mg
import thread_health

ROOT = Path(__file__).resolve().parent


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def test_every_main_worker_has_an_expectation():
    tree = ast.parse((ROOT / "bot.py").read_text(encoding="utf-8"))
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    names = set()
    for node in ast.walk(main):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "Thread":
            target = next((kw.value for kw in node.keywords if kw.arg == "target"), None)
            if isinstance(target, ast.Call) and getattr(target.func, "id", None) == "safe_thread":
                target = target.args[0]
            if isinstance(target, ast.Name):
                names.add(target.id)
    names -= {"run_flask", *mg.ONESHOT_WORKERS}
    missing = sorted(n for n in names if n not in mg.WORKER_EXPECTATIONS)
    assert missing == [], missing
    assert "xvl_evaluator_thread" in mg.WORKER_EXPECTATIONS


def test_worker_rows_flag_stale_dead_restart_unmonitored_and_failed_oneshot():
    clock = Clock()
    reg = thread_health.ThreadHealthRegistry(clock=clock)
    reg.register("ok", 1.0)
    reg.register("stale", 1.0, min_stale_sec=5)
    reg.beat("ok")
    reg.beat("stale")
    reg.beat("unmon")
    clock.t += 10
    reg.beat("ok")
    reg.restart("ok", "RuntimeError: x")
    snap = mg.worker_rows(reg.snapshot(), {"retired_tile_boundary_worker": {"state": "FAILED"}},
                          expected=["ok", "stale", "never"], now=clock.t)
    rows = {r["name"]: r for r in snap["workers"]}
    assert "STALE" in rows["stale"]["alerts"]
    assert "RESTARTED_15M" in rows["ok"]["alerts"]
    assert "UNMONITORED" in rows["unmon"]["alerts"]
    assert "NOT_REGISTERED" in rows["never"]["alerts"]
    assert rows["retired_tile_boundary_worker"]["level"] == "RED"
    assert snap["level"] == "RED"


def test_unregister_drops_disabled_worker():
    reg = thread_health.ThreadHealthRegistry()
    reg.register("x", 1.0)
    reg.unregister("x")
    assert "x" not in reg.snapshot()


def test_fill_sim_counts_errors_and_outside_bbo():
    clock = Clock()
    c = mg.FillSimCounters(clock=clock)
    c.poll()
    c.maker_fill("L", 100.0, 99.9, 100.1)
    snap = c.snapshot()
    assert snap["level"] == "GREEN" and snap["touch_grid_polls"] == 1
    c.error(ValueError("boom"))
    c.maker_fill("L", 100.2, 99.9, 100.0)  # 20 bp outside
    snap = c.snapshot()
    assert snap["errors"] == 1 and snap["errors_5m"] == 1 and "boom" in snap["last_error"]
    assert snap["lanes"]["L"]["outside_bbo_gt_1bp"] == 1
    assert snap["level"] == "RED"
    clock.t += 400
    assert c.snapshot()["errors_5m"] == 0


def test_protective_coverage_uncovered_is_red_fallback_amber():
    red = mg.protective_coverage([{"trade_id": "a", "lane": "X", "covered": False, "reason": "NO_STOP_PRICE"}])
    assert red["level"] == "RED" and red["uncovered_count"] == 1
    amber = mg.protective_coverage([{"trade_id": "a", "lane": "X", "covered": True, "source": "FALLBACK"}], 1)
    assert amber["level"] == "AMBER" and amber["uncovered_count"] == 0
    assert mg.protective_coverage([])["level"] == "GREEN"


def test_tile_switch_disagreements_and_build_text():
    tiles = [
        {"lane": "R", "number": 8, "label": "ROUTER", "retired": True, "paper_on": False, "live_orders_on": False,
         "active_listed": False, "evaluations_since_off": 352},
        {"lane": "G", "number": 3, "label": "GS-01", "retired": False, "paper_on": True, "live_orders_on": True,
         "relay_eligible": True},
    ]
    out = mg.tile_switches(tiles)
    assert out["level"] == "RED" and "R:RETIRED_STILL_EVALUATED" in out["alerts"]
    text = mg.build_features_text(tiles)
    assert "live copy ON: 1 (T3 GS-01)" in text and "ROUTER" not in text and "eight" not in text


def test_config_redacts_secrets_and_url_credentials():
    env = {"BOT_ADMIN_TOKEN": "s3cret", "DATABASE_URL": "postgres://u:p@h/db", "FOO": "1"}
    out = mg.effective_config(["BOT_ADMIN_TOKEN", "DATABASE_URL", "FOO", "BAR"], env, {"BAR": "x"}, {"FOO": "2"})
    rows = {r["key"]: r for r in out["keys"]}
    assert rows["BOT_ADMIN_TOKEN"]["effective"]["redacted"] is True
    assert "s3cret" not in json.dumps(out)
    assert "u:p@" not in json.dumps(out)
    assert rows["BAR"]["source"] == "default"
    assert out["expected_mismatches"] == ["FOO"] and out["level"] == "AMBER"
    assert len(out["config_hash"]) == 16


def test_env_keys_in_source():
    src = 'a=os.getenv("A_B")\nb=os.environ.get(\'C\')\nc=os.environ["D"]'
    assert mg.env_keys_in_source(src) == ["A_B", "C", "D"]


def test_preentry_alerts():
    assert mg.preentry_level("DEGRADED", 130, 10, 0)[1] == ["PREENTRY_DEGRADED_GT_2M"]
    assert mg.preentry_level("OK", None, 61, 3)[1] == ["PREENTRY_PENDING_GT_60S_WITH_TRIGGERS"]
    assert mg.preentry_level("OK", None, 61, 0)[0] == "GREEN"
    clock = Clock()
    t = mg.DegradedTimer(clock=clock)
    assert t.observe(True) == 0.0
    clock.t += 150
    assert t.observe(True) == 150.0
    assert t.observe(False) is None


def test_drop_rate_while_open():
    clock = Clock()
    d = mg.DropRate(clock=clock)
    d.drop(False)
    assert d.snapshot()["level"] == "GREEN"
    d.drop(True)
    snap = d.snapshot()
    assert snap["dropped_while_open"] == 1 and snap["level"] == "AMBER"
    clock.t += 120
    assert d.snapshot()["drops_last_min_while_open"] == 0


def test_persisted_windows_survive_restart(tmp_path):
    clock = Clock(10_000.0)
    path = tmp_path / "sub" / "w.json"
    w = mg.PersistedWindows(str(path), clock=clock)
    w.add("ai_calls_ok")
    w.add("ai_calls_ok")
    assert w.flush(force=True)
    clock.t += 4000
    w2 = mg.PersistedWindows(str(path), clock=clock)
    snap = w2.snapshot()
    assert snap["windows"]["1h"]["window_complete"] is True
    assert snap["windows"]["24h"]["window_complete"] is False
    assert snap["windows"]["24h"]["counts"]["ai_calls_ok"] == 2
    assert snap["windows"]["1h"]["counts"]["ai_calls_ok"] == 0


def test_blind_time_accumulates_and_alerts(tmp_path):
    clock = Clock(1_000_000.0)
    b = mg.BlindTime(str(tmp_path / "b.json"), clock=clock)
    for _ in range(800):
        b.observe("GS01", True)
        clock.t += 5
    snap = b.snapshot()
    assert snap["tiles"]["GS01"]["blind_minutes_24h"] > 60
    assert snap["level"] == "AMBER"
    b.flush()
    again = mg.BlindTime(str(tmp_path / "b.json"), clock=clock).snapshot()
    assert again["tiles"]["GS01"]["blind_minutes_24h"] > 60


def test_regime_comparator():
    clock = Clock()
    c = mg.RegimeComparator(clock=clock)
    src = lambda a, b, ok, ts: {"state.regime": {"label": a}, "regime_bars_3m": {"label": b, "bar_ok": ok, "close_ts": ts},
                                "dyn_regime": {"label": "UNKNOWN"}}
    assert c.compare(src("TREND", "TREND", True, 1))["agree"] is True
    c.compare(src("TREND", "RANGE", True, 2))
    clock.t += 700
    out = c.compare(src("TREND", "RANGE", True, 3))
    assert "REGIME_SOURCES_DISAGREE_GT_10M" in out["alerts"]
    for ts in range(4, 9):
        out = c.compare(src(None, None, False, ts))
    assert "REGIME_BAR_NOT_OK_GT_3_BARS" in out["alerts"]


def test_custody_lag():
    clock = Clock()
    c = mg.CustodyLag(clock=clock)
    out = c.observe({"laptop_acked_seq": 6571, "custody_through_seq": 6511, "prune_mode": "dry_run",
                     "pruned_through_seq": 6400})
    assert out["custody_lag_segments"] == 60 and "CUSTODY_LAG_GT_30" in out["alerts"]
    assert out["prune_safe"] is True
    assert c.observe({"laptop_acked_seq": 10, "custody_through_seq": 10})["level"] == "GREEN"


def test_new_routes_are_monitor_token_readable():
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    start = src.index("_MONITOR_TILE_PATHS = frozenset({")
    block = src[start:src.index("})", start)]
    for path in ("/api/monitor/workers", "/api/monitor/fill-sim", "/api/monitor/protective-exit",
                 "/api/monitor/tiles/switches", "/api/monitor/config", "/api/monitor/preentry",
                 "/api/monitor/exit-queues", "/api/monitor/counters", "/api/monitor/warmup",
                 "/api/monitor/regime", "/api/monitor/shipper", "/api/monitor/gaps",
                 "/api/runtime/headroom", "/api/data_size", "/api/analyzer-mirror/status"):
        assert f'"{path}"' in block, path


def test_fill_sim_error_is_not_swallowed():
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    i = src.index("_poll_chase_offset_touch_grid(price, grid_bid, grid_ask, grid_bid_qty, grid_ask_qty)")
    window = src[i:i + 600]
    assert "except Exception:\n        pass" not in window
    assert "_FILL_SIM_COUNTERS.error(exc" in window


def test_protective_levels_never_none():
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    i = src.index("def _protective_exit_levels_ex(pos: dict):")
    body = src[i:src.index("def _protective_exit_levels(pos: dict):")]
    assert "return None" not in body and "FALLBACK" in body


def test_live_flags_snapshot_never_empty():
    import live_copy_monitor_api as api
    api._LIVE_FLAGS_CACHE.update({"ts": 0.0, "payload": None})
    saved = dict(api._CTX)
    try:
        api._CTX.clear()
        out = api.live_flags_snapshot()
        assert "effective_live" in out and out.get("error") == "MONITOR_NOT_WIRED"
    finally:
        api._CTX.clear(); api._CTX.update(saved)
