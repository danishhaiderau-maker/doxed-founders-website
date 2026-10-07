"""Shadow-exit recorder: pure path/exit math, registry ownership, bounded recorder, runtime wiring."""
import json
import time
from pathlib import Path

import pytest

import combo_pathway_config as registry
import shadow_exit_paths as sxp

ROOT = Path(__file__).resolve().parent
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
T0 = 1_791_000_000.0
ENTRY = 100_000.0


def _ticks(pnls_bp, *, direction="LONG", start=T0, step=1.0, spread=1.0):
    """Ticks whose executable exit side reproduces ``pnls_bp`` exactly."""
    out = []
    sign = 1 if direction == "LONG" else -1
    for i, pnl in enumerate(pnls_bp):
        px = ENTRY * (1 + sign * pnl / 1e4)
        bid, ask = (px, px + spread) if sign > 0 else (px - spread, px)
        out.append((start + i * step, bid, ask, px))
    return out


def _series(pnls_bp, step=1.0):
    return [(i * step, float(p)) for i, p in enumerate(pnls_bp)]


def _exit(rows, sid):
    return next(row for row in rows if row["id"] == sid)


# H-C carries the full catalog as card shadows (its live exit is time + stop only).
LANE = "FAMILY_PREMIUM_REVERSION_60M"
SET = registry.tile_shadow_exit_set(LANE)


def test_registry_catalog_translates_to_supported_recorder_kinds():
    assert sxp.validate_shadow_exit_set(SET) == ()
    assert registry.SHADOW_EXIT_KINDS <= sxp.SUPPORTED_KINDS
    assert set(registry.SHADOW_EXIT_FAMILY_KINDS) == {item["family"] for item in registry.SHADOW_EXIT_SET.values()}
    ids = [s["id"] for s in SET]
    assert set(registry.ALL_SHADOW_EXITS) <= set(ids)
    assert {"tile_stop_backstop_only", "hold_to_horizon"} <= set(ids)
    assert all(s.get("label") for s in SET)


def test_all_tiles_score_the_full_registry_catalog_under_their_own_guards():
    assert registry.validate_tile_registry() == ()
    for lane in registry.ACTIVE_TILE_ORDER:
        tile = registry.ACTIVE_TILE_REGISTRY[lane]
        specs = registry.tile_shadow_exit_set(lane)
        by_id = {s["id"]: s for s in specs}
        assert set(registry.ALL_SHADOW_EXITS) <= set(by_id)
        assert {k for k, s in by_id.items() if s["role"] == "CARD_SHADOW"} == set(tile["shadow_exits"])
        guard = registry._shadow_exit_guard(lane)
        assert guard["backstop_sec"] == tile["exit_policy"]["max_duration_sec"]
        assert all(s.get("backstop_sec") == guard["backstop_sec"] for s in specs if s["kind"] != "COMPOSITE")
        assert "hard_stop_bp" not in by_id["ATR_HARD_STOP_3ATR_25_60"]
        assert "hard_stop_bp" not in by_id["hold_to_horizon"]
        assert by_id["LATE_BE_20_5"]["hard_stop_bp"] == guard["hard_stop_bp"]
        assert "shadow_exit" not in json.dumps(tile["exit_policy"])
    fade = registry._shadow_exit_guard("FAMILY_COMMITTED_FADE_TAKER_90")
    assert fade == {"hard_stop_bp": 40.0, "backstop_sec": 5400}
    assert registry._shadow_exit_guard(LANE) == {"hard_stop_bp": 40.0, "backstop_sec": 3600}
    assert registry._shadow_exit_guard("NOT_A_TILE") == {
        "hard_stop_bp": registry.SHADOW_EXIT_DEFAULT_HARD_STOP_BP,
        "backstop_sec": registry.SHADOW_EXIT_DEFAULT_BACKSTOP_SEC}
    assert all(not s["role"] == "CARD_SHADOW" for s in registry.tile_shadow_exit_set("NOT_A_TILE"))


def test_registry_rejects_an_untranslatable_shadow_exit_family(monkeypatch):
    monkeypatch.setitem(registry.SHADOW_EXIT_SET, "BROKEN", {"family": "NOPE", "label": "x"})
    defects = registry.validate_tile_registry()
    assert f"{LANE}:INVALID_SHADOW_EXIT_SET" in defects


