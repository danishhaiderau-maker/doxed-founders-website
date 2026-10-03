"""Read-only monitor APIs: digest token isolation, sanitization caps, summary/lanes shape, /ready pre-registration."""
import importlib.util
import json
import time
from pathlib import Path

import pytest

import combo_pathway_config as registry
import monitor_api

ADMIN = "health-test-token"
MONITOR = "monitor-read-token-for-tests-0123456789"
REMOTE = {"REMOTE_ADDR": "198.51.100.7"}
LAPTOP_DIGEST = Path(__file__).resolve().parents[2] / "scripts" / "grokbot_digest.py"


def _report(**extra):
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return {"schema": "system_health_v1", "verdict": "AMBER", "generated_at": now, "counts": {"GREEN": 3, "AMBER": 1},
            "failing": [{"id": "railway.relay", "status": "AMBER", "observed": "mode PAUSED armedAt None"}],
            "open_alarms": [{"id": "analyzer.studies", "since": "2026-10-02T09:00:00Z"}],
            "check_status": {"railway.relay": "AMBER", "bitfinex.exposure": "GREEN"}, **extra}


def _digest(**extra):
    return {"schema": "grokbot_digest_v1", "generated_at": "2026-10-03T14:00:00Z", "read_only": True,
            "watcher": {"verdict": "AMBER", "failing": [{"id": "x", "observed": "token=abc123 at C:\\Users\\me\\vault"}]},
            "tiles_24h": [{"lane": "L", "closes": 3, "net_usd": -0.1}], **extra}


def test_scrub_rules_match_the_laptop_digest():
    if not LAPTOP_DIGEST.exists():
        pytest.skip("scripts/grokbot_digest.py not in this checkout")
    spec = importlib.util.spec_from_file_location("grokbot_digest", LAPTOP_DIGEST)
    laptop = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(laptop)
    samples = ["Bearer abc.def", "password: hunter2", "see C:\\Users\\x\\y.json", "https://h/p?key=v",
               "a" * 50, "x" * 400, 7, None, True]
    assert [monitor_api.scrub(s) for s in samples] == [laptop.scrub(s) for s in samples]


def test_sanitize_digest_keeps_known_sections_and_redacts():
    assert monitor_api.sanitize_digest({"schema": "other"}) is None
    assert monitor_api.sanitize_digest("not a mapping") is None
    raw = _digest(unknown_section={"a": 1}, fees={"status": "OK", "api_key": "k", "BOT_ADMIN_TOKEN": "t"})
    out = monitor_api.sanitize_digest(raw)
    assert "unknown_section" not in out and out["read_only"] is True
    assert out["fees"] == {"status": "OK"}
    observed = out["watcher"]["failing"][0]["observed"]
    assert "abc123" not in observed and "Users" not in observed and "<redacted>" in observed


def test_sanitize_digest_caps_size_and_depth():
    big = _digest(tile_verdicts=[{"key": f"k{i}", "note": "n" * 230} for i in range(40)],
                  analyzer={"sections_not_green": [{"id": "s" * 60, "label": "l" * 230} for _ in range(40)]},
                  selfaware={"findings": [{"observed": "o" * 230, "expected": "e" * 230} for _ in range(40)]},
                  capacity={"a": {"b": {"c": {"d": {"e": {"f": {"g": 1}}}}}}})
    out = monitor_api.sanitize_digest(big)
    assert len(json.dumps(out).encode("utf-8")) <= monitor_api.MAX_DIGEST_BYTES
    assert out["capacity"]["a"]["b"]["c"]["d"]["e"] is None
    many = _digest(tiles_24h=[{"lane": "x" * 200, "n": i} for i in range(5000)])
    assert len(monitor_api.sanitize_digest(many)["tiles_24h"]) <= monitor_api.MAX_ITEMS


