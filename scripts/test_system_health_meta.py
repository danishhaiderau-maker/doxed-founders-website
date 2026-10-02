from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import system_health as sh  # noqa: E402
import system_health_meta as meta  # noqa: E402

NOW = 1_790_950_000.0


def run(m, state=None, local_ts=None, **thr):
    return {c["id"]: c for c in meta.meta_checks({**m, "parse_ts": sh.parse_ts}, state or {}, NOW, sh.check,
                                                   sh.fmt_age, local_generated_ts=local_ts, thresholds=thr)}


def test_parity_checker_reports_age_verdict_and_long_lock_holds():
    ok = {"verdict": "GREEN", "generated_at": sh.iso(NOW - 600), "seq": 10, "counts": {"missing": 0},
          "timing": {"lock_held_sec": 42.0, "hashed": 3, "hash_cache_hits": 900}}
    c = run({"parity": ok})["analyzer.parity_checker"]
    assert c["status"] == "GREEN" and c["observed_fields"]["timing"]["hash_cache_hits"] == 900
    assert run({"parity": {**ok, "verdict": "RED"}})["analyzer.parity_checker"]["status"] == "RED"
    assert run({"parity": {**ok, "generated_at": sh.iso(NOW - 4 * 3600)}})["analyzer.parity_checker"]["status"] == "AMBER"
    assert run({"parity": None})["analyzer.parity_checker"]["status"] == "AMBER"
    holder = {"holder": "research_segment_fly_parity", "pid": 7, "acquired_at": sh.iso(NOW - 1500)}
    status = {"last_attempt_result": "LOCK_BUSY", "consecutive_failures": 25, "lock_holder": holder}
    rows = run({"parity": ok, "lock_holder": holder, "puller_status": status})
    assert rows["analyzer.parity_checker"]["status"] == "AMBER"
    assert "holding the puller lock" in rows["analyzer.parity_checker"]["hint"]
    assert rows["laptop.puller_lock"]["status"] == "AMBER"
    assert rows["laptop.puller_lock"]["observed_fields"]["consecutive_failures"] == 25


def test_puller_lock_ignores_a_stale_holder_sidecar_and_escalates_long_holds():
    holder = {"holder": "x", "pid": 1, "acquired_at": sh.iso(NOW - 3 * 3600)}
    ok = {"last_attempt_result": "OK", "consecutive_failures": 0, "run_seconds": 12.5, "max_run_seconds": 900}
    c = run({"lock_holder": holder, "puller_status": ok})["laptop.puller_lock"]
    assert c["status"] == "GREEN" and "last run 12.5s of max 900s" in c["observed"]
    busy = {"last_attempt_result": "LOCK_BUSY", "consecutive_failures": 40, "lock_holder": holder}
    assert run({"lock_holder": holder, "puller_status": busy})["laptop.puller_lock"]["status"] == "RED"
    other = {**busy, "lock_holder": {"acquired_at": "other"}}
    assert run({"lock_holder": holder, "puller_status": other})["laptop.puller_lock"]["status"] == "GREEN"


def test_chain_monitor_alerts_surface_as_a_check_not_only_a_toast():
    mon = {"checkedAt": sh.iso(NOW - 60), "alerts": []}
    assert run({"chain_monitor": mon})["laptop.chain_monitor"]["status"] == "GREEN"
    warn = {**mon, "alerts": [{"code": "XVL_EVALUATOR_STALE", "severity": "warning", "detail": "d"}]}
    assert run({"chain_monitor": warn})["laptop.chain_monitor"]["status"] == "AMBER"
    crit = {**mon, "alerts": [{"code": "SYNC_HEARTBEAT_IN_PROGRESS_TOO_LONG", "severity": "critical"}]}
    assert run({"chain_monitor": crit})["laptop.chain_monitor"]["status"] == "RED"
    stale = {"checkedAt": sh.iso(NOW - 7200), "alerts": []}
    assert run({"chain_monitor": stale})["laptop.chain_monitor"]["status"] == "AMBER"


def test_incident_relay_heartbeat_and_maintenance_cap():
    inc = {"heartbeat_at": NOW - 60, "alerts": {"maintenance_since": None, "conditions": {}}}
    assert run({"incident": inc})["laptop.incident_relay"]["status"] == "GREEN"
    stuck = {"heartbeat_at": NOW - 60, "alerts": {"maintenance_since": NOW - 2 * 3600, "conditions": {}}}
    c = run({"incident": stuck})["laptop.incident_relay"]
    assert c["status"] == "RED" and c["observed_fields"]["maintenance_age_sec"] == 7200
    assert run({"incident": {"heartbeat_at": NOW - 3600}})["laptop.incident_relay"]["status"] == "AMBER"