def test_trade_id_prefix_maps_to_its_tile_only():
    for lane in registry.ACTIVE_TILE_ORDER:
        prefix = registry.ACTIVE_TILE_REGISTRY[lane]["id_prefix"]
        assert registry.tile_lane_for_trade_id(f"{prefix}-abc123") == lane
    assert registry.tile_lane_for_trade_id("scan-123") is None
    assert registry.tile_lane_for_trade_id(None) is None


def test_exit_latency_matches_realistic_v1_fill_model():
    from research import fill_model

    assert sxp.EXIT_LATENCY_SEC == fill_model.EXIT_LATENCY_SEC


def test_exit_latency_and_stop_slip_telemetry_surface_only_on_triggers():
    pnls = [0, -5, -16, -20, -31, -32]
    rows = sxp.evaluate_shadow_exits(_series(pnls), SET, atr_bp=10.0, horizon_sec=5)
    stop = _exit(rows, "ATR_STOP_1.5")
    assert stop["triggered"]
    assert stop["exit_latency_sec"] >= sxp.EXIT_LATENCY_SEC
    assert stop["stop_slippage_bp"] >= 0.0
    # Non-triggered (time/horizon) exits carry no exit-latency telemetry.
    untrig = _exit(rows, "LATE_BE_25_3")
    assert not untrig["triggered"]
    assert "exit_latency_sec" not in untrig and "stop_slippage_bp" not in untrig


def test_late_breakeven_arms_then_books_worse_of_trigger_and_post_latency_mark():
    pnls = [0, 10, 21, 15, 7, 6, 4, -3, -4]
    rows = sxp.evaluate_shadow_exits(_series(pnls), SET, atr_bp=None, horizon_sec=8)
    be20 = _exit(rows, "LATE_BE_20_5")
    assert be20["triggered"] and be20["reason"] == "LATE_BREAKEVEN"
    assert be20["exit_t_sec"] == 7.0 and be20["net_bp"] == -3.0
    expected_role = "CARD_SHADOW" if "LATE_BE_20_5" in registry.ACTIVE_TILE_REGISTRY[LANE]["shadow_exits"] else "CATALOG"
    assert be20["mfe_before_exit_bp"] == 21.0 and be20["label"] and be20["role"] == expected_role
    assert sxp.is_giveback(be20)
    assert not _exit(rows, "LATE_BE_25_3")["triggered"]


def test_conditional_early_cut_requires_thesis_never_worked():
    cut = [0, -3, -6, -11, -12, -12]
    rows = sxp.evaluate_shadow_exits(_series(cut, 30.0), SET, atr_bp=None, horizon_sec=600)
    assert _exit(rows, "COND_CUT_10_3M_MFE2")["reason"] == "THESIS_WRONG_EARLY_CUT"
    worked = [0, 3, -6, -11, -12, -12]
    rows = sxp.evaluate_shadow_exits(_series(worked, 30.0), SET, atr_bp=None, horizon_sec=600)
    assert not _exit(rows, "COND_CUT_10_3M_MFE2")["triggered"]
    late = [0, -1, -1, -1, -1, -1, -1, -11, -12]
    rows = sxp.evaluate_shadow_exits(_series(late, 30.0), SET, atr_bp=None, horizon_sec=600)
    assert not _exit(rows, "COND_CUT_10_3M_MFE2")["triggered"]
    assert _exit(rows, "COND_CUT_12_5M_MFE2")["triggered"]


