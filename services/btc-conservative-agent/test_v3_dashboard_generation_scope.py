import json
from pathlib import Path

from research import research_dashboard as dashboard


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _v3_report() -> dict:
    return {
        "schema": "safe_policy_genome_v3_report_v1",
        "status": "V3_COLLECTING",
        "qualification": "NO_SAFE_QUALIFIED_POLICY",
        "epoch_id": "epoch-v3-clean",
        "epoch_scope": {"contamination_detected": False},
        "integrity": {"passed": True},
        "collection": {
            "independent_opportunities": 4,
            "decision_branches": 8,
            "market_segments": 0,
            "decision_dispositions": {"ORDER_ELIGIBLE": 6, "REJECTED": 2},
        },
        "candidate_screen": {
            "descriptive_top_100": [],
            "unique_policies_evaluated": 0,
        },
        "search_progress": {
            "unique_policies_evaluated": 0,
            "nominal_full_cartesian": 123456,
        },
        "blockers": ["NO_SAFE_QUALIFIED_POLICY"],
        "number_one_strategy": None,
        "live_policy_change_allowed": False,
        "real_bitfinex_trading_allowed": False,
    }


def test_research_pages_prefer_clean_v3_and_hide_legacy(tmp_path, monkeypatch):
    dashboard._API_RESPONSE_CACHE.clear()
    v3_path = tmp_path / "safe_policy_genome_v3_report.json"
    conservative_path = tmp_path / "conservative_fill_descriptive_report.json"
    _write(v3_path, _v3_report())
    _write(conservative_path, {
        "schema": "conservative_fill_descriptive_cohort_v1",
        "epoch_id": "legacy-v2-epoch",
        "counts": {"events": 999, "fill": 999},
    })
    monkeypatch.setattr(dashboard, "SAFE_POLICY_GENOME_V3_REPORT_FILE", str(v3_path))
    monkeypatch.setattr(
        dashboard, "CONSERVATIVE_FILL_DESCRIPTIVE_REPORT_FILE", str(conservative_path)
    )

    client = dashboard.app.test_client()
    static = client.get("/api/static-policy-research").get_json()
    dynamic = client.get("/api/dynamic-policy-research").get_json()
    shadow = client.get("/api/shadow-policy-research").get_json()
    conservative = client.get("/api/conservative-fill-research").get_json()

    for payload in (static, dynamic, shadow, conservative):
        assert payload["epoch_id"] == "epoch-v3-clean"
        assert payload.get("live_policy_change_allowed") is False

    assert static["source_generation"] == "V3_SAFE_POLICY_GENOME"
    assert static["independent_episodes"] == 4
    assert static["profitable_policies"] == []
    assert dynamic["status"] == "WAITING_FOR_V3_REGIME_COVERAGE"
    assert dynamic["regimes"] == []
    assert shadow["current_epoch_rejected"] == 2
    assert shadow["v22_shadow"] == {}
    assert conservative["status"] == "WAITING_FOR_V3_MARKET_SEGMENTS"
    assert conservative["counts"]["events"] == 4
    assert conservative["counts"]["fill"] == 0
    dashboard._API_RESPONSE_CACHE.clear()


def test_contaminated_v3_does_not_override_fail_closed_legacy_path(tmp_path, monkeypatch):
    dashboard._API_RESPONSE_CACHE.clear()
    v3 = _v3_report()
    v3["epoch_scope"]["contamination_detected"] = True
    v3_path = tmp_path / "safe_policy_genome_v3_report.json"
    _write(v3_path, v3)
    monkeypatch.setattr(dashboard, "SAFE_POLICY_GENOME_V3_REPORT_FILE", str(v3_path))

    assert dashboard._current_v3_report() == {}
    dashboard._API_RESPONSE_CACHE.clear()
