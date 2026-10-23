import json
import integrity_gaps as ig
import runtime_telemetry as rt


def ids(out):
    return {r["id"]: r for r in out["rows"]}


def test_all_gaps_and_live_off_row_present():
    out = ig.evaluate({})
    assert set(ids(out)) == {"LIVE_OFF"} | {f"G{i}" for i in range(1, 22)}


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


def test_g14_is_na_after_disloc_removal():
    # Tiles 6/7 removed 23 Oct 2026: the PAUSED-skip ledger has no writer; G14 is NA whatever facts arrive.
    base = {"durable_total": 3, "survived_deploy": True, "guarded_deploys_with_paused_gt0": 0, "opens_while_paused": 1}
    for facts in ({}, {"paused": base}):
        assert ids(ig.evaluate(facts))["G14"]["status"] == "NA"


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
                f"runtime/data_epoch_boundary/{e}x.v3.json"):
        assert not ig.retained_by_design(bad), bad


def test_pinned_pre_epoch_archive_is_retained_but_not_other_ids():
    b = "runtime/data_epoch_boundary/"
    assert ig.retained_by_design(b + "ce-20261004-v31-freeze21b.pre_epoch_archive.json")
    assert not ig.retained_by_design(b + "ce-20261004-v31-freeze21.pre_epoch_archive.json")
    assert not ig.retained_by_design(b + "ce-20261004-v31-clean.pre_epoch_archive.json")
    assert not ig.retained_by_design(b + "sub/ce-20261004-v31-freeze21b.pre_epoch_archive.json")
    assert not ig.retained_by_design("ce-20261004-v31-freeze21b.pre_epoch_archive.json")


def test_only_the_active_reset_anchor_is_retained_not_old_reset_dirs(tmp_path):
    import json as _j
    active, old = "29f7a1efd216798db9309125", "aaaaaaaaaaaaaaaaaaaaaaaa"
    rr = tmp_path / "runtime" / "research_reset_receipts"
    for rid in (active, old):
        (rr / rid).mkdir(parents=True)
        (rr / rid / "binding.json").write_text("{}")
        (rr / rid / "operation.json").write_text("{}")
    (rr / "ACTIVE_RESET.json").write_text(_j.dumps({"reset_id": active, "binding_sha256": "x"}))
    assert ig.active_reset_id(str(tmp_path)) == active
    assert ig.retained_by_design(f"runtime/research_reset_receipts/{active}/binding.json", active)
    assert not ig.retained_by_design(f"runtime/research_reset_receipts/{old}/binding.json", active)
    assert not ig.retained_by_design(f"runtime/research_reset_receipts/{active}/binding.json", None)
    assert not ig.retained_by_design(f"runtime/research_reset_receipts/{active}/operation.json", active)
    assert not ig.retained_by_design(f"x/runtime/research_reset_receipts/{active}/binding.json", active)
    res = ig.scan_local_oldest(str(tmp_path), keep=())
    assert f"runtime/research_reset_receipts/{active}/binding.json" in res["kept"]
    assert f"runtime/research_reset_receipts/{old}/binding.json" in res["files"]


def test_active_reset_id_fails_closed_on_missing_or_malformed_pointer(tmp_path):
    assert ig.active_reset_id(str(tmp_path)) is None
    rr = tmp_path / "runtime" / "research_reset_receipts"
    rr.mkdir(parents=True)
    for bad in ('{"reset_id": "../../etc"}', '{"reset_id": "ABC"}', "not json", '{"reset_id": 5}'):
        (rr / "ACTIVE_RESET.json").write_text(bad)
        assert ig.active_reset_id(str(tmp_path)) is None


