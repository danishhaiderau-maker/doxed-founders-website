import json

from research.analyzer_integrity_reconciliation import (
    mark_analyzer_integrity_unchecked,
    reconcile_analyzer_integrity_with_policy_reports,
)
from research.generation_receipt import (
    build_generation_receipt,
    load_generation_receipt,
    write_generation_receipt,
)


def _valid_base(path):
    path.write_text(json.dumps({
        "schema": "analyzer_integrity_v1",
        "valid": True,
        "report_status": "VALID",
        "checks": [{"check": "base", "passed": True}],
    }), encoding="utf-8")


def test_failed_policy_cycle_marks_integrity_unchecked_never_valid(tmp_path):
    target = tmp_path / "analyzer_integrity_report.json"
    _valid_base(target)

    result = mark_analyzer_integrity_unchecked(target, "ValueError: POLICY_ID_SPEC_COLLISION:X")

    stored = json.loads(target.read_text(encoding="utf-8"))
    assert result["report_status"] == stored["report_status"] == "UNCHECKED"
    assert stored["valid"] is False
    assert "UNCHECKED" in stored["banner"]
    failed = {c["check"]: c for c in stored["failed_checks"]}
    assert failed["v3_policy_lifecycle_integrity"]["status"] == "UNCHECKED"
    assert "POLICY_ID_SPEC_COLLISION" in stored["policy_cycle_error"]


def test_successful_reconciliation_after_unchecked_clears_the_error(tmp_path):
    target = tmp_path / "analyzer_integrity_report.json"
    _valid_base(target)
    mark_analyzer_integrity_unchecked(target, "boom")

    result = reconcile_analyzer_integrity_with_policy_reports(target, [("best.json", {})])

    assert result["report_status"] == "VALID"
    assert "policy_cycle_error" not in result


def test_missing_integrity_receipt_is_still_unchecked(tmp_path):
    result = mark_analyzer_integrity_unchecked(tmp_path / "missing.json", "boom")
    assert result["report_status"] == "UNCHECKED" and result["valid"] is False


def _manifest(**required):
    return {"generation_id": "g1", "generation_revision": "abc", "required_report_status": required}


def test_receipt_is_red_when_a_required_study_errors():
    receipt = build_generation_receipt(
        _manifest(**{
            "best_policy_research_report.json": {
                "available_in_generation": False,
                "generation_error": "ValueError: POLICY_ID_SPEC_COLLISION:X",
            },
            "other.json": {"available_in_generation": True, "generation_error": None},
        }),
        integrity={"report_status": "UNCHECKED"},
    )
    assert receipt["level"] == "RED"
    assert receipt["complete"] is False
    assert receipt["failed_required_studies"] == ["best_policy_research_report.json"]
    best = next(s for s in receipt["studies"] if s["name"] == "best_policy_research_report.json")
    assert best["status"] == "ERROR" and best["error_class"] == "ValueError"
    assert any("UNCHECKED" in reason for reason in receipt["reasons"])


def test_receipt_green_only_when_everything_ran_and_integrity_valid():
    manifest = _manifest(**{"a.json": {"available_in_generation": True}})
    assert build_generation_receipt(manifest, integrity={"report_status": "VALID"})["level"] == "GREEN"
    assert build_generation_receipt(manifest, integrity={})["level"] == "RED"


def test_receipt_flags_truncated_replay_and_blocked_inputs():
    manifest = _manifest(**{"a.json": {"available_in_generation": True}})
    receipt = build_generation_receipt(
        manifest,
        integrity={"report_status": "VALID"},
        protection_replay_window={"truncated": True, "events_eligible": 2415, "events_selected": 150},
        input_blockers={"level": "AMBER", "items": [
            {"input": "exit_ladder", "status": "BLOCKED", "reason_code": "NO_ELIGIBLE"}]},
        optional_errors={"cross_world_evidence": None},
    )
    # Known input limits degrade the generation; RED is reserved for failed studies/integrity.
    assert receipt["level"] == "AMBER"
    assert receipt["protection_replay_window"]["truncated"] is True
    assert receipt["complete"] is True
    assert any("150/2415" in reason for reason in receipt["reasons"])
    assert any("exit_ladder=BLOCKED" in reason for reason in receipt["reasons"])


def test_receipt_names_mark_fallback_instead_of_truncation():
    manifest = _manifest(**{"a.json": {"available_in_generation": True}})
    receipt = build_generation_receipt(
        manifest,
        integrity={"report_status": "VALID"},
        protection_replay_window={"truncated": False, "events_eligible": 2800, "events_selected": 2800,
                                  "alert_level": "AMBER", "reason": "REPLAY_MARK_FALLBACK",
                                  "mark_fallback_events": 12},
    )
    assert receipt["level"] == "AMBER"
    assert not any("truncated" in reason for reason in receipt["reasons"])
    assert any("REPLAY_MARK_FALLBACK" in reason and "12 events" in reason for reason in receipt["reasons"])


def test_receipt_round_trips(tmp_path):
    receipt = build_generation_receipt(_manifest(), integrity={"report_status": "VALID"})
    write_generation_receipt(tmp_path, receipt)
    assert load_generation_receipt(tmp_path)["level"] == "GREEN"
    assert load_generation_receipt(tmp_path / "none") == {}
