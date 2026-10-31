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


def test_r17_future_path_loop_is_not_expected():
    """R17 retired the worker; expecting it caused false RED (NOT_STARTED)."""
    name = "all_opportunity_future_path_evidence_loop"
    assert name not in mg.WORKER_EXPECTATIONS
    # Function remains for historical/tests; main must not start a Thread on it.
    tree = ast.parse((ROOT / "bot.py").read_text(encoding="utf-8"))
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    started = set()
    for node in ast.walk(main):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "Thread":
            target = next((kw.value for kw in node.keywords if kw.arg == "target"), None)
            if isinstance(target, ast.Call) and getattr(target.func, "id", None) == "safe_thread":
                target = target.args[0]
            if isinstance(target, ast.Name):
                started.add(target.id)
    assert name not in started
    assert any(
        isinstance(n, ast.FunctionDef) and n.name == name for n in tree.body
    ), "loop function must remain for historical/tests"


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
    assert "live-eligible switch ON: 1 (T3 GS-01)" in text and "ROUTER" not in text and "eight" not in text
    assert "live arming state unknown" in text and "live copy ON" not in text
    off = mg.build_features_text(tiles, {"effective_live": False, "force_paper_mode": True})
    assert "live copy DISARMED (force_paper)" in off and "live copy ON" not in off
    assert "live copy ARMED" in mg.build_features_text(tiles, {"effective_live": True})
    # T6/T7 style rows: paper OFF but live-eligible switch ON must read as eligibility, never as live ON
    t67 = [{"lane": "D", "number": 6, "label": "DISLOC", "retired": False, "paper_on": False, "live_orders_on": True}]
    msg = mg.build_features_text(t67, {"effective_live": False})
    assert "paper ON: 0 (none)" in msg and "live-eligible switch ON: 1 (T6 DISLOC)" in msg and "DISARMED" in msg


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
    # One drop while open is WS jitter — stay GREEN (false-amber flap fix).
    d.drop(True)
    snap = d.snapshot()
    assert snap["dropped_while_open"] == 1 and snap["level"] == "GREEN"
    assert snap["drops_last_min_while_open"] == 1
    assert snap["alert_min_drops_while_open"] == mg.DropRate.ALERT_MIN_DROPS_WHILE_OPEN
    d.drop(True)
    assert d.snapshot()["level"] == "GREEN"
    d.drop(True)
    snap = d.snapshot()
    assert snap["drops_last_min_while_open"] == 3 and snap["level"] == "AMBER"
    assert snap["alerts"] == ["EXIT_TICK_DROPS_WHILE_OPEN"]
    clock.t += 120
    assert d.snapshot()["drops_last_min_while_open"] == 0 and d.snapshot()["level"] == "GREEN"


def test_drop_rate_alert_min_mutation_still_gates_burst():
    """Mutation proof: alert_min=1 must AMBER on the first while-open drop."""
    clock = Clock()
    d = mg.DropRate(clock=clock, alert_min_drops=1)
    d.drop(True)
    assert d.snapshot()["level"] == "AMBER"


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
    out = c.observe({"laptop_acked_seq": 6511, "shipped_seq": 6571, "custody_through_seq": 6511, "prune_mode": "dry_run"})
    # BTC-480: lag alone is not an alert (no AMBER ladder); prune_mode is never read
    assert out["ack_lag_segments"] == 60 and out["custody_lag_segments"] == 60
    assert out["alerts"] == [] and out["level"] == "GREEN"
    assert out["prune_mode"] == "off"
    # caught up on custody: GREEN even if laptop ACK is somehow behind
    assert c.observe({"laptop_acked_seq": 10, "shipped_seq": 100, "custody_through_seq": 100})["level"] == "GREEN"


