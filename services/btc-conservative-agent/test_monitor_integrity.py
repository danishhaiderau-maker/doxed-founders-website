"""GET /api/monitor/integrity: 1 s tape continuity, AI call window, process identity (read-only, token-gated)."""
import json
from pathlib import Path

import pytest

import monitor_api
import monitor_integrity as mi

ADMIN = "health-test-token"
MONITOR = "monitor-read-token-for-tests-0123456789"
REMOTE = {"REMOTE_ADDR": "198.51.100.7"}
T0 = 1_791_000_000  # minute-aligned epoch second


class Clock:
    def __init__(self, t):
        self.t = float(t)

    def __call__(self):
        return self.t


# ------------------------------------------------------------------ tape tracker

def _feed(tracker, start, seconds, *, skip=(), fail=(), stale=(), late=()):
    for ts in range(start, start + seconds):
        if ts in skip:
            continue
        tracker.observe(ts, written=ts not in fail, fresh=ts not in stale, valid_bbo=True,
                        observed_at=ts + (5.0 if ts in late else 1.05))


def test_continuous_tape_is_ok_and_counts_match():
    clock = Clock(T0 + 600)
    tr = mi.TapeContinuityTracker(clock=clock)
    tr.prime(T0 - 1)
    _feed(tr, T0, 600)
    snap = tr.snapshot()
    one = snap["windows"]["1h"]
    assert snap["status"] == "OK" and snap["boot_gap"] is None
    assert one["written"] == 600 and one["missing"] == 0 and one["coverage_pct"] == 100.0
    assert one["gaps"] == 0 and one["window_complete"] is False


def test_gaps_failures_stale_late_and_restart_gap_are_reported():
    clock = Clock(T0 + 900)
    tr = mi.TapeContinuityTracker(clock=clock)
    tr.prime(T0 - 121)  # previous process stopped 120 s before this one started writing
    fail = set(range(T0 + 100, T0 + 140))            # 40 s of write failures
    skip = set(range(T0 + 500, T0 + 505))            # 5 s never observed (loop skip)
    _feed(tr, T0, 900, fail=fail, skip=skip, stale={T0 + 10}, late={T0 + 20, T0 + 21})
    snap = tr.snapshot()
    one = snap["windows"]["1h"]
    assert snap["boot_gap"]["seconds"] == 120 and snap["boot_gap"]["kind"] == "RESTART"
    assert one["write_failures"] == 40 and one["missing"] == 45
    assert one["stale_or_invalid"] == 1 and one["late_written"] == 2
    assert one["gaps"] == 2 and one["longest_gap_sec"] == 40
    kinds = {g["kind"]: g["seconds"] for g in snap["recent_gaps"]}
    assert kinds == {"WRITE_FAIL": 40, "SKIP": 5}
    assert snap["status"] == "GAPPY"


def test_stalled_writer_and_no_data():
    tr = mi.TapeContinuityTracker(clock=Clock(T0))
    assert tr.snapshot()["status"] == "NO_DATA"
    clock = Clock(T0 + 60)
    tr = mi.TapeContinuityTracker(clock=clock)
    _feed(tr, T0, 60)
    clock.t += 30
    assert tr.snapshot()["status"] == "STALLED"


def test_windows_prune_to_24h_and_observe_never_raises():
    clock = Clock(T0 + 26 * 3600)
    tr = mi.TapeContinuityTracker(clock=clock)
    for minute in range(26 * 60):
        tr.observe(T0 + minute * 60, written=True, observed_at=T0 + minute * 60 + 1)
    assert len(tr._minutes) <= mi.TAPE_MINUTES_KEPT
    tr.observe("not-a-ts", written=True)  # bad input is counted, never raised
    assert tr.observe_errors == 1
    assert tr.snapshot()["windows"]["24h"]["window_complete"] is True


def test_read_last_bucket_ts_is_bounded(tmp_path):
    path = tmp_path / "market_microstructure_1s.jsonl"
    rows = [json.dumps({"schema": "market_microstructure_1s_v1", "bucket_ts": T0 + i, "pad": "x" * 200})
            for i in range(2000)]
    path.write_text("\n".join(rows) + "\n")
    assert mi.read_last_bucket_ts(str(path)) == T0 + 1999
    assert mi.read_last_bucket_ts(str(path), max_bytes=64) is None or isinstance(
        mi.read_last_bucket_ts(str(path), max_bytes=64), int)
    assert mi.read_last_bucket_ts(str(tmp_path / "missing.jsonl")) is None


# ------------------------------------------------------------------ AI window

