"""Uninterrupted-runtime strip: durable tracker, Fly routes, analyzer route, banner, watcher push."""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import runtime_uptime as ru
import system_health_banner as shb

H = 3600.0
T0 = 1_790_000_000.0  # fixed epoch; all durations come from the passed clock
AUTH = {"X-Bot-Admin-Token": "uptime-test-token"}
REMOTE = {"REMOTE_ADDR": "198.51.100.9"}


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _healthy():
    return None


def _tracker(tmp_path):
    return ru.UptimeTracker(tmp_path / ru.STATE_FILE)


def test_first_boot_then_running_is_green_and_counts_from_first_healthy_observation(tmp_path):
    t = _tracker(tmp_path)
    opened = t.boot(T0, "c53f2d66b3d2")
    assert opened["kind"] == "first_boot"
    booting = t.summary(T0 + 5)
    assert booting["running"] is False and booting["colour"] == "red"
    t.observe(T0 + 10, _healthy())
    s = t.summary(T0 + 10 + 3 * H + 12 * 60)
    assert s["state"] == "RUNNING" and s["colour"] == "green"
    assert s["uninterrupted_label"] == "Running uninterrupted: 3h 12m"
    assert s["uninterrupted_sec"] == int(3 * H + 12 * 60)
    assert s["since_aest"].endswith("AEST") and s["since_utc"].endswith("UTC")
    assert s["last_interruption"]["kind"] == "first_boot"
    assert s["interruptions_24h"] == 1


def test_guarded_deploy_pause_plus_restart_is_one_amber_interruption(tmp_path):
    t = _tracker(tmp_path)
    t.boot(T0, "aaaaaaa00000")
    t.observe(T0 + 10, _healthy())
    pause = ru.problem_from(paused=True, pause_owner="DEPLOY_MAINTENANCE", pause_reason="DEPLOY_MAINTENANCE")
    t.observe(T0 + 5 * H, pause)
    paused = t.summary(T0 + 5 * H + 60)
    assert paused["state"] == "PAUSED_DEPLOY" and paused["colour"] == "amber"
    assert "DEPLOY_MAINTENANCE" in paused["uninterrupted_label"]

    # Old process stops; a new process on a new revision restores the durable state.
    t2 = _tracker(tmp_path)
    opened = t2.boot(T0 + 5 * H + 600, "c53f2d66b3d2")
    assert opened["kind"] == "deploy" and "deploy c53f2d6" in opened["text"]
    assert t2.summary(T0 + 5 * H + 610)["colour"] == "amber"
    t2.observe(T0 + 5 * H + 900, pause)
    t2.observe(T0 + 5 * H + 1200, _healthy())
    s = t2.summary(T0 + 6 * H)
    assert s["running"] and s["colour"] == "green"
    assert s["last_interruption"]["kind"] == "deploy"
    assert "deploy c53f2d6" in s["last_interruption"]["text"]
    assert s["interruptions_24h"] == 2  # first boot + the one guarded deploy
    assert s["longest_run_7d_sec"] == int(5 * H - 10)


def test_operator_pause_paper_off_and_ai_stall_are_red(tmp_path):
    t = _tracker(tmp_path)
    t.boot(T0, "abc1234")
    t.observe(T0 + 1, _healthy())
    t.observe(T0 + H, ru.problem_from(paused=True, pause_owner="OPERATOR"))
    s = t.summary(T0 + H + 30)
    assert s["state"] == "INTERRUPTED" and s["colour"] == "red" and "OPERATOR" in s["uninterrupted_label"]
    t.observe(T0 + 2 * H, _healthy())
    t.observe(T0 + 3 * H, ru.problem_from(paused=False, tiles_on=0, tiles_total=3))
    assert t.summary(T0 + 3 * H + 1)["current_blocker"]["kind"] == "paper_off"
    t.observe(T0 + 4 * H, _healthy())
    assert t.summary(T0 + 4 * H + 1)["interruptions_24h"] == 3
    assert t.summary(T0 + 4 * H + 1)["last_interruption"]["text"] == "paper off (0/3 tiles ON)"


