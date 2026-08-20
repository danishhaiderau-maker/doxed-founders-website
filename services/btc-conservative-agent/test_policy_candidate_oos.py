import json

from research import policy_candidate_oos as policy_oos
from research.policy_candidate_oos import (
    _oos_policy_comparison,
    _regime_key,
    build_policy_candidate_oos_report,
)


def _score(episodes, expectancy, net):
    return {"independent_episodes": episodes, "expectancy_usd": expectancy, "net_pnl_usd": net}


def test_oos_comparison_requires_absolute_profitability():
    negative = _oos_policy_comparison(_score(15, -3.7646, -56.4694), _score(15, -0.7179, -10.7684))
    assert negative["winner_kind"] == "NONE"
    assert negative["winner_status"] == "NO_PROFITABLE_OOS_WINNER"
    assert negative["relative_leader_kind"] == "DYNAMIC"
    assert negative["comparison_delta"]["dynamic_minus_static_expectancy_usd"] > 0

    positive = _oos_policy_comparison(_score(10, 1.0, 10.0), _score(10, 2.0, 20.0))
    assert positive["winner_kind"] == "DYNAMIC"
    assert positive["winner_status"] == "PROFITABLE_OOS_WINNER"

    positive_tie = _oos_policy_comparison(_score(10, 1.0, 10.0), _score(10, 1.0, 10.0))
    assert positive_tie["winner_kind"] == "TIE"
    assert positive_tie["winner_status"] == "PROFITABLE_OOS_WINNER"

    tie = _oos_policy_comparison(_score(10, -1.0, -10.0), _score(10, -1.0, -10.0))
    assert tie["winner_kind"] == "NONE"
    assert tie["relative_leader_kind"] == "TIE"

    empty = _oos_policy_comparison(_score(0, None, 0.0), _score(0, None, 0.0))
    assert empty["winner_kind"] == "NONE"
    assert empty["relative_leader_kind"] == "NONE"


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


def test_descriptive_pages_use_train_rank_and_include_rejected_shadow(tmp_path, monkeypatch):
    events = []
    for index in range(10):
        outcome = "REJECTED" if index in {2, 8} else "ACCEPTED_FILLED"
        events.append({
            "event_id": f"event-{index}",
            "event_episode_id": f"episode-{index}",
            # Exercise envelope-only identity, which is valid collector v2.2.
            "envelope": {
                "signal_ts": 1_800_000_000 + index,
                "epoch_id": "epoch-current",
                "policy_epoch_id": "policy-current",
                "policy_signature": "signature-current",
                "primary_outcome": outcome,
            },
            "feature_snapshot_at_signal": {
                "cycle_3m_universe": {
                    "regime": "BULL" if index < 7 else "SIDEWAYS",
                    "adx14": 32,
                    "atr14_pct_3m": 0.18,
                    "session_utc": "EU",
                },
            },
        })
    (tmp_path / policy_oos.RESEARCH_EVENTS_FILE).write_text(
        "".join(json.dumps(row) + "\n" for row in events), encoding="utf-8"
    )
    monkeypatch.setattr(policy_oos, "validate_replay_eligibility", lambda row: {"eligible": True})
    monkeypatch.setattr(
        policy_oos,
        "_policy_outcomes",
        lambda row: {
            "CONTROL": -1.0,
            "POLICY_GOOD": 2.0,
            # Wins training but loses untouched OOS, so it must not be shown.
            "POLICY_OVERFIT": 4.0 if int(row["event_id"].split("-")[-1]) < 7 else -10.0,
        },
    )

    report = build_policy_candidate_oos_report(tmp_path, tmp_path)

    challenger = report["descriptive_challenger"]
    assert [row["policy_id"] for row in challenger["profitable_static_policies"]] == ["POLICY_GOOD"]
    assert challenger["dynamic_regimes"]
    assert all(row["fallback"] is True for row in challenger["dynamic_regimes"])
    assert all(row["selected_policy_id"] == "CONTROL_OR_NO_TRADE" for row in challenger["dynamic_regimes"])
    assert all("research_candidate_policy_id" in row for row in challenger["dynamic_regimes"])
    assert report["evidence"]["independent_episodes"] == 10
    assert report["shadow_research"]["independent_episodes"] == 2
    assert report["shadow_research"]["profitable_policies"][0]["policy_id"] == "POLICY_GOOD"
    assert report["shadow_research"]["qualification"] == "DESCRIPTIVE_ONLY"
