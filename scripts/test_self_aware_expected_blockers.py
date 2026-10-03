"""Declared expected blockers: registry validation, AMBER while pending, RED once overdue, finding annotation."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from self_aware import diagnose, expected_blockers as eb  # noqa: E402
from self_aware.facts import parse_ts  # noqa: E402

BEFORE = parse_ts("2026-10-04T00:00:00Z")
AFTER = parse_ts("2026-10-05T00:00:00Z")
BLOCKER = {"id": "T-DELTA-CHANGE", "ledger": "gap 29", "reason": "dead input", "fix": "#317",
           "deploy": "#351 step 3", "eta": "2026-10-04T15:00:00Z",
           "match": {"finding": "data.dead_fields", "observed_contains": "ai_calls.context.delta_change"}}


def _dead(observed="ai_calls.context.delta_change=DEAD_ZERO (0.0)", severity="AMBER"):
    return diagnose.Finding("data.dead_fields", "dead", "data", severity, observed, "alive")


def test_shipped_registry_is_valid_and_names_a_fix_and_eta_for_every_blocker():
    blockers = eb.load()
    assert blockers, "registry must not be empty while post-freeze fixes are pending"
    for b in blockers:
        assert b["fix"].startswith("https://github.com/") and parse_ts(b["eta"])
    assert any((b.get("match") or {}).get("finding") == "data.dead_fields" for b in blockers)


def test_registry_rejects_missing_fields_duplicates_and_bad_eta(tmp_path):
    def write(rows):
        p = tmp_path / "b.json"
        p.write_text(json.dumps({"blockers": rows}), encoding="utf-8")
        return p

    with pytest.raises(ValueError, match="missing"):
        eb.load(write([{"id": "x"}]))
    with pytest.raises(ValueError, match="twice"):
        eb.load(write([BLOCKER, BLOCKER]))
    with pytest.raises(ValueError, match="timestamp"):
        eb.load(write([{**BLOCKER, "eta": "soon"}]))


def test_pending_blocker_is_amber_and_annotates_matching_finding_without_downgrading():
    f = _dead()
    res = eb.assess([f], BEFORE, [BLOCKER])
    assert res["severity"] == "AMBER" and "T-DELTA-CHANGE" in res["observed"]
    assert res["blockers"][0]["annotates"] == ["data.dead_fields"] and not res["blockers"][0]["overdue"]
    assert f.severity == "AMBER" and f.evidence["expected_blockers"][0]["fix"] == "#317"


def test_overdue_blocker_turns_red_and_non_matching_findings_are_untouched():
    other = _dead(observed="adaptive_entry_decisions.ai_feature.win_prob=DEAD_ZERO (0)")
    res = eb.assess([other], AFTER, [BLOCKER])
    assert res["severity"] == "RED" and "past ETA" in res["observed"]
    assert res["blockers"][0]["annotates"] == [] and "expected_blockers" not in other.evidence
    assert eb.assess([], AFTER, [])["severity"] == "GREEN"


def test_green_finding_is_not_annotated():
    f = _dead(severity="GREEN")
    eb.assess([f], BEFORE, [BLOCKER])
    assert "expected_blockers" not in f.evidence


def test_diagnose_summary_finding_alarms_only_when_overdue():
    pending = diagnose.check_expected_blockers({"now": BEFORE}, [_dead()])
    assert pending.id == "selfaware.expected_blockers" and pending.severity == "AMBER" and not pending.emit_alarm
    overdue = diagnose.check_expected_blockers({"now": parse_ts("2030-01-01T00:00:00Z")}, [])
    assert overdue.severity == "RED" and overdue.emit_alarm
