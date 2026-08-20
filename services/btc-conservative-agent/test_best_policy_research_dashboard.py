import json
from pathlib import Path

from research import research_dashboard as dashboard
from research.best_policy_research import build_best_policy_research_report


def _event(event_id, outcome, episode_id, *, complete=True):
    signal_ts = 1_800_000_000.0
    candle_count = 60 if complete else 10
    path = [
        [(signal_ts + minute * 60) * 1000, 100, 101, 99, 100, 1]
        for minute in range(candle_count)
    ]
    return {
        "event_id": event_id,
        "epoch_id": "epoch-clean",
        "event_episode_id": episode_id,
        "collector_version": "collector_v2.2",
        "primary_outcome": outcome,
        "observation_status": "PATH_COMPLETE",
        "envelope": {"signal_ts": signal_ts, "epoch_id": "epoch-clean"},
        "canonical_tape": {"path_1m": path},
        "entry_children": [],
    }


def _write_fixture(tmp_path: Path, events, report):
    (tmp_path / dashboard.RESEARCH_EVENTS_FILE).write_text(
        "".join(json.dumps(row) + "\n" for row in events), encoding="utf-8"
    )
    (tmp_path / dashboard.BEST_POLICY_RESEARCH_REPORT_FILE).write_text(
        json.dumps(report), encoding="utf-8"
    )
    (tmp_path / dashboard.REPORT_MANIFEST_FILE).write_text(
        json.dumps({"generated_at": "2026-08-20T00:00:00+00:00"}), encoding="utf-8"
    )


def test_best_policy_is_hidden_until_current_epoch_oos_is_qualified(tmp_path, monkeypatch):
    events = [
        _event("filled", "ACCEPTED_FILLED", "episode-1"),
        _event("unfilled", "ACCEPTED_UNFILLED", "episode-2"),
        _event("rejected", "REJECTED", "episode-3"),
    ]
    _write_fixture(tmp_path, events, {
        "epoch_id": "epoch-clean",
        "status": "PROVISIONAL",
        "candidate": {"policy_id": "tempting-but-unqualified"},
        "evidence": {"qualified_oos_episodes": 9},
        "qualification_gates": {"chronological_oos": False},
    })
    monkeypatch.setattr(dashboard, "_data_file_candidates", lambda name: [tmp_path / name])

    payload = dashboard._best_policy_research_payload()

    assert payload["status"] == "NO QUALIFIED POLICY"
    assert payload["current_candidate"] is None
    assert payload["live_policy_change_allowed"] is False
    assert "INDEPENDENT_OOS_EVIDENCE_MISSING" in payload["blockers"]
    assert payload["evidence"]["completed_paths"] == 3
    assert payload["evidence"]["independent_episode_count"] == 3
    assert payload["evidence"]["outcome_coverage"] == {
        "ACCEPTED_FILLED": 1, "ACCEPTED_UNFILLED": 1, "REJECTED": 1,
    }


def test_best_policy_requires_complete_paths_and_exact_epoch(tmp_path, monkeypatch):
    events = [
        _event("filled", "ACCEPTED_FILLED", "episode-1", complete=False),
        _event("unfilled", "ACCEPTED_UNFILLED", "episode-2"),
        _event("rejected", "REJECTED", "episode-3"),
    ]
    _write_fixture(tmp_path, events, {
        "epoch_id": "epoch-old",
        "status": "QUALIFIED",
        "candidate": {"policy_id": "must-not-leak"},
        "independent_oos_qualified": True,
        "qualification_gates": {"chronological_oos": True, "costed_expectancy": True},
    })
    monkeypatch.setattr(dashboard, "_data_file_candidates", lambda name: [tmp_path / name])

    payload = dashboard._best_policy_research_payload()

    assert payload["status"] == "NO QUALIFIED POLICY"
    assert payload["current_candidate"] is None
    assert payload["evidence"]["replay_ineligible_events"] == 1
    assert "REPLAY_INELIGIBLE_PATHS_PRESENT" in payload["blockers"]
    assert "BEST_POLICY_REPORT_EPOCH_MISMATCH" in payload["blockers"]


def test_exact_epoch_qualified_oos_report_can_show_candidate(tmp_path, monkeypatch):
    events = [
        _event("filled", "ACCEPTED_FILLED", "episode-1"),
        _event("unfilled", "ACCEPTED_UNFILLED", "episode-2"),
        _event("rejected", "REJECTED", "episode-3"),
    ]
    _write_fixture(tmp_path, events, {
        "epoch_id": "epoch-clean",
        "status": "QUALIFIED",
        "candidate": {"policy_id": "policy-oos-1"},
        "independent_oos_qualified": True,
        "evidence": {"qualified_oos_episodes": 3},
        "qualification_gates": {
            "chronological_oos": True,
            "costed_expectancy": True,
            "parameter_stability": True,
        },
    })
    monkeypatch.setattr(dashboard, "_data_file_candidates", lambda name: [tmp_path / name])

    payload = dashboard._best_policy_research_payload()
    compatibility = dashboard._decision_readiness_payload()

    assert payload["status"] == "QUALIFIED"
    assert payload["current_candidate"] == {"policy_id": "policy-oos-1"}
    assert payload["live_policy_change_allowed"] is True
    assert compatibility["questions"][0]["key"] == "best_policy_research"
    assert len(compatibility["questions"]) == 1


def test_dashboard_retires_five_question_cards():
    source = Path(dashboard.__file__).read_text(encoding="utf-8")
    assert "<h2>Best Policy Research</h2>" in source
    assert "Live-policy question readiness" not in source
    assert "Cluster distance" not in source
    assert "Thesis fast-cut" not in source
    assert "Chase timing and limits" not in source
    assert "fetch('/api/best-policy-research')" in source


def test_analyzer_adapter_emits_fail_closed_current_epoch_artifact(tmp_path):
    events = [
        _event("filled", "ACCEPTED_FILLED", "episode-1"),
        _event("unfilled", "ACCEPTED_UNFILLED", "episode-2"),
        _event("rejected", "REJECTED", "episode-3"),
    ]
    (tmp_path / dashboard.RESEARCH_EVENTS_FILE).write_text(
        "".join(json.dumps(row) + "\n" for row in events), encoding="utf-8"
    )
    (tmp_path / "policy_candidate_oos_report.json").write_text(json.dumps({
        "epoch_id": "epoch-clean",
        "status": "PROVISIONAL",
        "candidate": {"policy_id": "not-yet"},
        "qualification_gates": {"chronological_oos": False},
    }), encoding="utf-8")

    report = build_best_policy_research_report(tmp_path, tmp_path)
    written = json.loads((tmp_path / dashboard.BEST_POLICY_RESEARCH_REPORT_FILE).read_text(encoding="utf-8"))

    assert report["status"] == "NO QUALIFIED POLICY"
    assert report["current_candidate"] is None
    assert report["evidence"]["completed_paths"] == 3
    assert written == report
