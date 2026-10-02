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
