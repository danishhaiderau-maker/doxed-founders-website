"""Equal-rights ranking: same worlds, after-cost expectancy, no fake SAFE."""
from __future__ import annotations

import json
from pathlib import Path

from research_v3_ranking import REQUIRED_GATES

from equal_rights_ranking import (
    build_equal_rights_ranking,
    equal_rights_from_report,
    sanitize_equal_rights,
)


ROOT = Path(__file__).resolve().parent


def _passing_gates() -> dict[str, bool]:
    return {name: True for name in REQUIRED_GATES}


def test_empty_report_keeps_three_empty_worlds_and_no_crown():
    payload = build_equal_rights_ranking(report={})
    assert [row["world"] for row in payload["surfaces"]] == [
        "OBSERVED_PAPER",
        "IDEAL_TOUCH",
        "CONSERVATIVE_BBO",
    ]
    assert [row["id"] for row in payload["surfaces"]] == ["paper", "shadow", "counterfactual"]
    assert all(row["closed_n"] == 0 for row in payload["surfaces"])
    assert all(row["qualification"] == "EMPTY" for row in payload["surfaces"])
    assert payload["number_one"] is None
    assert payload["safe_badge"] is None
    assert payload["qualification"] == "NO_SAFE_QUALIFIED_POLICY"
    assert payload["win_rate_is_rank"] is False
    assert payload["primary_metric"] == "after_cost_expectancy_usd"
    assert [row["id"] for row in payload["evidence"]] == ["microstructure", "funnel", "blocked"]
    assert all(row["safe_badge"] is None for row in payload["evidence"])
    assert "no strategy is crowned" in payload["headline"].lower() or "NO_SAFE" in payload["headline"]


def test_paper_closes_rank_by_expectancy_and_stay_no_safe_without_gates():
    payload = build_equal_rights_ranking(
        report={"collection": {"market_segments": 4, "decision_branches": 10, "decision_outcomes": {"REJECTED": 3}}},
        lifecycles=[
            {
                "terminal": True,
                "observation_status": "PAPER_POSITION_CLOSED",
                "effective_execution_mode": "PAPER_OBSERVED",
                "outcome_state": "REALIZED_PROFIT",
                "policy_id": "HIGH_WIN_LOW_EV",
                "net_pnl_usd": -2.0,
            },
            {
                "terminal": True,
                "observation_status": "PAPER_POSITION_CLOSED",
                "effective_execution_mode": "PAPER_OBSERVED",
                "outcome_state": "REALIZED_LOSS",
                "policy_id": "LOW_WIN_HIGH_EV",
                "net_pnl_usd": 5.0,
            },
            {
                "terminal": True,
                "outcome_state": "PAPER_REALIZED",
                "observation_status": "LEGACY_PAPER_CLOSE",
                "policy_id": "LOW_WIN_HIGH_EV",
                "net_pnl_usd": 1.0,
            },
        ],
    )
    paper = payload["surfaces"][0]
    shadow = payload["surfaces"][1]
    counterfactual = payload["surfaces"][2]
    assert paper["world"] == "OBSERVED_PAPER"
    assert paper["closed_n"] == 3
    assert paper["qualification"] == "NO_SAFE_QUALIFIED_POLICY"
    assert paper["safe_badge"] is None
    assert paper["rows"][0]["policy_id"] == "LOW_WIN_HIGH_EV"
    assert paper["rows"][0]["rank"] == 1
    assert paper["rows"][0]["after_cost_expectancy_usd"] == 3.0
    assert paper["rows"][1]["policy_id"] == "HIGH_WIN_LOW_EV"
    assert shadow["closed_n"] == 0 and shadow["qualification"] == "EMPTY"
    assert counterfactual["closed_n"] == 0 and counterfactual["qualification"] == "EMPTY"
    assert payload["number_one"] is None
    assert payload["evidence"][0]["count"] == 4
    assert payload["evidence"][2]["count"] == 3
    comparison = payload["comparison_rows"][0]
    assert comparison["policy_id"] == "LOW_WIN_HIGH_EV"
    assert comparison["worlds"]["paper"]["closed_n"] == 2
    assert comparison["worlds"]["shadow"]["closed_n"] == 0
    assert comparison["worlds"]["counterfactual"]["closed_n"] == 0


