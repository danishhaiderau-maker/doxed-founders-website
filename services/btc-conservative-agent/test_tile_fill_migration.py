"""Lanes 1-5 migration (Danish 16 Oct 17:01): every tile prices through paper_fill; CI guards."""
import ast
import pathlib
import pytest

import combo_pathway_config as cpc
import paper_fill as pf
import live_ack_pool
import tile_fill as tf

ROOT = pathlib.Path(__file__).parent
BOOK = {"s": 100, "a": [[100.0, 1.0], [101.0, 5.0]], "b": [[99.0, 1.0], [98.0, 5.0]]}


def _use_book(monkeypatch, book=BOOK):
    monkeypatch.setattr(pf, "book10l_at", lambda t: {**book, "s": int(t) - 1} if book else None)


def test_roster_completeness_and_empty_pending():
    registry = set(cpc.ACTIVE_TILE_REGISTRY)
    assert registry <= set(pf.TILE_FILL_SITES), registry - set(pf.TILE_FILL_SITES)
    assert not pf.PENDING_MIGRATION
    assert tf.MIGRATED_LANES <= registry
    for lane in tf.MIGRATED_LANES:
        assert pf.TILE_FILL_SITES[lane] == (("tile_fill.py", "price_entry"), ("tile_fill.py", "price_exit"),
                                            ("tile_fill.py", "price_exit_resident"),  # REALISM_V2 exchange stop
                                            ("tile_fill.py", "price_exit_forced"))  # RK 17:18 (4) forced no-book


def test_every_fill_site_in_roster_passes_checker_and_book_is_positional():
    for lane, sites in pf.TILE_FILL_SITES.items():
        for module, func in sites:
            src = (ROOT / module).read_text(encoding="utf-8")
            assert pf.fill_site_violations(src, func) == [], (lane, func)
            fn = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef) and n.name == func)
            call = fn.body[-1].value
            assert len(call.args) == 5 and "book_at" not in {k.arg for k in call.keywords}
            assert {k.arg for k in call.keywords} <= pf.ALLOWED_FILL_KWARGS


def test_tile_fill_module_scan_clean_and_no_legacy_models():
    src = (ROOT / "tile_fill.py").read_text(encoding="utf-8")
    assert pf.tile_module_violations(src, "tile_fill.py", ("price_entry", "price_exit")) == []
    for bad in ("REALISTIC_V1", "TRADE_THROUGH", "QUEUE_CONSUMED", "state[", "book_tape_1s", "ticker"):
        if bad != "ticker":
            assert bad not in src
    assert pf.ALLOWED_FILL_MODELS == {pf.FILL_MODEL}


def test_checker_rejects_planted_kwargs_and_book_keyword():
    planted = ("import paper_fill\ndef price_entry(o):\n"
               "    return paper_fill.paper_fill(o['side'], o['qty'], o['signal_ts'], o['delay_s'], "
               "book_at=paper_fill.book10l_at, leg='entry')\n")
    v = pf.fill_site_violations(planted, "price_entry")
    assert any("book_at" in x for x in v)


def test_entry_is_ioc_at_signal_plus_delay_with_provenance(monkeypatch):
    _use_book(monkeypatch)
    r = tf.entry_fill("LONG", 0.5, 99.0, 1.5, "default_until_n10")
    assert r["status"] == "FILLED" and r["price"] == 100.0 and r["fill_ts"] == 100.5
    assert r["fill_model"] == pf.FILL_MODEL and r["fill_model_version"] == 1
    assert r["delay_source"] == "default_until_n10" and r["depth_snapshot_fill"]["a"][0] == [100.0, 1.0]
    p = tf.provenance(r)
    assert p["fee_model"] == tf.FEE_MODEL and p["depth_snapshot_fill"]


def test_entry_cap_blocks_deep_levels(monkeypatch):
    _use_book(monkeypatch)
    r = tf.entry_fill("LONG", 3.0, 99.0, 1.5, "x")  # cap = 100.05: only the 100.0 level is eligible
    assert r["status"] == "PARTIAL" and r["filled_qty"] == 1.0
    s = tf.entry_fill("SHORT", 0.5, 99.0, 1.5, "x")
    assert s["status"] == "FILLED" and s["price"] == 99.0


def test_no_book_means_no_fill_never_ticker(monkeypatch):
    _use_book(monkeypatch, book=None)
    for r in (tf.entry_fill("LONG", 1, 99.0, 1.5, "x"), tf.exit_fill("LONG", 1, 99.0, 1.5, "x")):
        assert r["status"] == "REJECT_NO_BOOK" and r["price"] is None


def test_exit_priced_from_book_other_side(monkeypatch):
    _use_book(monkeypatch)
    assert tf.exit_fill("LONG", 0.5, 99.0, 1.5, "x")["price"] == 99.0
    assert tf.exit_fill("SHORT", 0.5, 99.0, 1.5, "x")["price"] == 100.0


def test_row_provenance_guard_and_legacy_tag():
    good = {"entry_provenance": {"fill_model": pf.FILL_MODEL, "fill_model_version": 1, "delay_source": "d",
                                 "depth_snapshot_fill": {"s": 1}},
            "exit_provenance": {"fill_model": pf.FILL_MODEL, "fill_model_version": 1, "delay_source": "d",
                                "depth_snapshot_fill": {"s": 2}}}
    assert tf.row_provenance_complete(good)
    bad = {**good, "exit_provenance": {"fill_model": pf.FILL_MODEL}}
    assert not tf.row_provenance_complete(bad)
    assert pf.row_fresh_eligible({"research_lane": "X", "fill_model_tag": pf.LEGACY_TAG}) is False
    assert pf.row_fresh_eligible({"research_lane": "X", "fresh_eligible": False}) is False
    assert pf.row_fresh_eligible({"research_lane": "X", "fresh_eligible": True}) is True


def test_bot_hooks_route_lanes_1_to_5_only():
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    def seg(name):
        return ast.get_source_segment(src, next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name))
    assert "_paper_lane_entry_result" in seg("resolve_sim_fill_with_depth")
    assert "_paper_lane_exit" in seg("close_position") and "_paper_lane_exit" not in seg("resolve_sim_exit_price")
    assert "_paper_lane_entry_result" in seg("_pending_limit_ready_for_fill")
    assert "paper_ioc_no_fill" in seg("process_pending_orders")
    assert "tile_fill" in seg("_is_paper_book_lane") and "MIGRATED_LANES" in seg("_is_paper_book_lane")


def test_epoch_filter_and_legacy_tagging(monkeypatch):
    monkeypatch.setattr(pf, "FRESH_EPOCH_TS", 1000.0)
    ok = {"research_lane": "X", "exit_reason": "TP", "fill_model_version": 1, "entry_ts": 1500.0}
    assert pf.row_fresh_eligible(ok) is True
    assert pf.row_fresh_eligible({**ok, "entry_ts": 900.0}) is False  # pre-epoch
    legacy = {"research_lane": "X", "exit_reason": "TP", "entry_ts": 1500.0}  # no fill_model_version
    t = pf.tag_legacy(legacy)
    assert t["legacy_pricing"] is True and t["fill_model_tag"] == pf.LEGACY_TAG
    assert pf.row_fresh_eligible(legacy) is False


def test_vwap_and_leg_hooks_in_bot():
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    ns = {}
    node = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "_exit_price_vwap")
    exec(compile(ast.Module([node], []), "bot.py", "exec"), ns)
    pos = {"partial_exit_receipts": [{"closed_qty": 1.0, "price": 100.0, "realized_gross_usd": 1.0},
                                     {"closed_qty": 9, "price": 1, "realized_gross_usd": None}]}
    assert ns["_exit_price_vwap"](pos, 110.0, 1.0) == 105.0
    assert "_tp1_gate" in src and "leg_trigger_ts" in src and "leg_fill_ts" in src


def test_fee_model_and_funding_fields():
    assert tf.FEE_MODEL == "NO_TRADING_FEE, funding only"
    assert tf.provenance({})["fee_model"] == tf.FEE_MODEL
    f = tf.funding_fields(7 * 3600, 17 * 3600, 0.0001,
                          settlements=[{"ts": 8 * 3600, "rate": 0.0001}])  # crosses 08:00 and 16:00
    assert f["funding_rate"] == 0.0001 and f["hold_seconds"] == 36000.0
    assert f["funding_ts_crossed"] == [8 * 3600, 16 * 3600]
    assert f["funding_settlement_rates"] == [{"ts": 8 * 3600, "rate": 0.0001}]
    assert "8h settlement" in f["funding_source"]
    assert tf.funding_fields(100, 200, 0.0)["funding_ts_crossed"] == []
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    assert "funding_fields(pos.get(\"entry_ts\")" in src and "BITFINEX_FEE_RATES" not in src.split("def close_position")[1][:20000]


# ----------------------------------------------------------------------------- HM blockers B1-B5 + nits
import json
import threading
import types

import monitor_tiles as mt


@pytest.fixture(autouse=True)
def _r17_proof_rule_out_of_scope(monkeypatch):
    """R17 proof rule (countable only with book/delay/walk/level proof) is covered by test_r17_exclusion.py.
    These synthetic rows test other properties, so the proof check is neutralised here; the pinned-list and
    stamped exclusions still apply."""
    import r17_exclusion
    monkeypatch.setattr(r17_exclusion, "proof_failures", lambda row: [])



def _bot_ns(names, **extra):
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    ns = {"tile_fill": tf, "paper_fill": pf, "threading": threading, "copy": __import__("copy"), "math": __import__("math"),
          "trade_lock": threading.RLock(), "json": json, "os": __import__("os"), **extra}
    for name in names:
        node = next(n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.Assign))
                    and (getattr(n, "name", None) == name or any(getattr(t, "id", "") == name for t in getattr(n, "targets", []))))
        exec(compile(ast.Module([node], []), "bot.py", "exec"), ns)
    return ns


