"""Tests for scripts/system_health.py, including replays of real incidents.

The replays drive the evaluator with the evidence that existed at each
5-minute watcher tick (fixtures extracted from the laptop mirror and logs) and
prove each incident would have opened a RED alarm within its threshold plus
one tick.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import laptop_chain_incident as incident  # noqa: E402
import system_health as sh  # noqa: E402
import system_health_server as server  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "system_health"
TICK = 5 * 60.0
LANES = ["FAMILY_ADAPTIVE_REGIME", "FAMILY_ADAPTIVE_REGIME_LADDER", "FAMILY_TREND_FADE_60"]


def ts(text: str) -> float:
    return sh.parse_ts(text)


def healthy(now: float) -> dict:
    """Inputs of a fully healthy system at ``now``."""
    return {
        "now": now,
        "errors": {},
        "fly_status": {
            "process_alive": True, "system_ready": True, "git_rev": "a76a52a0dd50", "bot_instance_id": "i1",
            "execution_paused": False, "pause_owner": None, "live_armed": False, "bitfinex_live_enabled": False,
            "force_paper_mode": True, "ws_age": 3.0, "tile_registry_signature": "sig",
            "active_tiles": [{"lane": lane} for lane in LANES],
            "strategy_progress": {"ai_age_sec": 60.0, "process_startup_age_sec": 7200.0, "ws_progressing": True},
            "collection": {"research_coverage": {"collection_health": {"status": "OK", "alarms": []}},
                           "cross_venue_tape": {"status": "OK"}},
        },
        "fly_health": {
            "volume": {"free_bytes": 40e9, "hours_to_full": 400,
                       "transfer": {"segments_enabled": True, "shipped_seq": 100, "laptop_acked_seq": 100,
                                    "unshipped_bytes": 2_000_000, "store_bytes": 1e9, "max_store_bytes": 10e9,
                                    "last_segment_at": now - 120, "last_error": None,
                                    "segment_status_age_sec": 60}},
        },
        "fly_state": {
            "research_lane_enabled": {lane: True for lane in LANES},
            "tile_route_counts": {lane: {"closed": 1, "expired": 0, "open": 0, "pending": 0} for lane in LANES},
            "lane_position_counts": {lane: {"open": 0, "pending": 0} for lane in LANES},
            "ai_history": [{"time": sh.iso(now - 60 - i * 180), "ai_error": False, "ai_direction_raw": "LONG"}
                           for i in range(5)],
            "trades": [{"trade_id": f"t-{lane}", "research_lane": lane, "ts": sh.iso(now - 1800), "dur_min": 10}
                       for lane in LANES],
            "positions": [], "orders": [], "expired_orders": [],
            "orphan_order_ids": [], "orphan_position_ids": [],
            "dashboard_truth": {"deepseek": {"status": "OK", "label": "DeepSeek OK"}},
        },
        "analyzer_api": {"ok": True, "runtime_sync_match": True, "source_revision_parity": {"match": True},
                         "epoch_parity": {"match": True}},
        "railway_health": {"status": "ok", "services": {"api": "ok", "database": "ok"}},
        "pull_status": {"finishedAt": sh.iso(now - 60), "appliedSeq": 100},
        "puller_status": {"applied_seq": 100},
        "ack_receipt": {"through_seq": 100, "ok": True},
        "relay_snapshot": {"ok": True, "observedAt": sh.iso(now - 60), "status": "PAUSED",
                           "relayExecutionMode": "PAUSED", "relayArmedAt": None,
                           "reconciliation": {"signedExchangePositionQty": 0, "alert": False},
                           "exchangeOrderAudit": {"activeOrderCount": 0},
                           "relayExecutor": {"status": "PAUSED_HEALTHY", "healthy": True, "heartbeatAgeMs": 30000}},
        "deploy_runs": {"runs": [{"status": "completed", "conclusion": "success", "createdAt": "2026-10-01T20:00:00Z"}]},
        "analyzer_status": {"lastCompletedGenerationAt": sh.iso(now - 600), "state": "COMPLETED", "exitCode": 0},
        "cycle_status": {"startedAt": sh.iso(now - 1500), "finishedAt": sh.iso(now - 600), "exitCode": 0},
        "proof_active": {"status": {"result": "RUNNING"}},
        "proof_last_row": {"at": sh.iso(now - 600), "status": "PASS", "failed_checks": []},
        "supervisor_tick_at": now - 120,
        "mirror": {"ai_events": [], "lane_orders": {}, "expired": {}, "filled": {}},
        "exports": {"present": False},
        "registry": {"signature": "x", "lanes": list(LANES)},
        "analyzer_head": "a76a52a0dd50ffff",
        "master_sha": "a76a52a0dd50ffff",
        "laptop_disk": {"free": 300e9, "total": 1e12},
        "neon": None,
    }


def by_id(checks: list[dict]) -> dict[str, dict]:
    return {c["id"]: c for c in checks}


def run(inputs: dict, state: dict) -> tuple[dict, list[dict]]:
    checks = sh.evaluate(inputs, state)
    report = sh.summarize(checks, state, inputs["now"])
    events = sh.alarm_transitions(report, state, inputs["now"])
    return report, events


# ------------------------------------------------------------------ basics

def test_healthy_system_is_green_with_every_field():
    report, events = run(healthy(ts("2026-10-02T00:00:00Z")), {})
    assert report["verdict"] == sh.GREEN, report["failing"]
    assert not events
    ids = {c["id"] for c in report["checks"]}
    for required in ("fly.process", "fly.paused", "fly.revision", "ai.success", "ai.failures", "ai.decision_mix",
                     "trading.orders", "trading.orphans", "trading.lifecycle", "ws.ticks", "shipper.progress",
                     "laptop.pull_ack", "laptop.supervisor", "analyzer.generation", "analyzer.api", "analyzer.cycle",
                     "exports.freshness", "streams.coverage", "dashboards.parity", "railway.relay", "railway.api",
                     "neon.usage", "bitfinex.exposure", "proof.latest", "disk.space"):
        assert required in ids
    for c in report["checks"]:
        assert set(c) >= {"status", "observed", "threshold", "last_good_at", "hint", "runbook"}
        assert c["runbook"].startswith("docs/SYSTEM_HEALTH_RUNBOOK.md#")


def test_runbook_documents_every_check():
    doc = (Path(__file__).resolve().parents[1] / "docs" / "SYSTEM_HEALTH_RUNBOOK.md").read_text(encoding="utf-8")
    report, _ = run(healthy(ts("2026-10-02T00:00:00Z")), {})
    for c in report["checks"]:
        anchor = c["runbook"].split("#", 1)[1]
        assert f"<a id=\"{anchor}\"></a>" in doc, anchor
    assert '<a id="watcher-stale"></a>' in doc


# ----------------------------------------------------------------- replays

def ai_inputs(now: float, success: list[float], failure: list[float]) -> dict:
    """What Fly showed at ``now``: attempt cadence normal, ai_history = last 5 calls."""
    inputs = healthy(now)
    calls = sorted([(t, True) for t in success if t <= now] + [(t, False) for t in failure if t <= now])[-5:]
    inputs["fly_state"]["ai_history"] = [
        {"time": sh.iso(t), "ai_error": not ok, "ai_direction_raw": "LONG" if ok else None} for t, ok in calls]
    inputs["fly_status"]["strategy_progress"]["ai_age_sec"] = 70.0  # attempts kept ai_age "normal"
    return inputs


def test_replay_deepseek_outage_20261001_alarms_within_threshold():
    fx = json.loads((FIX / "ai_outage_20261001.json").read_text(encoding="utf-8"))
    success = [ts(x) for x in fx["ai_success"]]
    failure = [ts(x) for x in fx["ai_failure"]]
    last_success = max(success)
    assert sh.iso(last_success).startswith("2026-10-01T18:56:02")
    state: dict = {}
    first_red = opened = parity_red = None
    now = ts("2026-10-01T18:30:00Z")
    while now <= ts("2026-10-01T20:00:00Z"):
        report, events = run(ai_inputs(now, success, failure), state)
        checks = by_id(report["checks"])
        if now < last_success + sh.THRESHOLDS["ai_success_red_sec"]:
            assert checks["ai.success"]["status"] != sh.RED, sh.iso(now)
        if checks["ai.success"]["status"] == sh.RED and first_red is None:
            first_red = now
        if checks["dashboards.parity"]["status"] == sh.RED and parity_red is None:
            parity_red = now
        if any(e["event"] == "OPEN" and e["check"] == "ai.success" for e in events):
            opened = now
        now += TICK
    assert first_red is not None and opened == first_red
    latency = first_red - last_success
    assert sh.THRESHOLDS["ai_success_red_sec"] < latency <= sh.THRESHOLDS["ai_success_red_sec"] + TICK
    assert sh.iso(first_red).startswith("2026-10-01T19:10")
    # The dashboard's attempt-based "DeepSeek OK" label is flagged as a contradiction too.
    assert parity_red == first_red


def stall_inputs(now: float, points: list, last_error: str, stall_start: float, stall_end: float) -> dict:
    inputs = healthy(now)
    seen = [p for p in points if ts(p[0]) <= now]
    published = seen[-1][1]
    changed = next(ts(p[0]) for p in seen if p[1] == published)
    inputs["fly_health"]["volume"]["transfer"].update(
        shipped_seq=published, laptop_acked_seq=published, last_segment_at=changed,
        # Live streams always append while the shipper is stalled (backlog > 0).
        unshipped_bytes=4_000_000 + int(max(0.0, now - changed) * 2_000),
        last_error=last_error if stall_start < now < stall_end else None)
    inputs["puller_status"]["applied_seq"] = published
    inputs["pull_status"]["appliedSeq"] = published
    inputs["ack_receipt"]["through_seq"] = published
    return inputs


def test_replay_shipper_plan_race_stall_20261001_alarms_within_threshold():
    fx = json.loads((FIX / "shipper_stall_20261001.json").read_text(encoding="utf-8"))
    points = fx["points"]
    stall_start = ts("2026-10-01T18:03:28Z")
    stall_end = ts("2026-10-01T19:01:55Z")
    assert any(p[0].startswith("2026-10-01T18:03:28") and p[1] == 2010 for p in points)
    state: dict = {}
    opened, recovered = [], []
    now = ts("2026-10-01T17:20:00Z")
    while now <= ts("2026-10-01T19:10:00Z"):
        inputs = stall_inputs(now, points, fx["last_error"], stall_start, stall_end)
        report, events = run(inputs, state)
        st = by_id(report["checks"])["shipper.progress"]["status"]
        transfer = inputs["fly_health"]["volume"]["transfer"]
        seg_age = now - transfer["last_segment_at"]
        expect_red = (transfer["last_error"] and seg_age > sh.THRESHOLDS["shipper_stall_red_sec"]) or \
            seg_age > sh.THRESHOLDS["shipper_stall_backlog_red_sec"]
        assert (st == sh.RED) == bool(expect_red), (sh.iso(now), st, seg_age)
        if not expect_red and seg_age > sh.THRESHOLDS["shipper_stall_amber_sec"]:
            assert st == sh.AMBER  # the 12-min idle gap 17:14:59Z-17:27:23Z is AMBER, not a page
        opened += [now for e in events if e["event"] == "OPEN" and e["check"] == "shipper.progress"]
        recovered += [now for e in events if e["event"] == "RECOVERED" and e["check"] == "shipper.progress"]
        now += TICK
    # Starved idle cycles 17:27:23Z-18:00:18Z (missed by the legacy monitor) alarm at 17:50Z.
    assert sh.iso(opened[0]).startswith("2026-10-01T17:50")
    # The PLAN_RACE stall from 18:03:28Z alarms within 9 min + one tick, 20 min before the legacy monitor.
    plan_race = [t for t in opened if stall_start < t < stall_end]
    assert plan_race and plan_race[0] - stall_start <= sh.THRESHOLDS["shipper_stall_red_sec"] + TICK
    assert sh.iso(plan_race[0]).startswith("2026-10-01T18:15")
    assert plan_race[0] < ts(fx["legacy_alarm_at"])
    assert any(t > stall_end for t in recovered)


def test_post_fix_idle_gap_does_not_page():
    # Real post-#257 gap: seq 2040 at 21:11:42Z, next at 21:25:30Z with a few MB backlog, no error.
    now = ts("2026-10-01T21:25:00Z")
    inputs = healthy(now)
    inputs["fly_health"]["volume"]["transfer"].update(last_segment_at=ts("2026-10-01T21:11:42Z"),
                                                      unshipped_bytes=3_000_000, last_error=None)
    assert by_id(sh.evaluate(inputs, {}))["shipper.progress"]["status"] == sh.AMBER


def test_zero_orders_three_hours_while_tiles_on_is_red():
    now = ts("2026-10-02T03:00:00Z")
    inputs = healthy(now)
    for trade in inputs["fly_state"]["trades"]:
        trade["ts"] = sh.iso(now - 3.5 * 3600)
    report, events = run(inputs, {})
    check = by_id(report["checks"])["trading.orders"]
    assert check["status"] == sh.RED and "no tile is placing orders" in check["hint"]
    assert any(e["event"] == "OPEN" and e["check"] == "trading.orders" for e in events)


def test_zero_orders_not_red_when_tiles_off_or_one_tile_active():
    now = ts("2026-10-02T03:00:00Z")
    inputs = healthy(now)
    inputs["fly_state"]["research_lane_enabled"] = {lane: False for lane in LANES}
    for trade in inputs["fly_state"]["trades"]:
        trade["ts"] = sh.iso(now - 10 * 3600)
    assert by_id(sh.evaluate(inputs, {}))["trading.orders"]["status"] == sh.GREEN
    inputs = healthy(now)
    inputs["fly_state"]["trades"][0]["ts"] = sh.iso(now - 7 * 3600)
    assert by_id(sh.evaluate(inputs, {}))["trading.orders"]["status"] == sh.AMBER


def test_route_counter_progress_counts_as_orders():
    now = ts("2026-10-02T03:00:00Z")
    state: dict = {}
    inputs = healthy(now)
    for trade in inputs["fly_state"]["trades"]:
        trade["ts"] = sh.iso(now - 5 * 3600)
    sh.evaluate(inputs, state)
    later = copy.deepcopy(inputs)
    later["now"] = now + 300
    later["fly_state"]["tile_route_counts"][LANES[0]]["pending"] = 1
    assert by_id(sh.evaluate(later, state))["trading.orders"]["status"] != sh.RED


def test_analyzer_staleness_and_crash():
    now = ts("2026-10-02T03:00:00Z")
    inputs = healthy(now)
    inputs["analyzer_status"]["lastCompletedGenerationAt"] = sh.iso(now - 50 * 60)
    assert by_id(sh.evaluate(inputs, {}))["analyzer.generation"]["status"] == sh.AMBER
    inputs["analyzer_status"]["lastCompletedGenerationAt"] = sh.iso(now - 95 * 60)
    assert by_id(sh.evaluate(inputs, {}))["analyzer.generation"]["status"] == sh.RED
    state: dict = {}
    crashed = healthy(now)
    crashed["analyzer_api"] = None
    report, events = run(crashed, state)
    assert by_id(report["checks"])["analyzer.api"]["status"] == sh.RED
    assert not any(e["check"] == "analyzer.api" and e["event"] == "OPEN" for e in events)  # sustain 2
    crashed["now"] = now + TICK
    _, events = run(crashed, state)
    assert any(e["check"] == "analyzer.api" and e["event"] == "OPEN" for e in events)


def test_analyzer_down_during_cycle_is_amber_until_bound():
    now = ts("2026-10-02T03:00:00Z")
    inputs = healthy(now)
    inputs["analyzer_api"] = None
    inputs["cycle_status"] = {"startedAt": sh.iso(now - 600), "finishedAt": None, "phase": "ANALYZER"}
    state: dict = {}
    assert by_id(sh.evaluate(inputs, state))["analyzer.api"]["status"] == sh.AMBER
    inputs["now"] = now + 25 * 60
    assert by_id(sh.evaluate(inputs, state))["analyzer.api"]["status"] == sh.RED


def test_fill_expiry_contradiction():
    now = ts("2026-10-02T03:00:00Z")
    inputs = healthy(now)
    inputs["fly_state"]["expired_orders"] = [{"trade_id": "t-FAMILY_TREND_FADE_60", "expired_ts": now - 600}]
    check = by_id(sh.evaluate(inputs, {}))["trading.lifecycle"]
    assert check["status"] == sh.RED and "t-FAMILY_TREND_FADE_60" in check["observed"]
    inputs = healthy(now)
    inputs["mirror"]["expired"] = {"old": now - 10 * 3600}
    inputs["mirror"]["filled"] = {"old": now - 10 * 3600}
    assert by_id(sh.evaluate(inputs, {}))["trading.lifecycle"]["status"] == sh.AMBER


def test_orphans_red_after_sustain():
    now = ts("2026-10-02T03:00:00Z")
    inputs = healthy(now)
    inputs["fly_state"]["orphan_order_ids"] = ["o1"]
    state: dict = {}
    _, events = run(inputs, state)
    assert not any(e["event"] == "OPEN" for e in events if e["check"] == "trading.orphans")
    inputs["now"] += TICK
    _, events = run(inputs, state)
    assert any(e["event"] == "OPEN" for e in events if e["check"] == "trading.orphans")


def test_dashboard_contradictions():
    now = ts("2026-10-02T03:00:00Z")
    state: dict = {}
    inputs = healthy(now)
    inputs["registry"]["lanes"] = LANES[:2]
    assert by_id(sh.evaluate(inputs, state))["dashboards.parity"]["status"] == sh.AMBER
    inputs["now"] = now + 31 * 60
    assert by_id(sh.evaluate(inputs, state))["dashboards.parity"]["status"] == sh.RED
    inputs = healthy(now)
    inputs["fly_state"]["lane_position_counts"][LANES[0]]["open"] = 1
    assert by_id(sh.evaluate(inputs, {}))["dashboards.parity"]["status"] == sh.AMBER


def test_ws_stale_and_paused_states():
    now = ts("2026-10-02T03:00:00Z")
    inputs = healthy(now)
    inputs["fly_status"]["ws_age"] = 200
    assert by_id(sh.evaluate(inputs, {}))["ws.ticks"]["status"] == sh.RED
    state: dict = {}
    inputs = healthy(now)
    inputs["fly_status"].update(execution_paused=True, pause_owner="DEPLOY_MAINTENANCE")
    assert by_id(sh.evaluate(inputs, state))["fly.paused"]["status"] == sh.AMBER
    inputs["now"] = now + 50 * 60
    assert by_id(sh.evaluate(inputs, state))["fly.paused"]["status"] == sh.RED
    inputs = healthy(now)
    inputs["fly_status"].update(execution_paused=True, pause_owner="THREAD_CRASH")
    assert by_id(sh.evaluate(inputs, {}))["fly.paused"]["status"] == sh.RED


def test_pull_and_ack_lag():
    now = ts("2026-10-02T03:00:00Z")
    state: dict = {}
    inputs = healthy(now)
    sh.evaluate(inputs, state)
    inputs = healthy(now + 20 * 60)
    inputs["fly_health"]["volume"]["transfer"]["shipped_seq"] = 110
    inputs["fly_health"]["volume"]["transfer"]["last_segment_at"] = now + 19 * 60
    check = by_id(sh.evaluate(inputs, state))["laptop.pull_ack"]
    assert check["status"] == sh.RED and "applied behind published" in check["hint"]
    inputs = healthy(now)
    inputs["pull_status"]["finishedAt"] = sh.iso(now - 20 * 60)
    assert by_id(sh.evaluate(inputs, {}))["laptop.pull_ack"]["status"] == sh.RED


def test_bitfinex_and_relay_safety():
    now = ts("2026-10-02T03:00:00Z")
    inputs = healthy(now)
    inputs["fly_status"]["live_armed"] = True
    assert by_id(sh.evaluate(inputs, {}))["bitfinex.exposure"]["status"] == sh.RED
    inputs = healthy(now)
    inputs["relay_snapshot"]["reconciliation"]["signedExchangePositionQty"] = 0.0003
    assert by_id(sh.evaluate(inputs, {}))["bitfinex.exposure"]["status"] == sh.RED
    inputs = healthy(now)
    inputs["relay_snapshot"]["relayArmedAt"] = "2026-10-02T02:00:00Z"
    assert by_id(sh.evaluate(inputs, {}))["railway.relay"]["status"] == sh.RED


def test_disk_and_store_cap():
    now = ts("2026-10-02T03:00:00Z")
    inputs = healthy(now)
    inputs["fly_health"]["volume"]["free_bytes"] = 3 * 1024**3
    assert by_id(sh.evaluate(inputs, {}))["disk.space"]["status"] == sh.RED
    inputs = healthy(now)
    inputs["fly_health"]["volume"]["transfer"]["store_bytes"] = 8.5e9
    assert by_id(sh.evaluate(inputs, {}))["disk.space"]["status"] == sh.AMBER


def test_provider_health_block_from_ai_repair_is_preferred():
    now = ts("2026-10-02T03:00:00Z")
    inputs = healthy(now)
    inputs["fly_state"]["ai_history"] = []
    inputs["fly_status"]["strategy_progress"]["ai_provider_health"] = {
        "last_success_ts": now - 20 * 60, "consecutive_failures": 7}
    checks = by_id(sh.evaluate(inputs, {}))
    assert checks["ai.success"]["status"] == sh.RED
    assert checks["ai.failures"]["status"] == sh.RED


def test_all_neutral_decisions_amber():
    now = ts("2026-10-02T03:00:00Z")
    inputs = healthy(now)
    state = {"memory": {"ai_events": [{"ts": now - i * 180, "ok": True, "direction": "NO_TRADE"} for i in range(25)]}}
    inputs["fly_state"]["ai_history"] = []
    assert by_id(sh.evaluate(inputs, state))["ai.decision_mix"]["status"] == sh.AMBER


# ------------------------------------------------------------------ alarms

def test_alarm_edge_dedupe_renotify_and_recovery():
    now = ts("2026-10-02T03:00:00Z")
    state: dict = {}
    bad = healthy(now)
    bad["fly_status"]["ws_age"] = 500
    _, events = run(bad, state)
    assert [e["event"] for e in events if e["check"] == "ws.ticks"] == ["OPEN"]
    bad["now"] = now + TICK
    _, events = run(bad, state)
    assert not [e for e in events if e["check"] == "ws.ticks"]
    bad["now"] = now + sh.THRESHOLDS["renotify_sec"] + 1
    _, events = run(bad, state)
    assert [e["event"] for e in events if e["check"] == "ws.ticks"] == ["STILL_RED"]
    good = healthy(now + sh.THRESHOLDS["renotify_sec"] + TICK)
    report, events = run(good, state)
    assert [e["event"] for e in events if e["check"] == "ws.ticks"] == ["RECOVERED"]
    assert report["open_alarms"] == []


def test_notify_channels(tmp_path):
    events = [{"event": "OPEN", "check": "ws.ticks", "observed": "500s", "hint": "ws down", "status": "RED"},
              {"event": "AMBER", "check": "x", "observed": "", "hint": "", "status": "AMBER"}]
    toasts, posts = [], []
    result = sh.notify(events, tmp_path, toast=lambda t, b: toasts.append((t, b)) or True,
                       post=lambda *a, **k: posts.append((a, k)) or ({}, None))
    assert result["pushed"] == 1 and result["webhook"] == "not_configured" and toasts and not posts
    (tmp_path / "health").mkdir()
    (tmp_path / "health" / "alarm-channels.json").write_text(json.dumps({"webhook_url": "https://example.invalid/h"}))
    result = sh.notify(events, tmp_path, toast=lambda t, b: True, post=lambda *a, **k: posts.append((a, k)) or ({}, None))
    assert result["webhook"] == "ok" and "ws.ticks" in posts[0][1]["body"]["content"]
    assert sh.notify([events[1]], tmp_path, toast=lambda t, b: True)["pushed"] == 0


def test_incident_escalation_reads_open_alarms():
    now = 1_790_000_000.0
    report = {"generated_ts": now - 60, "open_alarms": ["ai.success"],
              "failing": [{"id": "ai.success", "observed": "14m", "runbook": "docs/x#ai-success"}]}
    found = incident.system_health_findings(report, now)
    assert "system_health_red" in found and "ai.success" in found["system_health_red"]
    assert incident.system_health_findings({**report, "generated_ts": now - 3600}, now) == {}
    assert incident.system_health_findings({**report, "open_alarms": []}, now) == {}


def test_tick_lock_is_exclusive(tmp_path):
    with sh.TickLock(tmp_path / "tick.lock") as first:
        assert first
        with sh.TickLock(tmp_path / "tick.lock") as second:
            assert not second


def test_server_marks_stale_verdict_amber(tmp_path):
    (tmp_path / "health").mkdir()
    old = sh.utcnow() - 3600
    report = sh.summarize(sh.evaluate(healthy(old), {}), {}, old)
    (tmp_path / "health" / "system-health-latest.json").write_text(json.dumps(report))
    out = server.published(str(tmp_path))
    assert out["stale"] is True and out["verdict"] == sh.AMBER and out["failing"][0]["id"] == "watcher.stale"


def test_banner_payload_is_bounded_and_secret_free():
    report = sh.summarize(sh.evaluate(healthy(ts("2026-10-02T00:00:00Z")), {}), {}, ts("2026-10-02T00:00:00Z"))
    payload = sh.banner_payload(report)
    assert set(payload) == {"schema", "verdict", "generated_at", "open_alarms", "failing", "counts", "source"}
    assert len(json.dumps(payload)) < 8000


def test_banner_payload_is_accepted_by_fly_sanitizer():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "btc-conservative-agent"))
    system_health_banner = pytest.importorskip("system_health_banner")

    now = ts("2026-10-02T00:00:00Z")
    inputs = healthy(now)
    inputs["fly_health"]["volume"]["transfer"]["last_error"] = "PLAN_RACE"
    report = sh.summarize(sh.evaluate(inputs, {}), {}, now)
    clean = system_health_banner.sanitize_report(sh.banner_payload(report))
    assert clean is not None and clean["verdict"] == report["verdict"]
    assert [c["id"] for c in clean["failing"]] == [c["id"] for c in report["failing"][:12]]


def test_refuses_onedrive_state_dir(capsys):
    assert sh.main(["--state-dir", r"C:\Users\x\OneDrive\laptop-chain", "--latest"]) == 2
