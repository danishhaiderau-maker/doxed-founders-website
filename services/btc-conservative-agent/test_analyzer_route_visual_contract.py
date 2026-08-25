import re
from datetime import datetime, timedelta, timezone

from research import research_dashboard as dashboard


def test_every_dashboard_navigation_item_has_renderable_section_scope_and_loader():
    """Static visual contract: clicking a visible subtab must never blank the page."""
    source = dashboard.DASHBOARD_HTML
    loader_block = source.split("const SECTION_LOADERS = {", 1)[1].split("};", 1)[0]
    scope_block = source.split("const EVIDENCE_SCOPES = {", 1)[1].split("};", 1)[0]

    for section_id, _label, _report in dashboard.REPORT_NAV:
        assert f'id="sec-{section_id}"' in source, section_id
        assert re.search(rf"(?:^|\s|')({re.escape(section_id)})'?\s*:", scope_block, re.MULTILINE), section_id
        assert re.search(rf"(?:^|\s|')({re.escape(section_id)})'?\s*:", loader_block, re.MULTILINE), section_id


def test_primary_analyzer_routes_and_links_render_without_dead_ends():
    client = dashboard.app.test_client()
    routes = {
        "/": b"Research Dashboard",
        "/safe-policy-genome-v3.1": b"Safe Policy Genome V3.1",
        "/static-policies": b"Static Profitable Policy Research",
        "/dynamic-policies": b"Dynamic Market-Regime Policy Research",
        "/shadow-research": b"Shadow and Rejected-Opportunity Research",
    }
    for route, marker in routes.items():
        response = client.get(route)
        assert response.status_code == 200, route
        assert marker in response.data, route

    root = client.get("/").get_data(as_text=True)
    for route in routes:
        if route != "/":
            assert f'href="{route}"' in root


def test_empty_safe_genome_state_is_truthful(monkeypatch):
    monkeypatch.setattr(dashboard, "_safe_policy_v3_dashboard_source", lambda: {"report": {}})
    monkeypatch.setattr(dashboard, "_read_report", lambda *args, **kwargs: {})
    dashboard._API_RESPONSE_CACHE.clear()
    client = dashboard.app.test_client()

    genome = client.get("/api/safe-policy-genome-v3.1").get_json()
    assert genome["status"] == "V3_REPORT_NOT_GENERATED"
    assert genome["qualification"] == "NO_SAFE_QUALIFIED_POLICY"
    assert genome["live_policy_change_allowed"] is False
    assert genome["real_bitfinex_trading_allowed"] is False
    assert "V3_REPORT_NOT_GENERATED" in genome["blockers"]

def test_analyzer_pages_keep_wide_evidence_inside_mobile_viewport():
    source = dashboard.DASHBOARD_HTML
    assert "main { padding: 20px 24px; width: 100%; max-width: 1200px; min-width: 0; overflow: hidden; }" in source
    assert "table { display: block; width: 100%; max-width: 100%; overflow-x: auto;" in source
    page = dashboard.app.test_client().get("/safe-policy-genome-v3.1").get_data(as_text=True)
    assert 'name="viewport"' in page


def test_chase_tables_label_every_rendered_metric_column():
    source = dashboard.DASHBOARD_HTML
    assert '<th>EV</th><th>Avg hold (min)</th></tr></thead><tbody id="chase-body">' in source
    assert '<th>EV</th><th>Avg hold (min)</th></tr></thead><tbody id="chase-threshold-body">' in source


def test_genome_overview_keeps_large_policy_grid_out_of_rendered_debug_text():
    source = dashboard.DASHBOARD_HTML
    assert "candidate_screen:cs" not in source
    assert "chase_families_materialized" in source
    assert "detailed_route: '/safe-policy-genome-v3.1'" in source


def test_pathway_audit_reads_contract_receipts_without_treating_them_as_current_analyzer_evidence(tmp_path, monkeypatch):
    old_generated = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    receipt = tmp_path / "tile_independence_report.json"
    receipt.write_text(
        '{"generated_at": "' + old_generated + '", "verdict": "PASS", "tests": []}',
        encoding="utf-8",
    )
    monkeypatch.setattr(dashboard, "ROOT", tmp_path)
    monkeypatch.setattr(dashboard, "_AGENT_ROOT", tmp_path)
    monkeypatch.setattr(dashboard, "DATA_ROOT", tmp_path)

    payload, status = dashboard._read_contract_receipt("tile_independence_report.json")

    assert payload["verdict"] == "PASS"
    assert status["status"] == "STALE_CONTRACT_RECEIPT"
    assert status["age_seconds"] >= 7200


def test_pathway_audit_reports_missing_receipt_truthfully(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "ROOT", tmp_path)
    monkeypatch.setattr(dashboard, "_AGENT_ROOT", tmp_path)
    monkeypatch.setattr(dashboard, "DATA_ROOT", tmp_path)

    payload, status = dashboard._read_contract_receipt("runtime_pathway_integrity.json")

    assert payload == {}
    assert status["status"] == "NOT_PUBLISHED"