def test_new_routes_are_monitor_token_readable():
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    start = src.index("_MONITOR_TILE_PATHS = frozenset({")
    block = src[start:src.index("})", start)]
    for path in ("/api/monitor/workers", "/api/monitor/fill-sim", "/api/monitor/protective-exit",
                 "/api/monitor/tiles/switches", "/api/monitor/config", "/api/monitor/preentry",
                 "/api/monitor/exit-queues", "/api/monitor/counters", "/api/monitor/warmup",
                 "/api/monitor/regime", "/api/monitor/shipper", "/api/monitor/gaps", "/api/monitor/integrity-gaps",
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


def test_arming_word_is_unknown_unless_state_is_clean_boolean():
    tiles = [{"lane": "G", "number": 3, "label": "GS-01", "retired": False, "paper_on": True, "live_orders_on": True}]
    word = lambda st: mg.build_features_text(tiles, st)
    assert "live copy DISARMED" in word({"effective_live": False})
    assert "live copy ARMED" in word({"effective_live": True})
    # website unreachable / error key / not wired / non-boolean / missing: unknown, never DISARMED
    for bad in ({"effective_live": False, "blockers": ["WEBSITE_UNREACHABLE"]},
                {"effective_live": False, "blockers": ["website_unreachable", "X"]},
                {"effective_live": False, "error": "TimeoutError"},
                {"effective_live": False, "error": "MONITOR_NOT_WIRED"},
                {"effective_live": None}, {"effective_live": "False"}, {}, None):
        text = word(bad)
        assert "live arming state unknown" in text and "DISARMED" not in text and "ARMED" not in text.replace("DISARMED", ""), bad


def test_c5_custody_seq_stall_single_red_after_2h_keyed_on_shipped_minus_custody():
    import time as _t
    now = 2_000_000_000.0
    iso = lambda ts: _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime(ts))
    # Fresh observer each call so bootstrap uses custody_received_at as the stall clock.
    base = {"laptop_acked_seq": 9542, "shipped_seq": 9542, "custody_through_seq": 5093}
    for minutes in (5, 30, 65, 119):     # lag with recent custody advance stays GREEN
        c = mg.CustodyLag(clock=lambda: now)
        out = c.observe({**base, "custody_received_at": iso(now - minutes * 60)})
        assert out["alerts"] == [] and out["level"] == "GREEN" and out["custody_stall_reason"] is None, minutes
        assert out["custody_lag_segments"] == 9542 - 5093
    c = mg.CustodyLag(clock=lambda: now)
    out = c.observe({**base, "custody_received_at": iso(now - 2 * 3600 - 60)})
    assert out["alerts"] == [mg.SHIPPED_MINUS_CUSTODY_LAG] and out["level"] == "RED"  # single RED
    assert out["custody_last_advanced_utc"] == iso(now - 2 * 3600 - 60)
    assert out["custody_stall_red"] is True and out["laptop_ack_stall_red"] is True
    assert out["custody_stall_reason"] == mg.SHIPPED_MINUS_CUSTODY_LAG
    # custody caught up with shipped: never stalled, however old the receipt
    c = mg.CustodyLag(clock=lambda: now)
    out = c.observe({"laptop_acked_seq": 1153, "shipped_seq": 1153, "custody_through_seq": 1153,
                     "custody_received_at": iso(now - 9 * 3600)})
    assert out["alerts"] == [] and out["level"] == "GREEN"
    # false-GREEN proof shape: laptop ACK current while custody stuck → RED
    c = mg.CustodyLag(clock=lambda: now)
    out = c.observe({"laptop_acked_seq": 9542, "shipped_seq": 9542, "custody_through_seq": 5093,
                     "laptop_ack_advanced_at": iso(now - 60),
                     "custody_received_at": iso(now - 6 * 24 * 3600)})
    assert out["level"] == "RED" and out["custody_stall_reason"] == mg.SHIPPED_MINUS_CUSTODY_LAG
    assert out["custody_lag_segments"] == 9542 - 5093


