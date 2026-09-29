import copy
import json

from research.policy_evidence_coverage import (
    build_policy_evidence_coverage_report,
    episode_coverage,
)


SHA_A = "a" * 64
SHA_B = "b" * 64


def complete_row():
    return {
        "epoch_id": "epoch-1",
        "event_id": "event-1",
        "episode_id": "episode-1",
        "opportunity_id": "opportunity:episode-1",
        "policy_signature": "policy-1",
        "tape_ids": [f"sha256:{SHA_B}", SHA_A],
        "schedule_sha256": SHA_B,
        "required_entry_horizons_complete": True,
        "required_post_exit_horizons_complete": True,
    }


def test_exact_binding_is_coverage_only_and_deterministic():
    row = complete_row()
    episode = episode_coverage(row)
    assert episode["coverage_status"] == "EXACTLY_BOUND"
    assert episode["unknown_reason_codes"] == []
    assert episode["conservative_outcome"] is None
    first = build_policy_evidence_coverage_report([row, {**row, "event_id": "event-2"}])
    second = build_policy_evidence_coverage_report(list(reversed([
        row, {**row, "event_id": "event-2"}
    ])))
    assert first == second
    assert first["outcome_evaluation_performed"] is False
    assert first["timestamp_join_performed"] is False


def test_missing_identity_and_horizons_fail_closed_to_unknown():
    row = complete_row()
    for key in ("opportunity_id", "schedule_sha256", "tape_ids"):
        row.pop(key)
    row["required_entry_horizons_complete"] = False
    row["required_post_exit_horizons_complete"] = None
    episode = episode_coverage(row)
    assert episode["coverage_status"] == "UNKNOWN_UNVERIFIABLE"
    assert episode["unknown_reason_codes"] == [
        "UNKNOWN_OPPORTUNITY_ID_MISSING",
        "UNKNOWN_REQUIRED_ENTRY_HORIZONS_INCOMPLETE",
        "UNKNOWN_REQUIRED_POST_EXIT_HORIZONS_INCOMPLETE",
        "UNKNOWN_SCHEDULE_SHA256_MISSING",
        "UNKNOWN_TAPE_IDS_MISSING",
    ]


def test_non_content_addressed_receipts_are_not_exact_bindings():
    row = complete_row()
    row["tape_ids"] = ["tape-by-timestamp"]
    row["schedule_sha256"] = "schedule-1"
    report = build_policy_evidence_coverage_report([row])
    assert report["exactly_bound_episode_count"] == 0
    assert report["unknown_reason_counts"] == {
        "UNKNOWN_SCHEDULE_SHA256_INVALID": 1,
        "UNKNOWN_TAPE_IDS_NOT_CONTENT_ADDRESSED": 1,
    }
    # The builder does not mutate source evidence while normalizing its output.
    assert row["tape_ids"] == ["tape-by-timestamp"]


def test_nested_receipt_identity_is_supported_without_inference():
    row = complete_row()
    identity = {key: row.pop(key) for key in (
        "epoch_id", "event_id", "episode_id", "opportunity_id",
        "policy_signature", "tape_ids", "schedule_sha256",
    )}
    identity["candidate_policy_signature"] = identity.pop("policy_signature")
    row["receipt_identity"] = identity
    assert episode_coverage(copy.deepcopy(row))["exact_binding_complete"] is True