class _Log:
    def __init__(self):
        self.lines = []

    def warning(self, m, *a, **k):
        self.lines.append(("warning", m))

    def error(self, m, *a, **k):
        self.lines.append(("error", m))


class _Clock:
    def __init__(self, now):
        self.now, self.slept = float(now), []

    def time(self):
        return self.now

    def sleep(self, s):
        self.slept.append(s)
        self.now += s


def _lane_ns(clock, extra_names=(), delay=(1.5, "default_until_n10"), xstop=None, **extra):
    t = types.SimpleNamespace(time=clock.time, sleep=clock.sleep)
    return _bot_ns(["_PAPER_EXIT_TLS", "_apply_paper_exit_marks", "_tp1_cancel_pending", "_paper_forced_reasons", "_tp1_gate", "_paper_exit_sim", "_paper_forced_exit", "_paper_lane_exit", "_r0_stop_fill_reason", "_paper_exit_missing_book_seconds",
                    "_paper_unpriced_marks", "_paper_exit_model_times", "_exit_book_age_ms", "_finite_nonneg", "_tp1_pending_action",
                    "_paper_lane_entry_result", "_paper_lane_entry_core", "_rest_unsized_stamp", "_paper_no_fill_shadow_row", "_paper_entry_quote_gate",
                    "_order_entry_decision", "PAPER_ENTRY_TAPE_LAG_S", "_paper_entry_order_model",
                    "_paper_entry_rest_finish", "_paper_entry_maker_rest", "_paper_entry_rest_close", "_rest_queue_stamp",
                    "_paper_entry_rest_drop", "_real_mark_at", "_liquidation_stamp", "_paper_entry_pool_key", "_paper_own_s2s_entry", "_paper_own_s2s_exit", *extra_names],
                   trades_map={}, time=t, logger=_Log(), _paper_delay=lambda lane, leg, **kw: delay,
                   _live_ack_pool=live_ack_pool,
                   _paper_stamp_pool=lambda res, lane, leg: res, _paper_note_exit_trigger=lambda pos, t0: None,
                   ACTIVE_TILE_REGISTRY=cpc.ACTIVE_TILE_REGISTRY, _is_paper_book_lane=lambda p: True,
                   _exchange_stop_trigger=(xstop or (lambda pos, a, b: None)),
                   _normalize_order_side_to_dir=lambda v: "LONG" if str(v).upper() in ("BUY", "LONG") else "SHORT",
                   **extra)


def test_b2_tp1_gate_never_sleeps_and_prices_after_due(monkeypatch):
    _use_book(monkeypatch)
    clk = _Clock(1000.0)
    ns = _lane_ns(clk)
    pos = {"research_lane": "FAMILY_GSB1_CVD_DIV_REGIME", "policy_original_qty": 1.0, "qty": 1.0}
    assert ns["_tp1_gate"](pos, "TP1", 1000.0, "LONG", 0.5) == ("WAIT", None)
    assert pos["_tp1_pending"]["due_ts"] == 1001.5
    clk.now = 1001.0  # the gate reads the wall clock; `now` is the trigger time
    assert ns["_tp1_gate"](pos, "TP1", 1001.0, "LONG", 0.5) == ("WAIT", None)
    clk.now = 1001.6
    state, leg = ns["_tp1_gate"](pos, "TP1", 1001.6, "LONG", 0.5)
    assert state == "DUE" and leg["status"] == "FILLED" and leg["_trigger_ts"] == 1000.0
    assert leg["fill_ts"] == 1001.5 and leg["fill_model"] == pf.FILL_MODEL
    assert clk.slept == []  # no sleep anywhere in the TP1 path


def test_realism_v2_tp1_late_book_row_retries_same_model_time_then_prices(monkeypatch):
    """TP1_UNPRICED_LEG root cause (HM 23 Oct): the book row for trigger + delay lands ~1-2 s late; the leg used to be
    priced once at due and booked at its level with fill_valid False. Now it re-prices the SAME model time."""
    landed = {"ok": False}
    monkeypatch.setattr(pf, "book10l_at", lambda t: ({**BOOK, "s": int(t) - 1} if landed["ok"] else None))
    clk = _Clock(1000.0)
    ns = _lane_ns(clk)
    pos = {"research_lane": "FAMILY_GSB1_CVD_DIV_REGIME", "policy_original_qty": 1.0, "trade_id": "x"}
    assert ns["_tp1_gate"](pos, "TP1", 1000.0, "LONG", 0.5) == ("WAIT", None)
    clk.now = 1001.6
    assert ns["_tp1_gate"](pos, "TP1", 1100.0, "LONG", 0.5) == ("WAIT", None)  # row not written yet: WAIT
    assert pos["_tp1_pending"]["trigger_ts"] == 1000.0  # never restarted / re-triggered
    landed["ok"] = True
    clk.now = 1003.4
    state, leg = ns["_tp1_gate"](pos, "TP1", 1003.4, "LONG", 0.5)
    assert state == "DUE" and leg["status"] == "FILLED" and leg["price"] == 99.0
    assert leg["fill_ts"] == 1001.5 and leg["_trigger_ts"] == 1000.0 and leg["model_fill_ts"] == 1001.5
    assert leg["pricing_attempts"] == 2 and leg["priced_wall_ts"] == 1003.4
    assert leg["delay_source"] == "default_until_n10" and clk.slept == []


def test_realism_v2_tp1_no_book_after_force_window_is_unpriced_not_level(monkeypatch):
    monkeypatch.setattr(pf, "book10l_at", lambda t: None)
    clk = _Clock(1000.0)
    ns = _lane_ns(clk)
    pos = {"research_lane": "FAMILY_GSB1_CVD_DIV_REGIME", "policy_original_qty": 1.0, "trade_id": "x"}
    ns["_tp1_gate"](pos, "TP1", 1000.0, "LONG", 0.5)
    clk.now = 1001.5 + tf.EXIT_FORCE_AFTER_S - 0.1
    assert ns["_tp1_gate"](pos, "TP1", clk.now, "LONG", 0.5) == ("WAIT", None)
    clk.now = 1001.5 + tf.EXIT_FORCE_AFTER_S + 0.1
    state, leg = ns["_tp1_gate"](pos, "TP1", clk.now, "LONG", 0.5)
    assert state == "DUE" and leg["price"] is None and leg["exit_pricing_basis"] == "NO_BOOK_UNPRICED"
    assert leg.get("delay_source") != "FORCED_NO_DELAY"


def test_realism_v2_tp1_committed_action_survives_policy_change(monkeypatch):
    _use_book(monkeypatch)
    ns = _lane_ns(_Clock(1000.0))
    act = types.SimpleNamespace(close_fraction=0.5, remaining_fraction=0.5, reason="GS_LADDER_TP1",
                                partial_key="ladder_tp1", stop_price=None, peak_price=101.0, book_price=100.5)
    pos = {"research_lane": "FAMILY_GSB1_CVD_DIV_REGIME", "policy_original_qty": 1.0}
    ns["_tp1_gate"](pos, "ladder_tp1", 1000.0, "LONG", 0.5, act)
    got = ns["_tp1_pending_action"](pos)
    assert got.reason == "GS_LADDER_TP1" and got.close_fraction == 0.5 and got.partial_key == "ladder_tp1"
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    seg = src[src.index("def _apply_family_tile_exit"):src.index("def _apply_position_exits")]
    assert "_tp1_pending_action(source_pos)" in seg and "TP1_UNPRICED_LEG" in seg


def test_b2_bot_tp1_site_returns_before_commit_and_prunes():
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    seg = src[src.index("def _apply_family_tile_exit"):src.index("def _apply_position_exits")]
    assert "_tp1_gate(" in seg and 'if _gate == "WAIT":' in seg and "time.sleep" not in seg
    assert 'source_pos.pop("_tp1_pending"' in seg and "leg_fallback" in seg and "leg_fresh_eligible" in seg


def test_realism_v2_forced_close_is_bot_sent_at_model_time(monkeypatch):
    """REALISM_V2 F4: forced / admin closes are BOT_SENT market orders at trigger + delay (no `now - 0.3 s`)."""
    _use_book(monkeypatch)
    for reason in ("DEPLOY_FORCE_FLAT", "ADMIN_MANUAL_CLOSE", "TILE_RETIRED", "MANUAL_CLOSE"):
        clk = _Clock(5000.0)
        ns = _lane_ns(clk)
        pos = {"research_lane": "FAMILY_RANDOM_CONTROL_TAKER_90", "dir": "LONG", "qty": 0.5, "_exit_eval_price": 100.0}
        out, marks = ns["_paper_lane_exit"](pos, reason)
        px, sim = out
        assert px == 99.0 and sim["delay_source"] == "default_until_n10" and sim["delay_s"] == 1.5
        assert sim["fill_ts"] == 5001.5 and abs(sum(clk.slept) - 1.5) < 1e-9
        assert marks["exit_status"] == "PAPER_FILL" and marks["exit_pricing_basis"] == "MODEL_TIME"
        assert marks["exit_order_class"] == "BOT_SENT" and marks["exit_model_fill_ts"] == 5001.5
        assert "exit_status" not in pos and "fresh_data_excluded" not in pos  # nothing written before the claim


def test_realism_v2_no_book_exit_is_unpriced_never_a_ticker_fallback(monkeypatch):
    _use_book(monkeypatch, book=None)
    clk = _Clock(5000.0)
    ns = _lane_ns(clk)
    pos = {"research_lane": "FAMILY_RANDOM_CONTROL_TAKER_90", "dir": "LONG", "qty": 0.5}
    out, marks = ns["_paper_lane_exit"](pos, "DEPLOY_FORCE_FLAT")
    assert out is None
    assert marks["exit_status"] == "NO_BOOK_UNPRICED" and marks["exit_fallback"] is None
    assert marks["exit_pricing_basis"] == "NO_BOOK_UNPRICED" and marks["fill_valid"] is False
    assert marks["fresh_data_excluded"] is True and "exit_status" not in pos
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    seg = _func_src(src, "_paper_lane_exit") + _func_src(src, "_paper_forced_exit") + _func_src(src, "_tp1_gate")
    for gone in ("REPRICED_CURRENT_TIME", "FORCED_NO_DELAY", "LEGACY_BBO_FALLBACK", "FORCED_LOOKBACK_S"):
        assert gone not in seg, gone


