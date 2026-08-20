import json
from pathlib import Path

from collector_v22_schema import RESEARCH_EVENTS_FILE
from research import research_dashboard as dashboard
from research.policy_cycle_snapshot import build_policy_cycle_reports


SIGNAL_TS = 1_800_000_000.0


def _event(index, outcome="ACCEPTED_UNFILLED"):
    path = [
        [(SIGNAL_TS + index * 10_000 + minute * 60) * 1000, 100, 101, 99, 100, 1]
        for minute in range(60)
    ]
    signal_ts = SIGNAL_TS + index * 10_000
    return {
        "event_id": f"event-{index}",
        "event_episode_id": f"episode-{index}",
        "epoch_id": "epoch-cycle",
        "policy_epoch_id": "policy-epoch-cycle",
        "policy_signature": "policy-signature-cycle",
        "collector_version": "collector_v2.2",
        "primary_outcome": outcome,
        "observation_status": "PATH_COMPLETE",
        "envelope": {
            "event_id": f"event-{index}",
            "event_episode_id": f"episode-{index}",
            "epoch_id": "epoch-cycle",
            "policy_epoch_id": "policy-epoch-cycle",
            "policy_signature": "policy-signature-cycle",
            "signal_ts": signal_ts,
            "primary_outcome": outcome,
            "direction": "LONG",
        },
        "canonical_tape": {"path_1m": path},
        "entry_children": [],
    }


def _append(path: Path, row):
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")


def test_policy_builders_share_pinned_snapshot_while_mirror_grows(tmp_path, monkeypatch):
    event_path = tmp_path / RESEARCH_EVENTS_FILE
    for index, outcome in enumerate(("ACCEPTED_FILLED", "ACCEPTED_UNFILLED", "REJECTED")):
        _append(event_path, _event(index, outcome))

    reports = build_policy_cycle_reports(
        tmp_path,
        tmp_path,
        between_builders_hook=lambda: _append(event_path, _event(3)),
    )
    candidate = reports["candidate"]
    best = reports["best"]

    assert sum(1 for _ in event_path.open(encoding="utf-8")) == 4
    assert reports["cycle_snapshot"]["row_count"] == 3
    assert candidate["cycle_snapshot"] == best["cycle_snapshot"] == reports["cycle_snapshot"]
    assert candidate["evidence"]["current_events"] == best["evidence"]["current_epoch_events"] == 3
    assert candidate["evidence"]["eligible_events"] == best["evidence"]["replay_eligible_events"] == 3
    assert candidate["evidence"]["independent_episodes"] == best["evidence"]["independent_episode_count"] == 3
    assert "POLICY_CYCLE_SNAPSHOT_MISMATCH" not in best["blockers"]

    monkeypatch.setattr(dashboard, "_data_file_candidates", lambda name: [tmp_path / name])
    api_payload = dashboard._best_policy_research_payload()
    assert api_payload["cycle_snapshot"]["snapshot_id"] == reports["cycle_snapshot"]["snapshot_id"]
    assert api_payload["evidence"]["current_epoch_events"] == 3
    assert api_payload["live_observed_evidence"]["current_epoch_events"] == 4


def test_analyzer_uses_single_policy_cycle_orchestrator():
    source = Path(__file__).with_name("analyzer_research_engine_v62.py").read_text(encoding="utf-8")
    start = source.index("def write_report_manifest")
    manifest_body = source[start:start + 12_000]
    assert "build_policy_cycle_reports" in manifest_body
    assert "build_policy_candidate_oos_report(" not in manifest_body
    assert "build_best_policy_research_report(" not in manifest_body
