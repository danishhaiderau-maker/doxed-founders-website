"""HM-MISSING-20261012: integrity checks, late-window repricing, monitor-token routes."""
import json

import pytest

import monitor_api
import monitor_checks as mc
import monitor_tiles

ADMIN = "health-test-token"
MONITOR = "monitor-read-token-for-tests-0123456789"
REMOTE = {"REMOTE_ADDR": "203.0.113.9"}
ROUTES = ("/api/monitor/checks", "/api/monitor/exit-truth", "/api/monitor/pnl/reconcile",
          "/api/monitor/rules/conformance", "/api/monitor/data/completeness", "/api/monitor/tape/retention",
          "/api/monitor/exits/latency-guard", "/api/monitor/alerts",
          "/api/monitor/shadow-exits", "/api/monitor/chase_buckets", "/api/monitor/gap_buckets")


def test_late_window_table_matches_hm_audit():
    doc = monitor_tiles.late_window_table()
    assert len(doc["trades"]) == 129
    over = sum(v["booked_bp"] - v["market_bp"] for v in doc["trades"].values()) * 25e-4
    assert abs(over - 1.917) < 0.002          # HM PNL-INTEGRITY table sum


def test_late_window_row_is_flagged_and_repriced():
    tid, fix = next(iter(monitor_tiles.late_window_table()["trades"].items()))
    row = {"trade_id": tid, "pnl": {"bp": fix["booked_bp"], "net_usd": fix["booked_bp"] * 25e-4}}
    monitor_tiles.apply_late_window(row, monitor_tiles.late_window_table()["window_ts"][0] + 60, 25.0, 1.0)
    p = row["pnl"]
    assert p["late_window"] and p["repriced"] and p["bp"] == round(fix["market_bp"], 4)
    assert p["displayed_bp"] == fix["booked_bp"]
    assert abs(p["net_usd"] - fix["market_bp"] * 25e-4) < 1e-6


def test_row_outside_window_untouched():
    row = {"trade_id": "x", "pnl": {"bp": 3.0, "net_usd": 0.0075}}
    monitor_tiles.apply_late_window(row, 1.0, 25.0, 1.0)
    assert "late_window" not in row["pnl"]


def test_exit_truth_flags_stale_trigger_booking():
    rows = [{"trade_id": "a", "side": "LONG", "status": "CLOSED",
             "exit": {"close_ts": 100.0, "exit_price": 100.10, "exit_reason": "BREAKEVEN_LOCK"}}]
    out = mc.exit_truth(rows, [99.5], [{"bid": 100.0, "ask": 100.01}], since=0)
    assert out["status"] == "RED" and out["alert"] and out["violations"][0]["trade_id"] == "a"
    rows[0]["exit"]["exit_price"] = 100.0
    assert mc.exit_truth(rows, [99.5], [{"bid": 100.0, "ask": 100.01}], since=0)["status"] == "GREEN"