def test_safe_badge_requires_every_gate_and_closed_evidence():
    almost = _passing_gates()
    almost["sealed_holdout_pass"] = False
    payload = build_equal_rights_ranking(
        lifecycles=[{
            "terminal": True,
            "effective_execution_mode": "PAPER_OBSERVED",
            "outcome_state": "REALIZED_PROFIT",
            "policy_id": "ALMOST",
            "net_pnl_usd": 9.0,
            "gates": almost,
        }],
        candidates=[{
            "policy_id": "IDEAL_ONLY",
            "oos_episodes": 2,
            "sealed_oos_net_usd": 4.0,
            "policy_spec": {"fill": {"execution_world": "IDEAL_TOUCH_DIAGNOSTIC"}},
            "gates": _passing_gates(),
        }, {
            "policy_id": "BBO_GATED",
            "oos_episodes": 4,
            "sealed_oos_net_usd": 8.0,
            "policy_spec": {"fill": {"execution_world": "CONSERVATIVE_BBO_DEPTH_TAPE"}},
            "gates": _passing_gates(),
        }],
    )
    paper = next(row for row in payload["surfaces"] if row["id"] == "paper")
    shadow = next(row for row in payload["surfaces"] if row["id"] == "shadow")
    cf = next(row for row in payload["surfaces"] if row["id"] == "counterfactual")
    assert paper["rows"][0]["safe_badge"] is None
    assert shadow["rows"][0]["safe_badge"] == "SAFE"
    assert cf["rows"][0]["safe_badge"] == "SAFE"
    assert payload["safe_badge"] == "SAFE"
    assert payload["number_one"]["policy_id"] == "BBO_GATED"
    assert payload["number_one"]["safe_badge"] == "SAFE"


def test_stale_safe_badge_without_gates_is_stripped():
    forged = build_equal_rights_ranking(lifecycles=[{
        "terminal": True,
        "effective_execution_mode": "PAPER_OBSERVED",
        "outcome_state": "REALIZED_PROFIT",
        "policy_id": "FORGED",
        "net_pnl_usd": 3.0,
    }])
    forged["safe_badge"] = "SAFE"
    forged["qualification"] = "QUALIFIED"
    forged["surfaces"][0]["safe_badge"] = "SAFE"
    forged["surfaces"][0]["rows"][0]["safe_badge"] = "SAFE"
    forged["number_one"] = {"policy_id": "FORGED", "safe_badge": "SAFE"}
    cleaned = sanitize_equal_rights(forged, {})
    assert cleaned["safe_badge"] is None
    assert cleaned["number_one"] is None
    assert cleaned["surfaces"][0]["safe_badge"] is None
    assert cleaned["surfaces"][0]["rows"][0]["safe_badge"] is None


def test_outcome_counts_show_paper_n_without_inventing_expectancy_or_safe():
    payload = equal_rights_from_report({
        "collection": {"outcome_states": {"REALIZED_PROFIT": 20, "REALIZED_LOSS": 19}},
        "candidate_screen": {"descriptive_top_100": []},
    })
    paper = payload["surfaces"][0]
    assert paper["closed_n"] == 39
    assert paper["after_cost_expectancy_usd"] is None
    assert paper["qualification"] == "NO_SAFE_QUALIFIED_POLICY"
    assert paper["safe_badge"] is None
    assert payload["number_one"] is None
    assert payload["comparison_rows"][0]["rank"] is None


def test_dashboard_and_bot_expose_the_same_equal_rights_surface():
    dashboard = (ROOT / "research" / "research_dashboard.py").read_text(encoding="utf-8")
    bot = (ROOT / "bot.py").read_text(encoding="utf-8")
    assert '@app.route("/api/equal-rights-ranking")' in dashboard
    assert "equal-rights-root" in dashboard
    assert "OBSERVED_PAPER" in dashboard
    assert "After-cost expectancy" in dashboard
    assert "Win probability (95% CI)" in dashboard
    assert "@app.route('/api/equal-rights-ranking')" in bot
    assert 'id="equal-rights-root"' in bot
    assert "Paper after-cost EV" in bot or "renderEqualRights" in bot
    page = dashboard.split("def safe_policy_genome_v3_page", 1)[1].split("def api_conservative_fill_research", 1)[0]
    assert "Qualified number one" in page
    assert "Number one complete safe strategy" not in page


def test_dashboard_api_is_honest_when_the_report_is_missing():
    from research import research_dashboard as dashboard

    original = dashboard._read_json
    dashboard._read_json = lambda name, default=None: {}
    try:
        client = dashboard.app.test_client()
        payload = client.get("/api/equal-rights-ranking").get_json()
    finally:
        dashboard._read_json = original
    assert payload["schema"] == "equal_rights_ranking_v1"
    assert payload["safe_badge"] is None
    assert payload["number_one"] is None
    assert [row["world"] for row in payload["surfaces"]] == [
        "OBSERVED_PAPER", "IDEAL_TOUCH", "CONSERVATIVE_BBO",
    ]
    html = client.get("/safe-policy-genome-v3.1").get_data(as_text=True)
    assert "Equal-rights ranks" in html
    assert "NO_SAFE" in html
    assert "After-cost expectancy" in html