def test_realism_v2_overdue_exit_prices_its_model_time_without_waiting(monkeypatch):
    _use_book(monkeypatch)
    clk = _Clock(5000.0)
    ns = _lane_ns(clk)
    pos = {"research_lane": "FAMILY_RANDOM_CONTROL_TAKER_90", "dir": "LONG", "qty": 0.5, "exit_trigger_ts": 4960.0}
    (_, sim), marks = ns["_paper_lane_exit"](pos, "TIME_EXIT")
    assert sim["fill_ts"] == 4961.5 and clk.slept == [] and marks["exit_model_trigger_to_fill_s"] == 1.5
    assert marks["exit_book_wait_s"] == 5000.0 - 4961.5  # visible per row: how late the pricing happened


def test_realism_v2_exchange_stop_wins_race_against_in_flight_bot_exit(monkeypatch):
    _use_book(monkeypatch)
    clk = _Clock(5000.0)
    hit = {"trigger_ts": 5000.7, "trigger_basis": "LAST_TRADE"}
    ns = _lane_ns(clk, xstop=lambda pos, a, b: hit if a < hit["trigger_ts"] <= b else None)
    pos = {"research_lane": "FAMILY_GS01_XV_PREMIUM_ATR_TP", "dir": "LONG", "qty": 0.5, "exit_trigger_ts": 5000.0,
           "exchange_stop_bp": 35.0, "exchange_stop_px": 99.0}
    # Race applies to delayed BOT_SENT (TIME_EXIT); paper STOP reasons are R0 FIRST_AFTER delay-0 and have no flight window.
    (px, sim), marks = ns["_paper_lane_exit"](pos, "TIME_EXIT")
    assert marks["exit_order_class"] == "EXCHANGE_RESIDENT" and marks["exit_reason_fired"] == "EXCHANGE_STOP_35BP"
    assert marks["exit_race_lost_by"] == "TIME_EXIT"
    assert sim["fill_ts"] == 5000.7 and sim["delay_s"] == 0.0 and sim["delay_source"] == "EXCHANGE_RESIDENT_NO_DELAY"
    assert marks["exit_pricing_basis"] == "EXCHANGE_RESIDENT_FIRST_ROW_AT_OR_AFTER_TRIGGER"
    # no resident trigger in flight -> TIME_EXIT fills at trigger + delay
    ns2 = _lane_ns(_Clock(5000.0))
    (_, sim2), marks2 = ns2["_paper_lane_exit"](dict(pos), "TIME_EXIT")
    assert marks2["exit_order_class"] == "BOT_SENT" and sim2["fill_ts"] == 5001.5


def test_r0_paper_stop_fills_first_after_not_latest_model_time(monkeypatch):
    """R0: PHYSICAL_HARD_STOP / INITIAL_ATR_STOP must walk FIRST_AFTER (STOP_RULE_NO_POOL), never LATEST at trigger+delay."""
    _use_book(monkeypatch)
    clk = _Clock(5000.0)
    ns = _lane_ns(clk)
    pos = {"research_lane": "FAMILY_RANDOM_CONTROL_TAKER_90", "dir": "LONG", "qty": 0.5, "exit_trigger_ts": 5000.0,
           "_exit_eval_price": 99.5}
    (px, sim), marks = ns["_paper_lane_exit"](pos, "PHYSICAL_HARD_STOP_10PCT")
    assert px == 99.0  # side-correct bid walk, not the trigger 99.5 and not the ask 100
    assert marks["exit_order_class"] == "BOT_SENT" and marks["exit_stop_rule"] is True
    assert sim["delay_s"] == 0.0 and sim["delay_source"] == "STOP_RULE_NO_POOL"
    assert sim["book_row_rule"] == pf.ROW_RULE_FIRST_AFTER and sim["leg"] == "exit_resident"
    assert marks["exit_pricing_basis"] == "STOP_RULE_FIRST_ROW_AT_OR_AFTER_TRIGGER"
    assert clk.slept == []
    (_, sim_be), marks_be = ns["_paper_lane_exit"](dict(pos), "BREAKEVEN_LOCK")  # no "STOP" token -> still BOT_SENT delayed
    assert marks_be.get("exit_stop_rule") is False and sim_be["delay_s"] == 1.5


def test_realism_v2_exchange_stop_exit_fills_at_trigger_instant(monkeypatch):
    _use_book(monkeypatch)
    clk = _Clock(5000.0)
    ns = _lane_ns(clk)
    pos = {"research_lane": "FAMILY_GS01_XV_PREMIUM_ATR_TP", "dir": "LONG", "qty": 0.5, "exit_trigger_ts": 4999.2}
    (px, sim), marks = ns["_paper_lane_exit"](pos, "EXCHANGE_STOP_35BP")
    assert sim["fill_ts"] == 4999.2 and sim["delay_s"] == 0.0 and clk.slept == []
    assert marks["exit_order_class"] == "EXCHANGE_RESIDENT"


def test_nit_exit_uses_exit_trigger_ts_and_waits_only_remaining_delay(monkeypatch):
    _use_book(monkeypatch)
    clk = _Clock(5000.0)
    ns = _lane_ns(clk)
    pos = {"research_lane": "FAMILY_RANDOM_CONTROL_TAKER_90", "dir": "LONG", "qty": 0.5, "exit_trigger_ts": 4999.0}
    (px, sim), marks = ns["_paper_lane_exit"](pos, "TIME_EXIT")
    assert sim["delay_source"] == "default_until_n10" and sim["fill_ts"] == 5000.5
    assert abs(sum(clk.slept) - 0.5) < 1e-9  # trigger was 1.0 s ago, delay 1.5 s -> waits 0.5 s, not 1.5 s
    assert marks["exit_status"] == "PAPER_FILL" and marks["_paper_exit_trigger_ts"] is None
    assert "exit_status" not in pos and "_paper_exit_trigger_ts" not in pos  # (HM b) applied only after the claim


def test_nit_entry_waits_for_signal_plus_delay_and_force_does_not_cache_no_book(monkeypatch):
    clk = _Clock(1000.0)
    ns = _lane_ns(clk)
    order = {"research_lane": "FAMILY_GSB1_CVD_DIV_REGIME", "signal_dir": "BUY", "qty": 0.5, "signal_created_ts": 999.0}
    _use_book(monkeypatch)
    assert ns["_paper_lane_entry_result"](order, 1000.0) is None          # 999 + 1.5 > 1000
    res = ns["_paper_lane_entry_result"](order, 1000.6)
    assert res["status"] == "FILLED" and order["paper_fill_result"] is res
    order2 = {"research_lane": "FAMILY_GSB1_CVD_DIV_REGIME", "signal_dir": "BUY", "qty": 0.5, "signal_created_ts": 999.0}
    _use_book(monkeypatch, book=None)
    # RK 17:18: book is the only entry quote source - no book at the signal fails the 2 s gate (reason NO_BOOK)
    forced = ns["_paper_lane_entry_result"](order2, 1000.0, force=True)
    assert forced["status"] == "QUOTE_STALE" and forced["entry_quote_reason"] == "NO_BOOK"
    assert "paper_fill_result" not in order2


def test_b5_no_fill_shadow_row_fields(monkeypatch):
    _use_book(monkeypatch, book={"a": [[200.0, 1.0]], "b": [[199.0, 1.0]]})  # far above the signal-ask cap
    ns = _lane_ns(_Clock(1000.0))
    order = {"research_lane": "FAMILY_GSB1_CVD_DIV_REGIME", "trade_id": "t1", "signal_dir": "BUY", "qty": 0.5,
             "signal_created_ts": 990.0}
    # signal book at 990 is also 200; make the fill-second book jump out of the cap with a stepping book
    seq = {"n": 0}

    def stepped(t):
        seq["n"] += 1
        a = 200.0 if seq["n"] <= 2 else 210.0  # calls 1-2: entry quote gate + signal cap (both at the signal second)
        return {"s": int(t) - 1, "a": [[a, 1.0]], "b": [[199.0, 1.0]]}
    monkeypatch.setattr(pf, "book10l_at", stepped)
    import quote_trigger as qt
    monkeypatch.setattr(qt, "trades_between", lambda a, b: [])
    res = ns["_paper_lane_entry_result"](order, 1000.0)
    # REALISM_V2: the walk got nothing inside the 5 bp cap; the remainder rested 3 s with no trade-through -> NO_FILL
    assert res["status"] == "NO_FILL" and res["entry_walk_status"] == "NO_FILL_IOC_CAP"
    assert res["entry_unfilled_qty"] == 0.5 and res["entry_order_model"] == "WALK_THEN_REST"
    row = ns["_paper_no_fill_shadow_row"](order, "PAPER_IOC_NO_FILL_IOC_CAP")
    for k in ("signal_ts", "cap_px", "delay_s", "delay_source", "depth_snapshot_signal", "depth_snapshot_fill"):
        assert row[k] is not None, k
    assert row["event"] == "PAPER_SHADOW_NO_FILL" and row["shadow_only"] is True and row["trade_id"] == "t1"
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    seg = src[src.index("def process_pending_orders"):src.index("def _release_unfilled_fill_handoff")]
    assert "PAPER_IOC_NO_FILL_SHADOW_FILE" in seg and "_paper_no_fill_shadow_row" in seg
    assert "paper_no_fill" in src[src.index("def _record_expired_order"):src.index("def _record_expired_order") + 9000]


