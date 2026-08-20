import json

from research.policy_candidate_oos import build_policy_candidate_oos_report, _regime_key


def test_empty_dataset_fails_closed(tmp_path):
    report = build_policy_candidate_oos_report(tmp_path, tmp_path)
    assert report["status"] == "BLOCKED"
    assert report["candidate"] is None
    assert report["independent_oos_qualified"] is False
    persisted = json.loads((tmp_path / "policy_candidate_oos_report.json").read_text())
    assert persisted["qualification_gates"]["conservative_execution"] is False


def test_regime_key_uses_only_frozen_signal_snapshot():
    event = {"feature_snapshot_at_signal": {
        "cycle_3m_universe": {"regime": "BULL", "adx14": 27, "atr14_pct_3m": 0.12, "session_utc": "ASIA"},
    }}
    assert _regime_key(event) == "BULL|ADX_25_30|ATR_MID|ASIA"