def test_monitor_token_configuration_and_bearer_match():
    assert monitor_api.configured_monitor_token("", ADMIN) == ""
    assert monitor_api.configured_monitor_token("short", ADMIN) == ""
    assert monitor_api.configured_monitor_token(MONITOR, MONITOR) == ""
    assert monitor_api.configured_monitor_token(f"  {MONITOR} ", ADMIN) == MONITOR
    assert monitor_api.bearer_matches(f"Bearer {MONITOR}", MONITOR)
    assert monitor_api.bearer_matches(f"bearer {MONITOR}", MONITOR)
    assert not monitor_api.bearer_matches(MONITOR, MONITOR)
    assert not monitor_api.bearer_matches(f"Basic {MONITOR}", MONITOR)
    assert not monitor_api.bearer_matches(f"Bearer {MONITOR}x", MONITOR)
    assert not monitor_api.bearer_matches(f"Bearer {MONITOR}", "")


def test_lane_stats_drawdown_is_true_peak_to_trough():
    rows = [
        {"close_ts": 3, "net_pnl_usd": -3.0, "notional_usd": 25.0, "direction": "SHORT"},
        {"close_ts": 1, "net_pnl_usd": 1.0, "notional_usd": 25.0, "direction": "LONG", "book_slippage_usd": 0.02},
        {"close_ts": 4, "net_pnl_usd": 1.0, "notional_usd": 25.0, "direction": "LONG", "book_slippage_usd": 0.04},
        {"close_ts": 2, "net_pnl_usd": 0.0, "notional_usd": 25.0, "direction": "SHORT"},
    ]
    out = monitor_api.lane_stats(rows)
    assert (out["closes"], out["wins"], out["losses"]) == (4, 2, 1)
    assert out["net_usd"] == -1.0 and out["long_net_usd"] == 2.0 and out["short_net_usd"] == -3.0
    assert out["max_drawdown_usd"] == 3.0
    assert out["mean_bp"] == -100.0
    assert out["mean_book_slippage_usd"] == 0.03
    assert out["last_trade_at"] == "1970-01-01T00:00:04Z"
    assert monitor_api.lane_stats([])["mean_bp"] is None


def test_digest_view_staleness_uses_receive_clock():
    fresh = monitor_api.digest_view({"digest": {"a": 1}, "received_ts": 1000.0}, 1100.0, "boot-1")
    assert fresh["stale"] is False and fresh["age_sec"] == 100 and fresh["boot_id"] == "boot-1"
    old = monitor_api.digest_view({"digest": {"a": 1}, "received_ts": 0.0}, 10_000.0, "boot-1")
    assert old["stale"] is True
    assert monitor_api.digest_view(None, 1.0, "b")["stale"] is True


def test_pre_registration_is_read_from_the_registry_only():
    for lane in registry.ACTIVE_TILE_ORDER:
        spec = registry.ACTIVE_TILE_REGISTRY[lane]
        summary = registry.tile_pre_registration_summary(lane)
        pre = spec.get("pre_registration") or {}
        assert summary["declared"] is bool(pre)
        assert summary["registered_at"] == pre.get("registered_utc")
        assert summary["kill"]["summary"] == spec.get("kill_criteria")
        assert summary["promote"]["summary"] == spec.get("promotion_criteria")
        json.dumps(summary)
    signature = registry.active_tile_registry_signature()
    assert all("pre_registration" not in tile for tile in registry.active_tile_lifecycle_manifest())
    assert registry.active_tile_registry_signature() == signature


# ----------------------------------------------------------------- Fly routes

