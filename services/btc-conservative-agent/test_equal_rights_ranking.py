"""Equal-rights ranking: same worlds, after-cost expectancy, no fake SAFE."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from research_v3_ranking import REQUIRED_GATES

from equal_rights_ranking import (
    build_equal_rights_ranking,
    canonical_analyzer_roots,
    equal_rights_from_report,
    load_analyzer_companions,
    sanitize_equal_rights,
)


ROOT = Path(__file__).resolve().parent


@pytest.fixture(autouse=True)
def _isolate_host_canonical_analyzer(monkeypatch):
    """Host FRESH digests must not leak into empty fixtures.

    Ops machines set BTC_CANONICAL_ANALYZER_DATA or have a sibling
    btc-v31-current tree. Tests that need a canonical root set the env to a
    temp directory themselves.
    """
    monkeypatch.delenv("BTC_CANONICAL_ANALYZER_DATA", raising=False)


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


def test_fills_and_drawdown_in_surface_rows():
    payload = build_equal_rights_ranking(
        candidates=[{
            "policy_id": "WITH_DD",
            "oos_episodes": 3,
            "oos_fills": 2,
            "sealed_oos_net_usd": 6.0,
            "max_drawdown_usd": -1.5,
            "policy_spec": {"fill": {"execution_world": "IDEAL_TOUCH"}},
        }],
    )
    shadow = payload["surfaces"][1]
    assert shadow["fills"] == 2
    assert shadow["max_drawdown_usd"] == -1.5
    row = shadow["rows"][0]
    assert row["fills"] == 2
    assert row["max_drawdown_usd"] == -1.5
    assert "fills" in shadow["columns"]
    assert "max_drawdown_usd" in shadow["columns"]


def test_unavailable_in_js_for_missing_values():
    from equal_rights_ranking import EQUAL_RIGHTS_CLIENT_JS
    assert "UNAVAILABLE" in EQUAL_RIGHTS_CLIENT_JS
    assert "intOrUnavail" in EQUAL_RIGHTS_CLIENT_JS
    assert "ddFmt" in EQUAL_RIGHTS_CLIENT_JS


def test_secondary_worlds_in_digest():
    payload = build_equal_rights_ranking(
        report={
            "collection": {
                "decision_outcomes": {"SHADOW_BLOCKED_SPREAD": 5, "CF_MISSED": 2, "TIMEOUT_EXPIRED": 3},
                "decision_dispositions": {"REJECTED_SHADOW": 1},
            },
        },
    )
    digest = payload["digest"]
    sws = digest["secondary_worlds"]
    assert len(sws) == 3
    assert sws[0]["world"] == "SHADOW_BLOCKED"
    assert sws[0]["closed_n"] == 6
    assert sws[0]["role"] == "EVIDENCE_ONLY"
    assert sws[2]["world"] == "MISSED"
    assert sws[2]["closed_n"] >= 3


def test_genome_surface_in_digest():
    payload = build_equal_rights_ranking(report={})
    genome = payload["digest"]["genome"]
    assert genome["gates_passed"] == 0
    assert genome["gates_total"] > 0
    assert genome["all_pass"] is False
    assert genome["label"].startswith("0/")


def test_post_fresh_diff_in_digest():
    payload = build_equal_rights_ranking(
        report={
            "epoch_id": "EP_42",
            "data_scope": "FRESH-COLLECTION",
            "generated_at": "2026-09-22T01:00:00Z",
            "status": "V3_READY_FOR_FRESH_EPOCH",
            "schema": "safe_policy_genome_v3_1_report_v1",
        },
    )
    diff = payload["digest"]["post_fresh_diff"]
    assert diff["epoch_id"] == "EP_42"
    assert diff["data_scope"] == "FRESH-COLLECTION"
    assert diff["status"] == "V3_READY_FOR_FRESH_EPOCH"


def test_comparison_rows_include_fills_and_drawdown():
    payload = build_equal_rights_ranking(
        candidates=[{
            "policy_id": "DD_POLICY",
            "oos_episodes": 5,
            "oos_fills": 3,
            "sealed_oos_net_usd": 10.0,
            "max_drawdown_usd": -2.0,
            "policy_spec": {"fill": {"execution_world": "CONSERVATIVE_BBO"}},
        }],
    )
    cf_world = payload["comparison_rows"][0]["worlds"]["counterfactual"]
    assert cf_world["fills"] == 3
    assert cf_world["max_drawdown_usd"] == -2.0


def test_heartbeat_identity_reader():
    import tempfile
    src = Path(__file__).resolve().parent / "analyzer_research_engine_v62.py"
    text = src.read_text(encoding="utf-8")
    assert "_read_heartbeat_identity" in text
    assert "_FLY_HEARTBEAT_FILE" in text
    assert "sourceRevision" in text
    assert "skipped" in text
    assert "ANALYZER_MIRROR_SYNC_MAX_AGE_SEC" in text


def test_mirror_stale_banner():
    payload = build_equal_rights_ranking(
        report={"generated_at": "2020-01-01T00:00:00Z"},
    )
    banner_ids = [b["id"] for b in payload["digest"]["banners"]]
    assert "MIRROR_STALE" in banner_ids


def test_compatible_analyzer_counts_are_not_spuriously_zero():
    """FRESH digest n and shadow/CF/missed artifacts must show up when the genome embed is empty."""
    empty = build_equal_rights_ranking(report={})
    payload = equal_rights_from_report(
        {
            "generated_at": "2026-09-26T01:00:00Z",
            "data_scope": "FRESH-COLLECTION",
            "live_policy_change_allowed": False,
            "collection": {},
            "equal_rights": empty,
        },
        companions={
            "compact": {
                "schema": "research_hierarchy_v1",
                "data_scope": "session",
                "performance": {"trades": 57, "net_pnl_usd": -12.5, "expectancy_usd": -0.22},
            },
            "shadow_fill": {"shadow_cohort": 20, "shadow_filled": 11},
            "counterfactual": {
                "schema": "counterfactual_coverage_v1",
                "n_shadow": 20,
                "n_cf_in": 8,
                "n_compact_out": 28,
            },
            "missed": {"schema": "missed_opportunity_heatmap_v1", "totals": {"events": 4}},
            "paused_shadow": {"overall": {"closed": 3, "filled": 2, "net_pnl_usd": 1.5}},
        },
    )
    by_id = {row["id"]: row for row in payload["surfaces"]}
    assert by_id["paper"]["closed_n"] == 57
    assert by_id["paper"]["fills"] == 57
    assert by_id["paper"]["after_cost_expectancy_usd"] == round(-12.5 / 57, 6)
    assert by_id["paper"]["safe_badge"] is None
    assert by_id["shadow"]["closed_n"] == 11
    assert by_id["shadow"]["after_cost_expectancy_usd"] is None
    assert by_id["shadow"]["safe_badge"] is None
    assert by_id["counterfactual"]["closed_n"] == 0
    assert by_id["counterfactual"]["qualification"] == "EMPTY"
    assert by_id["counterfactual"]["safe_badge"] is None
    secondary = {row["id"]: row for row in payload["digest"]["secondary_worlds"]}
    assert secondary["cf_evidence"]["closed_n"] == 8
    assert secondary["cf_evidence"]["world"] == "CF"
    assert secondary["cf_evidence"]["safe_badge"] is None
    assert secondary["cf_evidence"]["qualification"] == "NOT_A_STRATEGY_RANK"
    assert secondary["missed"]["closed_n"] == 4
    assert secondary["shadow_blocked"]["closed_n"] == 3
    assert secondary["missed"]["safe_badge"] is None
    assert secondary["missed"]["qualification"] == "NOT_A_STRATEGY_RANK"
    assert payload["safe_badge"] is None
    assert payload["number_one"] is None
    assert payload["qualification"] == "NO_SAFE_QUALIFIED_POLICY"
    assert payload["comparison_rows"][0]["rank"] is None


def test_missing_companion_artifacts_stay_empty():
    payload = equal_rights_from_report(
        {"equal_rights": build_equal_rights_ranking(report={})},
        companions={
            "compact": {},
            "real_edge": {},
            "shadow_fill": {"shadow_cohort": 9, "shadow_filled": 0},
            "counterfactual": {"n_cf_in": 0, "n_compact_out": 9},
            "missed": {"totals": {"events": 0}, "heatmap": []},
            "paused_shadow": {"overall": {"closed": 0}},
        },
    )
    assert all(row["closed_n"] == 0 for row in payload["surfaces"])
    assert all(row["qualification"] == "EMPTY" for row in payload["surfaces"])
    assert all(row["closed_n"] == 0 for row in payload["digest"]["secondary_worlds"])
    assert payload["safe_badge"] is None
    assert payload["number_one"] is None


def test_genome_counts_are_not_replaced_by_digest_trades():
    payload = equal_rights_from_report(
        {"collection": {"outcome_states": {"REALIZED_PROFIT": 2}}},
        companions={"compact": {"performance": {"trades": 57, "net_pnl_usd": 100.0}}},
    )
    paper = payload["surfaces"][0]
    assert paper["closed_n"] == 2
    assert paper["after_cost_expectancy_usd"] is None
    assert paper["safe_badge"] is None
    assert payload["number_one"] is None


def test_digest_fallback_uses_real_edge_executed_when_trades_missing():
    payload = equal_rights_from_report(
        {},
        companions={"real_edge": {"executed": 57, "executed_pnl_usd": 3.0}},
    )
    paper = payload["surfaces"][0]
    assert paper["closed_n"] == 57
    assert paper["after_cost_expectancy_usd"] == round(3.0 / 57, 6)
    assert paper["safe_badge"] is None
    assert payload["number_one"] is None


def test_dashboard_binds_the_same_digest_artifacts():
    dashboard = (ROOT / "research" / "research_dashboard.py").read_text(encoding="utf-8")
    api = dashboard.split("def api_equal_rights_ranking", 1)[1].split("\ndef ", 1)[0]
    assert "load_analyzer_companions" in api
    assert "DATA_ROOT" in api
    assert "companions" in api
    bot = (ROOT / "bot.py").read_text(encoding="utf-8")
    bot_api = bot.split("def api_equal_rights_ranking", 1)[1].split("\ndef ", 1)[0]
    assert "load_analyzer_companions" in bot_api
    report = (ROOT / "research" / "research_v3_report.py").read_text(encoding="utf-8")
    assert "load_analyzer_companions(str(report_dir), str(data_dir))" in report


def _write_session_ledger(root: Path, rows: list[tuple[float, float]], *, start: float) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "research_session.json").write_text(
        json.dumps({
            "fresh_collection_mode": True,
            "fresh_collection_start_time": start,
        }),
        encoding="utf-8",
    )
    lines = ["trade_id,net_pnl_usd,close_ts"]
    for index, (stamp, pnl) in enumerate(rows, start=1):
        lines.append(f"t{index},{pnl},{stamp}")
    (root / "trades_3factor.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_empty_cwd_compact_uses_session_mirror_paper_counts():
    """Zero worktree compact must not hide FRESH closes on the session/mirror root."""
    import tempfile
    start = 1_700_000_000.0
    with tempfile.TemporaryDirectory() as tmp:
        cwd = Path(tmp) / "cwd"
        mirror = Path(tmp) / "mirror"
        cwd.mkdir()
        (cwd / "research_compact_summary.json").write_text(
            json.dumps({"performance": {"trades": 0, "net_pnl_usd": 0}, "data_scope": "session"}),
            encoding="utf-8",
        )
        (cwd / "shadow_fill_outcome_report.json").write_text(
            json.dumps({"shadow_filled": 0, "shadow_cohort": 9}),
            encoding="utf-8",
        )
        (cwd / "counterfactual_coverage_report.json").write_text(
            json.dumps({"n_cf_in": 0, "n_compact_out": 9}),
            encoding="utf-8",
        )
        in_session = [(start + 60, -12.5)] + [(start + 60 + index, 0.0) for index in range(1, 57)]
        _write_session_ledger(
            mirror,
            [(start - 100, 50.0), (start - 1, 25.0), *in_session],
            start=start,
        )
        (mirror / "shadow_fill_outcome_report.json").write_text(
            json.dumps({"shadow_filled": 11}),
            encoding="utf-8",
        )
        (mirror / "counterfactual_coverage_report.json").write_text(
            json.dumps({"n_cf_in": 8, "n_compact_out": 20}),
            encoding="utf-8",
        )
        payload = equal_rights_from_report(
            {},
            companions=load_analyzer_companions(str(cwd), str(mirror)),
        )
    by_id = {row["id"]: row for row in payload["surfaces"]}
    assert by_id["paper"]["closed_n"] == 57
    assert by_id["paper"]["world"] == "OBSERVED_PAPER"
    assert by_id["paper"]["after_cost_expectancy_usd"] == round(-12.5 / 57, 6)
    assert by_id["paper"]["safe_badge"] is None
    assert by_id["shadow"]["closed_n"] == 11
    assert by_id["shadow"]["after_cost_expectancy_usd"] is None
    assert by_id["counterfactual"]["closed_n"] == 0
    assert by_id["counterfactual"]["qualification"] == "EMPTY"
    secondary = {row["id"]: row for row in payload["digest"]["secondary_worlds"]}
    assert secondary["cf_evidence"]["closed_n"] == 8
    assert payload["safe_badge"] is None
    assert payload["number_one"] is None
    assert payload["digest"]["live_arm"] is False


def test_wal_identity_invalid_keeps_companion_counts():
    """EMERGENCY_WAL_IDENTITY_INVALID must not blank honest companion counts."""
    import os
    import tempfile

    import research_v3_store as store_mod
    from emergency_evidence_wal import EmergencyEvidenceWal
    from research.research_v3_report import build_safe_policy_genome_v3_report

    previous = os.environ.get("SOURCE_GIT_REV")
    os.environ["SOURCE_GIT_REV"] = "a" * 40
    store_mod._provenance_cache = None
    try:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            reports = Path(tmp) / "reports"
            data.mkdir()
            reports.mkdir()
            (reports / "research_compact_summary.json").write_text(
                json.dumps({
                    "data_scope": "session",
                    "performance": {"trades": 57, "net_pnl_usd": -12.5},
                }),
                encoding="utf-8",
            )
            store = store_mod.V3EvidenceStore(data, epoch_id="V3_NOT_STARTED")
            try:
                EmergencyEvidenceWal._validate_identity(store._identity_binding())
                identity_rejected = False
            except ValueError as exc:
                identity_rejected = str(exc) == "EMERGENCY_WAL_IDENTITY_INVALID"
            assert identity_rejected
            assert store._emergency_wal_identity_available() is False
            assert not (data / "v3" / "emergency_evidence_wal_v2").exists()
            report = build_safe_policy_genome_v3_report(
                str(data),
                str(reports),
                candidates=[],
            )
        paper = {row["id"]: row for row in report["equal_rights"]["surfaces"]}["paper"]
        assert paper["closed_n"] == 57
        assert paper["after_cost_expectancy_usd"] == round(-12.5 / 57, 6)
        assert paper["safe_badge"] is None
        assert report["equal_rights"]["safe_badge"] is None
        assert report["equal_rights"]["number_one"] is None
        assert report["equal_rights"]["digest"]["live_arm"] is False
        assert report["live_policy_change_allowed"] is False
        assert report["real_bitfinex_trading_allowed"] is False
        assert report["emergency_wal"]["status"] == "EMERGENCY_WAL_IDENTITY_INVALID"
        assert report["emergency_wal"]["effect"] == "RANKING_CONTINUES"
        assert report["emergency_wal"]["identity_accepted"] is False
    finally:
        if previous is None:
            os.environ.pop("SOURCE_GIT_REV", None)
        else:
            os.environ["SOURCE_GIT_REV"] = previous
        store_mod._provenance_cache = None


def test_report_root_empty_uses_data_root_mirror_counts():
    """Health split: empty report_root must not hide mirror data_root fills."""
    import tempfile
    from datetime import datetime, timezone

    from research.policy_cycle_snapshot import load_policy_cycle_snapshot

    generated = datetime.now(timezone.utc).isoformat()
    with tempfile.TemporaryDirectory() as tmp:
        report_root = Path(tmp) / "report"
        data_root = Path(tmp) / "fly-data-mirror"
        report_root.mkdir()
        data_root.mkdir()
        (report_root / "research_compact_summary.json").write_text(
            json.dumps({
                "performance": {"trades": 0, "net_pnl_usd": 0},
                "data_scope": "session",
            }),
            encoding="utf-8",
        )
        (report_root / "research_events_v22.jsonl").write_text("", encoding="utf-8")
        (data_root / "research_compact_summary.json").write_text(
            json.dumps({
                "generated_at": generated,
                "data_scope": "FRESH-COLLECTION",
                "performance": {"trades": 57, "net_pnl_usd": -12.5},
            }),
            encoding="utf-8",
        )
        (data_root / "shadow_fill_outcome_report.json").write_text(
            json.dumps({"shadow_filled": 4}),
            encoding="utf-8",
        )
        (data_root / "counterfactual_coverage_report.json").write_text(
            json.dumps({"n_cf_in": 6}),
            encoding="utf-8",
        )
        (data_root / "research_events_v22.jsonl").write_text(
            '{"event_id":"e1","epoch_id":"epoch-fresh"}\n',
            encoding="utf-8",
        )
        # Genome order is report_root then data_root. Dashboard order is the reverse.
        for roots in (
            (str(report_root), str(data_root)),
            (str(data_root), str(report_root)),
        ):
            payload = equal_rights_from_report({}, companions=load_analyzer_companions(*roots))
            by_id = {row["id"]: row for row in payload["surfaces"]}
            assert by_id["paper"]["closed_n"] == 57
            assert by_id["paper"]["world"] == "OBSERVED_PAPER"
            assert by_id["paper"]["after_cost_expectancy_usd"] == round(-12.5 / 57, 6)
            assert by_id["paper"]["safe_badge"] is None
            assert by_id["shadow"]["closed_n"] == 4
            assert by_id["counterfactual"]["closed_n"] == 0
            secondary = {row["id"]: row for row in payload["digest"]["secondary_worlds"]}
            assert secondary["cf_evidence"]["closed_n"] == 6
            assert payload["safe_badge"] is None
            assert payload["number_one"] is None
            assert payload["digest"]["live_arm"] is False
            assert payload["digest"]["freshness"] == "FRESH"
        snapshot = load_policy_cycle_snapshot(str(data_root), also_roots=(str(report_root),))
    assert snapshot["receipt"]["row_count"] == 1
    assert snapshot["receipt"]["source_root"] == "data_root"
    assert snapshot["receipt"]["epoch_id"] == "epoch-fresh"


def _fresh_compact(trades: int, *, epoch: str | None, generated_at: str, net: float = -12.5) -> dict:
    payload = {
        "generated_at": generated_at,
        "session_scope": "FRESH-COLLECTION",
        "data_scope": "session",
        "performance": {"trades": trades, "net_pnl_usd": net},
    }
    if epoch:
        payload["analysis_provenance"] = {"fresh_epoch_id": epoch}
    return payload


def test_canonical_analyzer_tree_supplies_fresh_counts_when_mirror_is_empty():
    """Empty fly mirror and empty worktree must not hide the current checkout digest."""
    import os
    import tempfile

    previous = os.environ.get("BTC_CANONICAL_ANALYZER_DATA")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            report_root = base / "btc-v31-analyzer-121" / "services" / "btc-conservative-agent"
            mirror = base / "fly-data-mirror"
            canonical = (
                base / "btc-v31-current" / "services" / "btc-conservative-agent"
                / "canonical-research-data" / "analyzer"
            )
            report_root.mkdir(parents=True)
            mirror.mkdir()
            (mirror / "v3" / "ledgers").mkdir(parents=True)
            (mirror / "v3" / "emergency_evidence_wal_v2").mkdir()
            (report_root / "research_compact_summary.json").write_text(
                json.dumps({"performance": {"trades": 0}, "data_scope": "session"}),
                encoding="utf-8",
            )
            archive = canonical / "research_session_archives" / "2026-09-26"
            archive.mkdir(parents=True)
            (archive / "research_compact_summary.json").write_text(
                json.dumps(_fresh_compact(
                    57,
                    epoch="epoch-fresh",
                    generated_at="2026-09-26T04:00:00+00:00",
                )),
                encoding="utf-8",
            )
            (archive / "shadow_fill_outcome_report.json").write_text(
                json.dumps({"shadow_filled": 4}),
                encoding="utf-8",
            )
            (canonical / "published_reports" / "latest").mkdir(parents=True)
            (canonical / "published_reports" / "latest" / "research_compact_summary.json").write_text(
                json.dumps({
                    "session_scope": "ALL-DATA",
                    "data_scope": "all",
                    "generated_at": "2026-09-26T05:00:00+00:00",
                    "performance": {"trades": 200, "net_pnl_usd": 99},
                }),
                encoding="utf-8",
            )
            (canonical / "research_session.json").write_text(
                json.dumps({
                    "fresh_collection_mode": True,
                    "fresh_collection_start_time": 1_758_800_000,
                    "collector_v22_epoch_id": "epoch-fresh",
                }),
                encoding="utf-8",
            )
            agent = report_root
            found = canonical_analyzer_roots(agent)
            assert canonical in found
            os.environ["BTC_CANONICAL_ANALYZER_DATA"] = str(canonical)
            payload = equal_rights_from_report(
                {},
                companions=load_analyzer_companions(str(report_root), str(mirror)),
            )
        by_id = {row["id"]: row for row in payload["surfaces"]}
        assert by_id["paper"]["closed_n"] == 57
        assert by_id["paper"]["world"] == "OBSERVED_PAPER"
        assert by_id["paper"]["safe_badge"] is None
        assert by_id["shadow"]["closed_n"] == 4
        assert by_id["counterfactual"]["closed_n"] == 0
        assert payload["safe_badge"] is None
        assert payload["number_one"] is None
        assert payload["digest"]["live_arm"] is False
        launcher = (ROOT.parent.parent / "scripts" / "start-home-analyzer.ps1").read_text(encoding="utf-8")
        assert "BTC_CANONICAL_ANALYZER_DATA" in launcher
        assert "canonical-research-data\\analyzer" in launcher
    finally:
        if previous is None:
            os.environ.pop("BTC_CANONICAL_ANALYZER_DATA", None)
        else:
            os.environ["BTC_CANONICAL_ANALYZER_DATA"] = previous


def test_incompatible_canonical_epoch_is_not_bound():
    import os
    import tempfile

    previous = os.environ.get("BTC_CANONICAL_ANALYZER_DATA")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            canonical = Path(tmp) / "analyzer"
            other = canonical / "research_session_archives" / "other"
            other.mkdir(parents=True)
            (other / "research_compact_summary.json").write_text(
                json.dumps(_fresh_compact(
                    90,
                    epoch="epoch-other",
                    generated_at="2026-09-26T06:00:00+00:00",
                    net=40,
                )),
                encoding="utf-8",
            )
            (canonical / "research_session.json").write_text(
                json.dumps({
                    "fresh_collection_mode": True,
                    "collector_v22_epoch_id": "epoch-fresh",
                    "fresh_collection_start_time": 1_758_800_000,
                }),
                encoding="utf-8",
            )
            os.environ["BTC_CANONICAL_ANALYZER_DATA"] = str(canonical)
            payload = equal_rights_from_report({}, companions=load_analyzer_companions())
        assert all(row["closed_n"] == 0 for row in payload["surfaces"])
        assert payload["safe_badge"] is None
        assert payload["digest"]["live_arm"] is False
    finally:
        if previous is None:
            os.environ.pop("BTC_CANONICAL_ANALYZER_DATA", None)
        else:
            os.environ["BTC_CANONICAL_ANALYZER_DATA"] = previous


def test_pytest_does_not_discover_host_canonical_trees():
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        return
    assert os.environ.get("BTC_CANONICAL_ANALYZER_DATA") in (None, "")
    assert canonical_analyzer_roots() == []


def test_all_sources_empty_stay_empty():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        cwd = Path(tmp) / "cwd"
        mirror = Path(tmp) / "mirror"
        cwd.mkdir()
        mirror.mkdir()
        (cwd / "research_compact_summary.json").write_text(
            json.dumps({"performance": {"trades": 0, "net_pnl_usd": 0}}),
            encoding="utf-8",
        )
        (mirror / "research_session.json").write_text(
            json.dumps({
                "fresh_collection_mode": True,
                "fresh_collection_start_time": 1_700_000_000,
            }),
            encoding="utf-8",
        )
        (mirror / "trades_3factor.csv").write_text(
            "trade_id,net_pnl_usd,close_ts\n",
            encoding="utf-8",
        )
        payload = equal_rights_from_report(
            {},
            companions=load_analyzer_companions(str(cwd), str(mirror)),
        )
    assert all(row["closed_n"] == 0 for row in payload["surfaces"])
    assert all(row["qualification"] == "EMPTY" for row in payload["surfaces"])
    assert all(row["closed_n"] == 0 for row in payload["digest"]["secondary_worlds"])
    assert payload["safe_badge"] is None
    assert payload["number_one"] is None
    assert payload["digest"]["live_arm"] is False


def test_data_watcher_watches_heartbeat_file():
    src = Path(__file__).resolve().parent / "analyzer_research_engine_v62.py"
    text = src.read_text(encoding="utf-8")
    assert ".fly-data-sync-loop.heartbeat.json" in text
    assert "_DATA_CHANGE_EVENT" in text
    assert "Event.wait" in text or "_DATA_CHANGE_EVENT.wait" in text


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
        test_fills_and_drawdown_in_surface_rows,
        test_unavailable_in_js_for_missing_values,
        test_secondary_worlds_in_digest,
        test_genome_surface_in_digest,
        test_post_fresh_diff_in_digest,
        test_comparison_rows_include_fills_and_drawdown,
        test_heartbeat_identity_reader,
        test_mirror_stale_banner,
        test_compatible_analyzer_counts_are_not_spuriously_zero,
        test_missing_companion_artifacts_stay_empty,
        test_genome_counts_are_not_replaced_by_digest_trades,
        test_digest_fallback_uses_real_edge_executed_when_trades_missing,
        test_dashboard_binds_the_same_digest_artifacts,
        test_empty_cwd_compact_uses_session_mirror_paper_counts,
        test_wal_identity_invalid_keeps_companion_counts,
        test_report_root_empty_uses_data_root_mirror_counts,
        test_canonical_analyzer_tree_supplies_fresh_counts_when_mirror_is_empty,
        test_incompatible_canonical_epoch_is_not_bound,
        test_pytest_does_not_discover_host_canonical_trees,
        test_all_sources_empty_stay_empty,
        test_data_watcher_watches_heartbeat_file,
    )
    for test in tests:
        test()
        print(f"[PASS] {test.__name__}")
    print(f"PASS: {len(tests)} equal-rights ranking checks")


if __name__ == "__main__":
    main()
