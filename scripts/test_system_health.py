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
LANES = ["FAMILY_TREND_FADE_60", "SYNTHETIC_TILE_B", "SYNTHETIC_TILE_C"]


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
        "analyzer_receipt": {"generated_at": sh.iso(now - 600), "level": "GREEN", "reasons": [],
                             "studies": [{"name": "best_policy_research_report.json", "status": "OK"}],
                             "failed_required_studies": []},
        "analyzer_integrity": {"report_status": "VALID"},
        "analyzer_manifest_generated_at": sh.iso(now - 600),
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
        "retention_last_run": {"mode": "enforce", "finished_at": sh.iso(now - 900), "level": "GREEN",
                               "bytes_after": 20e9, "cap_bytes": 50e9, "usage_fraction": 0.4,
                               "deny_reasons": [], "reclaimed_bytes": 0, "would_reclaim_bytes": 0,
                               "ledger_rows": 10},
        "archive_last_snapshot": {"snapshot_id": "s1", "written_at": sh.iso(now - 900),
                                  "segment_seq_through": 100},
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
                     "neon.usage", "bitfinex.exposure", "proof.latest", "disk.space", "ai.served_model",
                     "deepseek.balance"):
        assert required in ids
    for c in report["checks"]:
        assert set(c) >= {"status", "observed", "threshold", "last_good_at", "hint", "runbook"}
        assert c["runbook"].startswith("docs/SYSTEM_HEALTH_RUNBOOK.md#")


@pytest.mark.parametrize("status,amber", [("STALE", True), ("DEGRADED", True), ("OK", False),
                                          ("STARTING", False), ("DISABLED", False)])
def test_stalled_xvl_evaluator_turns_streams_amber(status, amber):
    inputs = healthy(ts("2026-10-02T00:00:00Z"))
    inputs["fly_status"]["collection"]["xvl_evaluator"] = {"status": status, "reason": "TICK_AGE_30S"}
    check = by_id(sh.evaluate(inputs, {}))["streams.coverage"]
    assert (check["status"] == sh.AMBER) is amber, check
    assert ("XVL evaluator" in check["observed"]) is amber


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


# Laptop applied/ACKed transitions from segment-pull-loop-2026100{1,2}.log after the #271 deploy.
APPLIED_20261002 = [("2026-10-01T23:41:45Z", 2066), ("2026-10-01T23:43:34Z", 2067), ("2026-10-01T23:45:24Z", 2068),
                    ("2026-10-01T23:49:18Z", 2069), ("2026-10-01T23:50:14Z", 2070), ("2026-10-01T23:52:51Z", 2071),
                    ("2026-10-01T23:57:30Z", 2072), ("2026-10-02T00:02:07Z", 2073), ("2026-10-02T00:06:13Z", 2074),
                    ("2026-10-02T00:09:32Z", 2075), ("2026-10-02T00:13:40Z", 2076), ("2026-10-02T00:16:50Z", 2077)]


def _seq_at(points: list, when: float) -> int:
    return [seq for at, seq in points if ts(at) <= when][-1]


def ack_chase_inputs(now: float, fly_acked: int) -> dict:
    inputs = healthy(now)
    applied = _seq_at(APPLIED_20261002, now)
    inputs["fly_health"]["volume"]["transfer"].update(shipped_seq=applied, laptop_acked_seq=fly_acked,
                                                      last_segment_at=now - 60)
    inputs["puller_status"]["applied_seq"] = inputs["pull_status"]["appliedSeq"] = applied
    inputs["ack_receipt"]["through_seq"] = applied
    return inputs


# (watcher sample, Fly laptop_acked_seq). Fly polls ACKs at most every 300s at the top of a
# ~300s loop, so laptop_acked trails applied by a segment or two yet keeps advancing.
FLY_ACKED_20261002 = [("2026-10-01T23:50:38Z", 2070), ("2026-10-01T23:55:38Z", 2070), ("2026-10-02T00:00:38Z", 2071),
                      ("2026-10-02T00:05:38Z", 2072), ("2026-10-02T00:09:10Z", 2073), ("2026-10-02T00:09:34Z", 2073),
                      ("2026-10-02T00:12:38Z", 2073)]


def test_replay_fly_ack_chasing_applied_20261002_is_not_red():
    # The legacy check paged "Fly laptop_acked behind applied for 28m" at 00:09Z although
    # the laptop's ACK reached the segment server and Fly's ACK advanced throughout.
    state: dict = {}
    legacy_red = []
    for at, fly_acked in FLY_ACKED_20261002:
        now = ts(at)
        inputs = ack_chase_inputs(now, fly_acked)
        check = by_id(sh.evaluate(inputs, state))["laptop.pull_ack"]
        assert check["status"] == sh.GREEN, (at, check["observed"], check["hint"])
        if now - ts(FLY_ACKED_20261002[0][0]) > sh.THRESHOLDS["ack_lag_red_sec"]:
            legacy_red.append(at)
    assert legacy_red[0] == "2026-10-02T00:09:10Z"


