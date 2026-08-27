import json
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
    identity = {
        "generation_revision": "revision-current",
        "source_data_revision": "source-current",
        "session_scope": "FRESH-COLLECTION",
        "analysis_provenance": {"fresh_epoch_id": "epoch-current"},
    }
    reports = {
        "benchmark_vs_lanes_report.json": {
            **identity,
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
        "lane_pnl_ledger.json": {**identity, "lanes": {}},
        "lane_lab_pnl_ledger.json": {
            **identity,
            "lanes": {
                "CONTINUOUS": {
                    "closes": 9,
                    "net_pnl_usd": 3.75,
                }
            }
        },
    }
    manifest = {
        **identity,
        "fresh_epoch": {"epoch_id": "epoch-current"},
        "reports": [{"file": name} for name in reports],
    }
    monkeypatch.setattr(dashboard, "ROOT", tmp_path)
    monkeypatch.setattr(
        dashboard,
        "_read_json",
        lambda name, default=None: (
            manifest
            if name == dashboard.REPORT_MANIFEST_FILE
            else reports.get(name, default or {})
        ),
    )
    monkeypatch.setattr(
        dashboard,
        "_iter_data_payloads",
        lambda name: iter([(tmp_path / name, reports[name], 1.0)]) if name in reports else iter(()),
    )
    monkeypatch.setattr(dashboard, "_opportunity_lane_stats", lambda: {})

    rows, _ = dashboard._lane_rows()
    continuous = next(row for row in rows if row["lane"] == "CONTINUOUS")

    assert continuous["executed_closes"] == 0
    assert continuous["pnl"] == 0.0
    assert continuous["counterfactual_closes"] == 9
    assert continuous["counterfactual_pnl"] == 3.75


def test_current_lanes_fail_closed_on_stale_revision_epoch_and_scope(monkeypatch):
    manifest = {
        "generation_revision": "revision-current",
        "source_data_revision": "source-current",
        "session_scope": "FRESH-COLLECTION",
        "fresh_epoch": {"epoch_id": "epoch-current"},
        "reports": [
            {"file": "benchmark_vs_lanes_report.json"},
            {"file": "lane_pnl_ledger.json"},
            {"file": "lane_lab_pnl_ledger.json"},
        ],
    }
    stale = {
        "generation_revision": "revision-old",
        "source_data_revision": "source-old",
        "session_scope": "ALL-TIME",
        "analysis_provenance": {"fresh_epoch_id": "epoch-old"},
        "benchmark_lane": "CONTINUOUS",
        "lanes": {
            ACTIVE_TILE_ORDER[0]: {
                "approves": 9,
                "real_fills": 7,
                "net_pnl_real": -5.25,
                "per_approve_ev": -0.58,
            }
        },
    }
    monkeypatch.setattr(
        dashboard,
        "_read_json",
        lambda name, default=None: manifest if name == dashboard.REPORT_MANIFEST_FILE else (default or {}),
    )
    monkeypatch.setattr(
        dashboard,
        "_iter_data_payloads",
        lambda name: iter([(Path("published_reports") / name, stale, 1.0)]),
    )
    monkeypatch.setattr(dashboard, "_opportunity_lane_stats", lambda: {})

    payload = dashboard.app.test_client().get("/api/lanes").get_json()
    lane = next(row for row in payload["lanes"] if row["lane"] == ACTIVE_TILE_ORDER[0])

    assert payload["evidence_status"] == "UNAVAILABLE_CURRENT_GENERATION"
    assert payload["evidence"]["historical_fallback_used"] is False
    assert {
        "GENERATION_REVISION_MISMATCH",
        "SOURCE_DATA_REVISION_MISMATCH",
        "EPOCH_ID_MISMATCH",
        "SCOPE_MISMATCH",
    }.issubset(set(payload["evidence"]["benchmark"]["blockers"]))
    assert lane["executed_closes"] == 0
    assert lane["pnl"] == 0.0
    assert lane["all_time_fills"] == 0
    assert lane["all_time_pnl"] == 0.0


def test_current_lanes_never_reads_all_data_fallback(monkeypatch, tmp_path):
    manifest = {
        "generation_revision": "revision-current",
        "source_data_revision": "source-current",
        "session_scope": "FRESH-COLLECTION",
        "fresh_epoch": {"epoch_id": "epoch-current"},
        "reports": [{"file": "benchmark_vs_lanes_report.json"}],
    }
    all_data = tmp_path / dashboard.ALL_DATA_REPORTS_DIR
    all_data.mkdir(parents=True)
    (all_data / "benchmark_vs_lanes_report.json").write_text(
        '{"lanes":{"%s":{"real_fills":99,"net_pnl_real":42.0}}}'
        % ACTIVE_TILE_ORDER[0],
        encoding="utf-8",
    )
    monkeypatch.setattr(dashboard, "ROOT", tmp_path)
    monkeypatch.setattr(
        dashboard,
        "_read_json",
        lambda name, default=None: manifest if name == dashboard.REPORT_MANIFEST_FILE else (default or {}),
    )
    def all_data_only(name):
        path = all_data / name
        if path.is_file():
            return iter([(path, json.loads(path.read_text(encoding="utf-8")), 1.0)])
        return iter(())

    monkeypatch.setattr(dashboard, "_iter_data_payloads", all_data_only)
    monkeypatch.setattr(dashboard, "_opportunity_lane_stats", lambda: {})

    payload = dashboard.app.test_client().get("/api/lanes").get_json()
    lane = next(row for row in payload["lanes"] if row["lane"] == ACTIVE_TILE_ORDER[0])

    assert payload["evidence_status"] == "UNAVAILABLE_CURRENT_GENERATION"
    assert payload["evidence"]["historical_fallback_used"] is False
    assert lane["executed_closes"] == 0
    assert lane["pnl"] == 0.0


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