def _closed_raw(**kw):
    raw = {"trade_id": "t", "research_lane": "FAMILY_GSB1_CVD_DIV_REGIME", "dir": "LONG", "entry": 100.0, "exit": 101.0,
           "exit_reason": "TP", "qty": 0.5, "leverage": 100, "margin_usdt": 0.25, "ts": 2000.0,
           "outcome_duration_sec": 60, "fill_model": pf.FILL_MODEL, "fill_model_version": 1, "delay_s": 1.5,
           "delay_source": "default_until_n10", "ask_age_ms": 120, "exit_ask_age_ms": 90,
           "depth_snapshot_signal": {"s": 1}, "depth_snapshot_fill": {"s": 2}, "exit_depth_snapshot_fill": {"s": 3},
           "exit_price_basis": "VWAP_ALL_LEGS", "exit_price": 100.8, "fee_model": tf.FEE_MODEL, "funding_rate": 0.0001,
           "funding_source": tf.FUNDING_SOURCE, "hold_seconds": 60.0, "funding_ts_crossed": [], "fresh_eligible": True,
           "legacy_pricing": False, "exit_status": "PAPER_FILL"}
    raw.update(kw)
    return raw


def _row(raw, evidence=None):
    return mt.tile_trade_row(raw, status="closed", short_names={}, epoch_id="e", forced_reasons=[],
                             fill_evidence=evidence, parse_ts=lambda v: float(v))


def test_b4_row_builder_exposes_pricing_from_the_row_itself():
    row = _row(_closed_raw(), evidence={"fill_model": "REALISTIC_V1", "fill_basis": "TRADE_THROUGH"})
    p = row["pricing"]
    for k in mt.PRICING_KEYS:
        assert k in p, k
    assert p["fresh_eligible"] is True and p["fee_model"] == "NO_TRADING_FEE, funding only"
    assert row["fill"]["fill_model"] == pf.FILL_MODEL  # not the old evidence index
    assert row["exit"]["exit_price"] == 100.8 and row["exit"]["exit_price_basis"] == "VWAP_ALL_LEGS"
    assert p["depth_snapshot_signal"] == {"s": 1} and p["exit_depth_snapshot_fill"] == {"s": 3}


def test_b4_legacy_rows_are_not_fresh_and_fresh_only_filters():
    legacy = _row(_closed_raw(fill_model=None, fill_model_version=None, fresh_eligible=None, exit_status=None,
                              exit_price_basis=None, exit_price=None))
    assert legacy["pricing"]["fresh_eligible"] is False
    good = _row(_closed_raw())
    assert mt.row_is_fresh(good) is True and mt.row_is_fresh(legacy) is False
    assert [r for r in (good, legacy) if mt.row_is_fresh(r)] == [good]
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    assert '"fresh_only": monitor_tiles.parse_bool(args.get("fresh_only"), False)' in src
    assert 'scope.get("fresh_only") and not monitor_tiles.row_is_fresh(row)' in src


def test_nit_epoch_initialised_at_boot_and_set_from_file(tmp_path, monkeypatch):
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    assert any(isinstance(n, ast.Expr) and isinstance(n.value, ast.Call) and getattr(n.value.func, "id", "") == "_fresh_start_epoch"
               for n in ast.parse(src).body), "boot-time call missing"
    f = tmp_path / "fresh_start_epoch.json"
    f.write_text(json.dumps({"epoch_ts": 12345.0}))
    ns = _bot_ns(["_fresh_start_epoch"], _fresh_start_epoch_path=lambda: str(f))
    monkeypatch.setattr(pf, "FRESH_EPOCH_TS", None)
    assert ns["_fresh_start_epoch"]()["epoch_ts"] == 12345.0 and pf.FRESH_EPOCH_TS == 12345.0


def test_nit_latency_measured_from_trigger_plus_delay():
    ns = _bot_ns(["_ts_or_none", "_trade_event_timestamps"])
    pos = {"exit_trigger_ts": 100.0, "exit_fill_sim": {"delay_s": 1.5}}
    out = ns["_trade_event_timestamps"](pos, {}, exit_fill_ts=101.7)
    assert out["exit_trigger_to_fill_sec"] == 0.2 and out["exit_delay_s"] == 1.5
    forced = ns["_trade_event_timestamps"]({"exit_trigger_ts": 100.0, "exit_fill_sim": {"delay_s": 0.0}}, {}, exit_fill_ts=100.4)
    assert forced["exit_trigger_to_fill_sec"] == 0.4


def test_nit_book_path_matches_runtime_dir_on_fly():
    entry = (ROOT / "fly-entrypoint.sh").read_text(encoding="utf-8")
    assert 'RUNTIME_DIR="$DATA_DIR/runtime"' in entry and 'cd "$RUNTIME_DIR"' in entry  # cwd = $BOT_DATA_DIR/runtime
    assert "os.getcwd()" in (ROOT / "paper_fill.py").read_text(encoding="utf-8")
    assert "Collector(os.getcwd())" in (ROOT / "strategist_streams_collector.py").read_text(encoding="utf-8")


def test_nit_funding_settlements_recorded_in_accrual():
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    seg = src[src.index("def accrue_position_funding"):src.index("def process_funding_accrual")]
    assert "funding_settlements" in seg


def test_b1_ci_runs_paper_fill_contract_before_deploy_and_on_prs():
    cmd = "python -m pytest test_paper_fill.py test_tile_fill_migration.py test_keep_manifest.py test_fresh_start_epoch.py test_integrity_gaps.py -q"
    repo = ROOT.parent.parent
    deploy = (repo / ".github/workflows/fly-bot-deploy.yml").read_text(encoding="utf-8")
    pr = (repo / ".github/workflows/paper-fill-ci.yml").read_text(encoding="utf-8")
    assert cmd in deploy and cmd in pr
    job = deploy[deploy.index("  test-and-deploy:"):]
    assert job.index(cmd) < job.index("Verify Fly and dashboard safety contracts")
    assert "pull_request:" in pr


def _func_src(src, name):
    node = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == name)
    return ast.get_source_segment(src, node)


def test_resid_a_inline_close_never_waits(monkeypatch):
    _use_book(monkeypatch)
    clk = _Clock(5000.0)
    ns = _lane_ns(clk)
    ns["_PAPER_EXIT_TLS"].no_wait = True
    pos = {"research_lane": "FAMILY_RANDOM_CONTROL_TAKER_90", "dir": "LONG", "qty": 0.5, "exit_trigger_ts": 5000.0}
    (_, sim), marks = ns["_paper_lane_exit"](pos, "TIME_EXIT")  # not a forced reason, trigger just now
    # REALISM_V2 F4: priced at the model time if its row exists (here the stub book always has it), else unpriced
    assert sim["fill_ts"] == 5001.5 and marks["exit_pricing_basis"] == "MODEL_TIME" and clk.slept == []
    ns["_PAPER_EXIT_TLS"].no_wait = False
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    seg = src[src.index("def _dispatch_position_close"):src.index("PROTECTIVE_FALLBACK_HARD_BP =")]
    assert "_PAPER_EXIT_TLS.no_wait = True" in seg and seg.index("no_wait = True") < seg.index("close_position(pos, reason)")


def test_hm_r4_inline_forced_model_time_marks_carry_exit_evidence(monkeypatch):
    """HM R4 (#845 review): the inline (no-wait) BOT_SENT MODEL_TIME close stamps exit_price_age_s, exit_evidence_status
    and exit_unverified_reason from the shared pricer's result, like the threaded path (no missing R17 evidence)."""
    _use_book(monkeypatch)
    clk = _Clock(5000.0)
    ns = _lane_ns(clk)
    ns["_PAPER_EXIT_TLS"].no_wait = True
    try:
        pos = {"research_lane": "FAMILY_RANDOM_CONTROL_TAKER_90", "dir": "LONG", "qty": 0.5, "exit_trigger_ts": 5000.0}
        (_, sim), marks = ns["_paper_lane_exit"](pos, "DEPLOY_FORCE_FLAT")
    finally:
        ns["_PAPER_EXIT_TLS"].no_wait = False
    assert marks["exit_pricing_basis"] == "MODEL_TIME" and marks["exit_order_class"] == "BOT_SENT"
    for k in ("exit_price_age_s", "exit_evidence_status", "exit_unverified_reason", "exit_book_row_rule",
              "exit_depth_short_qty", "exit_book_age_ms"):
        assert k in marks, k
    assert marks["exit_evidence_status"] == sim.get("evidence_status")
    assert marks["exit_price_age_s"] == sim.get("price_age_s")
    assert marks["exit_unverified_reason"] == sim.get("unverified_reason")


def test_resid_b_close_position_applies_marks_after_claim_only():
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    seg = _func_src(src, "close_position")
    assert seg.index("_paper_lane_exit(pos, exit_reason)") < seg.index("PositionCloseClaimScope(")
    assert seg.index("PositionCloseClaimScope(") < seg.index("_apply_paper_exit_marks(source_pos")
    assert seg.index("if not close_claimed:") < seg.index("_apply_paper_exit_marks(source_pos")


def test_realism_v2_tp1_never_restarts_or_prices_without_delay(monkeypatch):
    """The stale restart / FORCED_NO_DELAY livelock path is gone: the marker keeps its first trigger."""
    _use_book(monkeypatch, book=None)
    clk = _Clock(1000.0)
    ns = _lane_ns(clk)
    pos = {"research_lane": "FAMILY_GSB1_CVD_DIV_REGIME", "policy_original_qty": 1.0, "trade_id": "x"}
    ns["_tp1_gate"](pos, "TP1", 1000.0, "LONG", 0.5)
    for k in range(1, 4):
        clk.now = 1000.0 + 2.0 * k
        assert ns["_tp1_gate"](pos, "TP1", clk.now, "LONG", 0.5) == ("WAIT", None)
    assert pos["_tp1_pending"]["trigger_ts"] == 1000.0 and pos["_tp1_pending"]["attempts"] == 3
    assert "restarts" not in pos["_tp1_pending"]


