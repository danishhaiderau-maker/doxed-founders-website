from pathlib import Path

import pandas as pd

import analyzer_research_engine_v62 as analyzer
from combo_pathway_config import ACTIVE_TILE_ORDER, ACTIVE_TILE_REGISTRY


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
    protected = "OFFSET_029_ATR_PROTECTED"
    valid = ACTIVE_TILE_REGISTRY[protected]["raw_policy_id"]
    monkeypatch.setattr(analyzer, "_load_jsonl_rows", lambda _path: [
        {"research_lane": protected, "policy_version": valid, "ts": 1},
        {"research_lane": protected, "policy_version": "STALE_POLICY", "ts": 2},
        {"research_lane": "OFFSET_029_ATR_REGIME", "policy_version": "", "ts": 3},
    ])
    frame = analyzer._load_shadow_lane_outcome_df()
    assert len(frame) == 1
    assert frame.iloc[0]["policy_version"] == valid
    assert frame.attrs["policy_mismatch_rows_excluded"] == 2
