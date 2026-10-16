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


class _Route:
    """The real Flask app + real before_request guard, volume redirected to tmp, R2_RECEIPT_TOKEN set."""

    def __init__(self, tmp_path, monkeypatch):
        import bot
        import sys
        from pathlib import Path
        monkeypatch.delenv("BOT_ADMIN_TOKEN", raising=False)
        monkeypatch.setenv("R2_RECEIPT_TOKEN", "r2tok")
        monkeypatch.setattr(bot, "_data_sync_volume_root", lambda: tmp_path)
        monkeypatch.setattr(bot, "_DASHBOARD_BOOTSTRAP_COMPLETE", True)
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "r2_backup"))
        import r2_backup as rb
        self.bot, self.rb, self.tmp = bot, rb, tmp_path
        self.client = bot.app.test_client()
        self.now = __import__("time").time()

    def receipt(self, **kw):
        rb = self.rb
        keep = rb.load_keep(keep_prefixes="epoch-archive/")
        rest = {"checked": 100, "ok": 100, "bad": 0, "missing": 0, "passed": True}
        rep = {"missing": 0, "r2_objects": 150, "quarantined": 3, "outside_keep": 2}
        rec = rb.build_receipt(state={f"k{i}": {} for i in range(160)}, report=rep, restore=rest, keep=keep,
                               quarantine_pending=7, kind=kw.pop("kind", "sync"))
        rec["finished_ts"] = self.now - 30
        rec.update(kw)
        return {k: v for k, v in rec.items() if k in rb.RECEIPT_V2_FIELDS}

    def post(self, body, token="r2tok", headers=None):
        h = dict(headers or {})
        if token is not None:
            h["X-R2-Receipt-Token"] = token
        return self.client.post("/api/monitor/archive/receipt", json=body, headers=h)

    def stored(self):
        import json
        return json.loads((self.tmp / "archive_receipt.json").read_text())

    def log(self):
        import json
        p = self.tmp / "archive_receipts.jsonl"
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def test_route_keeps_every_v2_field_the_laptop_sends_and_pin_matches_the_laptops_hash(tmp_path, monkeypatch):
    r = _Route(tmp_path, monkeypatch)
    assert r.bot._r2_keep_pin_sha256() == r.rb.load_keep(keep_prefixes="epoch-archive/")["sha256"]
    sent = r.receipt()
    resp = r.post(sent)
    assert resp.status_code == 200 and resp.get_json()["kind"] == "sync"
    stored = r.stored()
    missing = [k for k in sent if k not in stored]
    assert not missing, f"Fly route drops laptop receipt field(s): {missing}"
    assert stored["quarantine_pending"] == 7 and stored["outside_keep"] == 2 and stored["quarantined"] == 3
    assert mc.archive_receipt(stored, now=NOW if False else r.now)["status"] == "AMBER"


def test_dedicated_token_only_admin_token_and_missing_config_are_refused(tmp_path, monkeypatch):
    r = _Route(tmp_path, monkeypatch)
    assert r.post(r.receipt(), token=None).status_code == 401
    assert r.post(r.receipt(), token="wrong").status_code == 401
    assert r.post(r.receipt(), token=None, headers={"X-Bot-Admin-Token": "anything"}).status_code == 401
    assert not (tmp_path / "archive_receipt.json").exists()
    monkeypatch.delenv("R2_RECEIPT_TOKEN")
    assert r.post(r.receipt(), token="x").status_code == 503


def test_validation_rejects_bad_receipts_and_logs_the_rejection(tmp_path, monkeypatch):
    r = _Route(tmp_path, monkeypatch)
    bad = [
        (dict(schema="r2_backup_receipt_v1"), "schema"),
        (dict(host=""), "host"),
        (dict(keep_manifest_sha256="00" * 32), "keep_manifest_sha256"),
        (dict(state_keys=10, r2_objects=150), "state_keys"),
        (dict(r2_objects=0), "r2_objects"),
        (dict(restore_test={"checked": 0, "bad": 0, "missing": 0}), "checked"),
        (dict(restore_test={"checked": 5000, "bad": 0, "missing": 0}), "checked"),
        (dict(restore_test={"checked": 200, "bad": 0, "missing": 0}), "exceeds"),
        (dict(finished_ts=r.now - 7 * 3600), "recent"),
        (dict(finished_ts=r.now + 3600), "recent"),
    ]
    for patch, needle in bad:
        resp = r.post(r.receipt(**patch))
        assert resp.status_code == 422, patch
        assert any(needle in p for p in resp.get_json()["problems"]), (patch, resp.get_json())
    assert not (tmp_path / "archive_receipt.json").exists()
    assert len(r.log()) == len(bad) and not any(e["accepted"] for e in r.log())


def test_stale_replay_is_rejected_and_every_accept_is_appended(tmp_path, monkeypatch):
    r = _Route(tmp_path, monkeypatch)
    first = r.receipt(finished_ts=r.now - 100)
    assert r.post(first).status_code == 200
    assert r.post(first).status_code == 422                                      # same ts: not newer
    older = r.receipt(finished_ts=r.now - 200)
    assert r.post(older).status_code == 422
    assert r.post(r.receipt(finished_ts=r.now - 10)).status_code == 200
    log = r.log()
    assert [e["accepted"] for e in log] == [True, False, False, True]            # append-only trail
    assert log[0]["receipt"]["finished_ts"] == first["finished_ts"]


def test_restore_test_receipt_never_overwrites_the_sync_receipts_amber_state(tmp_path, monkeypatch):
    r = _Route(tmp_path, monkeypatch)
    assert r.post(r.receipt(finished_ts=r.now - 100)).status_code == 200
    before = r.stored()
    assert mc.archive_receipt(before, now=r.now)["status"] == "AMBER"
    rt = r.receipt(kind="restore_test", finished_ts=r.now - 10)
    for k in ("state_keys", "r2_objects", "quarantine_pending", "outside_keep", "missing"):
        rt.pop(k, None)                                  # restore-test-only receipts carry no sync counts
    resp = r.post(rt)
    assert resp.status_code == 200 and resp.get_json()["kind"] == "restore_test"
    after = r.stored()
    for k in ("state_keys", "r2_objects", "quarantine_pending", "outside_keep", "missing", "quarantined",
              "finished_ts", "ok"):
        assert after[k] == before[k], k
    assert after["restore_test_finished_ts"] == rt["finished_ts"]
    assert mc.archive_receipt(after, now=r.now)["status"] == "AMBER"             # still AMBER, not GREEN/overwritten
    failing = r.receipt(kind="restore_test", finished_ts=r.now - 5, restore_test={"checked": 20, "ok": 0, "bad": 20, "missing": 0, "passed": False})
    assert r.post(failing).status_code == 200
    assert mc.archive_receipt(r.stored(), now=r.now)["status"] == "RED"          # a failed restore test is RED