def test_ai_window_rates_percentiles_and_classes():
    clock = Clock(T0 + 7200)
    ai = mi.AiCallWindow(clock=clock)
    for i in range(20):
        ai.observe(ok=True, latency_ms=1000 + i * 100, now=T0 + 3700 + i)
    ai.observe(ok=False, error_class="TIMEOUT", now=T0 + 3800)
    ai.observe(ok=False, error_class="RATE_LIMIT", now=T0 + 3801)
    ai.observe(ok=True, latency_ms=float("nan"), now=T0 + 3802)   # non-finite latency dropped
    ai.observe(ok=True, latency_ms=99999, now=T0 + 100)           # outside the 1h window
    snap = ai.snapshot()
    one = snap["windows"]["1h"]
    assert one["calls"] == 23 and one["failed"] == 2 and one["success_rate"] == round(21 / 23, 4)
    assert one["latency_ms_p50"] == 1950.0 and one["latency_ms_max"] == 2900.0
    assert one["error_classes"] == {"TIMEOUT": 1, "RATE_LIMIT": 1}
    assert snap["windows"]["24h"]["calls"] == 24 and snap["last_failure_class"] == "RATE_LIMIT"


def test_fill_markouts_aggregate_per_lane_and_liquidity():
    agg = mi.FillMarkoutAggregator()
    for i, (lane, liq, touch) in enumerate([("L1", "TAKER", -4.0), ("L1", "TAKER", -2.0), ("L2", "MAKER", 1.0)]):
        agg.observe({"research_lane": lane, "liquidity": liq, "markouts": {
            "1s": {"markout_mid_bps": touch + 1, "markout_exit_touch_bps": touch, "on_time": True},
            "60s": {"markout_mid_bps": None, "markout_exit_touch_bps": float("inf"), "on_time": False}}}, now=T0 + i)
    agg.observe("not a row")
    snap = agg.snapshot(now=T0 + 10)
    g1 = next(g for g in snap["groups"] if g["lane"] == "L1")
    assert g1["fills"] == 2 and g1["liquidity"] == "TAKER"
    assert g1["horizons"]["1s"] == {"mean_mid_bps": -2.0, "mean_exit_touch_bps": -3.0, "late_samples": 0}
    assert g1["horizons"]["60s"]["mean_exit_touch_bps"] is None and g1["horizons"]["60s"]["late_samples"] == 2
    assert snap["fills_observed"] == 3 and agg.observe_errors == 1


def test_process_identity_only_exposes_allowlisted_env():
    env = {"FLY_MACHINE_VERSION": "01ABC", "FLY_IMAGE_REF": "registry.fly.io/doxed-btc-bot:deployment-1",
           "BOT_ADMIN_TOKEN": "secret", "MONITOR_READ_TOKEN": "secret2", "DEEPSEEK_API_KEY": "k"}
    ident = mi.process_identity(env, pid=7, git_rev="abc", boot_id="b", process_started_ts=T0)
    assert ident["fly"] == {"fly_machine_version": "01ABC", "fly_image_ref": "registry.fly.io/doxed-btc-bot:deployment-1"}
    assert "secret" not in json.dumps(ident)


def test_payload_fits_budget_and_one_bad_section_does_not_hide_others():
    clock = Clock(T0 + 60)
    tr, ai = mi.TapeContinuityTracker(clock=clock), mi.AiCallWindow(clock=clock)
    _feed(tr, T0, 60)
    out = mi.build_payload(now=clock.t, tape=tr, ai=ai, identity={"git_rev": "x"},
                           extras={"boom": lambda: 1 / 0}, fit=monitor_api.fit_to_budget)
    assert out["schema"] == mi.SCHEMA and out["read_only"] is True
    assert out["tape_1s"]["status"] == "OK" and out["boom"]["status"] == "UNKNOWN"
    assert len(json.dumps(out).encode()) <= mi.MAX_BYTES


# ------------------------------------------------------------------ Fly route

@pytest.fixture
def bot(monkeypatch):
    import bot as bot_module

    monkeypatch.setattr(bot_module, "_BOT_ADMIN_TOKEN", ADMIN)
    monkeypatch.setattr(bot_module, "_MONITOR_READ_TOKEN", MONITOR)
    monkeypatch.setattr(bot_module, "_DASHBOARD_BOOTSTRAP_COMPLETE", True)
    monkeypatch.setattr(bot_module, "BOT_INSTANCE_ID", "integrity-test-boot")
    monkeypatch.setattr(bot_module, "_API_RATE_MAX_PER_IP", 10_000)
    monkeypatch.setattr(bot_module, "_TAPE_CONTINUITY", mi.TapeContinuityTracker())
    monkeypatch.setattr(bot_module, "_AI_CALL_WINDOW", mi.AiCallWindow())
    monkeypatch.setattr(bot_module, "_FILL_MARKOUT_AGG", mi.FillMarkoutAggregator())
    return bot_module