def test_analyzer_on_laptop_only_descendant_of_fly_rev_is_green():
    # 2026-10-02: v2c fast-forwarded past Fly ad30ecaed to 3376338f5 (#269/#273, scripts/analyzer only);
    # the cycle's ancestry guard ran exit 0, so the watcher must not hold analyzer.cycle AMBER.
    now = ts("2026-10-02T00:35:00Z")
    inputs = healthy(now)
    inputs["analyzer_head"] = "3376338f588e0000"
    inputs["analyzer_contains_fly"] = True
    assert by_id(sh.evaluate(inputs, {}))["analyzer.cycle"]["status"] == sh.GREEN
    inputs["analyzer_contains_fly"] = False
    check = by_id(sh.evaluate(inputs, {}))["analyzer.cycle"]
    assert check["status"] == sh.AMBER and "does not contain Fly" in check["observed"]


def test_fly_ack_frozen_while_laptop_applies_is_red_within_threshold():
    state: dict = {}
    start = ts("2026-10-01T23:50:38Z")
    now, first_red = start, None
    while now <= ts("2026-10-02T00:20:38Z"):
        check = by_id(sh.evaluate(ack_chase_inputs(now, 2070), state))["laptop.pull_ack"]
        if check["status"] == sh.RED and first_red is None:
            first_red = now
            assert "not advancing" in check["hint"]
        now += TICK
    assert first_red is not None and first_red - start <= sh.THRESHOLDS["ack_lag_red_sec"] + TICK


def test_fly_ack_far_behind_applied_is_red_even_if_advancing():
    now = ts("2026-10-02T03:00:00Z")
    inputs = healthy(now)
    inputs["fly_health"]["volume"]["transfer"]["laptop_acked_seq"] = 100 - sh.THRESHOLDS["ack_lag_seq_red"] - 1
    check = by_id(sh.evaluate(inputs, {}))["laptop.pull_ack"]
    assert check["status"] == sh.RED and "segments" in check["hint"]


def test_ack_lag_from_first_observation_is_not_masked_by_empty_memory():
    now = ts("2026-10-02T03:00:00Z")
    state: dict = {}
    for i in range(5):
        inputs = healthy(now + i * TICK)
        inputs["fly_health"]["volume"]["transfer"]["laptop_acked_seq"] = 98
        status = by_id(sh.evaluate(inputs, state))["laptop.pull_ack"]["status"]
    assert status == sh.RED


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


def test_provider_health_success_ts_key_from_ai_repair_is_read():
    now = ts("2026-10-02T03:00:00Z")
    inputs = healthy(now)
    inputs["fly_state"]["ai_history"] = []
    inputs["fly_status"]["ai_provider_health"] = {"last_ai_success_ts": now - 60, "consecutive_failures": 0}
    assert by_id(sh.evaluate(inputs, {}))["ai.success"]["status"] == sh.GREEN


def test_served_model_matches_configured_is_green_and_mismatch_amber():
    now = ts("2026-10-02T03:00:00Z")
    inputs = healthy(now)
    inputs["fly_status"]["ai_provider_health"] = {
        "last_ai_success_ts": now - 60, "consecutive_failures": 0, "configured_model": "deepseek-flash",
        "last_model_echo": "deepseek-flash", "last_system_fingerprint": "aeb56401"}
    check = by_id(sh.evaluate(inputs, {}))["ai.served_model"]
    assert check["status"] == sh.GREEN and "aeb56401" in check["observed"]
    inputs["fly_status"]["ai_provider_health"]["configured_model"] = "deepseek-v4-flash"
    assert by_id(sh.evaluate(inputs, {}))["ai.served_model"]["status"] == sh.AMBER


def test_served_model_unexpected_change_is_amber_for_six_hours():
    now = ts("2026-10-02T03:00:00Z")
    state: dict = {}
    inputs = healthy(now)
    prov = {"last_ai_success_ts": now - 60, "consecutive_failures": 0, "last_model_echo": "deepseek-flash"}
    inputs["fly_status"]["ai_provider_health"] = prov
    assert by_id(sh.evaluate(inputs, state))["ai.served_model"]["status"] == sh.GREEN
    prov["last_model_echo"] = "deepseek-flash-2"
    later = healthy(now + 300)
    later["fly_status"]["ai_provider_health"] = {**prov, "configured_model": "deepseek-flash-2"}
    check = by_id(sh.evaluate(later, state))["ai.served_model"]
    assert check["status"] == sh.AMBER and "deepseek-flash->deepseek-flash-2" in check["observed"]
    much_later = healthy(now + 7 * 3600)
    much_later["fly_status"]["ai_provider_health"] = {**prov, "configured_model": "deepseek-flash-2"}
    assert by_id(sh.evaluate(much_later, state))["ai.served_model"]["status"] == sh.GREEN


