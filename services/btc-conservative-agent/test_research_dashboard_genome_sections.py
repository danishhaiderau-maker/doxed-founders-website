"""/details genome grid panel and the per-section JSON + health API (sections never collapse silently)."""
import json
import time

from research import dashboard_sections as ds
from research import research_dashboard as dashboard


def _grid(generated_at=None, **over):
    summary = {axis: {"distinct_values": need, "headline_fill_world": "REALISTIC_V1",
                      "values": [{"value": str(i), "best_policy_id": f"{axis}-{i}", "best_fill_world": "REALISTIC_V1"}
                                 for i in range(need)]}
               for axis, need in ds.GENOME_AXIS_MINIMUMS.items()}
    top = [{"policy_id": f"p{i}", "by_episode_class": {"AI_COMMITTED": {"fills": 3}}, "cluster_1h": {"all": {}}}
           for i in range(150)]
    rep = {"schema": "genome_grid_report_v1",
           "generated_at": generated_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "evidence_label": "SIMULATED_COUNTERFACTUAL", "holdout": {"min_oos_fills_for_rank": 10},
           "headline_fill_world": "REALISTIC_V1",
           "coverage": {"episodes_evaluated": 900, "evaluated_by_class": {"AI_COMMITTED": 400, "AI_NO_TRADE_SCORE_LED": 500}},
           "episode_integrity": {"status": "PASS", "episodes": 900, "unique_decision_ids": 900, "duplicate_decision_ids": 0,
                                 "classes": {"AI_COMMITTED": 400, "AI_NO_TRADE_SCORE_LED": 500}},
           "walk_forward_by_utc_day": {"AI_DECISION": {"folds": []}},
           "grid": {"policies_evaluated": 20000, "policies_ranked": 40},
           "canonical_parity": {"status": "MATCH", "checked": 400}, "dimension_summary": summary,
           "top_100_by_world": {"REALISTIC_V1": top, "OPTIMISTIC_TOUCH_SHADOW": []}}
    rep.update(over)
    return rep


def test_genome_grid_api_unavailable_then_ok(tmp_path, monkeypatch):
    path = tmp_path / "genome_grid_report.json"
    monkeypatch.setattr(dashboard, "GENOME_GRID_REPORT_PATH", path)
    client = dashboard.app.test_client()
    body = client.get("/api/genome-grid").get_json()
    assert body["status"] == "UNAVAILABLE"
    path.write_text(json.dumps(_grid()), encoding="utf-8")
    body = client.get("/api/genome-grid?limit=20").get_json()
    assert body["status"] == "OK" and body["evidence_label"] == "SIMULATED_COUNTERFACTUAL"
    assert len(body["top_100_by_world"]["REALISTIC_V1"]) == 20 and body["age_sec"] < 60
    assert body["episode_integrity"]["status"] == "PASS" and "walk_forward_by_utc_day" in body


def test_combos_section_loads_genome_grid_before_legacy_table():
    loaders = ds.section_loaders(dashboard.DASHBOARD_HTML)
    assert loaders["combos"][:2] == ["loadGenomeGrid", "loadCombos"]
    assert "/api/genome-grid" in ds.loader_apis(dashboard.DASHBOARD_HTML, "loadGenomeGrid")
    html = dashboard.app.test_client().get("/details").get_data(as_text=True)
    assert 'id="genome-grid-body"' in html and "SIMULATED" in html


def test_section_index_covers_every_details_section_and_core_pages():
    index = {s["id"]: s for s in dashboard._section_index()}
    nav = [sid for _g, _l, items in dashboard.REPORT_NAV_GROUPS for sid, _label, _file in items]
    assert set(nav) <= set(index)
    assert {"/api/genome-grid", "/api/combos"} <= set(index["combos"]["apis"])
    assert all(index[s]["core"] for s in ds.CORE_SECTIONS if s in index)
    assert all(s["json"].startswith("/api/sections/") for s in index.values())


def test_collapsed_top100_is_red_and_full_grid_is_green():
    legacy = {"dimensions": list(ds.LEGACY_COMBO_DIMENSIONS)}
    now = time.time()
    red = ds.genome_grid_checks({"status": "UNAVAILABLE"}, legacy, now)
    assert red[0]["severity"] == ds.RED
    green = ds.genome_grid_checks(_grid(), legacy, now, collected_opportunities=1200)
    assert {c["severity"] for c in green} == {ds.GREEN}
    collapsed = _grid()
    collapsed["dimension_summary"]["entry_offset_pct"].update(distinct_values=1, values=collapsed["dimension_summary"]["entry_offset_pct"]["values"][:1])
    stale = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 13 * 3600))
    checks = {c["id"]: c["severity"] for c in ds.genome_grid_checks(
        dict(collapsed, generated_at=stale, canonical_parity={"status": "MISMATCH"}), legacy, now, 5000)}
    assert checks == {"genome_grid_present": ds.GREEN, "genome_grid_fresh": ds.RED, "genome_axes_complete": ds.AMBER,
                      "genome_ranked_rows": ds.GREEN, "genome_engine_parity": ds.RED, "genome_vs_collected": ds.AMBER,
                      "genome_episode_integrity": ds.GREEN, "genome_axes_headline_world": ds.GREEN,
                      "genome_axes_distinct": ds.GREEN, "genome_top100_inference": ds.GREEN}


