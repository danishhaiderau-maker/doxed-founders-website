import json

from collector_v22_schema import RESEARCH_EVENTS_FILE
from microstructure_tape import FILE_NAME, build_bucket, window_reference
from research.policy_cycle_snapshot import build_policy_cycle_reports
from test_policy_cycle_snapshot import _append, _event


def test_policy_cycle_pins_tape_and_keeps_conservative_cohort_separate(tmp_path):
    complete = _event(1, "ACCEPTED_FILLED")
    complete["microstructure_window"] = window_reference(
        complete["envelope"]["signal_ts"], complete["envelope"]["signal_ts"] + 1
    )
    incomplete = _event(2, "ACCEPTED_UNFILLED")
    incomplete["microstructure_window"] = window_reference(
        incomplete["envelope"]["signal_ts"], incomplete["envelope"]["signal_ts"] + 1
    )
    legacy = _event(3, "REJECTED")
    malformed = _event(4, "REJECTED")
    malformed["microstructure_window"] = {"schema": "wrong"}
    for event in (complete, incomplete, legacy, malformed):
        _append(tmp_path / RESEARCH_EVENTS_FILE, event)

    start = int(complete["envelope"]["signal_ts"])
    bucket = build_bucket(
        bucket_ts=start, bid=100, ask=101, bid_qty=2, ask_qty=3, last=100.5,
        source_ts=start + 0.5,
    )
    (tmp_path / FILE_NAME).write_text(json.dumps(bucket) + "\n", encoding="utf-8")

    reports = build_policy_cycle_reports(tmp_path, tmp_path)
    evidence = reports["microstructure"]
    assert evidence["referenced_events"] == 3
    assert evidence["complete_windows"] == 1
    assert evidence["incomplete_windows"] == 2
    assert evidence["unreferenced_events"] == 1
    assert evidence["conservative_evidence_event_ids"] == [complete["event_id"]]
    assert evidence["tape_snapshot"]["row_count"] == 1
    assert evidence["tape_snapshot"]["first_bucket_ts"] == start
    assert evidence["tape_snapshot"]["last_bucket_ts"] == start
    assert len(evidence["tape_snapshot"]["snapshot_sha256"]) == 64
    assert reports["candidate"]["conservative_microstructure_evidence"] == evidence
    assert reports["best"]["conservative_microstructure_evidence"] == evidence
    # Presence of one complete tape window does not qualify existing gates.
    assert reports["candidate"]["qualification_gates"]["conservative_execution"] is False
    assert reports["candidate"]["independent_oos_qualified"] is False
    assert reports["best"]["status"] == "NO QUALIFIED POLICY"


def test_policy_cycle_tape_snapshot_is_immutable_during_builders(tmp_path):
    event = _event(1)
    event["microstructure_window"] = window_reference(
        event["envelope"]["signal_ts"], event["envelope"]["signal_ts"] + 1
    )
    _append(tmp_path / RESEARCH_EVENTS_FILE, event)
    tape = tmp_path / FILE_NAME
    start = int(event["envelope"]["signal_ts"])

    def append_after_candidate():
        row = build_bucket(
            bucket_ts=start, bid=100, ask=101, bid_qty=1, ask_qty=1, last=100,
            source_ts=start + 0.5,
        )
        _append(tape, row)

    reports = build_policy_cycle_reports(
        tmp_path, tmp_path, between_builders_hook=append_after_candidate
    )
    evidence = reports["microstructure"]
    assert sum(1 for _ in tape.open(encoding="utf-8")) == 1
    assert evidence["tape_snapshot"]["row_count"] == 0
    assert evidence["complete_windows"] == 0
    assert evidence["incomplete_windows"] == 1
    assert reports["candidate"]["conservative_microstructure_evidence"] == reports["best"]["conservative_microstructure_evidence"]
