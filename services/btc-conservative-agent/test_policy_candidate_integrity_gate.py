from copy import deepcopy

from replay_eligibility import validate_replay_eligibility
from research.policy_candidate_oos import (
    _structured_integrity_cohorts,
    build_policy_candidate_oos_report,
)
from test_policy_cycle_snapshot import _event


def _ranked_event():
    row = _event(1)
    row["ranking_eligible"] = True
    row["negative_evidence"] = False
    row["replay_eligibility"] = validate_replay_eligibility(row)
    row["replay_outcomes"] = []
    return row


def _explicit_negative_event():
    row = _event(2)
    row["canonical_tape"]["path_1m"] = row["canonical_tape"]["path_1m"][:2]
    row["observation_status"] = "INSUFFICIENT_PATH"
    row["ranking_eligible"] = False
    row["negative_evidence"] = True
    row["replay_eligibility"] = validate_replay_eligibility(row)
    row["replay_outcomes"] = []
    return row


def test_explicit_terminal_negative_evidence_is_quarantined_without_failing_integrity(tmp_path):
    rows = [_ranked_event(), _explicit_negative_event()]
    result = _structured_integrity_cohorts(rows)
    assert result["passed"] is True
    assert result["ranked_count"] == 1
    assert result["excluded_count"] == 1
    assert result["defect_count"] == 0
    assert result["exclusion_reasons"]["ENTRY_WINDOW_INCOMPLETE"] == 1
    report = build_policy_candidate_oos_report(tmp_path, tmp_path, events=rows)
    assert report["qualification_gates"]["no_data_integrity_defects"] is True
    assert report["data_integrity"]["excluded_terminal_negative_events"] == 1


def test_independently_valid_legacy_row_without_ranking_flag_is_ranked_and_passes():
    row = _ranked_event()
    del row["ranking_eligible"]
    result = _structured_integrity_cohorts([row])
    assert result["passed"] is True
    assert result["ranked_count"] == 1
    assert result["excluded_count"] == 0


def test_malformed_or_silent_exclusion_fails_integrity(tmp_path):
    row = _explicit_negative_event()
    del row["replay_eligibility"]
    result = _structured_integrity_cohorts([row])
    assert result["passed"] is False
    assert result["excluded_count"] == 0
    assert result["defects"][0]["reason"] == "EXCLUSION_NOT_EXPLICIT_TERMINAL_NEGATIVE_EVIDENCE"
    report = build_policy_candidate_oos_report(tmp_path, tmp_path, events=[row])
    assert report["qualification_gates"]["no_data_integrity_defects"] is False


def test_ranking_eligible_row_that_independently_fails_replay_fails_integrity(tmp_path):
    row = deepcopy(_ranked_event())
    row["canonical_tape"]["path_1m"] = row["canonical_tape"]["path_1m"][:2]
    result = _structured_integrity_cohorts([row])
    assert result["passed"] is False
    assert result["ranked_count"] == 0
    assert result["defects"][0]["reason"] == "RANKED_ROW_REPLAY_INVALID"
    report = build_policy_candidate_oos_report(tmp_path, tmp_path, events=[row])
    assert report["qualification_gates"]["no_data_integrity_defects"] is False
