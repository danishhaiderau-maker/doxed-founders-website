from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import clean_epoch_certify as cc  # noqa: E402

de = cc.de
EPOCH = "ce-20261004-v31-clean"
NOW = 1_790_930_000.0


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def _inputs(age=3 * 3600, sev="GREEN", history=(), stamped=10):
    manifest = de.new_manifest(EPOCH, started_at_ts=NOW - age)
    health = {"generated_at": _iso(NOW - 60), "findings": [
        {"id": "contract.trades", "severity": sev}, {"id": "analyzer.sections", "severity": "GREEN"},
        {"id": "data.completeness", "severity": "GREEN"}, {"id": "data.compat_mixed", "severity": "GREEN"},
        {"id": "fly.relay", "severity": "AMBER"}]}
    compat = {"epoch": {"epoch_id": EPOCH}, "streams": [{"classes": {de.CURRENT: stamped}}]}
    return manifest, health, list(history), compat


def _cert(*a, **k):
    manifest, health, history, compat = _inputs(*a, **k)
    return de.certification_doc(manifest, checks=cc.evaluate(manifest, health, history, compat, NOW), now=NOW)


def test_green_window_certifies():
    cert = _cert()
    assert cert["status"] == "CERTIFIED" and cert["failing"] == []


def test_young_epoch_is_pending_and_never_certified():
    assert _cert(age=3600)["status"] == "PENDING"


def test_non_green_contract_or_red_in_window_rejects():
    assert "required.green_now" in _cert(sev="SKIP")["failing"]
    red_event = {"id": "contract.trades", "to": "RED", "at": _iso(NOW - 1800)}
    assert _cert(history=[red_event])["failing"] == ["required.no_red_in_window"]
    before = {"id": "contract.trades", "to": "RED", "at": _iso(NOW - 4 * 3600)}
    assert _cert(history=[before])["status"] == "CERTIFIED"


def test_unstamped_collector_rejects():
    assert _cert(stamped=0)["failing"] == ["compat.stamped_rows"]


def _declare(tmp_path, manifest, start, now, confirm=None, reason="laptop chain down during reset"):
    return cc.declare_window(manifest, start, reason, confirm, now, windows=tmp_path / "w.jsonl",
                             wall=tmp_path / "WALL.md")


def test_window_plan_writes_nothing_and_confirm_records_wall(tmp_path):
    manifest = _inputs()[0]
    code, plan = _declare(tmp_path, manifest, "now", NOW)
    assert code == 0 and plan["confirm_token"] == cc.window_token(EPOCH, float(int(NOW)))
    assert not (tmp_path / "w.jsonl").exists() and not (tmp_path / "WALL.md").exists()
    code, _ = _declare(tmp_path, manifest, "now", NOW + 5, confirm=plan["confirm_token"])
    assert code == 2 and not (tmp_path / "w.jsonl").exists()
    code, _ = _declare(tmp_path, manifest, plan["window_start_utc"], NOW + 5, confirm="CERT-WINDOW:x:y")
    assert code == 2 and not (tmp_path / "w.jsonl").exists()
    code, rec = _declare(tmp_path, manifest, plan["window_start_utc"], NOW + 5, confirm=plan["confirm_token"])
    assert code == 0 and rec["window_start_ts"] == float(int(NOW))
    assert cc.declared_window(tmp_path / "w.jsonl", EPOCH) == rec
    assert cc.declared_window(tmp_path / "w.jsonl", "ce-other") is None
    wall = (tmp_path / "WALL.md").read_text(encoding="utf-8")
    assert "CERT-WINDOW" in wall and rec["window_start_utc"] in wall and "no required RED during the window" in wall


def test_window_cannot_be_backdated_precede_epoch_or_lack_reason(tmp_path):
    manifest = _inputs(age=3 * 3600)[0]
    assert _declare(tmp_path, manifest, _iso(NOW - 600), NOW)[0] == 2
    assert _declare(tmp_path, manifest, _iso(NOW - 4 * 3600), NOW)[0] == 2
    assert _declare(tmp_path, manifest, _iso(NOW + 25 * 3600), NOW)[0] == 2
    assert _declare(tmp_path, manifest, "now", NOW, reason=" ")[0] == 2
    assert _declare(tmp_path, manifest, "yesterday", NOW)[0] == 2


def test_declared_window_resets_red_history_and_age_but_keeps_every_gate():
    manifest, health, _, compat = _inputs(age=6 * 3600)
    window = {"window_start_ts": NOW - 2.5 * 3600}
    old_red = {"id": "contract.trades", "to": "RED", "at": _iso(NOW - 4 * 3600)}
    new_red = {"id": "contract.trades", "to": "RED", "at": _iso(NOW - 1800)}

    def cert(history, win=window, h=health, now=NOW):
        return de.certification_doc(manifest, checks=cc.evaluate(manifest, h, history, compat, now, win),
                                    now=now, window=win)

    assert cert([old_red], win=None)["failing"] == ["required.no_red_in_window"]
    assert cert([old_red])["status"] == "CERTIFIED"
    assert cert([new_red])["failing"] == ["required.no_red_in_window"]
    young = {"window_start_ts": NOW - 3600}
    assert cert([old_red], win=young)["status"] == "PENDING"
    amber = {**health, "findings": [*health["findings"], {"id": "data.dead_fields", "severity": "AMBER"}]}
    assert cert([], h=amber)["failing"] == ["required.green_now"]
    red_any = {**health, "findings": [*health["findings"], {"id": "fly.relay2", "severity": "RED"}]}
    assert cert([], h=red_any)["failing"] == ["no_red_now"]