def test_exit_truth_vwap_multi_leg_uses_final_leg_not_blend_vs_final_touch():
    """BTC-477 / both-legs realism: gb2-899a1661f1ff booked VWAP 78301 vs final bid 78249 (+6.65 bp)
    while GS_LADDER_TP1 @ 78353 and GS_MFE_GIVEBACK @ 78249 each matched their fill-time touch.

    Comparing the VWAP blend to the final book top is a false RED; the book-top statistic must use
    the final leg's fill. Per-leg honesty stays on fill_book. A final leg booked better than the
    final touch still REDs.
    """
    snap_tp1 = {"b": [[78353.0, 0.04], [78340.0, 1.0]], "a": [[78362.0, 0.04]], "s": 1793333826}
    snap_gb = {"b": [[78249.0, 0.46], [78240.0, 1.0]], "a": [[78277.0, 0.1]], "s": 1793333846}
    legs = [
        {"price": 78353.0, "qty": 0.00016, "reason": "GS_LADDER_TP1", "trigger_price": 78353.0},
        {"price": 78249.0, "qty": 0.00016, "reason": "GS_MFE_GIVEBACK", "trigger_price": 78249.0},
    ]
    row = {
        "trade_id": "gb2-899a1661f1ff", "side": "LONG", "status": "CLOSED",
        "lane": "FAMILY_GSB2_REGIME_SWITCHER",
        "exit": {
            "close_ts": 1793333847.510706, "exit_price": 78301.0, "exit_price_basis": "VWAP_ALL_LEGS",
            "exit_reason": "GS_MFE_GIVEBACK", "exit_book_top_bid": 78249.0, "exit_book_top_ask": 78277.0,
            "exit_book_top_s": 1793333846.0, "legs": legs,
        },
        "pricing": {"exit_depth_snapshot_legs": [
            {"price": 78353.0, "qty": 0.00016, "fill": snap_tp1},
            {"price": 78249.0, "qty": 0.00016, "fill": snap_gb},
        ]},
    }
    # Naive VWAP-vs-final-touch would be +6.65 bp RED; both-legs compare uses final leg 78249 → GREEN.
    vwap_better = (78301.0 / 78249.0 - 1) * 1e4
    assert vwap_better > mc.EXIT_TRUTH_RED_BP
    out = mc.exit_truth([row], [], [], since=0)
    assert out["status"] == "GREEN" and not out["violations"] and not out["fill_book"]["violations"]
    assert out["checked"] == 1 and out["fill_book"]["max_better_bp"] == 0.0
    # Final leg booked better than the final touch → still RED on the book-top statistic.
    bad = {**row, "exit": {**row["exit"], "legs": [legs[0], {**legs[1], "price": 78301.0}]}}
    bad["pricing"] = {"exit_depth_snapshot_legs": [
        {"price": 78353.0, "qty": 0.00016, "fill": snap_tp1},
        {"price": 78301.0, "qty": 0.00016, "fill": snap_gb},
    ]}
    red = mc.exit_truth([bad], [], [], since=0)
    assert red["status"] == "RED"
    assert red["violations"][0]["trade_id"] == "gb2-899a1661f1ff"
    assert red["violations"][0]["booked"] == 78301.0
    assert red["violations"][0]["booked_better_than_touch_bp"] > mc.EXIT_TRUTH_RED_BP
    assert red["fill_book"]["violations"]  # final leg also better than its fill-time bid


def test_exit_truth_fill_book_statistic_sits_beside_trigger_check():
    """HM 19 Oct: the trigger-time comparison stays; a second stat compares the booked leg with the fill-time book."""
    snap = {"b": [[100.0, 1.0], [99.0, 1.0]], "a": [[100.5, 1.0]], "s": 1}
    row = {"trade_id": "a", "side": "LONG", "status": "CLOSED",
           "exit": {"close_ts": 100.0, "exit_price": 100.0, "exit_reason": "GS_THESIS_CUT", "exit_bid": 99.0, "exit_ask": 99.2},
           "pricing": {"exit_depth_snapshot_legs": [{"price": 100.0, "qty": 0.1, "fill": snap, "signal": snap}]}}
    out = mc.exit_truth([row], [], [], since=0)
    assert out["status"] == "RED" and out["violations"]            # trigger-time view still reported (reference 99.0 is low)
    assert out["fill_book"]["checked"] == 1 and out["fill_book"]["max_better_bp"] == 0.0 and not out["fill_book"]["violations"]
    row["pricing"]["exit_depth_snapshot_legs"][0]["price"] = 100.2          # better than any level in the walked book
    out = mc.exit_truth([row], [], [], since=0)
    assert out["fill_book"]["violations"][0]["trade_id"] == "a" and out["fill_book"]["max_better_bp"] > 5
    del row["pricing"]
    assert mc.exit_truth([row], [], [], since=0)["fill_book"]["without_snapshot"] == 1


def test_rules_conformance_fee_and_forced():
    out = mc.rules_conformance(active_tiles=["A"], retired_tiles=["R"], live_copy_tiles={}, open_lanes=["R"],
                               fee_rates_bp={"taker": 4.0}, totals_lanes={"A": {"forced_closes_counted": 1}})
    assert out["status"] == "RED" and len(out["reasons"]) == 4  # incl. A not live-copyable


def test_latency_guard_and_retention():
    assert mc.latency_guard({}, {"CTRL": {"exit": {"max_sec": 1260}}})["alert"] is True
    assert mc.latency_guard({}, {"CTRL": {"exit": {"max_sec": 1.0}}})["status"] == "GREEN"
    out = mc.tape_retention((1000.0, 2000.0, 1000), None, now=2000.0 + 3 * 3600)
    assert out["status"] == "AMBER" and out["alert"]