def test_route_requires_admin_or_monitor_token(bot):
    client = bot.app.test_client()
    get = lambda **h: client.get("/api/monitor/integrity", headers=h, environ_base=REMOTE)  # noqa: E731
    assert get().status_code == 401
    assert get(Authorization=f"Bearer {ADMIN}").status_code == 401
    assert get(Authorization="Bearer wrong-token-wrong-token-wrong").status_code == 401
    for headers in ({"X-Bot-Admin-Token": ADMIN}, {"Authorization": f"Bearer {MONITOR}"}):
        ok = get(**headers)
        assert ok.status_code == 200 and ok.headers["Cache-Control"] == "no-store"
        body = ok.get_json()
        assert body["schema"] == mi.SCHEMA and body["boot_id"] == "integrity-test-boot"
        assert set(body) >= {"tape_1s", "ai_calls", "fill_quality", "process", "verdict"}
        assert len(ok.data) <= mi.MAX_BYTES + 1024


def test_route_with_monitor_token_unset_is_admin_only(bot, monkeypatch):
    monkeypatch.setattr(bot, "_MONITOR_READ_TOKEN", "")
    client = bot.app.test_client()
    assert client.get("/api/monitor/integrity", headers={"Authorization": f"Bearer {MONITOR}"},
                      environ_base=REMOTE).status_code == 401
    assert client.get("/api/monitor/integrity", headers={"X-Bot-Admin-Token": ADMIN},
                      environ_base=REMOTE).status_code == 200


def test_route_is_get_only_and_monitor_token_opens_nothing_else(bot):
    client = bot.app.test_client()
    monitor = {"Authorization": f"Bearer {MONITOR}"}
    assert client.post("/api/monitor/integrity", headers=monitor, environ_base=REMOTE).status_code in (401, 405)
    assert client.post("/api/monitor/integrity", headers={"X-Bot-Admin-Token": ADMIN},
                       environ_base=REMOTE).status_code == 405
    assert client.get("/debug_state", headers=monitor, environ_base=REMOTE).status_code == 401
    assert client.get("/api/exchange_exposure_audit", headers=monitor, environ_base=REMOTE).status_code == 401
    assert client.post("/api/pause", headers=monitor, json={}, environ_base=REMOTE).status_code == 401
    assert bot._MONITOR_INTEGRITY_PATH not in bot._READ_ONLY_GET_PATHS


def test_route_reports_observed_hooks(bot):
    bot.record_ai_provider_outcome(next(iter(bot.AI_PROVIDER_HEALTH_PURPOSES)), ok=True, latency_ms=1234.0)
    bot.record_ai_provider_outcome(next(iter(bot.AI_PROVIDER_HEALTH_PURPOSES)), ok=False,
                                   error=RuntimeError("HTTP_ERROR: read timed out"))
    import time as _t
    now = int(_t.time())
    for ts in range(now - 30, now):
        bot._TAPE_CONTINUITY.observe(ts, written=True, fresh=True, valid_bbo=True, observed_at=ts + 1.1)
    body = bot.app.test_client().get("/api/monitor/integrity", headers={"X-Bot-Admin-Token": ADMIN},
                                     environ_base=REMOTE).get_json()
    ai = body["ai_calls"]["windows"]["1h"]
    assert ai["calls"] == 2 and ai["error_classes"] == {"TIMEOUT": 1} and ai["latency_ms_max"] == 1234.0
    assert body["tape_1s"]["windows"]["1h"]["written"] == 30


def test_bot_wires_both_hooks_without_touching_order_paths():
    src = (Path(__file__).resolve().parent / "bot.py").read_text(encoding="utf-8")
    loop = src.split("def microstructure_capture_loop():", 1)[1].split("\ndef ", 1)[0]
    assert 'globals().get("_TAPE_CONTINUITY")' in loop
    assert "tape_continuity.prime(" in loop and "tape_continuity.observe(" in loop
    outcome = src.split("def record_ai_provider_outcome(", 1)[1].split("\ndef ", 1)[0]
    assert 'globals().get("_AI_CALL_WINDOW")' in outcome and "ai_window.observe(" in outcome
    sampler = src.split("def _sample_execution_markouts(", 1)[1].split("\ndef ", 1)[0]
    assert 'globals().get("_FILL_MARKOUT_AGG")' in sampler and "fill_agg.observe(" in sampler
    route = src.split("def monitor_integrity():", 1)[1].split("\n@app.route", 1)[0]
    for forbidden in ("state_lock", "trade_lock", "open(", "requests.", "_place_", "cancel"):
        assert forbidden not in route, forbidden
