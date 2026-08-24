import re

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
        "/partial-reduction-reconciliation": b"Partial Reduction Reconciliation",
    }
    for route, marker in routes.items():
        response = client.get(route)
        assert response.status_code == 200, route
        assert marker in response.data, route

    root = client.get("/").get_data(as_text=True)
    for route in routes:
        if route != "/":
            assert f'href="{route}"' in root


def test_empty_safe_genome_and_partial_reduction_states_are_truthful(monkeypatch):
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

    reductions = client.get("/api/partial-reduction-reconciliation").get_json()
    assert reductions["status"] == "BLOCKED"
    assert reductions["qualification"] == "INSUFFICIENT"
    assert reductions["live_copy_allowed"] is False
    assert reductions["lanes"] == {}
    assert reductions["integrity"]["passed"] is False
    assert "PARTIAL_REDUCTION_REPORT_NOT_GENERATED" in reductions["blockers"]


def test_partial_reduction_subtab_exposes_explicit_insufficiency_copy():
    source = dashboard.DASHBOARD_HTML
    assert 'id="sec-partial-reductions"' in source
    assert "No signed partial-reduction evidence is available yet. Live copy remains blocked." in source
    assert "'partial-reductions': [loadPartialReductions]" in source


def test_analyzer_pages_keep_wide_evidence_inside_mobile_viewport():
    source = dashboard.DASHBOARD_HTML
    assert "main { padding: 20px 24px; width: 100%; max-width: 1200px; min-width: 0; overflow: hidden; }" in source
    assert "table { display: block; width: 100%; max-width: 100%; overflow-x: auto;" in source
    page = dashboard.app.test_client().get("/partial-reduction-reconciliation").get_data(as_text=True)
    assert 'name="viewport"' in page
    assert "paper receipt evidence sufficient" in page
    assert "live evidence sufficient" not in page