def test_resid_c_pending_marker_cancelled_when_policy_stops_returning_tp1():
    ns = _lane_ns(_Clock(0))
    pos = {"trade_id": "x", "_tp1_pending": {"key": "TP1", "trigger_ts": time_now()}}
    ns["_tp1_cancel_pending"](pos, "policy no longer returns the TP1 action")
    assert "_tp1_pending" not in pos
    assert any("cancelled" in m for _, m in ns["logger"].lines)
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    seg = src[src.index("def _apply_family_tile_exit"):src.index("def _apply_position_exits")]
    assert seg.count("_tp1_cancel_pending(source_pos") == 2


def time_now():
    import time as _t
    return _t.time()


def test_resid_d_legacy_fallback_tag_stays_visible():
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    seg = _func_src(src, "close_position")
    assert '"exit_fallback": pos.get("exit_fallback")' in seg
    assert '"legacy_pricing": not _row_priced_by_paper_fill(pos)' in seg


# ---- BLOCKER 1 (HM, 17 Oct): closed rows read entry pricing from entry_fill_sim --------------------------
def _entry_pos(monkeypatch, lane="FAMILY_GSB2_REGIME_SWITCHER", exit_status="PAPER_FILL"):
    _use_book(monkeypatch)
    ns = _bot_ns(["_paper_result_as_sim"])
    res = tf.entry_fill("LONG", 0.5, 99.0, 1.5, "default_until_n10")
    # the same shape _build_open_position stores: pos["entry_fill_sim"] = order["fill_sim"] (no "fill_sim" key)
    sim = {k: v for k, v in ns["_paper_result_as_sim"](res).items() if k not in ("avg_price", "filled_qty")}
    return {"research_lane": lane, "entry_fill_sim": sim, "exit_status": exit_status, "entry_filled_qty": 0.5,
            "fill_model": "AI_DIRECT"}  # the legacy label _resolve_fill_model puts on the position


def _row_ns():
    return _bot_ns(["_entry_fill_sim", "_entry_priced_by_paper_fill", "_entry_pricing_fields", "_book_receive_row_fields",
                    "_row_priced_by_paper_fill"], _is_paper_book_lane=lambda pos: pos.get("research_lane") in tf.MIGRATED_LANES)


def test_blocker1_closed_row_entry_fields_come_from_entry_fill_sim(monkeypatch):
    pos = _entry_pos(monkeypatch)
    assert "fill_sim" not in pos  # positions never carry fill_sim: the old reads were all None
    ns = _row_ns()
    f = ns["_entry_pricing_fields"](pos, {"fill_model": "AI_DIRECT"})
    assert f["fill_model"] == pf.FILL_MODEL == "BOOK10L_WALK_AT_SIGNAL_PLUS_DELAY"  # paper_fill wins over AI_DIRECT
    assert f["fill_model_version"] == 1 and f["delay_s"] == 1.5 and f["delay_source"] == "default_until_n10"
    assert f["depth_snapshot_signal"] and f["depth_snapshot_fill"] and f["ask_age_ms"] is not None
    assert f["entry_provenance"] and f["entry_provenance"]["depth_snapshot_fill"]
    assert ns["_row_priced_by_paper_fill"](pos) is True
    assert tf.row_provenance_complete({**f, "fill_model": f["fill_model"]}) in (True, False)


def test_blocker1_fixed_row_is_consumed_by_fresh_and_tile_integrity_checks(monkeypatch):
    import tile_integrity
    pos = _entry_pos(monkeypatch)
    f = _row_ns()["_entry_pricing_fields"](pos, {})
    row = {**f, "exit_status": "PAPER_FILL", "fill_model_tag": None, "legacy_pricing": False, "fresh_eligible": True}
    assert (row["fill_model_version"] or 0) >= 1  # tile_integrity.py v1 filter (>= 1)
    assert row["fill_model"] in pf.ALLOWED_FILL_MODELS
    assert pf.row_fresh_eligible(row) is True
    for k in tile_integrity.REQUIRED_V1_FIELDS:
        if k in f:
            assert f[k] is not None, k


def test_blocker1_old_pricer_or_missing_entry_is_never_fresh(monkeypatch):
    ns = _row_ns()
    legacy = {"research_lane": "FAMILY_GSB2_REGIME_SWITCHER", "exit_status": "PAPER_FILL", "fill_model": "AI_DIRECT",
              "entry_fill_sim": {"fill_model": "PRE_REALISTIC_V1_BBO_CROSS"}}
    assert ns["_row_priced_by_paper_fill"](legacy) is False
    assert ns["_entry_pricing_fields"](legacy, {})["fill_model"] == "PRE_REALISTIC_V1_BBO_CROSS"
    empty = {"research_lane": "FAMILY_GSB2_REGIME_SWITCHER", "exit_status": "PAPER_FILL", "fill_model": "AI_DIRECT"}
    assert ns["_row_priced_by_paper_fill"](empty) is False
    assert ns["_entry_pricing_fields"](empty, {"fill_model": "X"})["fill_model"] == "AI_DIRECT"  # legacy label kept
    other = {"research_lane": "FAMILY_X_NOT_MIGRATED", "entry_fill_sim": {}, "fill_model": "AI_DIRECT"}
    assert ns["_entry_pricing_fields"](other, {})["entry_provenance"] is None


def test_blocker1_close_position_literal_has_single_fill_model_and_reads_entry_fill_sim():
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "close_position")
    dicts = [d for d in ast.walk(fn) if isinstance(d, ast.Dict) and any(
        isinstance(k, ast.Constant) and k.value == "exit_price_basis" for k in d.keys)]
    assert len(dicts) == 1
    keys = [k.value for k in dicts[0].keys if isinstance(k, ast.Constant)]
    assert keys.count("fill_model") == 0  # supplied once by **_entry_pricing_fields (no later duplicate overwrite)
    assert len(keys) == len(set(keys)), sorted({k for k in keys if keys.count(k) > 1})
    seg = ast.get_source_segment(src, fn)
    assert "_entry_pricing_fields(pos, master)" in seg and 'pos.get("fill_sim")' not in seg


def test_blocker1_position_builder_stores_entry_fill_sim_and_no_pos_fill_sim():
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "_build_open_position")
    seg = ast.get_source_segment(src, fn)
    assert '"entry_fill_sim": copy.deepcopy(order.get("fill_sim") or {})' in seg
    assert '"fill_sim":' not in seg.replace('"entry_fill_sim":', "")


def test_blocker1_legacy_market_entry_is_refused():
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    flag = next(n for n in tree.body if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "LEGACY_MARKET_ENTRY_ENABLED")
    assert flag.value.value is False
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "execute_market_order")
    seg = ast.get_source_segment(src, fn)
    assert seg.index("LEGACY_MARKET_ENTRY_ENABLED") < seg.index("simulate_market_fill(")
    ns = _bot_ns(["LEGACY_MARKET_ENTRY_ENABLED"])
    assert ns["LEGACY_MARKET_ENTRY_ENABLED"] is False


# ---- BLOCKER (17 Oct): fresh_eligible must survive a CSV reload (bool("False") was True) -------------------
def _csv_ns(tmp_path, monkeypatch):
    import csv as _csv
    import re as _re
    names = ["_ascii_fold_csv_text", "safe_csv_row", "_atomic_write_csv_rows", "_dynamic_csv_writer_once",
             "_trade_row_in_session", "_restore_reloaded_row_types", "load_session_trades_from_csv",
             "_RELOAD_NUMERIC_FIELDS", "_RELOAD_INT_FIELDS", "_RELOAD_LITERAL_FIELDS"]
    path = str(tmp_path / "trades_3factor.csv")
    loaded = []
    ns = _bot_ns(names, csv=_csv, re=_re, unicodedata=__import__("unicodedata"), CSV_TRADES=path, trades=loaded,
                 parse_ts=lambda s: 0.0, _research_data_start_ts=lambda: 1.0, logger=__import__("logging").getLogger("t"),
                 _CSV_ASCII_REPLACEMENTS={}, ast=ast)
    return ns, path, loaded


def _closed_row(monkeypatch, tid, **over):
    pos = _entry_pos(monkeypatch)
    ns = _row_ns()
    fresh = ns["_row_priced_by_paper_fill"](pos)
    row = {"trade_id": tid, "ts": "2026-10-17T02:00:00", "entry_ts": 1.0, "research_lane": pos["research_lane"],
           **ns["_entry_pricing_fields"](pos, {}), "exit_status": "PAPER_FILL", "fill_model_tag": None,
           "legacy_pricing": not fresh, "fresh_eligible": fresh, "fresh_data_excluded": False}
    row.update(over)
    return row


def test_reload_roundtrip_keeps_fresh_eligible_and_fresh_only_identical(tmp_path, monkeypatch):
    import monitor_tiles as mt
    ns, path, loaded = _csv_ns(tmp_path, monkeypatch)
    rows = [
        _closed_row(monkeypatch, "ok-1"),
        _closed_row(monkeypatch, "nobook-1", exit_status="FORCED_NO_BOOK", fresh_eligible=False),
        _closed_row(monkeypatch, "legacy-1", fill_model="AI_DIRECT", fill_model_version=None, delay_s=None,
                    fill_model_tag="LEGACY_BBO_FALLBACK_NO_BOOK", legacy_pricing=True, fresh_eligible=False,
                    depth_snapshot_signal=None, depth_snapshot_fill=None, entry_provenance=None),
        _closed_row(monkeypatch, "flagfalse-1", fresh_eligible=False),
    ]
    for r in rows:
        ns["_dynamic_csv_writer_once"](path, r)
    assert "False" in open(path).read()  # the writer really stores the string "False"
    assert ns["load_session_trades_from_csv"]() == 4
    back = {t["trade_id"]: t for t in loaded}
    for r in rows:
        before = mt.pricing_block(r)
        after = mt.pricing_block(back[r["trade_id"]])
        assert before["fresh_eligible"] == after["fresh_eligible"], r["trade_id"]
        assert before == after, r["trade_id"]  # incl. typed version, delay, depth snapshots
        tr = {"status": "CLOSED", "pricing": after}
        assert mt.row_is_fresh(tr) == before["fresh_eligible"]
    fresh = {k: mt.pricing_block(v)["fresh_eligible"] for k, v in back.items()}
    assert fresh == {"ok-1": True, "nobook-1": False, "legacy-1": False, "flagfalse-1": False}
    assert back["ok-1"]["fresh_eligible"] is True and back["nobook-1"]["fresh_eligible"] is False
    assert back["legacy-1"]["legacy_pricing"] is True and back["ok-1"]["legacy_pricing"] is False
    assert back["ok-1"]["fill_model_version"] == 1 and isinstance(back["ok-1"]["depth_snapshot_fill"], dict)


