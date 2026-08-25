from pathlib import Path


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
        "partial-reduction",
        "loadPartialReductions",
    )
    for token in retired:
        assert token not in DASHBOARD
    assert 'CURRENT_RESEARCH_LANES = frozenset(("CONTINUOUS", "OFFSET_029_ATR_TP_25"))' in DASHBOARD
    assert "partial_reduction_reconciliation" not in ANALYZER


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
        "partial_reduction",
    ):
        assert retired_token not in ANALYZER
        assert retired_token not in DASHBOARD