@pytest.fixture
def bot(monkeypatch, tmp_path):
    import bot as bot_module
    monkeypatch.setattr(bot_module, "_BOT_ADMIN_TOKEN", ADMIN)
    monkeypatch.setattr(bot_module, "_MONITOR_READ_TOKEN", MONITOR)
    monkeypatch.setattr(bot_module, "_DASHBOARD_BOOTSTRAP_COMPLETE", True)
    monkeypatch.setattr(bot_module, "_API_RATE_MAX_PER_IP", 10_000)
    monkeypatch.setattr(bot_module, "_data_sync_volume_root", lambda: tmp_path)
    return bot_module


def test_routes_monitor_token_readable_and_401_without(bot):
    client = bot.app.test_client()
    for path in ROUTES:
        assert client.get(path, environ_base=REMOTE).status_code == 401, path
        r = client.get(path, headers={"Authorization": f"Bearer {MONITOR}"}, environ_base=REMOTE)
        assert r.status_code == 200, (path, r.get_json())
    body = client.get("/api/monitor/checks", headers={"Authorization": f"Bearer {MONITOR}"},
                      environ_base=REMOTE).get_json()
    assert set(body["checks"]) == set(bot._MONITOR_CHECK_NAMES) and "alert" in body


def test_archive_receipt_post_needs_the_dedicated_r2_token_not_admin_or_monitor(bot, monkeypatch):
    import time as _t
    monkeypatch.setenv("R2_RECEIPT_TOKEN", "r2-only-token")
    client = bot.app.test_client()
    payload = {"schema": "r2_backup_receipt_v2", "kind": "sync", "host": "laptop", "bucket": "doxed-btc-archive",
               "errors": 0, "ok": True, "finished_ts": _t.time() - 5, "state_keys": 20, "r2_objects": 10,
               "missing": 0, "quarantined": 0, "quarantine_pending": 0, "outside_keep": 0,
               "keep_manifest_sha256": bot._r2_keep_pin_sha256(),
               "restore_test": {"checked": 10, "ok": 10, "bad": 0, "missing": 0, "passed": True}}
    for headers in ({"Authorization": f"Bearer {MONITOR}"}, {"X-Bot-Admin-Token": ADMIN}, {}):
        assert client.post("/api/monitor/archive/receipt", json=payload, headers=headers,
                           environ_base=REMOTE).status_code == 401
    assert client.post("/api/monitor/archive/receipt", json=payload, headers={"X-R2-Receipt-Token": "r2-only-token"},
                       environ_base=REMOTE).status_code == 200
    # The archive-receipt check and its GET route are retired (18 Oct, no permanent archive).
    assert client.get("/api/monitor/archive", headers={"Authorization": f"Bearer {MONITOR}"},
                      environ_base=REMOTE).status_code == 410


def test_tape_archive_strips_unlisted_fields(tmp_path):
    # The DISLOC 30m-shadow half of this test went with disloc_engine (Tiles 6/7 removed 23 Oct 2026).
    import tape_archive
    a = tape_archive.TapeArchive(lambda: tmp_path, clock=lambda: 1_760_000_000.0)
    a._thread = object()  # no background thread in tests
    a.append({"bucket_ts": 1_760_000_000, "bid": 1.0, "ask": 2.0, "x": 9}, ("bid", "ask"))
    assert a.flush() == 1 and a.oldest_ts() is not None
    import gzip
    f = next((tmp_path / "tape_1s").glob("*.jsonl.gz"))
    assert '"x"' not in gzip.decompress(f.read_bytes()).decode()


def test_lanes_use_late_window_basis():
    tid, fix = next(iter(monitor_tiles.late_window_table()["trades"].items()))
    row = {"trade_id": tid, "net_pnl_usd_raw": fix["booked_bp"] * 25e-4, "net_pnl_bp": fix["booked_bp"],
           "close_ts": 1, "direction": "LONG"}
    out = monitor_api.lane_stats([row])
    assert abs(out["net_usd"] - round(fix["market_bp"] * 25e-4, 6)) < 1e-6


def test_chase_gap_tables_and_side_scores():
    import chase_gap as cg
    import bot as b
    rows = [{"limit_chase_count": 0, "net_pnl_bp": 4.0, "close_ts": 3600 * i, "score_gap": 2.5, "exit_reason": "X"}
            for i in range(4)] + [{"limit_chase_count": 1, "net_pnl_bp": -2.0, "close_ts": 10, "exit_reason": "ADMIN_MANUAL_CLOSE"}]
    v = cg.chase_view(rows, [{"limit_chase_count": 0}], {"ADMIN_MANUAL_CLOSE"})
    assert v["buckets"]["0"]["fills"] == 4 and v["buckets"]["0"]["reached"] == 5 and v["buckets"]["0"]["mean_bp"] == 4.0
    assert "1" not in v["buckets"]
    assert cg.gap_view(rows, [], set())["buckets"]["2-3"]["n"] == 4
    s = b._trade_side_scores({"dir": "SHORT"}, {"signal_ref": {"long_score": 3, "short_score": 7}})
    assert s == {"long_score": 3.0, "short_score": 7.0, "score_gap": 4.0}