def test_pricing_block_never_trusts_a_stale_true_or_a_string_false(monkeypatch):
    import monitor_tiles as mt
    good = _closed_row(monkeypatch, "g")
    assert mt.pricing_block({**good, "fresh_eligible": "True"})["fresh_eligible"] is True
    assert mt.pricing_block({**good, "fresh_eligible": "False"})["fresh_eligible"] is False
    # a stored True is not enough: the pricing facts must agree
    assert mt.pricing_block({**good, "fresh_eligible": True, "fill_model_version": None})["fresh_eligible"] is False
    assert mt.pricing_block({**good, "fresh_eligible": "True", "fill_model_version": ""})["fresh_eligible"] is False
    assert mt.pricing_block({**good, "fresh_eligible": True, "exit_status": "FORCED_NO_BOOK"})["fresh_eligible"] is False
    assert mt.pricing_block({**good, "fresh_eligible": True, "exit_status": "PAPER_FILL_FORCED"})["fresh_eligible"] is False
    assert mt.pricing_block({**good, "fresh_eligible": True, "fill_model": "AI_DIRECT"})["fresh_eligible"] is False
    assert mt.pricing_block({**good, "fresh_eligible": True, "legacy_pricing": "True"})["fresh_eligible"] is False
    assert mt.pricing_block({k: v for k, v in good.items() if k != "fresh_eligible"})["fresh_eligible"] is True
    assert pf.as_bool("False") is False and pf.as_bool("0") is False and pf.as_bool("1") is True and pf.as_bool("") is None


def test_open_row_shows_entry_pricing_not_pre_realistic(monkeypatch):
    import monitor_tiles as mt
    pos = _entry_pos(monkeypatch)
    pos.update({"trade_id": "o-1", "entry_ts": 1.0, "entry": 100.0})
    row = mt.tile_trade_row(pos, status="open", short_names={}, epoch_id=None, forced_reasons=[],
                            fill_evidence={"fill_model": "PRE_REALISTIC_V1_BBO_CROSS"})
    assert row["pricing"]["fill_model"] == pf.FILL_MODEL and row["pricing"]["fill_model_version"] == 1
    assert row["pricing"]["state"].startswith("OPEN") and row["pricing"]["fresh_eligible"] is None
    assert row["fill"]["fill_model"] == pf.FILL_MODEL
    assert mt.row_is_fresh(row) is True  # open rows are not trades; fresh_only keeps showing them as before


# ---- measured slippage / drift (17 Oct): pure measurement over recorded fills, null not 0.0 -------------------
SNAP = {"s": 1, "a": [[100.0, 1.0], [100.05, 1.0]], "b": [[99.9, 1.0], [99.85, 1.0]]}


def test_leg_slippage_walk_is_measured_against_the_uncapped_best_touch_of_the_same_snapshot():
    # entry LONG buys the ask: recorded 100.0 == touch -> 0; recorded 100.025 (walked into level 2) -> cost
    flat = pf.leg_slippage("LONG", "entry", 0.5, 100.0, fill_snapshot=SNAP, signal_snapshot=SNAP)
    assert flat["walk_usd"] == 0.0 and flat["walk_bp"] == 0.0 and flat["touch_px"] == 100.0
    walked = pf.leg_slippage("LONG", "entry", 2.0, 100.025, fill_snapshot=SNAP)
    assert walked["walk_usd"] == pytest.approx(0.05) and walked["walk_bp"] == pytest.approx(2.5, abs=1e-3)
    assert walked["walk_within_cap"] is True
    assert pf.leg_slippage("LONG", "entry", 1.0, 100.2, fill_snapshot=SNAP)["walk_within_cap"] is False  # 20 bp
    # entry SHORT sells the bid; exit LONG sells the bid; exit SHORT buys the ask
    assert pf.leg_slippage("SHORT", "entry", 1.0, 99.85, fill_snapshot=SNAP)["walk_usd"] == pytest.approx(0.05)
    assert pf.leg_slippage("LONG", "exit", 1.0, 99.85, fill_snapshot=SNAP)["walk_usd"] == pytest.approx(0.05)
    assert pf.leg_slippage("SHORT", "exit", 1.0, 100.05, fill_snapshot=SNAP)["walk_usd"] == pytest.approx(0.05)


def test_leg_slippage_drift_signs_entry_vs_signal_touch_and_exit_trigger_to_fill():
    sig = {"s": 0, "a": [[100.0, 5.0]], "b": [[99.9, 5.0]]}
    fill = {"s": 1, "a": [[100.1, 5.0]], "b": [[99.8, 5.0]]}
    # entry LONG: filled 100.1 vs signal touch 100.0 -> adverse, negative drift, ~ -10 bp
    e = pf.leg_slippage("LONG", "entry", 1.0, 100.1, fill_snapshot=fill, signal_snapshot=sig)
    assert e["touch_drift_bp"] == pytest.approx(-10.0, abs=1e-3) and e["touch_drift_usd"] == pytest.approx(-0.1)
    assert e["trigger_drift_bp"] is None  # trigger-to-fill applies only to exits
    # exit LONG sells: trigger 100.0, filled 99.8 -> adverse (negative); overshoot = trigger vs signal bid
    x = pf.leg_slippage("LONG", "exit", 2.0, 99.8, fill_snapshot=fill, signal_snapshot=sig, trigger_price=100.0)
    assert x["trigger_drift_bp"] == pytest.approx(-20.0, abs=1e-3) and x["trigger_drift_usd"] == pytest.approx(-0.4)
    assert x["touch_drift_bp"] == pytest.approx(-10.0, abs=1e-2)
    assert x["trigger_overshoot_bp"] == pytest.approx(10.0, abs=1e-2)  # trigger above the signal-second bid
    # favourable fill is positive
    assert pf.leg_slippage("LONG", "exit", 1.0, 100.2, fill_snapshot=fill, trigger_price=100.0)["trigger_drift_bp"] > 0


def test_leg_slippage_is_null_not_zero_without_inputs_and_aggregate_goes_null_on_an_unmeasured_leg():
    miss = pf.leg_slippage("LONG", "exit", 1.0, 100.0, fill_snapshot=None, signal_snapshot=None, trigger_price=100.0)
    assert miss["walk_usd"] is None and miss["walk_bp"] is None and miss["touch_drift_bp"] is None
    assert miss["trigger_drift_bp"] == 0.0  # the trigger itself is known; only the book legs are null
    assert pf.leg_slippage("LONG", "entry", 1.0, None, fill_snapshot=SNAP)["walk_usd"] is None
    assert pf.leg_slippage("LONG", "entry", 1.0, 100.0, fill_snapshot={"a": [], "b": []})["walk_usd"] is None
    good = {**pf.leg_slippage("LONG", "exit", 1.0, 99.85, fill_snapshot=SNAP), "qty": 1.0}
    both = pf.aggregate_legs([good, {**good, "qty": 3.0}])
    assert both["walk_usd"] == pytest.approx(0.1) and both["walk_bp"] == pytest.approx(5.0, abs=1e-2) and both["legs_unmeasured"] == 0
    one_null = pf.aggregate_legs([good, {"qty": 1.0, **miss}])
    assert one_null["walk_usd"] is None and one_null["walk_bp"] is None and one_null["legs_unmeasured"] == 1
    assert pf.aggregate_legs([])["walk_usd"] is None


def _slip_ns():
    return _bot_ns(["_entry_fill_sim", "_entry_priced_by_paper_fill", "_SLIPPAGE_ROW_KEYS", "_slippage_row_fields", "_paper_exit_sim"],
                   _is_paper_book_lane=lambda pos: pos.get("research_lane") in tf.MIGRATED_LANES)


def _exit_sim(monkeypatch, direction="LONG", qty=0.5, trigger=100.5):
    _use_book(monkeypatch)
    ns = _slip_ns()
    res = tf.exit_fill(direction, qty, 120.0, 1.5, "default_until_n10")
    _px, sim = ns["_paper_exit_sim"](res, {"_exit_eval_price": trigger}, direction, qty, {})
    return sim


def test_entry_and_exit_sims_carry_measured_slippage_and_the_row_fields_are_consistent(monkeypatch):
    pos = _entry_pos(monkeypatch)
    pos["dir"] = "LONG"
    efs = pos["entry_fill_sim"]
    assert efs["slippage_usd"] is not None and efs["walk_bp"] is not None  # no longer a constant 0.0 placeholder
    assert efs["best_price"] == pf.best_touch("LONG", "entry", efs["depth_snapshot_fill"])
    assert "entry_delay_drift_bp" in efs and efs["signal_ts"] == 99.0 and efs["fill_ts"] == 100.5
    sim = _exit_sim(monkeypatch)
    assert sim["slippage_usd"] is not None and sim["exit_delay_drift_bp"] is not None
    assert sim["exit_trigger_overshoot_bp"] is not None
    ns = _slip_ns()
    out = ns["_slippage_row_fields"](pos, sim, 0.5)
    assert out["book_slippage_usd_total"] == pytest.approx(out["book_slippage_usd_entry"] + out["book_slippage_usd_exit"], abs=1e-6)
    assert out["exit_legs"] == 1 and out["exit_legs_unmeasured"] == 0 and len(out["exit_depth_snapshot_legs"]) == 1
    assert out["exit_depth_snapshot_signal"] == sim["depth_snapshot_signal"]
    assert out["pricer_signal_ts"] == 99.0 and out["model_fill_ts"] == 100.5
    assert out["exit_pricer_signal_ts"] == 120.0 and out["exit_model_fill_ts"] == 121.5
    assert out["exit_delay_drift_bp"] == sim["exit_delay_drift_bp"]  # headline exit drift = trigger to fill


