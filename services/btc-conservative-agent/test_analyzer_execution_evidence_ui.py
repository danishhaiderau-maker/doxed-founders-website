"""Truthful execution/diagnostic evidence contracts for the analyzer UI."""

from research import research_dashboard as dashboard


def _source_with(candidate):
    report = {
        "collection": {"independent_opportunities": 12},
        "search": {"counts": {"entry_cartesian": 1}},
        "candidate_screen": {
            "unique_policies_evaluated": 1,
            "descriptive_top_100": [candidate],
            "profitable_conservative_top_100": [candidate],
            "profitable_ideal_touch_diagnostic_top_100": [candidate],
        },
    }
    return {
        "report": report,
        "screen": report["candidate_screen"],
        "ranking": {},
        "epoch_id": "epoch-execution-ui",
        "qualified": False,
        "blockers": ["NO_SAFE_QUALIFIED_POLICY"],
    }


def test_policy_grid_exposes_exact_conservative_fill_classifications(monkeypatch):
    candidate = {
        "policy_id": "p-supported",
        "policy_family": "FIXED_TARGET",
        "episodes_total": 12,
        "oos_episodes": 10,
        "supported_conservative_episodes": 8,
        "full_fills": 3,
        "partial_fills": 1,
        "no_fills": 4,
        "unsupported_episodes": 2,
        "conservative_fill_rate": 0.5,
        "oos_wins": 3,
        "oos_losses": 1,
        "sealed_oos_net_usd": 2.0,
        "expectancy_lcb_usd": 0.05,
        "max_drawdown_usd": -0.75,
        "policy_spec": {"fill": {"execution_world": "CONSERVATIVE_BBO_DEPTH_V1"}},
        "ideal_touch_diagnostic": {
            "touches": 9,
            "no_touches": 1,
            "wins": 7,
            "losses": 2,
            "oos_net_usd": 3.0,
            "max_drawdown_usd": -0.5,
        },
    }
    monkeypatch.setattr(dashboard, "_safe_policy_v3_dashboard_source", lambda: _source_with(candidate))

    payload = dashboard._current_policy_grid_rows()
    row = payload["rows"][0]

    assert row["supported_conservative_episodes"] == 8
    assert row["full_fills"] == 3
    assert row["partial_fills"] == 1
    assert row["no_fills"] == 4
    assert row["unsupported_episodes"] == 2
    assert row["conservative_fill_rate"] == 0.5
    assert row["oos_expectancy_usd"] == 0.2
    assert row["oos_expectancy_lcb_usd"] == 0.05
    assert row["execution_metric_status"] == "SUPPORTED_TERMINAL_FILLS"
    assert payload["diagnostic_evidence_warnings"] == [
        "IDEAL_TOUCH_DIAGNOSTIC_ONLY",
        "NOT EXECUTION VERIFIED",
        "NOT QUALIFICATION ELIGIBLE",
    ]


def test_no_supported_terminal_fill_never_publishes_zero_execution_metrics():
    row = dashboard._public_policy_evidence_row({
        "policy_id": "p-no-fill",
        "supported_conservative_episodes": 7,
        "full_fills": 0,
        "partial_fills": 0,
        "no_fills": 7,
        "unsupported_episodes": 3,
        "conservative_fill_rate": 0.0,
        "sealed_oos_net_usd": 4.25,
        "expectancy_lcb_usd": 0.2,
        "max_drawdown_usd": 0.0,
        "cvar95_usd": 0.0,
        "oos_wins": 7,
        "oos_losses": 0,
    })

    assert row["supported_terminal_fills"] == 0
    assert row["sealed_oos_net_usd"] is None
    assert row["expectancy_lcb_usd"] is None
    assert row["max_drawdown_usd"] is None
    assert row["cvar95_usd"] is None
    assert row["oos_wins"] is None
    assert row["oos_losses"] is None
    assert row["execution_metric_status"] == "UNAVAILABLE_NO_SUPPORTED_TERMINAL_FILLS"
    assert row["qualification"] == "INSUFFICIENT_EXECUTION_EVIDENCE"


def test_dashboard_visibly_labels_diagnostic_and_execution_evidence():
    html = dashboard.app.test_client().get("/").get_data(as_text=True)
    safe_html = dashboard.app.test_client().get("/safe-policy-genome-v3.1").get_data(as_text=True)

    for warning in (
        "IDEAL_TOUCH_DIAGNOSTIC_ONLY",
        "NOT EXECUTION VERIFIED",
        "NOT QUALIFICATION ELIGIBLE",
    ):
        assert warning in html
        assert warning in safe_html
    for heading in (
        "Supported episodes",
        "Full fills",
        "Partial fills",
        "No fills",
        "Unsupported",
        "Fill rate",
        "Execution EV / episode",
    ):
        assert heading in html
    assert "Execution PnL, EV, wins/losses, and drawdown are UNAVAILABLE" in html