def test_c5_custody_stall_ignores_prune_mode_and_laptop_ack_clock():
    import time as _t
    now = 2_000_000_000.0
    iso = lambda ts: _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime(ts))
    for mode in ("off", "dry_run", "enforce", None):
        s = mg.custody_stall({"custody_through_seq": 5093, "shipped_seq": 9542, "prune_mode": mode,
                              "custody_received_at": iso(now - 3 * 3600)}, now)
        assert s["custody_stall_reason"] == mg.SHIPPED_MINUS_CUSTODY_LAG, mode
    # a fresh laptop ACK clock alone must not clear a stalled custody seq
    s = mg.custody_stall({"custody_through_seq": 5093, "shipped_seq": 9542, "laptop_acked_seq": 9542,
                          "laptop_ack_advanced_at": iso(now - 30),
                          "custody_received_at": iso(now - 9 * 3600)}, now)
    assert s["custody_stall_reason"] == mg.SHIPPED_MINUS_CUSTODY_LAG
    assert s["custody_lag_segments"] == 9542 - 5093
    # without custody_through_seq there is no shipped-minus-custody lag alert
    s = mg.custody_stall({"laptop_acked_seq": 1, "shipped_seq": 9, "custody_received_at": iso(now - 9 * 3600)}, now)
    assert s["custody_stall_reason"] is None and s["custody_lag_segments"] is None
    assert mg.custody_stall({"shipped_seq": 3, "last_error": "boom"}, 1000.0)["custody_last_error"] == "SHIPPER: boom"


def test_c5_stall_clock_advances_when_custody_seq_increases():
    import time as _t
    clock = Clock(t=2_000_000_000.0)
    iso = lambda ts: _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime(ts))
    c = mg.CustodyLag(clock=clock)
    first = c.observe({"shipped_seq": 6000, "custody_through_seq": 5093,
                       "custody_received_at": iso(clock.t - 9 * 3600)})
    assert first["level"] == "RED" and first["custody_stall_reason"] == mg.SHIPPED_MINUS_CUSTODY_LAG
    clock.t += 60
    # custody seq advances → stall clock resets; still behind but fresh → GREEN
    second = c.observe({"shipped_seq": 6000, "custody_through_seq": 5200,
                        "custody_received_at": iso(clock.t - 9 * 3600)})
    assert second["level"] == "GREEN" and second["custody_stall_reason"] is None
    assert second["custody_lag_segments"] == 800
    clock.t += 2 * 3600 + 120
    # same custody seq for >2h while shipped ahead → RED again
    third = c.observe({"shipped_seq": 6100, "custody_through_seq": 5200,
                       "custody_received_at": iso(clock.t - 9 * 3600)})
    assert third["level"] == "RED" and third["alerts"] == [mg.SHIPPED_MINUS_CUSTODY_LAG]


def test_c5_shipper_records_ack_advance_time(tmp_path):
    ship = (ROOT / "research_segment_shipper.py").read_text(encoding="utf-8")
    assert "laptop_ack_advanced_at" in ship
    src = (ROOT / "monitor_gaps.py").read_text(encoding="utf-8")
    body = src[src.index("def custody_stall("):src.index("class CustodyLag")]
    impl = body.split('"""', 2)[2]
    assert "prune_mode" not in impl  # never keyed on prune_mode (docstring aside)
    assert "laptop_ack_advanced_at" not in impl  # stall clock is custody seq, not laptop ACK
    assert "custody_through_seq" in impl and "custody_received_at" in impl


def test_status_shipper_carries_custody_stall_fields():
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    body = src[src.index("def _volume_transfer_snapshot"):src.index("def _volume_health_snapshot")]
    assert "_mg_custody.custody_stall(transfer, now)" in body
    assert "resolve_live_custody_for_lag" in body
    assert "apply_live_custody_to_transfer" in body
    assert "custody_through_seq_status" in body
    # T5: lag path must never call /head or research_segment_server for custody.
    impl = "".join(body.split('"""')[0::2])  # drop docstrings
    assert "research_segment_server" not in impl
    assert "._head" not in impl and " /head" not in impl and '"/head"' not in impl
    fields = src[src.index("_SEGMENT_STATUS_FIELDS = ("):][:1500]
    assert '"laptop_ack_advanced_at"' in fields and '"shipped_seq"' in fields and '"laptop_acked_seq"' in fields