def test_multi_leg_close_measures_each_leg_and_a_leg_without_a_snapshot_is_null(monkeypatch):
    pos = _entry_pos(monkeypatch)
    pos["dir"] = "LONG"
    sim = _exit_sim(monkeypatch)
    prov = {"depth_snapshot_signal": SNAP, "depth_snapshot_fill": SNAP}
    leg_ok = {"realized_gross_usd": 1.0, "closed_qty": 0.25, "price": 99.9, "leg_trigger_price": 100.0,
              "leg_provenance": prov}
    pos["partial_exit_receipts"] = [leg_ok]
    ns = _slip_ns()
    ok = ns["_slippage_row_fields"](pos, sim, 0.5)
    assert ok["exit_legs"] == 2 and ok["exit_legs_unmeasured"] == 0 and ok["book_slippage_usd_exit"] is not None
    assert ok["exit_depth_snapshot_legs"][0]["fill"] == SNAP
    pos["partial_exit_receipts"] = [{**leg_ok, "leg_provenance": {"depth_snapshot_signal": None, "depth_snapshot_fill": None}}]
    bad = ns["_slippage_row_fields"](pos, sim, 0.5)
    assert bad["exit_legs_unmeasured"] == 1 and bad["book_slippage_usd_exit"] is None and bad["book_slippage_usd_total"] is None
    assert bad["book_slippage_usd_entry"] is not None  # the entry leg is still measured
    pos["partial_exit_receipts"] = [{**leg_ok, "realized_gross_usd": None}]  # the terminal action is not a partial leg
    assert ns["_slippage_row_fields"](pos, sim, 0.5)["exit_legs"] == 1


def test_legacy_non_paper_and_unpriced_exit_rows_are_all_null_not_zero(monkeypatch):
    ns = _slip_ns()
    legacy = {"research_lane": "FAMILY_GSB2_REGIME_SWITCHER", "dir": "LONG",
              "entry_fill_sim": {"fill_model": "AI_DIRECT", "slippage_usd": 0.0}}
    assert all(v is None for v in ns["_slippage_row_fields"](legacy, None, 1.0).values())
    other = {**_entry_pos(monkeypatch), "research_lane": "FAMILY_DISLOC_BFX_DISLOCATION_1M", "dir": "LONG"}
    assert all(v is None for v in ns["_slippage_row_fields"](other, None, 1.0).values())
    pos = {**_entry_pos(monkeypatch), "dir": "LONG"}
    forced = ns["_slippage_row_fields"](pos, None, 0.5)  # exit never priced by the book (FORCED_NO_BOOK)
    assert forced["book_slippage_usd_exit"] is None and forced["book_slippage_usd_total"] is None
    assert forced["exit_legs_unmeasured"] == 1 and forced["book_slippage_usd_entry"] is not None


def test_lane_stats_averages_only_measured_rows_and_publishes_the_denominator():
    import monitor_api as ma
    rows = [{"close_ts": 1, "net_pnl_usd": 1.0, "book_slippage_usd": 0.2, "fill_model_version": 1, "exit_legs": 1},
            {"close_ts": 2, "net_pnl_usd": 1.0, "book_slippage_usd": 0.4, "fill_model_version": 1, "exit_legs": 2},
            {"close_ts": 3, "net_pnl_usd": 1.0, "book_slippage_usd": 0.0, "fill_model_version": None},  # legacy
            {"close_ts": 4, "net_pnl_usd": 1.0, "book_slippage_usd": 0.0, "fill_model_version": 1},  # old v1 constant 0.0
            {"close_ts": 5, "net_pnl_usd": 1.0, "book_slippage_usd": None, "fill_model_version": 1, "exit_legs": 1},
            {"close_ts": 6, "net_pnl_usd": 1.0, "book_slippage_usd": None}]  # DISLOC shape
    st = ma.lane_stats(rows)
    assert st["mean_book_slippage_usd"] == pytest.approx(0.3) and st["n_slippage_rows"] == 2 and st["closes"] == 6
    old_only = ma.lane_stats([{"close_ts": 1, "net_pnl_usd": 1.0, "book_slippage_usd": 0.0, "fill_model_version": 1}])
    assert old_only["mean_book_slippage_usd"] is None and old_only["n_slippage_rows"] == 0  # not a fake 0.0
    assert ma.lane_stats([])["mean_book_slippage_usd"] is None


def test_negative_zero_is_clamped_to_zero_in_walk_drift_and_aggregates():
    import math
    snap = {"s": 1, "a": [[100.0, 1.0]], "b": [[99.0, 1.0]]}
    # a fill 1e-9 better than the touch rounds to -0.0 before the clamp
    r = pf.leg_slippage("LONG", "exit", 1.0, 99.0 + 1e-9, fill_snapshot=snap, signal_snapshot=snap, trigger_price=99.0 + 1e-9)
    for k in ("walk_usd", "walk_bp", "touch_drift_usd", "touch_drift_bp", "trigger_drift_usd", "trigger_drift_bp"):
        assert r[k] == 0.0 and math.copysign(1.0, r[k]) == 1.0, (k, r[k])
    agg = pf.aggregate_legs([{**r, "qty": 1.0}, {**r, "qty": 2.0}])
    for k in ("walk_usd", "walk_bp", "touch_drift_usd", "trigger_drift_bp"):
        assert math.copysign(1.0, agg[k]) == 1.0, (k, agg[k])


def test_no_fill_paths_produce_no_price_and_no_slippage_numbers(monkeypatch):
    # IOC cap too tight for the book -> NO_FILL_IOC_CAP; empty side -> NO_FILL_NO_DEPTH; never a price or a 0.0 cost
    _use_book(monkeypatch)
    capped = pf.paper_fill("LONG", 0.5, 99.0, 1.5, pf.book10l_at, leg="entry", cap_px=99.5)
    assert capped["status"] == "NO_FILL_IOC_CAP" and capped["price"] is None and capped["filled_qty"] == 0.0
    _use_book(monkeypatch, {"s": 100, "a": [], "b": [[99.0, 1.0]]})
    empty = pf.paper_fill("LONG", 0.5, 99.0, 1.5, pf.book10l_at, leg="entry", cap_px=100.0)
    assert empty["status"] == "NO_FILL_NO_DEPTH" and empty["price"] is None
    ns = _bot_ns(["_paper_result_as_sim"])
    for res in (capped, empty):
        sim = ns["_paper_result_as_sim"](res)
        assert sim["fill_price"] == 0.0 and sim["filled_qty"] == 0.0 and sim["evidence_status"] == "UNKNOWN"
        assert sim["slippage_usd"] is None and sim["walk_bp"] is None and sim["entry_delay_drift_bp"] is None
        leg = pf.leg_slippage("LONG", "entry", res["filled_qty"], res["price"], fill_snapshot=res["depth_snapshot_fill"],
                              signal_snapshot=res["depth_snapshot_signal"])
        assert all(v is None for v in leg.values())


def test_slippage_fields_round_trip_through_the_csv_writer_and_loader(tmp_path, monkeypatch):
    import monitor_tiles as mt
    ns, path, loaded = _csv_ns(tmp_path, monkeypatch)
    pos = {**_entry_pos(monkeypatch), "dir": "LONG"}
    sim = _exit_sim(monkeypatch)
    sns = _slip_ns()
    measured = sns["_slippage_row_fields"](pos, sim, 0.5)
    unmeasured = sns["_slippage_row_fields"](pos, None, 0.5)  # exit/total/legs-null row, entry measured
    legacy = sns["_slippage_row_fields"]({"research_lane": "X"}, None, 0.5)
    # a row closed before #689: version 1, the old constant 0.0 slippage columns, none of the measured fields
    old_const = {"book_slippage_usd_entry": 0.0, "book_slippage_usd_exit": 0.0, "book_slippage_usd_total": 0.0}
    rows = [_closed_row(monkeypatch, "m-1", **measured), _closed_row(monkeypatch, "u-1", **unmeasured),
            _closed_row(monkeypatch, "l-1", **legacy), _closed_row(monkeypatch, "o-1", **old_const)]
    for r in rows:
        ns["_dynamic_csv_writer_once"](path, r)
    assert ns["load_session_trades_from_csv"]() == 4
    back = {t["trade_id"]: t for t in loaded}
    # the old-constant row keeps its 0.0 but has no exit_legs after reload -> excluded from the slippage mean
    assert back["o-1"]["book_slippage_usd_total"] == 0.0 and back["o-1"].get("exit_legs") is None
    import monitor_api as _ma
    shaped = [{"close_ts": i, "net_pnl_usd": 1.0, "book_slippage_usd": back[t]["book_slippage_usd_total"],
               "fill_model_version": back[t]["fill_model_version"], "exit_legs": back[t].get("exit_legs")}
              for i, t in enumerate(("m-1", "u-1", "l-1", "o-1"))]
    st = _ma.lane_stats(shaped)
    assert st["n_slippage_rows"] == 1 and st["mean_book_slippage_usd"] == pytest.approx(measured["book_slippage_usd_total"], abs=1e-4)
    for key in ("book_slippage_usd_total", "book_slippage_usd_exit", "exit_walk_bp", "exit_delay_drift_bp",
                "exit_depth_snapshot_signal", "exit_depth_snapshot_legs", "exit_legs", "exit_legs_unmeasured",
                "pricer_signal_ts", "exit_model_fill_ts", "entry_delay_drift_bp"):
        for tid, src in (("m-1", measured), ("u-1", unmeasured), ("l-1", legacy)):
            assert back[tid][key] == src[key], (tid, key, back[tid][key], src[key])
            assert (back[tid][key] is None) == (src[key] is None), (tid, key)  # '' must not become 0.0
    assert back["u-1"]["book_slippage_usd_total"] is None and back["l-1"]["book_slippage_usd_entry"] is None
    assert isinstance(back["m-1"]["exit_legs"], int) and isinstance(back["m-1"]["exit_depth_snapshot_legs"], list)
    for tid in ("m-1", "u-1", "l-1", "o-1"):
        assert mt.pricing_block(back[tid]) == mt.pricing_block(next(r for r in rows if r["trade_id"] == tid))
    mb = mt.pricing_block(back["m-1"])
    assert mb["exit_legs"] == 1 and mb["book_slippage_usd_total"] == measured["book_slippage_usd_total"]


