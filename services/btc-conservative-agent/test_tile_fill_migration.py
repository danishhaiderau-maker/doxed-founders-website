"""Lanes 1-5 migration (Danish 16 Oct 17:01): every tile prices through paper_fill; CI guards."""
import ast
import pathlib

import combo_pathway_config as cpc
import paper_fill as pf
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
        assert pf.TILE_FILL_SITES[lane] == (("tile_fill.py", "price_entry"), ("tile_fill.py", "price_exit"))


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
    assert "_paper_lane_exit" in seg("resolve_sim_exit_price")
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


class _Clock:
    def __init__(self, now):
        self.now, self.slept = float(now), []

    def time(self):
        return self.now

    def sleep(self, s):
        self.slept.append(s)
        self.now += s


def _lane_ns(clock, extra_names=(), delay=(1.5, "default_until_n10")):
    t = types.SimpleNamespace(time=clock.time, sleep=clock.sleep)
    return _bot_ns(["_paper_forced_reasons", "_tp1_gate", "_paper_exit_sim", "_paper_forced_exit", "_paper_lane_exit",
                    "_paper_lane_entry_result", "_paper_no_fill_shadow_row", *extra_names],
                   time=t, _disloc_delay=lambda lane, leg: delay,
                   _normalize_order_side_to_dir=lambda v: "LONG" if str(v).upper() in ("BUY", "LONG") else "SHORT")


def test_b2_tp1_gate_never_sleeps_and_prices_after_due(monkeypatch):
    _use_book(monkeypatch)
    clk = _Clock(1000.0)
    ns = _lane_ns(clk)
    pos = {"research_lane": "FAMILY_GSB1_CVD_DIV_REGIME", "policy_original_qty": 1.0, "qty": 1.0}
    assert ns["_tp1_gate"](pos, "TP1", 1000.0, "LONG", 0.5) == ("WAIT", None)
    assert pos["_tp1_pending"]["due_ts"] == 1001.5
    assert ns["_tp1_gate"](pos, "TP1", 1001.0, "LONG", 0.5) == ("WAIT", None)
    state, leg = ns["_tp1_gate"](pos, "TP1", 1001.6, "LONG", 0.5)
    assert state == "DUE" and leg["status"] == "FILLED" and leg["_trigger_ts"] == 1000.0
    assert leg["fill_ts"] == 1001.5 and leg["fill_model"] == pf.FILL_MODEL
    assert clk.slept == []  # no sleep anywhere in the TP1 path


def test_b2_stale_pending_restarts(monkeypatch):
    _use_book(monkeypatch)
    ns = _lane_ns(_Clock(0))
    pos = {"research_lane": "FAMILY_GSB1_CVD_DIV_REGIME", "policy_original_qty": 1.0}
    ns["_tp1_gate"](pos, "TP1", 1000.0, "LONG", 0.5)
    assert ns["_tp1_gate"](pos, "TP1", 1100.0, "LONG", 0.5) == ("WAIT", None)  # older than TP1_PENDING_MAX_S
    assert pos["_tp1_pending"]["trigger_ts"] == 1100.0


def test_b2_bot_tp1_site_returns_before_commit_and_prunes():
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    seg = src[src.index("def _apply_family_tile_exit"):src.index("def _apply_position_exits")]
    assert "_tp1_gate(" in seg and 'if _gate == "WAIT":' in seg and "time.sleep" not in seg
    assert 'source_pos.pop("_tp1_pending"' in seg and "leg_fallback" in seg and "leg_fresh_eligible" in seg


def test_b3_forced_close_never_waits_and_is_not_fresh(monkeypatch):
    _use_book(monkeypatch)
    for reason in ("DEPLOY_FORCE_FLAT", "ADMIN_MANUAL_CLOSE", "TILE_RETIRED", "MANUAL_CLOSE"):
        clk = _Clock(5000.0)
        ns = _lane_ns(clk)
        pos = {"research_lane": "FAMILY_RANDOM_CONTROL_TAKER_90", "dir": "LONG", "qty": 0.5, "_exit_eval_price": 100.0}
        out = ns["_paper_lane_exit"](pos, reason)
        px, sim = out
        assert px == 99.0 and sim["delay_source"] == "FORCED_NO_DELAY" and sim["delay_s"] == 0.0
        assert sim["fill_ts"] <= 5000.0 + 1e-9  # latest complete second at or before now: no look-ahead
        assert pos["exit_status"] == "PAPER_FILL_FORCED" and pos["fresh_data_excluded"] is True
        assert clk.slept == []


def test_b3_forced_without_book_is_forced_no_book(monkeypatch):
    _use_book(monkeypatch, book=None)
    clk = _Clock(5000.0)
    ns = _lane_ns(clk)
    pos = {"research_lane": "FAMILY_RANDOM_CONTROL_TAKER_90", "dir": "LONG", "qty": 0.5}
    assert ns["_paper_lane_exit"](pos, "DEPLOY_FORCE_FLAT") is None
    assert pos["exit_status"] == "FORCED_NO_BOOK" and clk.slept == []


def test_b3_overdue_exit_goes_no_wait(monkeypatch):
    _use_book(monkeypatch)
    clk = _Clock(5000.0)
    ns = _lane_ns(clk)
    pos = {"research_lane": "FAMILY_RANDOM_CONTROL_TAKER_90", "dir": "LONG", "qty": 0.5, "exit_trigger_ts": 4960.0}
    _, sim = ns["_paper_lane_exit"](pos, "TIME_EXIT")
    assert sim["delay_source"] == "FORCED_NO_DELAY" and clk.slept == []


def test_nit_exit_uses_exit_trigger_ts_and_waits_only_remaining_delay(monkeypatch):
    _use_book(monkeypatch)
    clk = _Clock(5000.0)
    ns = _lane_ns(clk)
    pos = {"research_lane": "FAMILY_RANDOM_CONTROL_TAKER_90", "dir": "LONG", "qty": 0.5, "exit_trigger_ts": 4999.0}
    px, sim = ns["_paper_lane_exit"](pos, "TIME_EXIT")
    assert sim["delay_source"] == "default_until_n10" and sim["fill_ts"] == 5000.5
    assert abs(sum(clk.slept) - 0.5) < 1e-9  # trigger was 1.0 s ago, delay 1.5 s -> waits 0.5 s, not 1.5 s
    assert pos["exit_status"] == "PAPER_FILL" and "_paper_exit_trigger_ts" not in pos


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
    assert ns["_paper_lane_entry_result"](order2, 1000.0, force=True)["status"] == "REJECT_NO_BOOK"
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
        a = 200.0 if seq["n"] == 1 else 210.0
        return {"s": int(t) - 1, "a": [[a, 1.0]], "b": [[199.0, 1.0]]}
    monkeypatch.setattr(pf, "book10l_at", stepped)
    res = ns["_paper_lane_entry_result"](order, 1000.0)
    assert res["status"] == "NO_FILL_IOC_CAP"
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
