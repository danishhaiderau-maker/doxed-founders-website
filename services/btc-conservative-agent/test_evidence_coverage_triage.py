import hashlib
import json

from research.evidence_coverage_triage import (
    UNRESOLVED_EPISODE_ID,
    build_evidence_coverage_triage_report,
    ledger_source_counts,
    verify_archive_receipts,
    verify_report_checksum,
)


def binding(episode, *, exact=True, schedule="EXACT", tapes=("a" * 64,)):
    return {
        "episode_id": episode,
        "opportunity_id": f"opportunity:{episode}",
        "event_id": f"decision:{episode}",
        "exact_binding_complete": exact,
        "schedule_id": f"schedule:{episode}" if schedule == "EXACT" else None,
        "schedule_status": schedule,
        "tape_ids": list(tapes),
        "unknown_reason_codes": [] if exact else ["UNKNOWN_TAPE_OBJECT_MISSING"],
    }


def result(episode, classification="NO_FILL", *, supported=True, origin=None):
    row = {"episode_id": episode, "classification": classification, "supported": supported}
    if origin:
        row["evidence_origin"] = origin
    return row


def test_goal_shaped_totals_are_deterministic_and_checksum_verifiable():
    bindings = [binding("episode-b"), binding("episode-a")]
    results = [result("episode-a", "FULL_FILL"), result("episode-b", "PARTIAL_FILL")]
    counts = {"opportunities": 2, "decisions": 2, "order_intents": 5,
              "executions": 1, "lifecycles": 8, "market_segments": 7}
    first = build_evidence_coverage_triage_report(
        {"bindings": bindings}, results, source_counts=counts,
    )
    second = build_evidence_coverage_triage_report(
        {"bindings": list(reversed(bindings))}, list(reversed(results)), source_counts=counts,
    )
    assert first == second
    assert verify_report_checksum(first)
    assert first["totals"] == {
        "opportunities": 2, "episodes": 2, "decisions": 2,
        "order_intents": 5, "executions": 1, "lifecycles": 8,
        "market_segments": 7, "complete_schedules": 2, "market_paths": 1,
        "terminal_outcomes": 2, "exact_episodes": 2,
        "reconstructed_episodes": 0, "unknown_episodes": 0,
    }


def test_explicit_reconstruction_is_separate_but_must_still_be_complete():
    report = build_evidence_coverage_triage_report(
        {"bindings": [binding("episode-r")]},
        [result("episode-r", "NO_FILL", origin="CHECKSUM_VERIFIED_RECONSTRUCTION")],
    )
    assert report["episodes"][0]["coverage_status"] == "RECONSTRUCTED"
    assert report["totals"]["reconstructed_episodes"] == 1

    incomplete = build_evidence_coverage_triage_report(
        {"bindings": [binding("episode-r", exact=False)]},
        [result("episode-r", "NO_FILL", origin="RECONSTRUCTED")],
    )
    assert incomplete["episodes"][0]["coverage_status"] == "UNKNOWN"


def test_missing_or_unsupported_terminal_evidence_never_becomes_no_fill():
    report = build_evidence_coverage_triage_report(
        {"bindings": [binding("episode-a"), binding("episode-b")]},
        [result("episode-b", "NO_FILL", supported=False)],
    )
    rows = {row["episode_id"]: row for row in report["episodes"]}
    assert rows["episode-a"]["coverage_status"] == "UNKNOWN"
    assert "UNKNOWN_TERMINAL_OUTCOME_MISSING" in rows["episode-a"]["unknown_reason_codes"]
    assert rows["episode-b"]["terminal_outcomes"] == ["UNKNOWN"]
    assert report["terminal_outcome_counts"] == {"UNKNOWN": 1}


def test_evaluator_unknown_reasons_are_included_and_source_counts_default_unknown():
    evaluated = result("episode-a", "UNKNOWN")
    evaluated["unknown_reason_codes"] = ["UNKNOWN_QUEUE_EVIDENCE_MISSING"]
    report = build_evidence_coverage_triage_report(
        {"bindings": [binding("episode-a")]}, [evaluated]
    )
    assert report["missing_evidence_reason_counts"]["UNKNOWN_QUEUE_EVIDENCE_MISSING"] == 1
    assert report["totals"]["opportunities"] == "UNKNOWN"
    assert report["totals"]["order_intents"] == "UNKNOWN"
    assert report["episodes"][0]["schedule_reference_count"] == 1


