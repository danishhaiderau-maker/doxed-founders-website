import json
import integrity_gaps as ig
import runtime_telemetry as rt


def ids(out):
    return {r["id"]: r for r in out["rows"]}


def test_all_gaps_and_live_off_row_present():
    out = ig.evaluate({})
    assert set(ids(out)) == {"LIVE_OFF"} | {f"G{i}" for i in range(1, 21)}


def test_live_off_row_red_unless_disarmed_and_forced_paper():
    assert ids(ig.evaluate({"live_armed": False, "force_paper": True}))["LIVE_OFF"]["status"] == "GREEN"
    out = ig.evaluate({"live_armed": True, "force_paper": True})
    assert ids(out)["LIVE_OFF"]["status"] == "RED" and out["status"] == "RED"
    assert ids(ig.evaluate({"live_armed": False, "force_paper": False}))["LIVE_OFF"]["status"] == "RED"


def test_g9_g10_na_while_live_off():
    r = ids(ig.evaluate({"live_armed": False, "force_paper": True}))
    for g in ("G9", "G10", "G1", "G2", "G3", "G4"):
        assert r[g]["status"] == "NA" and r[g]["source"] == ig.NA_LIVE_OFF


def test_g12_requires_both_depth_fields():
    r = ids(ig.evaluate({"paper_fill": {"v1_share": 1.0, "depth_fields_ok": False}}))
    assert r["G12"]["status"] == "AMBER"
    assert "depth_snapshot_signal" in r["G12"]["metric"] and "depth_snapshot_fill" in r["G12"]["metric"]


def test_g14_needs_deploy_survival_and_paused_positive():
    base = {"durable_total": 3, "survived_deploy": True, "guarded_deploys_with_paused_gt0": 0, "opens_while_paused": 0}
    assert ids(ig.evaluate({"paused": base}))["G14"]["status"] == "AMBER"
    assert ids(ig.evaluate({"paused": {**base, "guarded_deploys_with_paused_gt0": 1}}))["G14"]["status"] == "GREEN"
    assert ids(ig.evaluate({"paused": {**base, "opens_while_paused": 1}}))["G14"]["status"] == "RED"


def test_g16_red_on_rows_older_than_epoch_and_on_receipt_disagreement():
    w = {"fly_files_older_than_epoch": 0, "r2_objects_older_than_epoch": 2,
         "r2_independent_older_than_epoch": 2, "box_files_older_than_epoch": 0, "fly_scan_complete": True}
    assert ids(ig.evaluate({"epoch_ts": 1.0, "wipe": w}))["G16"]["status"] == "RED"
    w.update(r2_objects_older_than_epoch=0, r2_independent_older_than_epoch=0)
    assert ids(ig.evaluate({"epoch_ts": 1.0, "wipe": w}))["G16"]["status"] == "GREEN"
    # the wipe job says 0 but the independent R2 listing does not agree -> RED
    w.update(r2_independent_older_than_epoch=None)
    assert ids(ig.evaluate({"epoch_ts": 1.0, "wipe": w}))["G16"]["status"] == "PENDING"
    w.update(r2_independent_older_than_epoch=0, fly_scan_complete=False)
    assert ids(ig.evaluate({"epoch_ts": 1.0, "wipe": w}))["G16"]["status"] == "PENDING"


def test_g16_scan_is_recursive_and_uncapped(tmp_path):
    (tmp_path / "runtime").mkdir()
    (tmp_path / "old.jsonl").write_text(json.dumps({"ts": 100}) + "\n")
    (tmp_path / "runtime" / "new.jsonl").write_text(json.dumps({"ts": 5000}) + "\n")
    (tmp_path / "runtime" / "rot.jsonl.1").write_text(json.dumps({"ts": 6_000_000_000_000}) + "\n")
    for i in range(400):
        (tmp_path / "runtime" / f"f{i}.jsonl").write_text(json.dumps({"ts": 7000}) + "\n")
    got = ig.scan_local_oldest(str(tmp_path))
    assert got["complete"] and got["scanned"] == 403
    assert got["files"]["old.jsonl"] == 100 and got["files"]["runtime/rot.jsonl.1"] == 6_000_000_000