@pytest.fixture
def bot(monkeypatch):
    import bot as bot_module

    monkeypatch.setattr(bot_module, "_BOT_ADMIN_TOKEN", ADMIN)
    monkeypatch.setattr(bot_module, "_MONITOR_READ_TOKEN", MONITOR)
    monkeypatch.setattr(bot_module, "_DASHBOARD_BOOTSTRAP_COMPLETE", True)
    monkeypatch.setattr(bot_module, "BOT_INSTANCE_ID", "dashboard-test-boot")
    monkeypatch.setitem(bot_module._SYSTEM_HEALTH_REPORT, "report", None)
    monkeypatch.setitem(bot_module._SYSTEM_HEALTH_ALARMS, "statuses", {})
    monkeypatch.setitem(bot_module._SYSTEM_HEALTH_ALARMS, "statuses_at", None)
    monkeypatch.setitem(bot_module._MONITOR_DIGEST, "digest", None)
    monkeypatch.setitem(bot_module._MONITOR_DIGEST, "received_ts", None)
    monkeypatch.setattr(bot_module, "_MONITOR_CACHE", {})
    monkeypatch.setattr(bot_module, "_API_RATE_MAX_PER_IP", 10_000)
    return bot_module


def test_digest_route_is_404_when_token_unset(bot, monkeypatch):
    monkeypatch.setattr(bot, "_MONITOR_READ_TOKEN", "")
    client = bot.app.test_client()
    for headers in ({}, {"Authorization": f"Bearer {MONITOR}"}, {"X-Bot-Admin-Token": ADMIN}):
        assert client.get("/api/monitor/digest", headers=headers, environ_base=REMOTE).status_code == 404


def test_digest_route_accepts_only_the_monitor_token(bot):
    client = bot.app.test_client()
    get = lambda **h: client.get("/api/monitor/digest", headers=h, environ_base=REMOTE)  # noqa: E731
    assert get().status_code == 401
    assert get(**{"X-Bot-Admin-Token": ADMIN}).status_code == 401
    assert get(Authorization=f"Bearer {ADMIN}").status_code == 401
    ok = get(Authorization=f"Bearer {MONITOR}")
    assert ok.status_code == 200 and ok.headers["Cache-Control"] == "no-store"
    body = ok.get_json()
    assert body["schema"] == monitor_api.DIGEST_SCHEMA and body["stale"] is True and body["digest"] is None
    assert body["boot_id"] == "dashboard-test-boot"


def test_monitor_token_never_authorizes_admin_or_post_routes(bot):
    client = bot.app.test_client()
    monitor = {"Authorization": f"Bearer {MONITOR}"}
    assert client.get("/debug_state", headers=monitor, environ_base=REMOTE).status_code == 401
    assert client.get("/api/research-segments/v2/head", headers=monitor, environ_base=REMOTE).status_code == 401
    assert client.post("/api/pause", headers=monitor, json={}, environ_base=REMOTE).status_code == 401
    assert client.post("/api/system-health/report", headers=monitor, json=_report(),
                       environ_base=REMOTE).status_code == 401
    assert client.post("/api/monitor/digest", headers=monitor, environ_base=REMOTE).status_code in (401, 405)
    full = client.get("/api/state", headers=monitor, environ_base=REMOTE)
    assert full.status_code != 200 or full.get_json().get("public_sanitized") is True


def test_watcher_push_feeds_the_sanitized_digest(bot):
    client = bot.app.test_client()
    admin = {"X-Bot-Admin-Token": ADMIN}
    assert client.post("/api/system-health/report", json=_report(monitor_digest={"schema": "nope"}),
                       headers=admin, environ_base=REMOTE).status_code == 200
    assert bot._MONITOR_DIGEST["digest"] is None
    assert client.post("/api/system-health/report", json=_report(monitor_digest=_digest()),
                       headers=admin, environ_base=REMOTE).status_code == 200
    body = client.get("/api/monitor/digest", headers={"Authorization": f"Bearer {MONITOR}"},
                      environ_base=REMOTE).get_json()
    assert body["stale"] is False and body["digest"]["schema"] == "grokbot_digest_v1"
    assert "abc123" not in json.dumps(body)
    assert client.get("/api/system-health", environ_base=REMOTE).status_code == 200


