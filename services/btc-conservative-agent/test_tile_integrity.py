"""Tile-integrity monitor: HM success tests (wrong fill, missing snapshot, P&L mismatch within one run)."""
import ast, pathlib
import tile_integrity as ti

BOOK = {100: {"s": 100, "a": [[50000.0, 0.0002], [50001.0, 1.0]], "b": [[49999.0, 1.0]]}}


def _row(px, **kw):
    return {"trade_id": "dsl-t", "side": "LONG", "qty": 0.0003, "book_s": 100, "fill_price": px,
            "fill_model_version": 1, **kw}


def _good_px():
    return (0.0002 * 50000.0 + 0.0001 * 50001.0) / 0.0003


def _eval(rows=(), pnl=None, chains=(), feed=None, v1=None):
    return ti.evaluate(now=1000.0, rows=list(rows), book_by_sec=BOOK, chains=list(chains), v1_since_ts=v1,
                       feed=feed or {"status": "GREEN", "reasons": []},
                       pnl_reconcile=pnl or {"status": "GREEN", "reasons": []})


def test_correct_fill_is_green_and_exact():
    r = _eval([_row(_good_px())])
    assert r["status"] == "GREEN"
    assert r["parts"]["fill-recompute"]["worst"][0]["diff_bp"] < ti.CODE_BUG_BP


def test_injected_wrong_fill_detected():
    r = _eval([_row(_good_px() * (1 + 3e-4))])  # +3 bp
    assert r["status"] == "RED" and any("FILL_OFF" in x for x in r["parts"]["fill-recompute"]["reasons"])
    a = _eval([_row(_good_px() * (1 + 1e-4))])  # +1 bp
    assert a["parts"]["fill-recompute"]["status"] == "AMBER"


def test_rolling10_red():
    rows = [_row(_good_px() * (1 + 0.6e-4), trade_id=f"t{i}") for i in range(10)]
    r = _eval(rows)
    assert any("ROLLING10" in x for x in r["parts"]["fill-recompute"]["reasons"])


def test_missing_snapshot_detected():
    r = _eval([_row(_good_px(), book_s=999)])
    assert r["status"] == "RED" and "MISSING_SNAPSHOT dsl-t/entry" in r["parts"]["fill-recompute"]["reasons"]
    p = _eval([{"trade_id": "x", "fill_ts": 2000.0, "fill_model_version": 1}], v1=1500.0)
    assert p["parts"]["presence"]["status"] == "RED"


def test_pnl_mismatch_detected():
    r = _eval(pnl={"status": "RED", "reasons": ["TILE_PNL != SUM(ROWS)"]})
    assert r["status"] == "RED" and r["parts"]["pnl"]["status"] == "RED"


def test_live_vs_shadow_thresholds():
    assert ti.live_vs_shadow_check([{"slippage_vs_paper_bp": 1.5}])["status"] == "AMBER"
    assert ti.live_vs_shadow_check([{"slippage_vs_paper_bp": 3.5}])["status"] == "RED"
    assert ti.live_vs_shadow_check([{"slippage_vs_paper_bp": 1.05}] * 10)["status"] == "RED"


def test_frozen_feed_uses_message_counter_not_bbo():
    w = ti.FeedWatch()
    assert w.observe(1, 100.0, 100.0)["status"] == "GREEN"
    assert w.observe(1, 100.0, 104.0)["status"] == "GREEN"      # 4 s, under 5 s
    assert w.observe(1, 100.0, 106.0)["status"] == "RED"        # no msgs 6 s and age 6 s
    assert w.observe(2, 106.0, 106.5)["status"] == "GREEN"      # messages advance (BBO may be unchanged)


def test_heartbeat_red_after_10_min():
    assert ti.heartbeat_check(None, 0)["status"] == "RED"
    assert ti.heartbeat_check({"run_ts": 0, "status": "GREEN"}, 601)["status"] == "RED"
    assert ti.heartbeat_check({"run_ts": 0, "status": "GREEN"}, 300)["status"] == "GREEN"


def test_fill_detect_lag_ms():
    out = ti.fill_detect_lag([{"correlation_id": "c", "fill_at_ts": 10.0, "fill_detected_at_ts": 10.25}])
    assert out["p50_ms"] == 250