def test_slippage_helpers_are_pure_measurement_and_never_touch_fill_sites():
    import ast as _ast
    src = pathlib.Path(pf.__file__).read_text()
    tree = _ast.parse(src)
    for name in ("best_touch", "leg_slippage", "aggregate_legs"):
        fn = next(n for n in tree.body if isinstance(n, _ast.FunctionDef) and n.name == name)
        calls = {c.func.id for c in _ast.walk(fn) if isinstance(c, _ast.Call) and isinstance(c.func, _ast.Name)}
        assert not (calls & {"paper_fill", "_walk", "book10l_at"}), (name, calls)  # never re-walk or re-price
    for lane, sites in pf.TILE_FILL_SITES.items():
        for module, func in sites:
            assert not pf.fill_site_violations((ROOT / module).read_text(), func), (lane, func)
    assert not pf.tile_module_violations((ROOT / "tile_fill.py").read_text(), "tile_fill.py", (("tile_fill.py", "price_entry"), ("tile_fill.py", "price_exit")))


def test_realism_v2_walk_then_rest_partial_and_trade_through(monkeypatch):
    import quote_trigger as qt
    # signal book ask 100 (cap 100.05) has 0.2 size; fill-second book the same -> walk fills 0.2 of 0.5
    monkeypatch.setattr(pf, "book10l_at", lambda t: {"s": int(t) - 1, "a": [[100.0, 0.2], [101.0, 5.0]],
                                                     "b": [[99.0, 1.0]]})
    monkeypatch.setattr(qt, "trades_between", lambda a, b: [{"ts": a + 1.0, "px": 100.04, "amt": -0.1}])
    clk = _Clock(1000.0)
    ns = _lane_ns(clk)
    order = {"research_lane": "FAMILY_RANDOM_CONTROL_TAKER_90", "signal_dir": "BUY", "qty": 0.5,
             "signal_created_ts": 999.0}
    assert ns["_paper_lane_entry_result"](order, 1000.6) is None  # walk done, resting until ttl + tape lag
    res = ns["_paper_lane_entry_result"](order, 1000.5 + 3.0 + 1.0)
    assert res["status"] == "PARTIAL" and abs(res["filled_qty"] - 0.3) < 1e-9
    assert abs(res["entry_unfilled_qty"] - 0.2) < 1e-9 and res["entry_rest_filled_qty"] == 0.1
    assert res["entry_walk_filled_qty"] == 0.2 and res["entry_rest_end_reason"] == "TTL_CANCEL"
    assert ns["_paper_lane_entry_result"](order, 2000.0) is res  # cached


def test_realism_v2_maker_rest_queue_reset_on_reprice_and_drop(monkeypatch):
    import quote_trigger as qt
    monkeypatch.setattr(pf, "book10l_at", lambda t: {"s": int(t) - 1, "a": [[100.1, 1.0]], "b": [[100.0, 2.0]]})
    prints = {"rows": []}
    monkeypatch.setattr(qt, "trades_between", lambda a, b: [r for r in prints["rows"] if a < r["ts"] <= b])
    clk = _Clock(1000.0)
    tm = {"t1": {"signal_ref": {"adaptive_entry_decision": {"action": "MAKER", "exec": {"kind": "PASSIVE",
                                                                                         "ttl_sec": 900, "reprice_sec": 60}}}}}
    ns = _lane_ns(clk)
    ns["trades_map"] = tm
    order = {"research_lane": "FAMILY_GSB1_CVD_DIV_REGIME", "trade_id": "t1", "signal_dir": "BUY", "qty": 1.0,
             "signal_created_ts": 999.0, "limit_price": 100.0}
    assert ns["_paper_lane_entry_result"](order, 1001.0) is None
    assert order["_entry_rest"]["queue_ahead"] == 2.0  # joined behind 2.0 at the bid
    # RK R0: filled = min(remaining, trade-through volume since placement - queue ahead at placement); at-limit never
    prints["rows"].append({"ts": 1004.0, "px": 100.0, "amt": -2.5, "id": 1})  # at the limit: never fills
    prints["rows"].append({"ts": 1005.0, "px": 99.99, "amt": -0.5, "id": 2})  # through 0.5 < queue 2.0: no fill
    prints["rows"].append({"ts": 1006.0, "px": 99.98, "amt": -2.0, "id": 3})  # through 2.5 - 2.0 = 0.5 -> PARTIAL
    assert ns["_paper_lane_entry_result"](order, 1010.0) is None
    st = order["_entry_rest"]
    assert st["fills"] == [[1006.0, 100.0, 0.5]] and st["remaining"] == 0.5 and st["through_seen"] == 2.5
    assert st["fill_stamps"][0]["print_ts"] == 1006.0 and st["fill_stamps"][0]["queue_ahead"] == 2.0
    assert st["fill_stamps"][0]["qty"] == 0.5
    assert ns["_paper_lane_entry_result"](order, 1011.0) is None  # re-evaluation never re-counts a print
    assert st["fills"] == [[1006.0, 100.0, 0.5]]
    order["limit_price"] = 99.9  # reprice -> queue AND through count restart at the replacement; 0.5 stays filled
    assert ns["_paper_lane_entry_result"](order, 1020.0) is None
    assert st["reprices"] == 1 and st["queue_ahead"] == 0.0 and st["through_seen"] == 0.0
    assert st["queue_reset_ts"] == 1010.0 and st["fills"] == [[1006.0, 100.0, 0.5]]
    prints["rows"].append({"ts": 1025.0, "px": 99.8, "amt": -0.3, "id": 4})
    prints["rows"].append({"ts": 1026.0, "px": 99.85, "amt": -0.9, "id": 5})  # capped at the remaining 0.2
    res = ns["_paper_lane_entry_result"](order, 1030.0)
    res = res or order.get("paper_fill_result")
    assert res and res["status"] == "FILLED" and res["entry_order_model"] == "MAKER_REST"
    assert res["filled_qty"] == pytest.approx(1.0) and res["price"] == pytest.approx((0.5 * 100.0 + 0.5 * 99.9) / 1.0)
    assert res["entry_rest_fill_rule"] == pf.MAKER_REST_RULE and res["entry_rest_queue_ahead"] == 0.0
    assert [(f["print_ts"], f["queue_ahead"], f["qty"]) for f in res["entry_rest_fill_stamps"]] == \
        [(1006.0, 2.0, 0.5), (1025.0, 0.0, 0.3), (1026.0, 0.0, 0.2)]
    assert res["entry_rest_book_age_s"] is not None and res["entry_rest_price_age_s"] == pytest.approx(1026.0 - 1010.0)
    empty = {"research_lane": "FAMILY_GSB1_CVD_DIV_REGIME", "trade_id": "t1", "_entry_rest": {"fills": []}}
    assert ns["_paper_entry_rest_drop"](empty, "X") is False  # nothing filled -> the caller cancels (NO_FILL)


def test_rk2_paper_bot_sent_exit_own_s2s_is_trigger_to_send_code_point(monkeypatch):
    """RK (2): every paper BOT_SENT exit prices with its OWN trigger -> send code point (close_position's exit_sent_ts;
    the _tp1_gate marker for a partial leg) + the pooled EXIT_RO_MKT send->ack; a resident stop stays at delay 0 and
    never asks the pool; a missing send stamp passes None (fail closed)."""
    _use_book(monkeypatch)
    seen = []
    clk = _Clock(1000.25)
    ns = _lane_ns(clk)
    ns["_paper_delay"] = lambda lane, leg, **kw: (seen.append((leg, kw)) or (0.5, "default_until_n10"))
    pos = {"research_lane": "FAMILY_GSB1_CVD_DIV_REGIME", "policy_original_qty": 1.0, "qty": 1.0}
    ns["_tp1_gate"](pos, "TP1", 1000.0, "LONG", 0.5)
    assert seen[-1] == ("exit", {"pool_key": "EXIT_RO_MKT", "own_signal_to_send_s": 0.25})
    assert ns["_paper_own_s2s_exit"](1000.0, 1000.4) == pytest.approx(0.4)
    assert ns["_paper_own_s2s_exit"](1000.0, None) is None and ns["_paper_own_s2s_exit"](1000.0, 999.0) is None
    assert ns["_paper_own_s2s_exit"](None, 1000.0) is None
    ns["_PAPER_EXIT_TLS"].no_wait = True
    try:
        pos = {"research_lane": "FAMILY_GSB1_CVD_DIV_REGIME", "dir": "LONG", "qty": 1.0, "entry": 100.0,
               "exit_trigger_ts": 1000.0, "exit_sent_ts": 1000.3}
        ns["_paper_lane_exit"](pos, "ATR_TP")
        assert seen[-1][1]["pool_key"] == "EXIT_RO_MKT" and seen[-1][1]["own_signal_to_send_s"] == pytest.approx(0.3)
        assert "0.0, \"EXCHANGE_RESIDENT_NO_DELAY\"" in _func_src((ROOT / "bot.py").read_text(encoding="utf-8"), "_paper_lane_exit")
        pos.pop("exit_sent_ts")
        ns["_paper_lane_exit"](pos, "ATR_TP")
        assert seen[-1][1]["own_signal_to_send_s"] is None
    finally:
        ns["_PAPER_EXIT_TLS"].no_wait = False