def test_served_model_change_reported_by_runtime_is_amber():
    now = ts("2026-10-02T03:00:00Z")
    inputs = healthy(now)
    inputs["fly_status"]["ai_provider_health"] = {
        "last_ai_success_ts": now - 60, "consecutive_failures": 0, "configured_model": "deepseek-flash",
        "last_model_echo": "deepseek-flash",
        "served_model_changes": [{"from": "deepseek-v4-flash", "to": "deepseek-flash", "at": sh.iso(now - 3600)}]}
    assert by_id(sh.evaluate(inputs, {}))["ai.served_model"]["status"] == sh.AMBER


@pytest.mark.parametrize("total,available,expected", [
    (12.0, True, sh.GREEN), (4.99, True, sh.AMBER), (1.23, True, sh.AMBER), (0.99, True, sh.RED), (20.0, False, sh.RED),
])
def test_deepseek_balance_thresholds(total, available, expected):
    now = ts("2026-10-02T03:00:00Z")
    inputs = healthy(now)
    inputs["deepseek_balance"] = {"checked_at": now, "source": "laptop", "total_usd": total, "is_available": available}
    assert by_id(sh.evaluate(inputs, {}))["deepseek.balance"]["status"] == expected


def test_deepseek_balance_prefers_fresh_fly_value_and_skips_without_key():
    now = ts("2026-10-02T03:00:00Z")
    inputs = healthy(now)
    inputs["deepseek_balance"] = {"checked_at": now, "source": "laptop", "error": "KEY_MISSING"}
    assert by_id(sh.evaluate(inputs, {}))["deepseek.balance"]["status"] == sh.SKIP
    inputs["fly_status"]["ai_provider_health"] = {"deepseek_balance": {
        "total_usd": 0.5, "is_available": True, "checked_at": sh.iso(now - 120)}}
    check = by_id(sh.evaluate(inputs, {}))["deepseek.balance"]
    assert check["status"] == sh.RED and "source=fly" in check["observed"]


def test_deepseek_balance_parse_and_never_echo_key(monkeypatch):
    now = ts("2026-10-02T03:00:00Z")
    payload = {"is_available": True, "balance_infos": [
        {"currency": "CNY", "total_balance": "9.00"}, {"currency": "USD", "total_balance": "1.23"}]}
    assert sh.parse_deepseek_balance(payload, None, now, "laptop")["total_usd"] == pytest.approx(1.23)
    assert sh.parse_deepseek_balance(None, "HTTP_401", now, "laptop") == {
        "checked_at": now, "source": "laptop", "error": "HTTP_401"}
    seen = {}

    def fake_http(url, *, headers=None, timeout=20.0):
        seen["url"], seen["auth"] = url, headers["Authorization"]
        return payload, None

    monkeypatch.setattr(sh, "http_json", fake_http)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    cache: dict = {}
    result = sh.collect_deepseek_balance({"DEEPSEEK_API_KEY": "sk-test-secret"}, cache, now)
    assert seen == {"url": sh.DEEPSEEK_BALANCE_URL, "auth": "Bearer sk-test-secret"}
    assert "sk-test-secret" not in json.dumps(result) and "sk-test-secret" not in json.dumps(cache)
    seen.clear()
    assert sh.collect_deepseek_balance({"DEEPSEEK_API_KEY": "sk-test-secret"}, cache, now + 60)["total_usd"] == 1.23
    assert not seen  # cached for 10 minutes


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


def test_notify_is_toast_only_even_with_legacy_channel_config(tmp_path, monkeypatch):
    events = [{"event": "OPEN", "check": "ws.ticks", "observed": "500s", "hint": "ws down", "status": "RED"},
              {"event": "AMBER", "check": "x", "observed": "", "hint": "", "status": "AMBER"}]
    (tmp_path / "health").mkdir()
    (tmp_path / "health" / "alarm-channels.json").write_text(json.dumps({"webhook_url": "https://example.invalid/h"}))
    monkeypatch.setenv("DOXXED_ALERT_WEBHOOK_URL", "https://example.invalid/w")
    monkeypatch.setenv("DOXXED_ALERT_TELEGRAM_BOT_TOKEN", "1:x")
    monkeypatch.setenv("DOXXED_ALERT_TELEGRAM_CHAT_ID", "1")
    monkeypatch.setattr(sh, "http_json", lambda *a, **k: pytest.fail("no network channel may be called"))
    toasts = []
    result = sh.notify(events, tmp_path, toast=lambda t, b: toasts.append((t, b)) or True)
    assert result == {"pushed": 1, "toast": True}
    assert toasts and "ws.ticks" in toasts[0][1]
    assert sh.notify([events[1]], tmp_path, toast=lambda t, b: True)["pushed"] == 0