def test_schedule_counts_are_unique_and_never_inferred_from_schedule_status():
    first = binding("episode-a")
    duplicate = dict(first, event_id="decision:duplicate")
    missing = binding("episode-b", exact=False, schedule="UNKNOWN")
    report = build_evidence_coverage_triage_report(
        {"bindings": [first, duplicate, missing]},
        [result("episode-a"), result("episode-b")],
        source_counts={"order_intents": 9},
    )
    assert report["totals"]["order_intents"] == 9
    assert report["totals"]["complete_schedules"] == 1
    rows = {row["episode_id"]: row for row in report["episodes"]}
    assert rows["episode-b"]["schedule_reference_count"] == 0


def test_ledger_inputs_supply_authoritative_counts(tmp_path):
    ledgers = tmp_path / "v3" / "ledgers"
    ledgers.mkdir(parents=True)
    (ledgers / "opportunity.jsonl").write_text("{}\n{}\n", encoding="utf-8")
    (ledgers / "decision.jsonl").write_text("{}\n", encoding="utf-8")
    counts = ledger_source_counts(tmp_path / "v3")
    assert counts == {
        "opportunities": 2, "decisions": 1, "order_intents": "UNKNOWN",
        "executions": "UNKNOWN", "lifecycles": "UNKNOWN", "market_segments": "UNKNOWN",
    }

    (ledgers / "order_intent.jsonl").write_text("", encoding="utf-8")
    assert ledger_source_counts(tmp_path / "v3")["order_intents"] == 0


def test_named_orphan_is_forced_unknown_and_reported_separately():
    report = build_evidence_coverage_triage_report(
        {"bindings": [binding(UNRESOLVED_EPISODE_ID)]},
        [result(UNRESOLVED_EPISODE_ID, "FULL_FILL")],
    )
    assert report["unresolved_episode"]["present_in_inputs"] is True
    assert report["unresolved_episode"]["status"] == "UNKNOWN"
    assert report["totals"]["unknown_episodes"] == 1
    assert "UNKNOWN_UNRESOLVED_ORPHAN_LIFECYCLE" in report["episodes"][0]["unknown_reason_codes"]


def test_archive_receipts_verify_retained_bytes_and_dedupe_checksums(tmp_path):
    session = tmp_path / "session_001"
    payload = session / "payload" / "000001_decision.jsonl"
    payload.parent.mkdir(parents=True)
    payload.write_bytes(b'{"episode_id":"episode-a"}\n')
    digest = hashlib.sha256(payload.read_bytes()).hexdigest()
    receipt = {
        "integrity": {"verified": True}, "raw_payloads_retained": True,
        "source_inventory": [
            {"preserved_path": "payload/000001_decision.jsonl", "preserved_sha256": digest},
            {"preserved_path": "payload/000001_decision.jsonl", "preserved_sha256": digest},
        ],
    }
    (session / "archive_meta.json").write_text(json.dumps(receipt), encoding="utf-8")
    summary = verify_archive_receipts(tmp_path)
    assert summary["verified_session_count"] == 1
    assert summary["unverifiable_session_count"] == 0
    assert summary["invalid_session_count"] == 0
    assert summary["retained_file_count"] == 2
    assert summary["retained_unique_checksum_count"] == 1

    payload.write_bytes(b"changed")
    broken = verify_archive_receipts(tmp_path)
    assert broken["invalid_session_count"] == 1
    assert broken["sessions"][0]["error_codes"] == ["ARCHIVE_CHECKSUM_MISMATCH"]


def test_legacy_archive_without_retained_payload_is_unverifiable_not_invalid(tmp_path):
    session = tmp_path / "session_legacy"
    session.mkdir(parents=True)
    receipt = {
        "integrity": {"verified": False, "file_count": 0},
        "raw_payloads_retained": False,
        "source_inventory": [],
    }
    (session / "archive_meta.json").write_text(json.dumps(receipt), encoding="utf-8")

    summary = verify_archive_receipts(tmp_path)

    assert summary["verified_session_count"] == 0
    assert summary["unverifiable_session_count"] == 1
    assert summary["invalid_session_count"] == 0
    assert summary["sessions"][0]["verification_status"] == "UNVERIFIABLE"