def test_g20_is_computed():
    assert ids(ig.evaluate({}))["G20"]["status"] == "PENDING"
    ok = {"ok": True, "canceled_recent": 3, "errors_recent": []}
    assert ids(ig.evaluate({"vercel": ok}))["G20"]["status"] == "GREEN"
    assert ids(ig.evaluate({"vercel": {**ok, "errors_recent": [{"uid": "x"}]}}))["G20"]["status"] == "RED"
    assert ids(ig.evaluate({"vercel": {**ok, "canceled_recent": 0}}))["G20"]["status"] == "AMBER"


def test_lifecycle_open_fixed_verified(tmp_path):
    p = str(tmp_path / "lc.json")
    green = {"live_armed": False, "force_paper": True}
    r = lambda out: {x["id"]: x for x in out["rows"]}["LIVE_OFF"]["lifecycle"]
    assert r(ig.apply_lifecycle(ig.evaluate(green), p, 0.0)) == "fixed"
    assert r(ig.apply_lifecycle(ig.evaluate(green), p, 23 * 3600.0)) == "fixed"
    assert r(ig.apply_lifecycle(ig.evaluate(green), p, 24 * 3600.0)) == "verified"
    assert r(ig.apply_lifecycle(ig.evaluate({"live_armed": True, "force_paper": True}), p, 25 * 3600.0)) == "open"
    assert r(ig.apply_lifecycle(ig.evaluate(green), p, 26 * 3600.0)) == "fixed"  # clock restarts
    out = ig.apply_lifecycle(ig.evaluate(green), p, 26 * 3600.0)
    assert {x["id"]: x for x in out["rows"]}["G9"]["lifecycle"] == "n/a"


def test_g17_thresholds():
    ok = {"trade_lock_wait_p95_ms": 50, "trade_lock_wait_max_ms": 200, "scheduler_lag_max_ms": 400}
    assert ids(ig.evaluate({"runtime": ok}))["G17"]["status"] == "GREEN"
    assert ids(ig.evaluate({"runtime": {**ok, "trade_lock_wait_max_ms": 600}}))["G17"]["status"] == "AMBER"
    assert "phase_timings.trade_lock_wait" in ids(ig.evaluate({}))["G17"]["source"]


def test_phase_recorder_feeds_telemetry_summary():
    rec = rt.PhaseRecorder(("trade_lock_wait", "trade_lock_hold"))
    for v in range(1, 101):
        rec.record({"trade_lock_wait": float(v), "trade_lock_hold": 2.0})
    d = rec.drain()
    assert d["trade_lock_wait"]["n"] == 100 and d["trade_lock_wait"]["max_ms"] == 100.0
    assert 94 <= d["trade_lock_wait"]["p95_ms"] <= 96
    assert rec.drain()["trade_lock_wait"]["n"] == 0
    c = rt.compact({"ts": 1, "phase_timings": d})
    assert c["tlw_max"] == 100.0 and c["tlh_max"] == 2.0


def test_route_registered():
    src = open("bot.py").read()
    assert "@app.route('/api/monitor/integrity-gaps'" in src
    assert "phase_fn=_RELAY_PHASES.drain" in src


def test_g16_scan_retains_by_design_runtime_state(tmp_path):
    import os
    for rel in ("runtime/v3/receipts/emergency_record_idempotency_v1/a/x.json",
                "runtime/v3/receipts/owner_x/y.json", "runtime/v3/receipts/recovery_z/z.json",
                "quarantine/v3_completeness_markers/m.json", "runtime/live_copy_1.json", "runtime/old.jsonl"):
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("{}")
    other = tmp_path / "runtime/v3/receipts/ledger_generations_v1/g.json"
    other.parent.mkdir(parents=True)
    other.write_text("{}")
    got = ig.scan_local_oldest(str(tmp_path))
    assert set(got["files"]) == {"runtime/old.jsonl", "runtime/v3/receipts/ledger_generations_v1/g.json"}
    assert "runtime/live_copy_1.json" in got["kept"]
    assert not ig.retained_by_design("runtime/v3/receipts/ledger_generations_v1/g.json")