def test_ai_stall_threshold_has_boot_grace_and_failure_trigger():
    assert ru.problem_from(paused=False, tiles_on=3, tiles_total=3, ai_success_age_sec=None,
                           process_age_sec=60) is None
    stall = ru.problem_from(paused=False, tiles_on=3, tiles_total=3, ai_success_age_sec=None,
                            process_age_sec=ru.AI_STALL_SEC + 1)
    assert stall["kind"] == "ai_stall"
    assert ru.problem_from(paused=False, tiles_on=1, tiles_total=3, ai_success_age_sec=200) is None
    assert ru.problem_from(paused=False, tiles_on=3, tiles_total=3, ai_success_age_sec=ru.AI_STALL_SEC + 1)["kind"] == "ai_stall"
    assert ru.problem_from(paused=False, tiles_on=3, tiles_total=3, ai_success_age_sec=30,
                           ai_consecutive_failures=3)["kind"] == "ai_stall"


def test_plain_restart_reports_downtime_and_resets_the_run(tmp_path):
    t = _tracker(tmp_path)
    t.boot(T0, "abc1234")
    t.observe(T0 + 1, _healthy())
    t.observe(T0 + 2 * H, _healthy())  # last persisted heartbeat
    t2 = _tracker(tmp_path)
    opened = t2.boot(T0 + 3 * H, "abc1234")
    assert opened["kind"] == "restart" and "down 1h 0m" in opened["text"]
    t2.observe(T0 + 3 * H + 30, _healthy())
    s = t2.summary(T0 + 3 * H + 90)
    assert s["uninterrupted_sec"] == 60
    assert s["longest_run_7d_sec"] == int(2 * H - 1)


def test_history_is_bounded_to_seven_days_and_24h_count_is_windowed(tmp_path):
    t = _tracker(tmp_path)
    t.boot(T0, "abc1234")
    t.observe(T0 + 1, _healthy())
    t.observe(T0 + 50 * H, ru.problem_from(paused=True, pause_owner="SAFETY"))  # 50h run
    t.observe(T0 + 50 * H + 60, _healthy())
    now = T0 + 9 * 24 * H
    t.observe(now, _healthy())
    s = t.summary(now)
    assert s["interruptions_24h"] == 0
    # The first run is clipped to the 7-day window (~2h left of it); the current run is longest.
    assert s["longest_run_7d_sec"] == int(now - (T0 + 50 * H + 60))
    t.observe(now + 2 * 24 * H, _healthy())
    assert t.summary(now + 2 * 24 * H)["longest_run_7d_sec"] == int(7 * 24 * H)
    stored = json.loads((tmp_path / ru.STATE_FILE).read_text(encoding="utf-8"))
    assert all(r["end"] >= now - ru.HISTORY_SEC for r in stored["runs"])