def test_atr_rules_trail_stop_clamp_and_are_unavailable_without_atr():
    pnls = [0, 10, 25, 32, 30, 22, 16, 15]
    rows = sxp.evaluate_shadow_exits(_series(pnls), SET, atr_bp=10.0, horizon_sec=7)
    trail = _exit(rows, "LATE_TRAIL_1.5ATR_ARM2ATR")
    assert trail["triggered"] and trail["reason"] == "LATE_ATR_TRAIL" and trail["net_bp"] == 15.0
    assert not _exit(rows, "LATE_TRAIL_2ATR_ARM2ATR")["triggered"]
    stop = sxp.evaluate_shadow_exits(_series([0, -5, -16, -20, -31, -32]), SET, atr_bp=10.0, horizon_sec=5)
    assert _exit(stop, "ATR_STOP_1.5")["exit_t_sec"] == 3.0
    assert _exit(stop, "ATR_HARD_STOP_3ATR_25_60")["reason"] == "ATR_HARD_STOP"
    assert _exit(stop, "ATR_HARD_STOP_3ATR_25_60")["exit_t_sec"] == 5.0
    floor = sxp.evaluate_shadow_exits(_series([0, -20, -26, -27]), SET, atr_bp=5.0, horizon_sec=3)
    assert _exit(floor, "ATR_HARD_STOP_3ATR_25_60")["exit_t_sec"] == 3.0
    none = sxp.evaluate_shadow_exits(_series(pnls), SET, atr_bp=None, horizon_sec=7)
    assert _exit(none, "LATE_TRAIL_2ATR_ARM2ATR")["reason"] == "NO_ATR"
    assert _exit(none, "LATE_TRAIL_2ATR_ARM2ATR")["net_bp"] is None


def test_giveback_ladder_and_first_trigger_wins_composite():
    pnls = [0, 9, 13, 20, 19, 15, 12, 9, 4, 3]
    rows = sxp.evaluate_shadow_exits(_series(pnls), SET, atr_bp=10.0, horizon_sec=9)
    giveback = _exit(rows, "LATE_GIVEBACK_KEEP50_ARM20")
    assert giveback["reason"] == "GIVEBACK" and giveback["exit_t_sec"] == 8.0
    ladder = _exit(rows, "LATE_LADDER_20_5_30_15_45_30")
    assert ladder["reason"] == "LADDER_LOCK" and ladder["exit_t_sec"] == 9.0
    hold = _exit(rows, "hold_to_horizon")
    assert hold["reason"] == "HORIZON_TIME_EXIT" and hold["net_bp"] == 3.0
    trail_first = [0, 10, 25, 32, 30, 22, 16, 15]
    rows = sxp.evaluate_shadow_exits(_series(trail_first), SET, atr_bp=10.0, horizon_sec=7)
    composite = _exit(rows, "COMPOSITE_LATE_BE20_5_TRAIL1.5_ARM2")
    assert composite["trigger_member"] == "COMPOSITE_LATE_BE20_5_TRAIL1.5_ARM2:trail"
    assert composite["role"] == "CARD_SHADOW"


def test_tile_hard_stop_and_time_backstop_guard_every_rule():
    rows = sxp.evaluate_shadow_exits(_series([0, -20, -41, -50, -50]), SET, atr_bp=None, horizon_sec=4)
    assert _exit(rows, "tile_stop_backstop_only")["reason"] == "HARD_STOP"
    assert _exit(rows, "LATE_BE_20_5")["reason"] == "HARD_STOP"
    assert not _exit(rows, "hold_to_horizon")["triggered"]
    guarded = ({"id": "b", "kind": "HOLD", "backstop_sec": 3},)
    rows = sxp.evaluate_shadow_exits(_series([0, 1, 2, 3, 4, 5]), guarded, atr_bp=None, horizon_sec=5)
    assert rows[0]["reason"] == "TIME_BACKSTOP" and rows[0]["exit_t_sec"] == 4.0


def test_short_path_is_censored_not_a_time_exit():
    rows = sxp.evaluate_shadow_exits(_series([0, 1, 2]), SET, atr_bp=None, horizon_sec=7200)
    assert _exit(rows, "hold_to_horizon")["reason"] == "PATH_END_CENSORED"


def test_horizons_and_minute_path_are_bounded_and_compact():
    pnls = [i % 40 - 10 for i in range(5 * 3600)]
    series = _series(pnls)
    horizons = sxp.horizon_extremes(series)
    assert set(horizons) == {"1", "2", "5", "10", "30", "60"}
    assert horizons["1"]["observed"] and horizons["60"]["mfe_bp"] == 29.0
    path = sxp.minute_path(series)
    assert path["minutes"] == sxp.MAX_PATH_MINUTES and path["truncated"]
    assert all(isinstance(v, int) for v in path["close"])
    assert len(json.dumps(path)) < 20_000