def test_t1_frozen_status_5093_does_not_keep_lag_red_when_live_caught_up():
    """T1: status custody_through_seq=5093, live ≈ shipped → SHIPPED_MINUS_CUSTODY_LAG OFF."""
    import time as _t
    now = 2_000_000_000.0
    iso = lambda ts: _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime(ts))
    live = mg.resolve_live_custody_for_lag(
        "/unused", "v3-w1792263671",
        reader=lambda *_: {"through_seq": 9919, "received_at": iso(now - 60)},
    )
    assert live["ok"] is True and live["through_seq"] == 9919
    transfer = mg.apply_live_custody_to_transfer(
        {"shipped_seq": 9919, "custody_through_seq": 5093, "laptop_acked_seq": 9919,
         "custody_received_at": iso(now - 9 * 3600)},
        live,
    )
    assert transfer["custody_through_seq_status"] == 5093
    assert transfer["custody_through_seq"] == 9919
    assert transfer["custody_source"] == "live"
    c = mg.CustodyLag(clock=lambda: now)
    out = c.observe(transfer)
    assert out["alerts"] == [] and out["level"] == "GREEN"
    assert out["custody_stall_red"] is False and out["laptop_ack_stall_red"] is False
    assert out["custody_stall_reason"] is None


def test_t2_live_caught_up_clears_stall_even_after_2h_wall_clock():
    """T2: live equals shipped; status still 5093; wall ≥2h since live advance → still GREEN."""
    import time as _t
    now = 2_000_000_000.0
    iso = lambda ts: _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime(ts))
    live = mg.resolve_live_custody_for_lag(
        "/unused", "v3-w1792263671",
        reader=lambda *_: {"through_seq": 9919, "received_at": iso(now - 2 * 3600 - 60)},
    )
    transfer = mg.apply_live_custody_to_transfer(
        {"shipped_seq": 9919, "custody_through_seq": 5093,
         "custody_received_at": iso(now - 9 * 3600)},
        live,
    )
    c = mg.CustodyLag(clock=lambda: now)
    out = c.observe(transfer)
    assert out["level"] == "GREEN" and out["custody_stall_red"] is False
    assert out["laptop_ack_stall_red"] is False
    assert out["custody_stall_reason"] is None
    # Mutation: lag still reading status 5093 only → would stay RED after 2h.
    mutant = mg.CustodyLag(clock=lambda: now)
    bad = mutant.observe({"shipped_seq": 9919, "custody_through_seq": 5093,
                          "custody_received_at": iso(now - 2 * 3600 - 60)})
    assert bad["level"] == "RED" and bad["custody_stall_reason"] == mg.SHIPPED_MINUS_CUSTODY_LAG


def test_t2b_live_custody_advance_resets_stall_clock():
    """T2b (HM non-blocking): a live custody advance resets the stall clock."""
    import time as _t
    clock = Clock(t=2_000_000_000.0)
    iso = lambda ts: _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime(ts))
    c = mg.CustodyLag(clock=clock)
    # Status frozen at 5093; live starts behind and stalled → RED.
    first_live = mg.apply_live_custody_to_transfer(
        {"shipped_seq": 6000, "custody_through_seq": 5093,
         "custody_received_at": iso(clock.t - 9 * 3600)},
        {"ok": True, "through_seq": 5093, "received_at": iso(clock.t - 9 * 3600), "source": "live"},
    )
    first = c.observe(first_live)
    assert first["level"] == "RED" and first["custody_stall_reason"] == mg.SHIPPED_MINUS_CUSTODY_LAG
    clock.t += 60
    # Live custody advances (still behind shipped) → stall clock resets → GREEN.
    second_live = mg.apply_live_custody_to_transfer(
        {"shipped_seq": 6000, "custody_through_seq": 5093,
         "custody_received_at": iso(clock.t - 9 * 3600)},
        {"ok": True, "through_seq": 5200, "received_at": iso(clock.t - 9 * 3600), "source": "live"},
    )
    second = c.observe(second_live)
    assert second["level"] == "GREEN" and second["custody_stall_reason"] is None
    assert second["custody_lag_segments"] == 800
    clock.t += 2 * 3600 + 120
    third_live = mg.apply_live_custody_to_transfer(
        {"shipped_seq": 6100, "custody_through_seq": 5093,
         "custody_received_at": iso(clock.t - 9 * 3600)},
        {"ok": True, "through_seq": 5200, "received_at": iso(clock.t - 9 * 3600), "source": "live"},
    )
    third = c.observe(third_live)
    assert third["level"] == "RED" and third["alerts"] == [mg.SHIPPED_MINUS_CUSTODY_LAG]