def test_allowlist_is_consistent(bot):
    assert "/api/ready" in bot._READ_ONLY_GET_PATHS
    assert "/debug_state" not in bot._READ_ONLY_GET_PATHS
    assert bot._MONITOR_DIGEST_PATH not in bot._READ_ONLY_GET_PATHS
    client = bot.app.test_client()
    assert client.get("/debug_state", environ_base=REMOTE).status_code == 401
    assert client.get("/debug_state", headers={"X-Bot-Admin-Token": ADMIN}, environ_base=REMOTE).status_code == 200


def test_api_ready_is_public_and_carries_pre_registration(bot):
    client = bot.app.test_client()
    for path in ("/ready", "/api/ready"):
        response = client.get(path, environ_base=REMOTE)
        assert response.status_code in (200, 503), path
        tiles = response.get_json()["active_tiles"]
        assert [t["lane"] for t in tiles] == list(registry.ACTIVE_TILE_ORDER)
        for tile in tiles:
            assert tile["pre_registration"] == json.loads(json.dumps(
                registry.tile_pre_registration_summary(tile["lane"])))


def _trade(lane, ts, net, direction="SHORT"):
    return {"research_lane": lane, "ts": ts, "net_pnl_usd": net, "margin_usdt": 0.25, "leverage": 100,
            "dir": direction, "book_slippage_usd_total": 0.01}


def test_lanes_are_active_only_bounded_and_carry_boot_id(bot, monkeypatch):
    active = registry.ACTIVE_TILE_ORDER[0]
    rows = [_trade(active, "2026-10-03T10:00:00Z", 0.05, "LONG"), _trade(active, "2026-10-03T11:00:00Z", -0.10),
            _trade("FAMILY_RETIRED_LANE_FOR_TEST", "2026-10-03T11:00:00Z", 9.0)]
    monkeypatch.setattr(bot, "trades", rows)
    monkeypatch.setattr(bot, "_showcase_trade_session_start", lambda: 0.0)
    response = bot.app.test_client().get("/api/monitor/lanes", environ_base=REMOTE)
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    assert len(response.data) < monitor_api.MAX_LANES_BYTES
    body = response.get_json()
    assert body["boot_id"] == "dashboard-test-boot"
    assert [row["lane"] for row in body["lanes"]] == list(registry.ACTIVE_TILE_ORDER)
    first = body["lanes"][0]
    assert first["closes"] == 2 and first["net_usd"] == -0.05 and first["max_drawdown_usd"] == 0.1
    assert first["mean_bp"] == -10.0 and first["last_trade_at"] == "2026-10-03T11:00:00Z"


def test_summary_is_small_public_and_complete(bot, monkeypatch):
    monkeypatch.setattr(bot, "trades", [])
    client = bot.app.test_client()
    client.post("/api/system-health/report", json=_report(), headers={"X-Bot-Admin-Token": ADMIN},
                environ_base=REMOTE)
    response = client.get("/api/monitor/summary", environ_base=REMOTE)
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    assert len(response.data) <= monitor_api.MAX_SUMMARY_BYTES
    body = response.get_json()
    assert body["schema"] == monitor_api.SUMMARY_SCHEMA and body["boot_id"] == "dashboard-test-boot"
    assert body["safety"]["live_armed"] is False and body["safety"]["bitfinex_live_enabled"] is False
    assert body["safety"]["relay"]["laptop_railway_relay_check"] == "AMBER"
    assert body["open_alarms"] == [{"id": "analyzer.studies", "first_seen": "2026-10-02T09:00:00Z"}]
    assert [t["lane"] for t in body["tiles"]] == list(registry.ACTIVE_TILE_ORDER)
    for key in ("ai_successes", "cycles_completed", "post_ai_evidence_completed", "xvl_ticks", "xvl_rows_written"):
        assert key in body["counters_since_boot"]
    assert set(body["custody"]) >= {"shipped_seq", "laptop_acked_seq"}
    assert "truncated" not in body