def test_record_for_short_trade_uses_ask_side_and_scores_actual_exit():
    pnls = [0, 5, 12, 25, 18, 10, 2, -1, -2] + [-2] * 30
    ticks = _ticks(pnls, direction="SHORT")
    rec = sxp.build_record(source=sxp.SOURCE_RUNTIME, trade_id="cfm-1", direction="SHORT", ticks=ticks,
                           signal_ts=T0 - 4, fill_ts=T0, entry_price=ENTRY, shadow_set=SET,
                           research_lane="FAMILY_X", exit_ts=T0 + 4, atr_pct=0.1, horizon_sec=30)
    assert rec["schema"] == sxp.SCHEMA and rec["path_side"] == "ASK" and rec["filled"]
    assert rec["tile"] == "FAMILY_X" and sxp.group_key(rec) == "FAMILY_X"
    actual = _exit(rec["shadow_exits"], "actual")
    assert actual["net_bp"] == 18.0 and actual["mfe_before_exit_bp"] == 25.0
    assert rec["entry_context"]["signal_to_fill_latency_sec"] == 4.0
    assert rec["entry_context"]["session"] in ("ASIA", "EU", "US")
    assert rec["extremes"]["mfe_bp"] == 25.0 and rec["extremes"]["peak_t_sec"] == 3.0
    assert rec["market_context"]["join"] == "DEFERRED_ANALYZER_JOIN"


def test_unfilled_signal_gets_taker_and_limit_touch_counterfactuals():
    pnls = [0, -5, -31, -20, 10, 30, 35] + [35] * 20
    ticks = _ticks(pnls, start=T0)
    limit = ENTRY * (1 - 0.003)
    rec = sxp.build_record(source=sxp.SOURCE_BACKFILL_TAPE, trade_id="ep-1", direction="LONG", ticks=ticks,
                           signal_ts=T0, fill_ts=None, entry_price=None, shadow_set=SET, lane="AI_COMMITTED",
                           limit_price=limit, horizon_sec=20)
    cf = rec["counterfactual"]
    assert not rec["filled"] and rec["shadow_exits"] == []
    assert cf["taker_at_signal"]["fill_model"] == "REALISTIC_V1_TAKER"
    assert cf["taker_at_signal"]["entry_ts"] == T0 + 1
    assert cf["limit_touch"]["touched"] and cf["limit_touch"]["role"] == "COMPARISON_SHADOW_NOT_HEADLINE"
    assert any(row["id"] == "hold_to_horizon" for row in cf["taker_shadow_exits"])
    assert sxp.group_key(rec) == "SIGNAL:AI_COMMITTED"


def _replay_row(direction="LONG", filled=True):
    ticks = []
    for i, pnl in enumerate([0, 8, 22, 26, 10, 1, -2] + [0] * 10):
        px = ENTRY * (1 + pnl / 1e4)
        ticks.append({"seq": i + 1, "t": float(i), "price": px, "best_bid": px, "best_ask": px + 1,
                      "observed_ts": T0 + i, "mark_source": "bbo",
                      "depth_bid_qty": 2.0, "depth_ask_qty": 1.0, "depth_best_bid": px, "depth_best_ask": px + 1})
    return {"schema": "signal_replay_v4", "trade_id": "ntt-1", "start_ts": "2026-10-04T00:00:00+00:00",
            "direction": direction, "lane": "executed", "virtual_entry": ENTRY if filled else None,
            "virtual_fill_t": 0.0 if filled else None, "entry_price": ENTRY if filled else None,
            "exit_t_rel": 5.0, "exit_reason": "PROFIT_LOCK_LADDER", "replay_complete": True, "ticks": ticks}


