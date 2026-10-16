"""HM (c): archive-receipt judges object/missing/quarantine counts and the restore test."""
import monitor_checks as mc

NOW = 1_792_130_000.0


def _r(**kw):
    base = {"schema": "r2_backup_receipt_v2", "finished_ts": NOW - 60, "errors": 0, "ok": True, "state_keys": 100,
            "r2_objects": 100, "missing": 0, "quarantined": 0, "quarantine_pending": 0,
            "restore_test": {"checked": 100, "ok": 100, "bad": 0, "missing": 0, "passed": True}}
    base.update(kw)
    return base


def test_clean_v2_receipt_is_green_and_echoes_counts():
    out = mc.archive_receipt(_r(), now=NOW)
    assert out["status"] == "GREEN" and out["receipt"]["r2_objects"] == 100 and out["receipt"]["missing"] == 0


def test_the_13_oct_receipt_shape_is_red():
    out = mc.archive_receipt(_r(state_keys=426780, r2_objects=5935, missing=420846, ok=True,
                                restore_test={"checked": 23, "ok": 2, "bad": 0, "missing": 21}), now=NOW)
    assert out["status"] == "RED" and out["alert"] is True
    text = " ".join(out["reasons"])
    assert "420846" in text and "restore test failed" in text


def test_legacy_restore_bad_is_red_and_pending_quarantine_is_amber():
    legacy = {"finished_ts": NOW - 60, "errors": 0, "files": 5, "restore_test": {"checked": 23, "ok": 2, "bad": 21}}
    assert mc.archive_receipt(legacy, now=NOW)["status"] == "RED"
    amber = mc.archive_receipt(_r(quarantine_pending=5930), now=NOW)
    assert amber["status"] == "AMBER" and "quarantine" in amber["reasons"][0]


def test_zero_sampled_objects_is_not_proof_and_v2_without_restore_test_is_red():
    assert mc.archive_receipt(_r(restore_test={"checked": 0, "ok": 0, "bad": 0, "missing": 0, "passed": False}),
                              now=NOW)["status"] == "RED"
    r = _r(); r.pop("restore_test")
    assert mc.archive_receipt(r, now=NOW)["status"] == "RED"
    assert mc.archive_receipt(_r(restore_test={"checked": 0, "bad": 0, "missing": 0, "passed": True}),
                              now=NOW)["status"] == "GREEN"      # explicit allow-empty pass


def test_post_route_keeps_the_v2_fields():
    import bot
    src = open(bot.__file__, encoding="utf-8").read()
    for field in ('"state_keys"', '"r2_objects"', '"missing"', '"quarantined"', '"quarantine_pending"',
                  '"keep_manifest_sha256"'):
        assert field in src.split("def monitor_archive_receipt_post")[1].split("return jsonify({\"ok\": True})")[0]