def test_interim_delivery_fly_copy_and_wall():
    rows = run({"interim": {"at": sh.iso(NOW - 120), "decision": "DEFERRED"}})
    assert rows["watcher.interim"]["status"] == "GREEN"
    assert run({"interim": {"at": sh.iso(NOW - 3600)}})["watcher.interim"]["status"] == "AMBER"
    state: dict = {}
    for _ in range(2):
        meta.record_delivery(state, "UNREACHABLE", {"pushed": 0})
    assert run({}, state)["watcher.delivery"]["status"] == "AMBER"
    meta.record_delivery(state, "ok alarms=3 sent=0", {"pushed": 0})
    assert run({}, state)["watcher.delivery"]["status"] == "GREEN"
    pub = {"generated_at": sh.iso(NOW - 3 * 3600)}
    assert run({"fly_published": pub}, local_ts=NOW - 300)["watcher.fly_copy"]["status"] == "RED"
    assert run({"fly_published": {"age_sec": 300}}, local_ts=NOW - 300)["watcher.fly_copy"]["status"] == "GREEN"
    good = [f"{sh.iso(NOW - 60)} | OWNER | msg | DONE"]
    assert run({"wall_tail": ["# header", *good]})["coordination.wall"]["status"] == "GREEN"
    assert run({"wall_tail": [*good, "free text line"]})["coordination.wall"]["status"] == "AMBER"


def test_dedupe_keeps_worst_and_acks_never_hide_red():
    a = sh.check("x.a", "x", "GREEN", "", "")
    b = sh.check("x.a", "x", "AMBER", "", "")
    c = sh.check("x.b", "x", "RED", "", "")
    rows = meta.dedupe([a, b, c])
    assert [r["id"] for r in rows] == ["x.a", "x.b"] and rows[0]["status"] == "AMBER"
    acks = {"acks": [{"check": "x.a", "until": sh.iso(NOW + 600), "by": "danish", "reason": "known"},
                     {"check": "x.b", "until": sh.iso(NOW + 600)}]}
    report = sh.summarize(rows, {}, NOW, acks=acks)
    assert report["verdict"] == "RED"
    assert [f["id"] for f in report["failing"]] == ["x.b"]
    assert report["acked"][0]["id"] == "x.a"
    by_id = {r["id"]: r for r in report["checks"]}
    assert by_id["x.b"]["ack_ignored"] and "acked" not in by_id["x.b"]
    expired = {"acks": [{"check": "x.a", "until": sh.iso(NOW - 1)}]}
    rows2 = [sh.check("x.a", "x", "AMBER", "", "")]
    assert sh.summarize(rows2, {}, NOW, acks=expired)["verdict"] == "AMBER"


def test_flapping_check_flags_oscillating_ids():
    state: dict = {}
    for i in range(8):
        rows = [sh.check("x.flap", "x", "RED" if i % 2 else "GREEN", "", ""), sh.check("x.calm", "x", "GREEN", "", "")]
        flap = meta.flapping(rows, state, sh.check)
    assert flap["status"] == "AMBER" and rows[0].get("flapping") and not rows[1].get("flapping")


def test_features_list_new_contracts():
    for f in ("parity_checker", "puller_lock", "amber_acks", "flapping", "epoch_parity_fields"):
        assert f in sh.WATCHER_FEATURES


def test_collect_meta_reads_files(tmp_path):
    state_dir, shadow = tmp_path / "chain", tmp_path / "shadow"
    (state_dir / "health").mkdir(parents=True)
    (shadow / ".puller").mkdir(parents=True)
    (shadow / "parity-latest.json").write_text(json.dumps({"verdict": "GREEN"}), encoding="utf-8")
    (state_dir / "health" / "acks.json").write_text(json.dumps({"acks": []}), encoding="utf-8")
    wall = tmp_path / "WALL.md"
    wall.write_text("a\nb\n", encoding="utf-8")
    m = meta.collect_meta(state_dir=state_dir, shadow_root=shadow, wall=wall, parse_ts=sh.parse_ts,
                          probe_processes=False)
    assert m["parity"]["verdict"] == "GREEN" and m["acks"] == {"acks": []} and m["wall_tail"] == ["a", "b"]