def test_persist_failure_never_raises(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    t = ru.UptimeTracker(blocker / "nested" / ru.STATE_FILE)
    t.boot(T0, "abc1234")
    t.observe(T0 + 1, _healthy())
    assert t.summary(T0 + 2)["running"] and t.summary(T0 + 2)["persist_error"]


def test_proof_progress_label_and_sanitizer(tmp_path):
    active = {"t0": _iso(T0), "ends_at": _iso(T0 + 48 * H), "status": {"result": "IN_PROGRESS"}}
    p = ru.proof_progress(active, T0 + 12.5 * H)
    assert p["label"] == "Proof: 12h / 48h" and p["window_hours"] == 48.0
    failing = ru.proof_progress(dict(active, status={"result": "FAILING", "reasons": ["1 FAIL row(s)"]}), T0 + H)
    assert failing["label"] == "Proof: 1h / 48h (FAILING)"
    assert ru.proof_progress(None) is None and ru.proof_progress({"t0": "x"}) is None
    (tmp_path / "unattended-proof").mkdir()
    (tmp_path / "unattended-proof" / "active.json").write_text(json.dumps(active), encoding="utf-8")
    assert ru.read_proof_progress(tmp_path, T0 + 2 * H)["label"] == "Proof: 2h / 48h"
    clean = ru.sanitize_proof(dict(p, label="y" * 500, extra="drop"))
    assert len(clean["label"]) <= 80 and "extra" not in clean
    assert ru.sanitize_proof({"elapsed_hours": "nope"}) is None


def test_fly_fetch_is_cached_and_keeps_last_good_value_on_429(monkeypatch):
    import io
    import urllib.error

    monkeypatch.setattr(ru, "_FLY_CACHE", {"at": 0.0, "value": None, "error": None, "good_at": 0.0, "good": None})
    calls = []

    def ok(url, timeout):
        calls.append(url)
        return io.BytesIO(json.dumps({"uptime": {"state": "RUNNING"}}).encode())

    def limited(url, timeout):
        calls.append(url)
        raise urllib.error.HTTPError(url, 429, "rate limit", {}, None)

    assert ru.fetch_fly_uptime("u", opener=ok)["uptime"]["state"] == "RUNNING"
    assert ru.fetch_fly_uptime("u", opener=ok)["uptime"]["state"] == "RUNNING" and len(calls) == 1
    out = ru.fetch_fly_uptime("u", opener=limited, max_age=0)
    assert out["uptime"]["state"] == "RUNNING" and "429" in out["error"] and "ago" in out["error"]
    ru._FLY_CACHE["good_at"] -= ru.FLY_LAST_GOOD_MAX_AGE_SEC + 1
    assert ru.fetch_fly_uptime("u", opener=limited, max_age=0)["uptime"] is None


def test_banner_script_always_renders_uptime_strip():
    js = shb.banner_script()
    assert "runtime-uptime-strip" in js and "uninterrupted_label" in js
    assert "#1b7f3b" in js and "#9a6700" in js and "#b00020" in js
    assert "all alerts" in js


def _bot(monkeypatch, tmp_path):
    import bot

    monkeypatch.setattr(bot, "_BOT_ADMIN_TOKEN", "uptime-test-token")
    monkeypatch.setattr(bot, "_DASHBOARD_BOOTSTRAP_COMPLETE", True)
    monkeypatch.setitem(bot._SYSTEM_HEALTH_REPORT, "report", None)
    monkeypatch.setitem(bot._SYSTEM_HEALTH_REPORT, "proof", None)
    tracker = ru.UptimeTracker(tmp_path / ru.STATE_FILE)
    now = time.time()
    tracker.boot(now - 2 * H, "c53f2d66b3d2")
    tracker.observe(now - 2 * H + 30, None)
    monkeypatch.setitem(bot._RUNTIME_UPTIME, "tracker", tracker)
    with bot.state_lock:
        monkeypatch.setitem(bot.state, "ws_last_tick", now)
        monkeypatch.setitem(bot.state, "execution_paused", False)
        monkeypatch.setitem(bot.state, "live_armed", False)
    return bot


def _report(age=0, proof=None):
    body = {"schema": shb.SCHEMA, "generated_at": _iso(time.time() - age), "verdict": "GREEN",
            "counts": {"GREEN": 20}, "failing": [], "open_alarms": []}
    if proof is not None:
        body["proof"] = proof
    return body


def test_fly_system_health_and_status_expose_uptime_publicly(monkeypatch, tmp_path):
    bot = _bot(monkeypatch, tmp_path)
    client = bot.app.test_client()
    proof = ru.proof_progress({"t0": _iso(time.time() - 12 * H), "ends_at": _iso(time.time() + 36 * H)})
    assert client.post("/api/system-health/report", json=_report(proof=proof), headers=AUTH,
                       environ_base=REMOTE).status_code == 200
    public = client.get("/api/system-health", environ_base=REMOTE).get_json()
    up = public["uptime"]
    assert up["state"] == "RUNNING" and up["colour"] == "green"
    assert up["uninterrupted_label"].startswith("Running uninterrupted: 1h 59m")
    assert up["proof"]["label"] == "Proof: 12h / 48h"
    assert up["interruptions_24h"] == 1 and up["last_interruption"]["kind"] == "first_boot"
    status = client.get("/api/status", environ_base=REMOTE).get_json()
    assert status["uptime"]["state"] == "RUNNING" and status["uptime"]["revision"] == "c53f2d66b3d2"


def test_fly_drops_proof_when_laptop_report_is_stale(monkeypatch, tmp_path):
    bot = _bot(monkeypatch, tmp_path)
    client = bot.app.test_client()
    proof = ru.proof_progress({"t0": _iso(time.time() - H), "ends_at": _iso(time.time() + 47 * H)})
    client.post("/api/system-health/report", json=_report(age=3 * H, proof=proof), headers=AUTH, environ_base=REMOTE)
    assert client.get("/api/system-health", environ_base=REMOTE).get_json()["uptime"]["proof"] is None


def test_fly_uptime_problem_reads_pause_owner_and_tile_toggles(monkeypatch, tmp_path):
    bot = _bot(monkeypatch, tmp_path)
    lanes = [str(lane) for lane in bot.ACTIVE_TILE_ORDER]
    monkeypatch.setitem(bot._ai_provider_health, "last_success_ts", time.time() - 60)
    monkeypatch.setitem(bot._ai_provider_health, "consecutive_failures", 0)
    with bot.state_lock:
        monkeypatch.setitem(bot.state, "research_lane_enabled", {lane: True for lane in lanes})
        monkeypatch.setitem(bot.state, "manual_admin_pause", False)
    assert bot._runtime_uptime_problem(time.time()) is None
    with bot.state_lock:
        monkeypatch.setitem(bot.state, "research_lane_enabled", {lane: False for lane in lanes})
    assert bot._runtime_uptime_problem(time.time())["kind"] == "paper_off"
    with bot.state_lock:
        monkeypatch.setitem(bot.state, "manual_admin_pause", True)
        monkeypatch.setitem(bot.state, "pause_intent", "DEPLOY_MAINTENANCE")
    assert bot._runtime_uptime_problem(time.time())["kind"] == "deploy_pause"


def test_analyzer_route_carries_fly_uptime_and_local_proof(monkeypatch, tmp_path):
    from research import research_dashboard as dashboard

    (tmp_path / "unattended-proof").mkdir()
    (tmp_path / "unattended-proof" / "active.json").write_text(json.dumps(
        {"t0": _iso(time.time() - 5 * H), "ends_at": _iso(time.time() + 43 * H)}), encoding="utf-8")
    monkeypatch.setenv("DOXXED_LAPTOP_CHAIN_STATE", str(tmp_path))
    fly_block = {"available": True, "state": "RUNNING", "colour": "green",
                 "uninterrupted_label": "Running uninterrupted: 4h 0m"}
    monkeypatch.setattr(ru, "fetch_fly_uptime", lambda url, **kw: {"uptime": fly_block, "error": None})
    up = dashboard.app.test_client().get("/api/system-health").get_json()["uptime"]
    assert up["uninterrupted_label"] == "Running uninterrupted: 4h 0m"
    assert up["proof"]["label"] == "Proof: 5h / 48h"
    monkeypatch.setattr(ru, "fetch_fly_uptime", lambda url, **kw: {"uptime": None, "error": "Fly unreachable"})
    down = dashboard.app.test_client().get("/api/system-health").get_json()["uptime"]
    assert down["available"] is False and down["note"] == "Fly unreachable"
    assert down["proof"]["label"] == "Proof: 5h / 48h"


def test_insights_fly_data_includes_uptime_and_proof(monkeypatch, tmp_path):
    from strategy_lab import insights

    monkeypatch.setattr(insights, "STATE_DIR", str(tmp_path))
    data = insights._fly_data({"uptime": {"state": "RUNNING", "uninterrupted_label": "x"}}, [], time.time())
    assert data["uptime"]["state"] == "RUNNING" and data["uptime"]["proof"] is None
    assert insights._fly_data({}, [], time.time())["uptime"]["available"] is False


def test_watcher_banner_payload_carries_proof():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import system_health as sh

    now = time.time()
    proof = sh.proof_summary({"t0": _iso(now - 3 * H), "ends_at": _iso(now + 45 * H)}, now)
    assert proof["label"] == "Proof: 3h / 48h"
    report = {"verdict": "GREEN", "generated_at": _iso(now), "failing": [], "counts": {}, "proof": proof}
    assert sh.banner_payload(report)["proof"]["label"] == "Proof: 3h / 48h"
    assert sh.proof_summary(None, now) is None
