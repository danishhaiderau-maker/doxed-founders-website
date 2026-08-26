from pathlib import Path

import pandas as pd

import analyzer_research_engine_v62 as analyzer
from combo_pathway_config import ACTIVE_TILE_ORDER, ACTIVE_TILE_REGISTRY
from research import research_dashboard as dashboard


ROOT = Path(__file__).resolve().parent
ANALYZER = (ROOT / "analyzer_research_engine_v62.py").read_text(encoding="utf-8")
DASHBOARD = (ROOT / "research" / "research_dashboard.py").read_text(encoding="utf-8")


def test_retired_named_generators_are_physically_absent():
    retired = (
        "lane_definition_report",
        "lane_retirement_report",
        "lane_chase_isolation_report",
        "urgent_chase_report",
        "type_b_predictor_report",
        "type_b_research_v2_report",
        "type_b_adx_v3_shadow_report",
        "_static_pathway_lane_specs",
    )
    for name in retired:
        assert f"def {name}(" not in ANALYZER


def test_dashboard_has_only_current_lane_routes_and_loaders():
    retired = (
        "/api/typeb",
        "/api/chase-iso",
        "/api/lanes-def",
        "/api/lane-retirement",
        "loadTypeB",
        "loadChaseIso",
        "loadLaneDefs",
        "loadRetirement",
    )
    for token in retired:
        assert token not in DASHBOARD
    assert "from pathway_lane_roster import DASHBOARD_PRIMARY_LANES as _CANONICAL_TILE_LANES" in DASHBOARD
    assert "CURRENT_RESEARCH_LANES = frozenset(_CANONICAL_TILE_LANES)" in DASHBOARD
    # Partial-reduction is a generic evidence/reconciliation view, not an
    # executable research lane.  It must survive tile retirement while its
    # policy rows remain registry-filtered and fail closed when unproven.
    assert '"/partial-reduction"' in DASHBOARD
    assert '"/api/partial-reduction"' in DASHBOARD
    assert "def api_partial_reduction()" in DASHBOARD


def test_current_lane_table_separates_executed_and_counterfactual_evidence():
    assert '"executed_closes": fills' in DASHBOARD
    assert '"counterfactual_closes": counterfactual_closes' in DASHBOARD
    assert "if not fills and lab.get(\"closes\")" not in DASHBOARD
    assert "lab.get(\"net_pnl_usd\") is not None and fills" not in DASHBOARD
    assert "<th>Executed closes</th>" in DASHBOARD
    assert "<th>Counterfactual terminals</th>" in DASHBOARD
    assert "Counterfactual results never count as fills" in DASHBOARD


def test_lab_ledger_cannot_be_promoted_to_executed_lane_results(monkeypatch, tmp_path):
    reports = {
        "benchmark_vs_lanes_report.json": {
            "benchmark_lane": "CONTINUOUS",
            "lanes": {
                "CONTINUOUS": {
                    "approves": 4,
                    "real_fills": 0,
                    "net_pnl_real": 0.0,
                    "per_approve_ev": 0.0,
                }
            },
        },
        "lane_pnl_ledger.json": {"lanes": {}},
        "lane_lab_pnl_ledger.json": {
            "lanes": {
                "CONTINUOUS": {
                    "closes": 9,
                    "net_pnl_usd": 3.75,
                }
            }
        },
    }
    monkeypatch.setattr(dashboard, "ROOT", tmp_path)
    monkeypatch.setattr(dashboard, "_read_json", lambda name: reports.get(name, {}))
    monkeypatch.setattr(dashboard, "_opportunity_lane_stats", lambda: {})

    rows, _ = dashboard._lane_rows()
    continuous = next(row for row in rows if row["lane"] == "CONTINUOUS")

    assert continuous["executed_closes"] == 0
    assert continuous["pnl"] == 0.0
    assert continuous["counterfactual_closes"] == 9
    assert continuous["counterfactual_pnl"] == 3.75


def test_generic_exit_grid_no_longer_depends_on_retired_type_b_taxonomy():
    assert '"trade_mfe_type"' not in ANALYZER
    assert "TYPE_B excluded" not in ANALYZER
    assert "TYPE_B excluded" not in DASHBOARD


def test_benchmark_report_has_no_dangling_retired_lane_status_helper():
    assert "_pathway_lane_status(" not in ANALYZER
    for retired_token in (
        "TYPE_B",
        "SR_MICRO",
        "A160",
        "AI60_SP3",
        "COMBO_65",
        "COMBO_604",
        "AI_DISAGREEMENT_REPLAY",
    ):
        assert retired_token not in ANALYZER
        assert retired_token not in DASHBOARD


def test_current_lane_catalog_cannot_re_admit_retired_data_rows():
    trades = pd.DataFrame([
        {"research_lane": "PROTECTED_W234_SCENARIO_C"},
        {"research_lane": "TYPE_B"},
    ])
    assert analyzer._ordered_lane_catalog(
        {"PROTECTED_W234_SCENARIO_C", "TYPE_B"}, trades
    ) == list(ACTIVE_TILE_ORDER)


def test_shadow_loader_requires_exact_registry_policy_identity(monkeypatch):
    lane = ACTIVE_TILE_ORDER[0]
    valid = ACTIVE_TILE_REGISTRY[lane]["raw_policy_id"]
    monkeypatch.setattr(analyzer, "_load_jsonl_rows", lambda _path: [
        {"research_lane": lane, "policy_version": valid, "ts": 1},
        {"research_lane": lane, "policy_version": "STALE_POLICY", "ts": 2},
        {
            "research_lane": ACTIVE_TILE_ORDER[1],
            "policy_version": "",
            "ts": 3,
        },
    ])
    frame = analyzer._load_shadow_lane_outcome_df()
    assert len(frame) == 1
    assert frame.iloc[0]["policy_version"] == valid
    assert frame.attrs["policy_mismatch_rows_excluded"] == 2