def _alarm_row(at, event="AMBER", check="analyzer.api"):
    return {"schema": sh.ALARM_SCHEMA, "at": at, "event": event, "check": check, "status": "AMBER",
            "observed": "o" * 900, "threshold": "t", "hint": "h", "runbook": "docs/SYSTEM_HEALTH_RUNBOOK.md#x"}


def test_fly_push_sends_unsynced_alarm_events_in_bounded_chunks(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_ADMIN_TOKEN", "t")
    opts = sh.parse_args(["--state-dir", str(tmp_path), "--vault", str(tmp_path / "none.env")])
    now = ts("2026-10-02T06:00:00Z")
    log = tmp_path / "health" / "alarms.jsonl"
    log.parent.mkdir(parents=True)
    rows = [_alarm_row("2026-08-01T00:00:00Z")]  # older than 30 days: never sent
    rows += [_alarm_row(f"2026-10-02T0{i // 60}:{i % 60:02d}:00Z", check=f"c{i}") for i in range(150)]
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    report = sh.summarize(sh.evaluate(healthy(now), {}), {}, now)
    held: list = []

    def fly(url, **kw):
        body = kw["body"]
        assert len(json.dumps(body)) < 256 * 1024
        held.extend(body.get("alarm_events") or [])
        last = max((sh.parse_ts(e["at"]) for e in held), default=None)
        return {"ok": True, "alarm_history": {"count": len(held), "through_ts": last}}, None

    state: dict = {}
    first = sh.push_fly_banner(report, opts, state, now, post=fly)
    assert first == "ok alarms=120 sent=120"
    assert all(len(e["observed"]) <= 240 for e in held)
    assert "check_status" in sh.banner_payload(report)
    second = sh.push_fly_banner(report, opts, state, now, post=fly)
    assert second == "ok alarms=150 sent=30"
    assert sh.push_fly_banner(report, opts, state, now, post=fly) == "ok alarms=150 sent=0"
    # Fly restarted (memory-only history): its empty answer resets the cursor and the log is re-sent.
    held.clear()
    state["fly_alarm_sync"]["through_ts"] = None
    assert sh.push_fly_banner(report, opts, state, now, post=fly) == "ok alarms=120 sent=120"


def test_fly_push_backs_off_when_fly_lacks_the_history_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_ADMIN_TOKEN", "t")
    opts = sh.parse_args(["--state-dir", str(tmp_path), "--vault", str(tmp_path / "none.env")])
    now = ts("2026-10-02T06:00:00Z")
    (tmp_path / "health").mkdir()
    (tmp_path / "health" / "alarms.jsonl").write_text(json.dumps(_alarm_row("2026-10-02T05:00:00Z")) + "\n")
    report = sh.summarize(sh.evaluate(healthy(now), {}), {}, now)
    sent = []
    old_fly = lambda url, **kw: (sent.append(len(kw["body"].get("alarm_events") or [])) or {"ok": True}, None)
    state: dict = {}
    assert "no alarm history endpoint" in sh.push_fly_banner(report, opts, state, now, post=old_fly)
    sh.push_fly_banner(report, opts, state, now + 60, post=old_fly)
    sh.push_fly_banner(report, opts, state, now + sh.ALARM_UNSUPPORTED_RETRY_SEC + 1, post=old_fly)
    assert sent == [1, 0, 1]


def test_neon_config_reads_env_then_uncommitted_state_file(tmp_path, monkeypatch):
    monkeypatch.delenv("NEON_API_KEY", raising=False)
    monkeypatch.delenv("NEON_PROJECT_ID", raising=False)
    assert sh.collect_neon({}, str(tmp_path)) is None
    (tmp_path / "health").mkdir()
    (tmp_path / "health" / "neon.env").write_text("NEON_PROJECT_ID=fancy-mode-1\n")
    assert sh.collect_neon({}, str(tmp_path)) == {"missing": ["NEON_API_KEY"]}
    inputs = healthy(ts("2026-10-02T00:00:00Z"))
    inputs["neon"] = {"missing": ["NEON_API_KEY"]}
    neon = next(c for c in sh.evaluate(inputs, {}) if c["id"] == "neon.usage")
    assert neon["status"] == sh.SKIP and "NEON_API_KEY" in neon["observed"]


def _neon_bucket(start, hours, **metrics):
    end = sh.iso(sh.parse_ts(start) + hours * 3600)
    return {"timeframe_start": start, "timeframe_end": end,
            "metrics": [{"metric_name": k, "value": v} for k, v in metrics.items()]}


def _neon_payload(*buckets, project="fancy-mode-1", plan="launch"):
    return {"projects": [{"project_id": project, "periods": [
        {"period_plan": plan, "period_start": "2026-10-01T00:00:00Z", "consumption": list(buckets)}]}]}


NEON_DAILY = _neon_payload(_neon_bucket("2026-10-01T00:00:00Z", 24, compute_unit_seconds=36_000,
                                        public_network_transfer_bytes=11_000_000_000,
                                        root_branch_bytes_month=4_000_000, instant_restore_bytes_month=36_000_000))
NEON_HOURLY = _neon_payload(
    _neon_bucket("2026-09-30T23:00:00Z", 1, public_network_transfer_bytes=999_000_000_000),  # previous month
    _neon_bucket("2026-10-02T05:00:00Z", 1, compute_unit_seconds=900, public_network_transfer_bytes=25_000_000,
                 root_branch_bytes_month=187_000),
    _neon_bucket("2026-10-02T06:00:00Z", 1, compute_unit_seconds=900, public_network_transfer_bytes=24_000_000,
                 root_branch_bytes_month=187_000),
    _neon_bucket("2026-10-02T07:00:00Z", 1, compute_unit_seconds=300, public_network_transfer_bytes=9_000_000))


def _neon_env(tmp_path, monkeypatch, body="NEON_API_KEY=napi_secret_test\nNEON_PROJECT_ID=fancy-mode-1\n"):
    for name in ("NEON_API_KEY", "NEON_PROJECT_ID", "NEON_ORG_ID", "NEON_EGRESS_BUDGET_BYTES_PER_HOUR"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "health").mkdir(exist_ok=True)
    (tmp_path / "health" / "neon.env").write_text(body)


def _neon_check(neon):
    inputs = healthy(ts("2026-10-02T07:40:00Z"))
    inputs["neon"] = neon
    return next(c for c in sh.evaluate(inputs, {}) if c["id"] == "neon.usage")


def test_neon_usage_reads_consumption_history_v2(tmp_path, monkeypatch):
    _neon_env(tmp_path, monkeypatch)
    now = ts("2026-10-02T07:40:00Z")
    calls = []

    def fake(url, **kw):
        calls.append((url, kw["headers"]))
        if "/consumption_history/v2/projects" not in url:
            return {"project": {"id": "fancy-mode-1", "org_id": "org-test-1", "data_transfer_bytes": 0}}, None
        return (NEON_HOURLY if "granularity=hourly" in url else NEON_DAILY), None

    monkeypatch.setattr(sh, "http_json", fake)
    cache = {}
    out = sh.collect_neon(cache, str(tmp_path), now)
    assert calls[0][0].endswith("/projects/fancy-mode-1")
    assert all(h["Authorization"] == "Bearer napi_secret_test" for _u, h in calls)
    hourly = next(u for u, _h in calls if "granularity=hourly" in u)
    daily = next(u for u, _h in calls if "granularity=daily" in u)
    assert "org_id=org-test-1" in hourly and "project_ids=fancy-mode-1" in hourly
    assert "public_network_transfer_bytes" in hourly and "compute_unit_seconds" in hourly
    assert "from=2026-10-01T00%3A00%3A00Z" in daily and "to=2026-10-02T00%3A00%3A00Z" in daily
    # MTD = Oct 1 (daily) + Oct 2 hourly; the Sept 30 hour only feeds the rate window, never the month.
    assert out["egress_bytes"] == 11_058_000_000
    assert out["compute_cu_hours"] == pytest.approx((36_000 + 2_100) / 3600)
    assert out["rate_bytes_per_hour"] == 24_000_000 and out["rate_hour"] == "2026-10-02T06:00:00Z"
    assert out["plan"] == "launch" and out["est_cost_usd"] == pytest.approx(
        round(38_100 / 3600 * 0.106 + 0.004374 * 0.35 + 0.036 * 0.20, 2))
    assert "napi_secret_test" not in json.dumps(out)
    # Cached for 15 min (and the resolved org is reused afterwards, no project lookup).
    n = len(calls)
    assert sh.collect_neon(cache, str(tmp_path), now + 60) == out and len(calls) == n
    sh.collect_neon(cache, str(tmp_path), now + 16 * 60)
    assert len(calls) == n + 2 and not any(u.endswith("/projects/fancy-mode-1") for u, _h in calls[n:])

    check = _neon_check(out)
    assert check["status"] == sh.GREEN, check
    assert "MTD egress 11.06GB" in check["observed"] and "rate 24.0MB/h (hour 06:00Z)" in check["observed"]
    assert "CU-h" in check["observed"] and "est. cost $" in check["observed"]
    assert "napi_secret_test" not in json.dumps(check)


def test_neon_usage_never_green_on_zero_empty_error_or_unknown_rate(tmp_path, monkeypatch):
    _neon_env(tmp_path, monkeypatch, "NEON_API_KEY=k\nNEON_PROJECT_ID=fancy-mode-1\nNEON_ORG_ID=org-1\n")
    now = ts("2026-10-02T07:40:00Z")
    monkeypatch.setattr(sh, "http_json", lambda url, **kw: (_neon_payload(), None))
    empty = sh.collect_neon({}, str(tmp_path), now)
    assert empty["buckets"] == 0
    check = _neon_check(empty)
    assert check["status"] == sh.AMBER and "no consumption reported" in check["observed"]

    zeros = _neon_payload(_neon_bucket("2026-10-02T06:00:00Z", 1, public_network_transfer_bytes=0))
    monkeypatch.setattr(sh, "http_json", lambda url, **kw: (zeros, None))
    assert _neon_check(sh.collect_neon({}, str(tmp_path), now))["status"] == sh.AMBER

    for code, words in (("HTTP_403", "not available on this plan"), ("HTTP_401", "key rejected")):
        monkeypatch.setattr(sh, "http_json", lambda url, code=code, **kw: (None, code))
        out = sh.collect_neon({}, str(tmp_path), now)
        check = _neon_check(out)
        assert check["status"] == sh.AMBER and code in check["observed"] and words in check["observed"]

    no_rate = sh.summarize_neon_usage(NEON_DAILY, _neon_payload(), "fancy-mode-1", now)
    check = _neon_check({**no_rate, "project": "fancy-mode-1"})
    assert no_rate["rate_bytes_per_hour"] is None and check["status"] == sh.AMBER and "rate ?" in check["observed"]

    hot = sh.summarize_neon_usage(NEON_DAILY, NEON_HOURLY, "fancy-mode-1", now)
    check = _neon_check({**hot, "budget_bytes_per_hour": "10000000"})
    assert check["status"] == sh.AMBER and "#261" in check["hint"]


def test_neon_org_lookup_failure_is_amber(tmp_path, monkeypatch):
    _neon_env(tmp_path, monkeypatch)
    monkeypatch.setattr(sh, "http_json", lambda url, **kw: ({"project": {"id": "fancy-mode-1"}}, None))
    out = sh.collect_neon({}, str(tmp_path), ts("2026-10-02T07:40:00Z"))
    assert out["error"] == "NO_ORG_ID" and _neon_check(out)["status"] == sh.AMBER


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
    assert set(payload) == {"schema", "verdict", "generated_at", "open_alarms", "failing", "counts", "check_status",
                            "source", "proof"}
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


def test_failed_required_study_or_unchecked_integrity_is_red_never_green():
    now = ts("2026-10-02T09:30:00Z")
    inputs = healthy(now)
    assert by_id(sh.evaluate(inputs, {}))["analyzer.studies"]["status"] == sh.GREEN
    inputs["analyzer_receipt"].update(level="RED", reasons=["required studies failed: best_policy_research_report.json"],
                                      failed_required_studies=["best_policy_research_report.json"])
    check = by_id(sh.evaluate(inputs, {}))["analyzer.studies"]
    assert check["status"] == sh.RED and "best_policy" in check["observed"]
    inputs = healthy(now)
    inputs["analyzer_integrity"] = {"report_status": "UNCHECKED"}
    assert by_id(sh.evaluate(inputs, {}))["analyzer.studies"]["status"] == sh.RED
    inputs = healthy(now)
    inputs["analyzer_receipt"] = None
    assert by_id(sh.evaluate(inputs, {}))["analyzer.studies"]["status"] == sh.AMBER
    inputs["analyzer_integrity"] = {"report_status": "UNCHECKED"}
    assert by_id(sh.evaluate(inputs, {}))["analyzer.studies"]["status"] == sh.RED


def test_receipt_older_than_the_current_generation_is_not_trusted():
    now = ts("2026-10-02T09:30:00Z")
    inputs = healthy(now)
    inputs["analyzer_receipt"]["generated_at"] = sh.iso(now - 5 * 3600)
    inputs["analyzer_manifest_generated_at"] = sh.iso(now - 600)
    assert by_id(sh.evaluate(inputs, {}))["analyzer.studies"]["status"] == sh.AMBER


def test_data_health_uses_mirror_relative_verdicts_only():
    now = ts("2026-10-02T09:30:00Z")
    inputs = healthy(now)
    inputs["data_health_report"] = {"streams": [{"stream": "coinbase_1s", "status": "STALE"}]}
    assert by_id(sh.evaluate(inputs, {}))["analyzer.data_health"]["status"] == sh.SKIP
    inputs["data_health_report"] = {"stream_status_basis": "mirror head", "mirror_status": "OK",
                                    "status_counts": {"OK": 16, "WARMUP": 1},
                                    "streams": [{"stream": "coinbase_1s", "status": "OK"},
                                                {"stream": "trailing_regime_1m", "status": "WARMUP"}]}
    assert by_id(sh.evaluate(inputs, {}))["analyzer.data_health"]["status"] == sh.GREEN
    inputs["data_health_report"]["streams"].append({"stream": "liq_okx", "status": "STALE"})
    check = by_id(sh.evaluate(inputs, {}))["analyzer.data_health"]
    assert check["status"] == sh.AMBER and "liq_okx=STALE" in check["observed"]


def test_streams_coverage_reads_fly_collection_blocks_not_a_missing_data_streams_key():
    now = ts("2026-10-02T09:30:00Z")
    inputs = healthy(now)
    inputs["fly_status"]["collection"].update({
        "market_context_tape": {"enabled": True, "status": "OK", "age_sec": 5, "stale_feeds": [],
                                "feeds": {"liq_okx": {"ok": True, "connected": True}}},
        "microstructure_tape": {"last_bucket_ts": now - 2, "write_failures_this_process": 0},
    })
    check = by_id(sh.evaluate(inputs, {}))["streams.coverage"]
    assert check["status"] == sh.GREEN
    assert "n/a" not in check["observed"] and "market_context:OK" in check["observed"]
    inputs["fly_status"]["collection"]["market_context_tape"].update(status="DEGRADED", stale_feeds=["liq_okx"])
    assert by_id(sh.evaluate(inputs, {}))["streams.coverage"]["status"] == sh.AMBER
    inputs = healthy(now)
    inputs["fly_status"]["collection"]["microstructure_tape"] = {"last_bucket_ts": now - 3600}
    assert by_id(sh.evaluate(inputs, {}))["streams.coverage"]["status"] == sh.AMBER


def test_retention_lock_failures_are_not_green(tmp_path):
    now = ts("2026-10-02T09:30:00Z")
    inputs = healthy(now)
    inputs["retention_exits"] = [{"exit": 0}, {"exit": 3, "detail": "shadow-root lock busy"}]
    check = by_id(sh.evaluate(inputs, {}))["storage.retention"]
    assert check["status"] == sh.AMBER and "lock busy" in check["observed"]
    inputs["retention_exits"] = [{"exit": 3}, {"exit": 3}, {"exit": 3}]
    assert by_id(sh.evaluate(inputs, {}))["storage.retention"]["status"] == sh.RED
    inputs["retention_exits"] = [{"exit": 3}, {"exit": 0}, {"exit": 0}]
    assert by_id(sh.evaluate(inputs, {}))["storage.retention"]["status"] == sh.GREEN
    log = tmp_path / "segment-analyzer-cycle-20261002.log"
    log.write_text(
        "2026-10-02T07:56:08.89Z pid=1 RETENTION exit=3 {\"ok\": false, \"error\": \"shadow-root lock busy\"}\n"
        "2026-10-02T08:00:00Z pid=1 MIGRATION exit=0\n"
        "2026-10-02T09:27:41.94Z pid=2 RETENTION exit=0 {\"ok\": true}\n", encoding="utf-8")
    rows = sh.last_retention_exits(tmp_path)
    assert [row["exit"] for row in rows] == [3, 0]


def test_retention_cap_and_archive_freshness():
    now = ts("2026-10-02T00:00:00Z")
    inputs = healthy(now)
    inputs["retention_last_run"].update(level="RED", usage_fraction=0.93, bytes_after=46.5e9)
    assert by_id(sh.evaluate(inputs, {}))["storage.retention"]["status"] == sh.RED
    inputs = healthy(now)
    inputs["retention_last_run"]["finished_at"] = sh.iso(now - 4 * 3600)
    assert by_id(sh.evaluate(inputs, {}))["storage.retention"]["status"] == sh.AMBER
    inputs = healthy(now)
    inputs["fly_health"]["volume"]["transfer"].update(pruned_through_seq=90, custody_through_seq=80)
    assert by_id(sh.evaluate(inputs, {}))["storage.retention"]["status"] == sh.RED
    inputs = healthy(now)
    inputs["archive_last_snapshot"]["written_at"] = sh.iso(now - 13 * 3600)
    assert by_id(sh.evaluate(inputs, {}))["archive.freshness"]["status"] == sh.RED
    inputs = healthy(now)
    inputs["retention_last_run"] = None
    inputs["archive_last_snapshot"] = None
    checks = by_id(sh.evaluate(inputs, {}))
    assert checks["storage.retention"]["status"] == sh.AMBER
    assert checks["archive.freshness"]["status"] == sh.AMBER


def _recon(now: float, ids_closes) -> dict:
    trades = [{"trade_id": tid, "research_lane": lane, "close_ts": sh.iso(close), "pnl_exact": pnl,
               "pnl_cents": round(pnl, 2), "net_pnl_basis": "TERMINAL_COST_RECEIPT_EXACT", "in_cohort": True,
               "quarantine_reason": None} for tid, lane, close, pnl in ids_closes]
    cohort = {lane: {"n": sum(1 for t in trades if t["research_lane"] == lane),
                     "net_pnl_usd": sum(t["pnl_exact"] for t in trades if t["research_lane"] == lane),
                     "win_pct": 100.0} for lane in LANES}
    return {"schema": "ledger_reconciliation_v1", "level": "GREEN", "reasons": [], "lanes": list(LANES),
            "source_data_through": sh.iso(now - 600), "analyzer_cohort": cohort, "mirror_ledger": cohort,
            "quarantined": {lane: 0 for lane in LANES}, "trades": trades,
            "win_pct_definition": "wins / closed"}


def _reconciled_inputs(now: float) -> dict:
    inputs = healthy(now)
    inputs["ledger_reconciliation"] = _recon(now, [(f"t-{lane}", lane, now - 1800, 0.004) for lane in LANES])
    inputs["mirror_trades"] = [{"trade_id": f"t-{lane}", "research_lane": lane, "close_ts": sh.iso(now - 1800),
                                "net_pnl_usd": "0.0", "epoch_id": ""} for lane in LANES]
    for row in inputs["fly_state"]["trades"]:
        row["net_pnl_usd"] = 0.0
    inputs["fly_state"]["lane_pnl_ledger"] = {lane: {"closes": 1, "net_pnl_usd": 0.0, "wins": 0} for lane in LANES}
    return inputs


def test_ledger_reconciliation_compares_fly_mirror_and_analyzer():
    now = ts("2026-10-02T00:00:00Z")
    inputs = _reconciled_inputs(now)
    check = by_id(sh.evaluate(inputs, {}))["ledger.reconciliation"]
    assert check["status"] == sh.GREEN, check["observed"]
    assert "fly 1/mirror 1+0/analyzer 1" in check["observed"]

    inputs["fly_state"]["lane_pnl_ledger"][LANES[0]]["closes"] = 3
    check = by_id(sh.evaluate(inputs, {}))["ledger.reconciliation"]
    assert check["status"] == sh.RED
    assert "trade counts differ" in check["observed"]


def test_read_ledger_rows_keeps_only_reconciliation_fields(tmp_path):
    path = tmp_path / "trades_3factor.csv"
    path.write_text("trade_id,research_lane,net_pnl_usd,extra\nt1,FAMILY_X,0.01,zzz\n", encoding="utf-8")
    assert sh.read_ledger_rows(path) == [{"trade_id": "t1", "research_lane": "FAMILY_X", "epoch_id": None,
                                          "close_ts": None, "ts": None, "net_pnl_usd": "0.01"}]
    assert sh.read_ledger_rows(tmp_path / "missing.csv") is None


def test_ledger_reconciliation_red_report_and_missing_inputs():
    now = ts("2026-10-02T00:00:00Z")
    assert by_id(sh.evaluate(healthy(now), {}))["ledger.reconciliation"]["status"] == sh.SKIP
    inputs = _reconciled_inputs(now)
    inputs["ledger_reconciliation"].update(
        level="RED", reasons=["1 current-epoch tile trades dropped from the cohort without a reason"])
    assert by_id(sh.evaluate(inputs, {}))["ledger.reconciliation"]["status"] == sh.RED
    inputs = _reconciled_inputs(now)
    inputs["fly_state"] = None
    assert by_id(sh.evaluate(inputs, {}))["ledger.reconciliation"]["status"] == sh.AMBER


def test_tier_a_health_drives_storage_tier_a():
    now = ts("2026-10-02T00:00:00Z")
    inputs = healthy(now)
    assert by_id(sh.evaluate(inputs, {}))["storage.tier_a"]["status"] == sh.SKIP
    inputs["tier_a_health"] = {"level": "GREEN", "backfilled": True, "datasets": [
        {"dataset": "bitfinex_l1_tape_1s", "level": "GREEN", "reasons": []}]}
    assert by_id(sh.evaluate(inputs, {}))["storage.tier_a"]["status"] == sh.GREEN
    inputs["tier_a_health"] = {"level": "RED", "backfilled": False, "datasets": [
        {"dataset": "bitfinex_l1_tape_1s", "level": "RED", "reasons": ["INVALID_DAY_ROWS"]}]}
    check = by_id(sh.evaluate(inputs, {}))["storage.tier_a"]
    assert check["status"] == sh.RED
    assert "INVALID_DAY_ROWS" in check["observed"]
