"""HM post-#872 nits: /api/monitor/trade-count exposes the total_trades overlay; live_flags.force_paper_mode
reads the same governing value as /api/status."""
import json
from pathlib import Path

import bot
import live_copy_monitor_api as lcm

PATH = "/api/monitor/trade-count"
CENSUS = json.loads((Path(__file__).resolve().parent / "ci" / "route_census_fields.json").read_text())


def test_trade_count_route_census_auth_and_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "_BOT_ADMIN_TOKEN", "t" * 24)
    monkeypatch.setattr(bot, "_DASHBOARD_BOOTSTRAP_COMPLETE", True)
    assert PATH in bot._MONITOR_TILE_PATHS
    methods = {m for r in bot.app.url_map.iter_rules() if r.rule == PATH for m in r.methods}
    assert methods and methods <= {"GET", "HEAD", "OPTIONS"}
    assert bot.app.test_client().get(PATH, environ_base={"REMOTE_ADDR": "203.0.113.9"}).status_code == 401
    csv = tmp_path / "t.csv"
    csv.write_text("id\na\nb\n")
    monkeypatch.setattr(bot, "CSV_TRADES", str(csv))
    monkeypatch.setattr(bot, "monitor_integrity_authorized", lambda: True)
    monkeypatch.setitem(bot.state, "analytics", {"total_trades": 1})
    body = bot.app.test_client().get(PATH).get_json()
    for k in CENSUS["routes"][PATH]["top"]:
        assert k in body, k
    assert body["total_trades_disk"] == 2 and body["total_trades_memory"] == 1 and body["total_trades"] == 2
    assert body["total_trades_source"] == "DISK_CSV" and body["total_trades_mismatch"] is True
    assert body["status"] == "AMBER"
    monkeypatch.setitem(bot.state, "analytics", {"total_trades": 2})
    body = bot.app.test_client().get(PATH).get_json()
    assert body["total_trades_mismatch"] is False and body["status"] == "GREEN"


def test_live_flags_force_paper_matches_status_when_live_copy_source_enabled(monkeypatch):
    # Root cause: live_flags reported the live-copy paper LOCK (FORCE_PAPER and not LIVE_COPY_SOURCE_ENABLED)
    # under the name force_paper_mode; with LIVE_COPY_SOURCE_ENABLED=1 it read False while /api/status read True.
    monkeypatch.setenv("FORCE_PAPER_MODE", "1")
    monkeypatch.setenv("LIVE_COPY_SOURCE_ENABLED", "1")
    ctx = {"force_paper_mode": bot._live_copy_paper_lock_active(),
           "fly_direct_force_paper_mode": bot._force_paper_mode_active(), "output": {}}
    monkeypatch.setitem(lcm._CTX, "provider", lambda: ctx)
    lcm._LIVE_FLAGS_CACHE.clear()
    out = lcm.live_flags_snapshot(max_age_s=-1)
    lcm._LIVE_FLAGS_CACHE.clear()
    assert out["force_paper_mode"] is True is bot._force_paper_mode_active()
    assert out["live_copy_paper_lock"] is False