def test_record_from_replay_matches_runtime_meta_contract():
    row = _replay_row()
    meta = {"start_ts": T0, "research_lane": "FAMILY_COMMITTED_FADE_TAKER_90", "atr14_pct_3m": 0.08,
            "adx_at_signal": 27.0, "limit_price": ENTRY * 0.999, "policy_signature": "sig"}
    rec = sxp.record_from_replay(row, shadow_set=SET, meta=meta)
    assert rec["filled"] and rec["source"] == sxp.SOURCE_RUNTIME
    assert rec["entry_context"]["adx"] == 27.0 and rec["entry_context"]["atr3m_pct"] == 0.08
    assert rec["entry_context"]["depth_imbalance"] == round(1 / 3, 4)
    assert rec["policy_signature"] == "sig"
    assert _exit(rec["shadow_exits"], "actual")["reason"] == "PROFIT_LOCK_LADDER"
    assert _exit(rec["shadow_exits"], "LATE_TRAIL_2ATR_ARM2ATR")["reason"] != "NO_ATR"


def test_hold_market_context_summarises_funding_oi_and_liquidation_bursts():
    rows = []
    for i in range(4):
        rows.append({"minute_ts": int(T0) + 60 * i,
                     "derivatives": {"bitfinex": {"status": "OK", "funding_rate": 0.0001 * (i + 1), "oi_btc": 100 + i}},
                     "liquidations": {"binance": {"long_usd": 2_000_000.0 if i == 2 else 10.0, "short_usd": 0.0,
                                                  "long_n": 3, "short_n": 0}},
                     "regime": {"rank_pct": 55.0, "label": "NORMAL", "rv15_bps": 4.2}})
    out = sxp.hold_market_context(rows, T0, T0 + 180)
    assert out["join"] == "JOINED" and out["minutes"] == 4
    assert out["funding_rate"]["bitfinex"] == {"start": 0.0001, "end": 0.0004}
    assert out["oi_change_pct"]["bitfinex"] == 3.0
    assert out["liquidations"]["burst_minutes"] == 1
    assert out["entry_regime"]["label"] == "NORMAL"
    assert sxp.hold_market_context([], T0, T0 + 60)["join"] == "NO_MARKET_CONTEXT_ROWS"


def test_recorder_never_blocks_drops_when_full_and_dedupes():
    written = []
    rec = sxp.ShadowExitRecorder(writer=lambda row: written.append(row) or True,
                                 shadow_set_for=lambda meta: SET, max_queue=2)
    item = lambda tid: {"replay": {**_replay_row(), "trade_id": tid}, "meta": {"start_ts": T0}}  # noqa: E731
    assert rec.submit(item("a")) and rec.submit(item("b"))
    assert not rec.submit(item("c"))
    assert not rec.submit(item("a"))
    status = rec.status()
    assert status["dropped_full"] == 1 and status["dropped_duplicate"] == 1 and status["queue_depth"] == 2
    assert status["submit_us_max"] is not None and status["submit_us_max"] < 50_000
    while rec._queue:
        rec.process(rec._queue.popleft())
    assert rec.counters["written"] == 2 and written[0]["schema"] == sxp.SCHEMA
    assert rec.aggregates()["SIGNAL:executed"]["hold_to_horizon"]["n"] == 2
    assert rec.recent(1)[0]["trade_id"] == "b"


def test_recorder_counts_failures_and_never_raises():
    rec = sxp.ShadowExitRecorder(writer=lambda row: False, shadow_set_for=lambda meta: SET)
    rec.process({"replay": _replay_row(), "meta": {"start_ts": T0}})
    assert rec.counters["write_failures"] == 1
    broken = sxp.ShadowExitRecorder(writer=lambda row: True, shadow_set_for=lambda meta: 1 / 0)
    assert broken.process({"replay": _replay_row(), "meta": {}}) is None
    assert broken.counters["errors"] == 1 and "ZeroDivisionError" in broken.status()["last_error"]
    disabled = sxp.ShadowExitRecorder(writer=lambda row: True, shadow_set_for=lambda meta: SET, enabled=False)
    disabled.start()
    assert not disabled.submit({"replay": _replay_row()}) and disabled.status()["worker_alive"] is False


def test_worker_thread_drains_off_the_caller_thread():
    written = []
    rec = sxp.ShadowExitRecorder(writer=lambda row: written.append(row) or True, shadow_set_for=lambda meta: SET)
    rec.start()
    started = time.perf_counter()
    for i in range(5):
        rec.submit({"replay": {**_replay_row(), "trade_id": f"t{i}"}, "meta": {"start_ts": T0}})
    submit_ms = (time.perf_counter() - started) * 1e3
    deadline = time.time() + 10
    while len(written) < 5 and time.time() < deadline:
        time.sleep(0.02)
    assert len(written) == 5 and rec.status()["worker_alive"]
    assert submit_ms < 50


