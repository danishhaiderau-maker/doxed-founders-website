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


def _laptop_receipt(**kw):
    """A receipt built by the real laptop script (scripts/r2_backup/r2_backup.py)."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "r2_backup"))
    import r2_backup as rb
    rest = {"checked": 100, "ok": 100, "bad": 0, "missing": 0, "passed": True}
    rep = {"missing": 0, "r2_objects": 100, "quarantined": 0, "outside_keep": 0}
    rep.update(kw.pop("report", {}))
    rec = rb.build_receipt(state={f"k{i}": {} for i in range(100)}, report=rep, restore=rest,
                           keep={"prefixes": ["epoch-archive/"], "sha256": "k"}, **kw)
    rec["finished_ts"] = NOW - 60
    return rec


def test_pending_quarantine_is_amber_not_red_and_a_clean_bucket_is_green():
    clean = _laptop_receipt()
    assert clean["ok"] is True and mc.archive_receipt(clean, now=NOW)["status"] == "GREEN"
    pending = _laptop_receipt(quarantine_pending=5930)
    assert pending["ok"] is True and pending["quarantine_pending"] == 5930      # HM #678 B1
    out = mc.archive_receipt(pending, now=NOW)
    assert out["status"] == "AMBER" and "quarantine" in out["reasons"][0]


def test_outside_keep_objects_are_amber():
    out = mc.archive_receipt(_laptop_receipt(report={"outside_keep": 12}), now=NOW)
    assert out["status"] == "AMBER" and any("outside the keep manifest" in r for r in out["reasons"])
    assert mc.archive_receipt(_laptop_receipt(report={"outside_keep": 0}), now=NOW)["status"] == "GREEN"
    # a real failure still wins over AMBER
    assert mc.archive_receipt(_laptop_receipt(report={"outside_keep": 12, "missing": 3}), now=NOW)["status"] == "RED"


def test_post_route_keeps_every_v2_field_the_laptop_sends(tmp_path, monkeypatch):
    import bot
    monkeypatch.setattr(bot, "_archive_receipt_path", lambda: tmp_path / "archive-receipt.json")
    sent = {k: v for k, v in _laptop_receipt(quarantine_pending=7, report={"outside_keep": 2, "quarantined": 3}).items()
            if k in __import__("sys").modules["r2_backup"].RECEIPT_V2_FIELDS}
    with bot.app.test_request_context("/api/monitor/archive/receipt", method="POST", json=sent):
        resp = bot.monitor_archive_receipt_post()
    assert resp.get_json() == {"ok": True}
    stored = __import__("json").loads((tmp_path / "archive-receipt.json").read_text())
    missing = [k for k in sent if k not in stored]
    assert not missing, f"Fly route drops laptop receipt field(s): {missing}"
    assert stored["quarantine_pending"] == 7 and stored["outside_keep"] == 2 and stored["quarantined"] == 3
