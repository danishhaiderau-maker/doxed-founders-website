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
          "/api/monitor/exits/latency-guard", "/api/monitor/archive", "/api/monitor/alerts",
          "/api/monitor/shadow-exits")


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


def test_rules_conformance_fee_and_forced():
    out = mc.rules_conformance(active_tiles=["A"], retired_tiles=["R"], live_copy_tiles={}, open_lanes=["R"],
                               fee_rates_bp={"taker": 4.0}, totals_lanes={"A": {"forced_closes_counted": 1}})
    assert out["status"] == "RED" and len(out["reasons"]) == 3


def test_latency_guard_and_retention():
    assert mc.latency_guard({}, {"CTRL": {"exit": {"max_sec": 1260}}})["alert"] is True
    assert mc.latency_guard({}, {"CTRL": {"exit": {"max_sec": 1.0}}})["status"] == "GREEN"
    out = mc.tape_retention((1000.0, 2000.0, 1000), None, now=2000.0 + 3 * 3600)
    assert out["status"] == "AMBER" and out["alert"]


def test_archive_receipt():
    assert mc.archive_receipt(None, now=0)["alert"]
    ok = mc.archive_receipt({"finished_ts": 100.0, "errors": 0, "disk_free_pct": 40}, now=200.0)
    assert ok["status"] == "GREEN"
    assert mc.archive_receipt({"finished_ts": 100.0, "disk_free_pct": 4}, now=200.0)["status"] == "RED"


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


def test_archive_receipt_post_admin_only(bot):
    client = bot.app.test_client()
    payload = {"bucket": "doxed-btc-archive", "errors": 0, "disk_free_pct": 50, "finished_ts": 1.0}
    assert client.post("/api/monitor/archive/receipt", json=payload, headers={"Authorization": f"Bearer {MONITOR}"},
                       environ_base=REMOTE).status_code == 401
    assert client.post("/api/monitor/archive/receipt", json=payload, headers={"X-Bot-Admin-Token": ADMIN},
                       environ_base=REMOTE).status_code == 200
    got = client.get("/api/monitor/archive", headers={"Authorization": f"Bearer {MONITOR}"},
                     environ_base=REMOTE).get_json()
    assert got["receipt"]["bucket"] == "doxed-btc-archive"