def test_rank_order_ignores_win_rate_fields():
    payload = build_equal_rights_ranking(candidates=[
        {
            "policy_id": "PRETTY_WINRATE",
            "oos_episodes": 10,
            "sealed_oos_net_usd": -5.0,
            "win_rate_pct": 90,
            "policy_spec": {"fill": {"execution_world": "IDEAL_TOUCH"}},
        },
        {
            "policy_id": "BETTER_AFTER_COST",
            "oos_episodes": 4,
            "sealed_oos_net_usd": 8.0,
            "win_rate_pct": 25,
            "policy_spec": {"fill": {"execution_world": "IDEAL_TOUCH"}},
        },
    ])
    rows = payload["surfaces"][1]["rows"]
    assert [row["policy_id"] for row in rows] == ["BETTER_AFTER_COST", "PRETTY_WINRATE"]
    assert "win_rate" not in json.dumps(rows[0])


def test_frozen_digest_has_banners_freshness_worlds_regime_leakage():
    from equal_rights_ranking import _WORLD_TAGS, MIN_EPISODES_FOR_ADEQUATE_SAMPLE
    payload = build_equal_rights_ranking(
        report={
            "generated_at": "2026-09-22T01:00:00Z",
            "live_policy_change_allowed": False,
            "collection": {
                "outcome_states": {"REALIZED_PROFIT": 10, "REALIZED_LOSS": 5},
                "market_segments": 3,
                "decision_branches": 8,
                "decision_outcomes": {"REJECTED_SPREAD": 2},
            },
            "candidate_screen": {"descriptive_top_100": []},
        },
    )
    digest = payload["digest"]
    assert digest is not None
    assert digest["freshness"] in ("FROZEN", "STALE")
    assert digest["world_tags"] == list(_WORLD_TAGS)
    assert digest["fixed_watch"] == "ATR_TRAIL + CHANDELIER_3"
    assert digest["regime_dynamic"] is False
    assert digest["live_arm"] is False
    banner_ids = [b["id"] for b in digest["banners"]]
    assert "NO_SAFE" in banner_ids
    assert "SAMPLE_POOR" in banner_ids
    assert "LIVE_LOCKED" in banner_ids
    assert digest["exit_leakage"]["id"] == "exit_leakage"
    assert digest["exit_leakage"]["role"] == "EVIDENCE_ONLY"
    rp = digest["regime_progress"]
    assert rp["min_required"] == 3
    assert 0 <= rp["progress_pct"] <= 100
    assert "copy" in digest


def test_profitable_hypothesis_decoupled_from_safe():
    payload = build_equal_rights_ranking(
        lifecycles=[{
            "terminal": True,
            "effective_execution_mode": "PAPER_OBSERVED",
            "outcome_state": "REALIZED_PROFIT",
            "policy_id": "PROFITABLE_NOT_SAFE",
            "net_pnl_usd": 5.0,
        }],
    )
    paper = payload["surfaces"][0]
    assert paper["after_cost_expectancy_usd"] == 5.0
    assert paper["safe_badge"] is None
    assert paper["qualification"] == "NO_SAFE_QUALIFIED_POLICY"
    assert payload["qualification"] == "NO_SAFE_QUALIFIED_POLICY"


def test_safe_badge_demoted_under_no_safe():
    from equal_rights_ranking import EQUAL_RIGHTS_CLIENT_JS
    assert "noSafe" in EQUAL_RIGHTS_CLIENT_JS
    assert "er-profitable-hypothesis" in EQUAL_RIGHTS_CLIENT_JS
    assert "er-banner" in EQUAL_RIGHTS_CLIENT_JS
    assert "er-chip" in EQUAL_RIGHTS_CLIENT_JS
    assert "regime_progress" in EQUAL_RIGHTS_CLIENT_JS or "regime" in EQUAL_RIGHTS_CLIENT_JS


def main() -> None:
    tests = (
        test_empty_report_keeps_three_empty_worlds_and_no_crown,
        test_paper_closes_rank_by_expectancy_and_stay_no_safe_without_gates,
        test_safe_badge_requires_every_gate_and_closed_evidence,
        test_stale_safe_badge_without_gates_is_stripped,
        test_outcome_counts_show_paper_n_without_inventing_expectancy_or_safe,
        test_dashboard_and_bot_expose_the_same_equal_rights_surface,
        test_dashboard_api_is_honest_when_the_report_is_missing,
        test_rank_order_ignores_win_rate_fields,
        test_frozen_digest_has_banners_freshness_worlds_regime_leakage,
        test_profitable_hypothesis_decoupled_from_safe,
        test_safe_badge_demoted_under_no_safe,
    )
    for test in tests:
        test()
        print(f"[PASS] {test.__name__}")
    print(f"PASS: {len(tests)} equal-rights ranking checks")


if __name__ == "__main__":
    main()