def test_public_summary_is_redacted():
    rec = sxp.ShadowExitRecorder(writer=lambda row: True, shadow_set_for=lambda meta: SET)
    rec.process({"replay": _replay_row(), "meta": {"start_ts": T0}})
    blob = json.dumps(rec.public_summary())
    assert "ntt-1" not in blob and "net_bp" not in blob and "entry_price" not in blob
    assert len(blob) < 1024


# ------------------------------------------------------------ runtime wiring

def test_dump_replay_submits_after_releasing_the_replay_lock():
    body = BOT[BOT.index("def dump_replay("):BOT.index("def load_policy(")]
    capture = body.index("shadow_exit_payload = {")
    submit = body.index("_submit_shadow_exit_path(shadow_exit_payload)")
    lock_block_end = body.index('logger.error(f"Replay dump failed for {trade_id}: {e}")')
    assert capture < lock_block_end < submit
    assert 'if buf.get("closed") and not terminal_reason:' in body


def test_runtime_declares_file_status_routes_and_reset_inventory():
    assert '"SHADOW_EXIT_PATH_FILE",' in BOT
    assert 'label="SHADOW_EXIT_PATH"' in BOT
    assert '"shadow_exit_recorder": shadow_exit_recorder_status_snapshot()' in BOT
    assert "@app.route('/api/research/shadow_exits')" in BOT
    assert "@app.route('/api/shadow_exits/summary')" in BOT
    read_only = BOT[BOT.index("_READ_ONLY_GET_PATHS = {"):BOT.index("}", BOT.index("_READ_ONLY_GET_PATHS = {"))]
    assert '"/api/shadow_exits/summary"' in read_only and "/api/research/shadow_exits" not in read_only
    wipe = BOT[BOT.index("def research_wipe_file_paths("):BOT.index("def _fresh_collection_derived_history_paths(")]
    assert "SHADOW_EXIT_PATH_FILE" in wipe
    import research_reset_inventory
    import data_retention_policy

    assert sxp.FILE_NAME in research_reset_inventory.RESEARCH_FILES
    assert data_retention_policy.TIER_A_DATASETS[sxp.FILE_NAME][0] == "shadow_exit_paths"


def test_recorder_module_has_no_order_or_relay_surface():
    import ast
    import sys

    source = (ROOT / "shadow_exit_paths.py").read_text(encoding="utf-8")
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= set(sys.stdlib_module_names) | {"__future__"}
    for forbidden in ("place_order", "cancel_order", "submit_order", "relay_arm", "set_toggle"):
        assert forbidden not in source


@pytest.fixture
def bot(monkeypatch):
    import bot as bot_module

    monkeypatch.setattr(bot_module, "_BOT_ADMIN_TOKEN", "shadow-exit-admin-token")
    monkeypatch.setattr(bot_module, "_DASHBOARD_BOOTSTRAP_COMPLETE", True)
    monkeypatch.setattr(bot_module, "_API_RATE_MAX_PER_IP", 10_000)
    return bot_module


def test_routes_public_summary_open_research_view_admin_only(bot):
    client = bot.app.test_client()
    remote = {"REMOTE_ADDR": "198.51.100.7"}
    public = client.get("/api/shadow_exits/summary", environ_base=remote)
    assert public.status_code == 200 and public.get_json()["schema"] == sxp.SUMMARY_SCHEMA
    assert client.get("/api/research/shadow_exits", environ_base=remote).status_code in (401, 403)
    authed = client.get("/api/research/shadow_exits", environ_base=remote,
                        headers={"X-Bot-Admin-Token": "shadow-exit-admin-token"})
    assert authed.status_code == 200
    body = authed.get_json()
    assert body["observation_only"] is True and body["status"]["schema"] == sxp.STATUS_SCHEMA
    assert set(body["tile_shadow_exit_set_ids"]) == set(registry.ACTIVE_TILE_ORDER)