def test_t3_real_live_lag_stays_red_after_2h():
    """T3: live still far behind shipped + stall >2h → RED stays (no masking)."""
    import time as _t
    now = 2_000_000_000.0
    iso = lambda ts: _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime(ts))
    transfer = mg.apply_live_custody_to_transfer(
        {"shipped_seq": 9919, "custody_through_seq": 5093,
         "custody_received_at": iso(now - 9 * 3600)},
        {"ok": True, "through_seq": 7000, "received_at": iso(now - 3 * 3600), "source": "live"},
    )
    c = mg.CustodyLag(clock=lambda: now)
    out = c.observe(transfer)
    assert out["level"] == "RED" and out["custody_stall_reason"] == mg.SHIPPED_MINUS_CUSTODY_LAG
    assert out["custody_lag_segments"] == 9919 - 7000


def test_t4_t6_live_custody_unavailable_is_red_never_5093():
    """T4/T6: live reader raises/None/unusable → RED CUSTODY_SOURCE_UNAVAILABLE; never 5093."""
    now = 2_000_000_000.0

    def boom(*_a, **_k):
        raise RuntimeError("store down")

    for reader in (boom, lambda *_: None, lambda *_: {"through_seq": "bad"}, lambda *_: {}):
        live = mg.resolve_live_custody_for_lag("/unused", "v3-w1792263671", reader=reader)
        assert live["ok"] is False and live["error"] == mg.CUSTODY_SOURCE_UNAVAILABLE
        transfer = mg.apply_live_custody_to_transfer(
            {"shipped_seq": 9919, "custody_through_seq": 5093}, live)
        assert transfer["custody_through_seq"] is None  # never status 5093
        assert transfer["custody_through_seq_status"] == 5093
        assert transfer["custody_source"] == "unavailable"
        c = mg.CustodyLag(clock=lambda: now)
        out = c.observe(transfer)
        assert out["level"] == "RED"
        assert out["custody_stall_reason"] == mg.CUSTODY_SOURCE_UNAVAILABLE
        assert out["custody_stall_red"] is True and out["laptop_ack_stall_red"] is True
        assert out["alerts"] == [mg.CUSTODY_SOURCE_UNAVAILABLE]
    # Mutation: force GREEN / substitute 5093 when source down → must fail T4/T6 contract.
    mutant = mg.apply_live_custody_to_transfer(
        {"shipped_seq": 9919, "custody_through_seq": 5093},
        {"ok": False, "error": mg.CUSTODY_SOURCE_UNAVAILABLE, "source": "unavailable"},
    )
    assert mutant["custody_through_seq"] is None  # must not be 5093
    assert mg.custody_stall(mutant, now)["custody_stall_reason"] == mg.CUSTODY_SOURCE_UNAVAILABLE


def test_t5_lag_helpers_never_read_head_custody():
    """T5: shared lag helpers must not wire to /head custody_through_seq."""
    src = (ROOT / "monitor_gaps.py").read_text(encoding="utf-8")
    start = src.index("def resolve_live_custody_for_lag")
    end = src.index("def custody_stall")
    body = src[start:end]
    impl = "".join(body.split('"""')[0::2])  # drop docstrings (may mention /head as forbidden)
    assert "research_segment_server" not in impl
    assert "._head" not in impl and '"/head"' not in impl and " /head" not in impl
    assert "_default_latest_custody" in src and "latest_custody" in src
    # Mutation: a helper that returns status/head custody_through_seq as lag truth fails T1/T2.
    live = mg.resolve_live_custody_for_lag(
        "/x", "p", reader=lambda *_: {"through_seq": 9919})
    t = mg.apply_live_custody_to_transfer({"custody_through_seq": 5093, "shipped_seq": 9919}, live)
    assert t["custody_through_seq"] == 9919 and t["custody_through_seq_status"] == 5093