def test_by_prefix_is_uncapped_and_counts_bytes_only_for_pre_epoch(tmp_path):
    files = {f"runtime/v3/lifecycle_transfer_bundles/aa/t{i}/events.jsonl": 100.0 for i in range(50)}
    files["runtime/v3/lifecycle_transfer_bundles/aa/new/events.jsonl"] = 5000.0
    files["runtime/relay_outbox_retired.jsonl"] = 100.0
    sizes = {k: 10 for k in files}
    out = ig.by_prefix(files, sizes, epoch=1000.0)
    assert out["runtime/v3/lifecycle_transfer_bundles"] == {"files": 50, "bytes": 500}  # 50 > the old cap of 20
    assert out["runtime"]["files"] == 1  # a file directly under runtime/ is grouped under its directory
    assert ig.by_prefix(files, sizes)["runtime/v3/lifecycle_transfer_bundles"]["files"] == 51


def test_active_reset_pointer_is_exempt_only_when_an_active_id_is_valid():
    p = "runtime/research_reset_receipts/ACTIVE_RESET.json"
    assert ig.retained_by_design(p, "29f7a1efd216798db9309125")
    assert not ig.retained_by_design(p, None)
    assert not ig.retained_by_design("runtime/research_reset_receipts/_progress/x.json", "29f7a1efd216798db9309125")


def test_v3_growth_counts_files_per_top_dir_and_reports_disk(tmp_path):
    import os
    import time
    v3 = tmp_path / "runtime" / "v3"
    for sub, n in (("receipts", 3), ("signal_snapshots_v1", 2)):
        (v3 / sub).mkdir(parents=True)
        for i in range(n):
            (v3 / sub / f"{i}.json").write_text("x")
    old = v3 / "receipts" / "0.json"
    os.utime(old, (time.time() - 3 * 86400,) * 2)
    ig._V3_GROWTH_CACHE.update(at=0.0, doc=None)
    doc = ig.v3_growth(str(tmp_path), ttl=0)
    assert doc["dirs"]["receipts"] == {"files": 3, "bytes": 3, "files_24h": 2}
    assert doc["files_24h_total"] == 4 and doc["complete"] is True and doc["disk"]["free_bytes"] > 0


def test_deletion_sidecar_is_exempt_for_the_active_reset_only_and_alerts_amber_above_200mb(tmp_path):
    import json as _j
    active, old = "29f7a1efd216798db9309125", "aaaaaaaaaaaaaaaaaaaaaaaa"
    rr = tmp_path / "runtime" / "research_reset_receipts"
    for rid in (active, old):
        (rr / rid).mkdir(parents=True)
        (rr / rid / "deletion.json.retained.jsonl").write_text("x" * 10)
    (rr / "ACTIVE_RESET.json").write_text(_j.dumps({"reset_id": active, "binding_sha256": "x"}))
    n = "deletion.json.retained.jsonl"
    assert ig.retained_by_design(f"runtime/research_reset_receipts/{active}/{n}", active)
    assert not ig.retained_by_design(f"runtime/research_reset_receipts/{old}/{n}", active)
    assert not ig.retained_by_design(f"runtime/research_reset_receipts/{active}/{n}", None)
    assert not ig.retained_by_design(f"x/runtime/research_reset_receipts/{active}/{n}", active)
    res = ig.scan_local_oldest(str(tmp_path), keep=())
    assert f"runtime/research_reset_receipts/{active}/{n}" in res["kept"]       # exempt (not an offender)
    assert f"runtime/research_reset_receipts/{old}/{n}" in res["files"]         # an old reset's sidecar is still flagged
    s = ig.sidecar_size(str(tmp_path))
    assert s["status"] == "GREEN" and s["bytes"] == 10
    with open(rr / active / n, "r+b") as fh:                                    # sparse 201 MB file
        fh.truncate(201 * 1024 * 1024)
    s = ig.sidecar_size(str(tmp_path))
    assert s["status"] == "AMBER" and s["mb"] == 201.0
    row = {r["id"]: r for r in ig.evaluate({"wipe": {"sidecar": s}})["rows"]}["G21"]
    assert row["status"] == "AMBER"                                              # visible, but never RED: it cannot block a wipe
    assert ig.sidecar_size(str(tmp_path / "nowhere"))["status"] == "PENDING"