def test_checks_and_exit_bbo_with_a_populated_tape_ring(bot, monkeypatch):
    import time
    import math
    import paper_fill
    ring = monitor_tiles.TapeSliceRing()
    now = time.time()
    for i in range(5):
        ring.append({"bucket_ts": int(now) - 4 + i, "bid": 100.0, "ask": 100.1})
    monkeypatch.setattr(bot, "_TILE_TAPE_RING", ring)
    # R3: the close stamp is the book top from bfx_book_10l_1s rows, never the ticker tape ring.
    monkeypatch.setattr(paper_fill, "book10l_at", lambda t: {"s": float(math.floor(t) - 1),
                                                             "b": [[99.5, 1.0]], "a": [[99.7, 1.0]]})
    row = {}
    bot._stamp_exit_bbo(row)
    assert row.get("exit_book_top_bid") == 99.5 and row.get("exit_book_top_ask") == 99.7
    assert row.get("exit_book_top_source") in ("BOOK_AT_CLOSE", "BOOK_AT_FILL_TS")
    assert "exit_bid" not in row and "exit_ask" not in row
    monkeypatch.setattr(paper_fill, "book10l_at", lambda t: {"s": float(t) - 30, "b": [[1, 1]], "a": [[2, 1]]})
    stale = {}
    bot._stamp_exit_bbo(stale)
    assert stale == {"exit_book_top_missing": True}
    r = bot.app.test_client().get("/api/monitor/checks", headers={"Authorization": f"Bearer {MONITOR}"},
                                  environ_base=REMOTE)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["checks"]["data-completeness"]["status"] in ("GREEN", "AMBER", "RED")


def test_stamp_exit_bbo_prefers_fill_snapshot_over_later_wall_book(monkeypatch):
    """Exit-truth R3: stamp the fill-time touch (CTRL/B2 false RED was wall-clock book after a stop cascade)."""
    import ast
    import pathlib
    import time as _time
    import paper_fill
    import quote_trigger as qt
    src = pathlib.Path(__file__).resolve().parent.joinpath("bot.py").read_text(encoding="utf-8")
    node = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "_stamp_exit_bbo")
    ns = {"time": _time, "paper_fill": paper_fill, "quote_trigger": qt}
    exec(compile(ast.Module([node], []), "bot.py", "exec"), ns)
    # Wall book is worse (LONG exit would look +6 bp better than this later bid if stamped from now).
    monkeypatch.setattr(paper_fill, "book10l_at",
                        lambda t: {"s": 200, "b": [[99.94, 1.0]], "a": [[99.96, 1.0]]})
    row = {"exit_depth_snapshot_fill": {"s": 100, "b": [[100.0, 1.0]], "a": [[100.02, 1.0]]},
           "exit_price": 100.0}
    ns["_stamp_exit_bbo"](row)
    assert row["exit_book_top_bid"] == 100.0 and row["exit_book_top_ask"] == 100.02
    assert row["exit_book_top_s"] == 100 and row["exit_book_top_source"] == "FILL_SNAPSHOT"
    # exit-truth vs fill-time touch is GREEN; vs wall book would have been RED.
    out = mc.exit_truth([{"trade_id": "ctrl", "side": "LONG", "status": "CLOSED",
                          "exit": {"close_ts": 100.0, "exit_price": 100.0, "exit_reason": "INITIAL_ATR_STOP",
                                   "exit_book_top_bid": row["exit_book_top_bid"],
                                   "exit_book_top_ask": row["exit_book_top_ask"]}}], [], [], since=0)
    assert out["status"] == "GREEN" and not out["violations"]
    wall = mc.exit_truth([{"trade_id": "ctrl", "side": "LONG", "status": "CLOSED",
                           "exit": {"close_ts": 100.0, "exit_price": 100.0, "exit_reason": "INITIAL_ATR_STOP",
                                    "exit_book_top_bid": 99.94, "exit_book_top_ask": 99.96}}], [], [], since=0)
    assert wall["status"] == "RED" and wall["violations"][0]["booked_better_than_touch_bp"] > 5.0
