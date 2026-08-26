"""Focused regression for canonical V3.1 policy-dashboard adapters."""
from research import research_dashboard as dashboard


def _safe_source():
    report = {
        "schema": "safe_policy_genome_v3_1_report_v1",
        "generated_at": "2026-08-23T05:00:00+00:00",
        "epoch_id": "epoch-v31-clean",
        "qualification": "NO_SAFE_QUALIFIED_POLICY",
        "live_policy_change_allowed": False,
        "real_bitfinex_trading_allowed": False,
        "blockers": ["V3_EXECUTION_PATHS_NOT_MATURED"],
        "collection": {
            "independent_opportunities": 5,
            "decision_branches": 10,
            "terminal_lifecycles": 10,
            "execution_rows": 0,
            "market_segments": 0,
            "decision_outcomes": {"CENSORED": 5, "NO_TRADE": 5},
        },
        "candidate_screen": {
            "unique_policies_evaluated": 1,
            "descriptive_top_100": [{
                "policy_id": "v31-policy-1",
                "policy_family": "ATR_TARGET",
                "episodes_total": 5,
                "oos_episodes": 2,
                "oos_fills": 0,
                "sealed_oos_net_usd": None,
                "max_drawdown_usd": None,
                "gates": {"conservative_execution": False},
            }],
        },
        "safe_policy_ranking": {
            "qualification": "NO_SAFE_QUALIFIED_POLICY",
            "number_one": None,
        },
        "search": {
            "counts": {
                "entry_cartesian": 2700,
                "nominal_full_cartesian": 1106127912960000000,
            }
        },
        "search_progress": {
            "independent_episodes": 5,
            "unique_policies_evaluated": 1,
        },
        "number_one_strategy": None,
    }
    return {
        "report": report,
        "screen": report["candidate_screen"],
        "ranking": report["safe_policy_ranking"],
        "epoch_id": report["epoch_id"],
        "qualified": False,
        "blockers": report["blockers"],
    }


def test_best_top_and_shadow_share_canonical_v31_epoch_and_counts(monkeypatch):
    source = _safe_source()
    legacy_combo = {
        "adx_bucket": "30+", "spread_bucket": "5+", "entry_mode": "DIRECT",
        "lane": "CONTINUOUS", "trades": 3, "pnl_usd": 9.0, "ev_usd": 3.0,
    }
    monkeypatch.setattr(dashboard, "_safe_policy_v3_dashboard_source", lambda: source)
    monkeypatch.setattr(
        dashboard, "_read_report",
        lambda name, default=None: {"top": [legacy_combo], "dimensions": ["legacy"]}
        if name == "top_combinations_report.json" else (default or {}),
    )
    monkeypatch.setattr(dashboard, "_read_json", lambda _name, *args: args[0] if args else {})

    client = dashboard.app.test_client()
    best = client.get("/api/best-policy-research").get_json()
    combos = client.get("/api/combos").get_json()
    shadow = client.get("/api/shadow-policy-research").get_json()

    assert best["collector_generation"] == "V3.1"
    assert best["epoch_id"] == "epoch-v31-clean"
    assert best["evidence"]["independent_opportunities"] == 5
    assert "NO_CURRENT_V22_EPOCH" not in best["blockers"]
    assert best["current_candidate"] is None

    assert combos["current_evidence_source"] == "safe_policy_genome_v3_report.json"
    # A zero-fill policy remains available in the canonical V3.1 source but
    # must not be promoted into the public profitable shortlist.
    assert combos["top"] == []
    assert combos["policy_grid"]["rows"] == []
    assert combos["policy_grid"]["epoch_id"] == "epoch-v31-clean"
    assert combos["policy_grid"]["search_counts"]["entry_cartesian"] == 2700
    assert combos["policy_grid"]["search_counts"]["nominal_full_cartesian"] == 1106127912960000000
    assert combos["policy_grid"]["search_counts"]["independent_episodes"] == 5
    assert combos["legacy_executed_combos"]["rows"] == [legacy_combo]
    assert combos["legacy_executed_combos"]["status"].startswith("DESCRIPTIVE_LEGACY")

    assert shadow["collector_generation"] == "V3.1"
    assert shadow["epoch_id"] == "epoch-v31-clean"
    assert shadow["current_v3_1_collection"]["independent_opportunities"] == 5
    assert shadow["v22_shadow"] == {}
    assert shadow["legacy_v22_excluded"]["status"].startswith("RETIRED_V2_2")
    assert shadow["live_policy_change_allowed"] is False