def test_independent_of_shared_fill_function():
    src = pathlib.Path(ti.__file__).read_text(encoding="utf-8")
    names = {a.name for n in ast.walk(ast.parse(src)) if isinstance(n, (ast.Import, ast.ImportFrom))
             for a in n.names} | {getattr(n, "module", None) for n in ast.walk(ast.parse(src))
                                  if isinstance(n, ast.ImportFrom)}
    assert not any(x and ("paper_fill" in x or "book_fill" in x or "disloc" in x) for x in names)


def test_regime_coverage_gap_check():
    rows = [{"trade_id": "a", "entry_ts": 10, "regime_at_signal": "TREND"}, {"trade_id": "b", "entry_ts": 11}]
    assert ti.regime_coverage_check(rows, 5)["status"] == "AMBER"
    assert ti.regime_coverage_check(rows[:1], 5)["status"] == "GREEN"


def test_exit_leg_long_reprices_on_bid():
    row = _row(_good_px(), exit_book_s=100, exit_price=49999.0, exit_filled_qty=0.0003)
    r = ti.fill_recompute_check([row], BOOK)
    legs = {x["leg"]: x for x in r["worst"]}
    assert legs["exit"]["recomputed"] == 49999.0 and legs["exit"]["diff_bp"] == 0
    bad = ti.fill_recompute_check([_row(_good_px(), exit_book_s=100, exit_price=50010.0)], BOOK)
    assert bad["status"] == "AMBER" or bad["status"] == "RED"  # exit priced on the ask = wrong side


def test_partial_fill_uses_filled_qty():
    book = {100: {"s": 100, "a": [[50000.0, 0.0001]], "b": []}}
    r = ti.recompute_fill({"trade_id": "p", "side": "LONG", "qty": 0.0003, "book_s": 100,
                           "fill_price": 50000.0, "filled_qty": 0.0001}, book)
    assert r["recomputed"] == 50000.0 and r["recomputed_qty"] == 0.0001 and not r["qty_mismatch"]
    r2 = ti.recompute_fill({"trade_id": "p", "side": "LONG", "qty": 0.0003, "book_s": 100,
                            "fill_price": 50000.0, "filled_qty": 0.0003}, book)
    assert r2["qty_mismatch"]


def test_no_depth_is_amber():
    book = {100: {"s": 100, "a": [], "b": []}}
    r = ti.fill_recompute_check([_row(1.0)], book)
    assert r["status"] == "AMBER" and any("NO_DEPTH" in x for x in r["reasons"])


def test_book_read_failure_path():
    r = _eval_none = ti.fill_recompute_check([_row(_good_px())], None)
    assert r["status"] == "AMBER" and r["reasons"] == ["BOOK_READ_FAILED"]


def test_signed_rolling10_bias():
    rows = [_row(_good_px() * (1 + 0.3e-4), trade_id=f"t{i}") for i in range(10)]
    r = ti.fill_recompute_check(rows, BOOK)
    assert r["rolling10_signed_bp"] > 0.29  # paper paid more than the book: positive bias


def test_red_propagates_to_checks_entry():
    hb = ti.heartbeat_check({"run_ts": 100.0, "status": "RED", "parts": {"fill-recompute": {"reasons": ["FILL_OFF x"]}}}, 200.0)
    assert hb["status"] == "RED" and hb["alert"] is True and "fill-recompute: FILL_OFF x" in hb["reasons"]


def test_delay_default_share():
    d = ti.delay_default_share([{"fill_model_version": 1, "delay_source": "default_until_n10"},
                                {"fill_model_version": 1, "delay_source": "lane_p50_n12"}])
    assert d == {"n": 2, "defaulted": 1, "share": 0.5}


def test_tile_integrity_is_a_monitor_check():
    src = (pathlib.Path(__file__).parent / "bot.py").read_text(encoding="utf-8")
    i = src.find("def _monitor_checks_all(")
    body = src[i:src.find("\ndef ", i + 10)]
    assert '"tile-integrity": _tile_integrity_check(now)' in body
    j = src.find("_MONITOR_CHECK_NAMES = (")
    assert '"tile-integrity"' in src[j:src.find(")", j)]
    # /api/monitor/checks: status = worst of all checks, alert = any check alert, alerts lists names
    k = src.find("def monitor_checks_all(")
    route = src[k:src.find("\ndef ", k + 10)]
    assert 'max((c["status"] for c in checks.values())' in route and '"alerts": sorted(' in route