def test_safe_genome_window_and_empty_shortlist_are_amber():
    report = {"schema": "safe_policy_genome_v3", "status": "OK",
              "protection_replay_window": {"alert_level": "RED", "events_replayed": 100, "events_eligible": 2533},
              "candidate_screen": {"unique_policies_evaluated": 21280}}
    checks = {c["id"]: c["severity"] for c in ds.safe_genome_checks(report)}
    assert checks["safe_genome_replay_window"] == ds.AMBER and checks["safe_genome_shortlist"] == ds.AMBER
    assert ds.safe_genome_checks({})[0]["severity"] == ds.RED


def test_sections_health_flags_failed_and_empty_core_sections(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard, "GENOME_GRID_REPORT_PATH", tmp_path / "missing.json")
    index = [{"id": "combos", "label": "Top combos", "kind": "details_section", "core": True,
              "apis": ["/api/genome-grid", "/api/combos"]},
             {"id": "summary", "label": "Overview", "kind": "details_section", "core": True, "apis": ["/api/summary"]},
             {"id": "misc", "label": "Misc", "kind": "details_section", "core": False, "apis": ["/api/misc"]}]
    monkeypatch.setattr(dashboard, "_section_index", lambda: index)
    responses = {"/api/genome-grid": (200, dashboard._genome_grid_payload()),
                 "/api/combos": (200, {"dimensions": list(ds.LEGACY_COMBO_DIMENSIONS), "rows": []}),
                 "/api/summary": (500, None), "/api/misc": (200, {"rows": []})}
    monkeypatch.setattr(dashboard, "_section_fetch", lambda apis: {a: responses[a] for a in apis})
    monkeypatch.setattr(dashboard, "_collected_opportunity_count", lambda: 1000)
    monkeypatch.setattr(dashboard, "_safe_policy_v3_dashboard_source", lambda: {"report": {}})
    body = dashboard.app.test_client().get("/api/sections/health").get_json()
    by_id = {s["id"]: s for s in body["sections"]}
    assert body["verdict"] == ds.RED
    assert by_id["combos"]["severity"] == ds.RED  # legacy-only Top-100 without a genome grid
    assert any(c["id"] == "api_ok" and c["severity"] == ds.RED for c in by_id["summary"]["checks"])
    assert by_id["misc"]["severity"] == ds.INFO  # non-core empty sections inform, never page


def test_single_section_endpoint(monkeypatch):
    monkeypatch.setattr(dashboard, "_section_index", lambda: [
        {"id": "combos", "label": "Top combos", "apis": ["/api/combos"], "core": True}])
    monkeypatch.setattr(dashboard, "_section_fetch", lambda apis: {"/api/combos": (200, {"rows": [1]})})
    client = dashboard.app.test_client()
    body = client.get("/api/sections/combos").get_json()
    assert body["payloads"]["/api/combos"] == {"status": 200, "data": {"rows": [1]}}
    assert client.get("/api/sections/nope").status_code == 404

def test_endpoint_derivation_handles_url_variables_dynamic_prefixes_and_json_twins():
    html = ('<script>var U="/api/system-health";fetch(U)</script><script>const R = "/api/decision";'
            "fetch(`/api/report/${name}`); fetch('/api/streams/x?limit=5')</script>")
    assert ds.page_apis(html) == ["/api/decision", "/api/streams/x"]
    index = ds.section_index("", [], [("Data health", "/data-health"), ("Decision", "/decision")],
                             {"/data-health": "", "/decision": html}, routes=["/api/streams/data-health", "/api/decision"])
    by_path = {s["path"]: s for s in index}
    assert by_path["/data-health"]["apis"] == ["/api/streams/data-health"]
    assert by_path["/decision"]["apis"] == ["/api/decision", "/api/streams/x"]
    live = {s["id"]: s for s in dashboard._section_index()}
    assert "/api/streams/data-health" in live["page-data-health"]["apis"]
    assert not any(api.endswith("/") for s in live.values() for api in s["apis"])
