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
    for rel in ("runtime/v3/receipts/emergency_record_idempotency_v1/a/x.json",
                "runtime/v3/receipts/owner_x/y.json", "runtime/v3/receipts/recovery_z/z.json",
                "quarantine/v3_completeness_markers/m.json", "runtime/live_copy_1.json", "runtime/old.jsonl",
                "research_archive/handoffs/x.json", "archive_receipt.json",
                "owner/runtime/old2.json", "other/live_copy_2.json",
                "runtime/v3/receipts/ledger_generations_v1/owner/g.json",
                "runtime/sub/live_copy_3.json"):
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("{}")
    got = ig.scan_local_oldest(str(tmp_path))
    assert set(got["files"]) == {"runtime/old.jsonl", "research_archive/handoffs/x.json", "archive_receipt.json",
                                 "owner/runtime/old2.json", "other/live_copy_2.json",
                                 "runtime/v3/receipts/ledger_generations_v1/owner/g.json",
                                 "runtime/sub/live_copy_3.json"}
    assert "runtime/live_copy_1.json" in got["kept"]


def test_retained_by_design_is_anchored():
    yes = ("runtime/v3/receipts/emergency_x/a/b.json", "runtime/v3/receipts/owner/a.json",
           "runtime/live_copy_a.json", "quarantine/v3_completeness_markers/a")
    no = ("research_archive/handoffs/x", "archive_receipt.json", "owner/runtime/a.json",
          "x/runtime/v3/receipts/owner/a.json", "runtime/v3/receipts/ledger/owner/a.json",
          "a/live_copy_x.json", "runtime/live_copy_x.json.bak", "quarantine/v3_completeness_markers_old/a",
          "runtime/v3/receipts/emergency")
    assert all(ig.retained_by_design(x) for x in yes)
    assert not any(ig.retained_by_design(x) for x in no)


def test_live_copy_tile_eligibility_sidecar_retained_only_at_volume_root(tmp_path):
    assert ig.retained_by_design("live_copy_tile_eligibility.json")
    for bad in ("x/live_copy_tile_eligibility.json",
                "live_copy_tile_eligibility.json.old", "live_copy_tile_eligibility.jsonx"):
        assert not ig.retained_by_design(bad), bad
    f = tmp_path / "live_copy_tile_eligibility.json"
    f.write_text("{}")
    import os
    os.utime(f, (1000, 1000))
    scan = ig.scan_local_oldest(str(tmp_path))
    assert "live_copy_tile_eligibility.json" in scan["kept"] and "live_copy_tile_eligibility.json" not in scan["files"]


def test_runtime_state_files_retained_only_at_their_anchored_paths():
    yes = ("runtime/data_epoch.json", "runtime/crash_dump.json", "runtime/clean-epoch-receipts/receipts.jsonl",
           "runtime/clean-epoch-receipts/pre-start/receipt-fly-1.json",
           "runtime/data_epoch_boundary/cancellation_evidence_handoffs.jsonl.compaction.json")
    no = ("data_epoch.json", "x/runtime/data_epoch.json", "runtime/data_epoch.json.bak", "x/runtime/crash_dump.json",
          "runtime/data_epoch_boundary/sub/a.compaction.json", "runtime/data_epoch_boundary/a.json",
          "archive_receipt.json", "research_archive/handoffs/cancellation_evidence_handoffs.jsonl.20261013T021532Z",
          "research_archive/handoffs/fill_evidence_handoffs.jsonl.20261013T212706Z")
    assert all(ig.retained_by_design(x) for x in yes)
    assert not any(ig.retained_by_design(x) for x in no), [x for x in no if ig.retained_by_design(x)]


def test_pinned_epoch_boundary_receipts_retained_only_for_the_pinned_id_at_the_anchored_path():
    e = ig.PINNED_EPOCH_ID
    assert e == "ce-20261004-v31-freeze21b"
    for ok in (f"runtime/data_epoch_boundary/{e}.v3.json", f"runtime/data_epoch_boundary/{e}.research_events_v22.json"):
        assert ig.retained_by_design(ok), ok
    for bad in ("runtime/data_epoch_boundary/ce-20261004-v31-clean.v3.json",
                "runtime/data_epoch_boundary/ce-20261004-v30-freeze21b.research_events_v22.json",
                f"x/runtime/data_epoch_boundary/{e}.v3.json", f"data_epoch_boundary/{e}.v3.json",
                f"runtime/data_epoch_boundary/sub/{e}.v3.json", f"runtime/data_epoch_boundary/{e}.v3.json.bak",
                f"runtime/data_epoch_boundary/{e}.pre_epoch_archive.json", f"runtime/data_epoch_boundary/{e}x.v3.json"):
        assert not ig.retained_by_design(bad), bad
