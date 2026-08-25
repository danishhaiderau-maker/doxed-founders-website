from pathlib import Path
import re


SOURCE = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
ENGINE_SOURCE = (
    Path(__file__).parents[1] / "btc-signal-engine" / "engine.py"
).read_text(encoding="utf-8")
ANALYZER_SOURCES = [
    Path(__file__).with_name("analyzer_research_engine_v62.py").read_text(encoding="utf-8"),
    (Path(__file__).with_name("research") / "analyzer_research_engine_v62.py").read_text(encoding="utf-8"),
]
CANONICAL_ANALYZER_SOURCE = ANALYZER_SOURCES[0]
LEGACY_ANALYZER_SOURCE = ANALYZER_SOURCES[1]
DEMO_HARNESS_SOURCE = (
    Path(__file__).parents[2] / "scripts" / "demo-harness.mjs"
).read_text(encoding="utf-8")


def _route(name: str, next_marker: str) -> str:
    return SOURCE.split(f"def {name}(", 1)[1].split(next_marker, 1)[0]


def test_unauthenticated_mobile_analysis_redirects_to_login():
    route = _route("analyzer_mirror_dashboard", "@app.route('/analysis/login'")
    assert "_analyzer_view_authed()" in route
    assert "resp.headers['Location'] = '/analysis/login'" in route
    assert "Cache-Control" in route
    assert "resp.headers['Location'] = '/analysis/'" in route
    assert "def analyzer_mirror_dashboard_index" in route
    assert "def analyzer_mirror_artifact" in route


def test_fly_dashboard_link_uses_same_origin_mobile_analysis_route():
    helper = _route("research_dashboard_public_url", "DAILY_DRAWDOWN_PAUSE_USD")
    assert 'os.getenv("FLY_APP_NAME")' in helper
    assert 'return "/analysis"' in helper
    assert helper.index('os.getenv("FLY_APP_NAME")') < helper.index('return "http://127.0.0.1:9001/"')


def test_four_tile_dashboard_cards_do_not_expand_the_mobile_page():
    assert "#pathwayLaneTiles > div { min-width:0; max-width:100%; overflow:hidden; }" in SOURCE
    assert "#pathwayLaneTiles > div > div:first-child { flex-wrap:wrap; }" in SOURCE
    assert '#pathwayLaneTiles [style*="grid-template-columns:repeat(6,1fr)"]' in SOURCE
    assert "grid-template-columns:repeat(2,minmax(0,1fr)) !important" in SOURCE


def test_expired_order_hint_explains_zero_age_duplicate_rejection():
    assert "Age 0.0 with DUPLICATE_LIMIT_PRICE" in SOURCE
    assert "rejected before placement" in SOURCE
    assert "no paper or exchange order was cancelled" in SOURCE


def test_duplicate_suppression_requires_canonical_intent_not_price_proximity():
    canonical = _route("_canonical_duplicate_intent", "def _find_duplicate_limit_exposure")
    duplicate_scan = _route("_find_duplicate_limit_exposure", "def _reject_duplicate_limit_order")
    assert "CANONICAL_DUPLICATE_LIFECYCLE_WINDOW_SEC" in canonical
    assert 'incoming.get("shared_ai_call_id")' in canonical
    assert "price_distance > 0.01" in canonical
    assert "_limit_prices_near" not in duplicate_scan
    assert '"same_trade_replay": True' in duplicate_scan
    assert "CANONICAL_SIGNAL_REPLAY no-op" in SOURCE
    assert "duplicate_price_distance_usd" in SOURCE
    assert "duplicate_lifecycle_distance_sec" in SOURCE


def test_canonical_duplicate_match_rejects_only_a_replayed_intent():
    namespace = {"CANONICAL_DUPLICATE_LIFECYCLE_WINDOW_SEC": 5.0}
    function_source = "def _canonical_duplicate_intent(" + _route(
        "_canonical_duplicate_intent", "def _find_duplicate_limit_exposure"
    )
    exec(function_source, namespace)
    matcher = namespace["_canonical_duplicate_intent"]
    incoming = {
        "trade_id": "cont-new",
        "shared_ai_call_id": "scan-one",
        "created_ts_ts": 100.0,
    }
    replay = {
        "trade_id": "cont-old",
        "shared_ai_call_id": "scan-one",
        "created_ts_ts": 103.0,
    }
    assert matcher(incoming, replay, incoming_limit=63000.00, existing_limit=63000.00) == {
        "shared_ai_call_id": "scan-one",
        "lifecycle_distance_sec": 3.0,
        "price_distance_usd": 0.0,
    }
    # Nearby but independent signals are allowed; the broad price band must
    # not turn them into duplicates.
    independent = {**replay, "shared_ai_call_id": "scan-two"}
    assert matcher(incoming, independent, incoming_limit=63000.00, existing_limit=63000.01) == {}


def test_duplicate_audit_keeps_allowed_nearby_intents_in_the_denominator():
    audit = _route("_nearest_same_direction_exposure", "def _record_duplicate_intent_audit")
    recorder = _route("_record_duplicate_intent_audit", "def _reject_duplicate_limit_order")
    rejection = _route("_reject_duplicate_limit_order", "def _place_simulated_limit_order")
    assert '"price_distance_usd"' in audit
    assert '"same_shared_ai_call"' in audit
    assert '"lifecycle_distance_sec"' in audit
    assert '"pending_count"' in recorder
    assert '"open_count"' in recorder
    assert 'DUPLICATE_INTENT_AUDIT_FILE' in recorder
    assert '"ALLOW_DISTINCT"' in rejection
    assert '"NO_OP_SAME_TRADE_REPLAY"' in rejection
    assert '"SUPPRESS_CANONICAL_INTENT"' in rejection


def test_final_hard_stop_is_thirty_percent_margin_loss():
    assert "MAX_SL_MARGIN_PCT = 30.0" in SOURCE
    helper = _route("sl_price_pct", "SL_PCT = sl_price_pct")
    assert "MAX_SL_MARGIN_PCT / (lev * 100.0)" in helper
    strategy_lines = _route("_strategy_detail_lines", "def _annotate_lanes_with_exec_mode")
    assert "Final hard stop:" in strategy_lines
    assert "hard_stop_margin_pct" in strategy_lines


def test_execution_and_research_hard_stop_policy_stay_in_parity():
    def assigned_value(source: str, name: str) -> float:
        match = re.search(rf"^{name}\s*=\s*([0-9.]+)\s*$", source, re.MULTILINE)
        assert match is not None, f"missing {name}"
        return float(match.group(1))

    expected = assigned_value(SOURCE, "MAX_SL_MARGIN_PCT")
    assert expected == 30.0
    assert assigned_value(ENGINE_SOURCE, "MAX_SL_MARGIN_PCT") == expected
    assert assigned_value(CANONICAL_ANALYZER_SOURCE, "HARD_STOP_MARGIN_PCT") == expected
    assert "Hard SL margin cap: {HARD_STOP_MARGIN_PCT:g}%" in CANONICAL_ANALYZER_SOURCE


def test_legacy_analyzer_is_a_minimal_fail_closed_stub():
    assert 'Legacy research analyzer is disabled fail-closed.' in LEGACY_ANALYZER_SOURCE
    assert 'Run ../analyzer_research_engine_v62.py' in LEGACY_ANALYZER_SOURCE
    assert "pandas" not in LEGACY_ANALYZER_SOURCE
    assert "write_report_manifest" not in LEGACY_ANALYZER_SOURCE


def test_report_manifest_exposes_cohort_provenance_and_revision():
    manifest = CANONICAL_ANALYZER_SOURCE.split(
        "def write_report_manifest(", 1
    )[1].split("def _manifest_category", 1)[0]
    assert '"cohort_schema": "analysis_cohorts_v1"' in manifest
    assert '"generation_revision": generation_revision' in manifest
    assert '"included_row_count": len(eligible)' in manifest
    assert '"exclusion_reason_counts": exclusions' in manifest
    assert '"analysis_provenance": analysis_provenance' in manifest
    assert '"cohort_schema": analysis_provenance["cohort_schema"]' in manifest
    assert '"generation_revision": analysis_provenance["generation_revision"]' in manifest
    assert '"source_data_revision": analysis_provenance["source_data_revision"]' in manifest
    assert '"policy_comparability_key": analysis_provenance["policy_comparability_key"]' in manifest
    assert '"policy_comparability_status": analysis_provenance["policy_comparability_status"]' in manifest
    assert '"cohorts": analysis_provenance["cohorts"]' in manifest
    assert "def _stamp_report_analysis_provenance(" in CANONICAL_ANALYZER_SOURCE
    assert '"classification": "DESCRIPTIVE_UNQUALIFIED"' in CANONICAL_ANALYZER_SOURCE
    assert '"REPORT_NOT_COHORT_GATED"' in CANONICAL_ANALYZER_SOURCE
    assert 'report["source_data_revision"] = analysis_provenance["source_data_revision"]' in CANONICAL_ANALYZER_SOURCE
    assert 'report.setdefault("live_policy_change_allowed", False)' in CANONICAL_ANALYZER_SOURCE
    assert "manifest_started_at = datetime.now(timezone.utc)" in manifest
    assert '"generation_started_at": manifest_started_at.isoformat()' in manifest
    for current_generator in ("build_policy_cycle_reports(",):
        assert manifest.index("manifest_generated_at = datetime.now(timezone.utc)") > manifest.index(
            current_generator
        )


def test_demo_harness_runs_canonical_analyzer_and_propagates_failure():
    assert "join(BOT_DIR, 'analyzer_research_engine_v62.py')" in DEMO_HARNESS_SOURCE
    assert "join(BOT_DIR, 'research', 'analyzer_research_engine_v62.py')" not in DEMO_HARNESS_SOURCE
    assert "[analyzerPath, '--once']" in DEMO_HARNESS_SOURCE
    assert "canonical analyzer failed" in DEMO_HARNESS_SOURCE


def test_analyzer_session_is_opaque_and_narrowly_scoped():
    helper = _route("_analyzer_view_cookie_value", "def _analyzer_view_authed")
    assert "hmac.new(" in helper
    assert "doxxed-analyzer-view-v1" in helper
    assert "hashlib.sha256" in helper

    login = _route("analyzer_mirror_login", "@app.route('/analysis/logout'")
    assert "request.form.get('token', '')" in login
    assert "hmac.compare_digest(token, _BOT_ADMIN_TOKEN)" in login
    assert "analyzer_view_session" in login
    assert "httponly=True" in login
    assert "path='/analysis'" in login
    assert "autocomplete=\"current-password\"" in login
    assert "admin_token=" not in login
    assert "bot_admin_token" not in login


def test_analyzer_cookie_cannot_authorize_mutations():
    admin = _route("_admin_authed", "def _admin_authed_strict")
    guard = _route("_emergency_api_guard", "state_lock =")
    assert "analyzer_view_session" not in admin
    assert "analyzer_view_session" not in guard
    assert "_analyzer_view_authed" not in guard


def test_analysis_response_has_browser_hardening_headers():
    route = _route("analyzer_mirror_dashboard", "@app.route('/analysis/login'")
    assert "X-Content-Type-Options" in route
    assert "X-Frame-Options" in route
    assert "Referrer-Policy" in route
    assert "Cache-Control" in route
    assert "Content-Security-Policy" in route
    assert "default-src 'none'" in route


def test_snapshot_routes_resolve_only_the_atomically_selected_generation():
    resolver = _route("_active_analyzer_mirror_dir", "def _safe_analyzer_bundle_members")
    routes = _route("api_analyzer_mirror_status", "@app.route('/analysis/login'")
    assert "analyzer_current.json" in SOURCE
    assert 'status.get("complete") is True' in SOURCE
    assert "_recover_latest_analyzer_generation()" in resolver
    assert "_prune_analyzer_generations(generation)" in SOURCE
    assert "_active_analyzer_mirror_dir()" in routes
    assert "mirror = _analyzer_mirror_dir().resolve()" not in routes
    assert "Cache-Control" in routes


def test_mobile_snapshot_is_truthfully_labelled_and_uses_portable_report_links():
    assert "Local Analyzer Snapshot" in CANONICAL_ANALYZER_SOURCE
    assert "Read-only Fly mirror of the locally generated analyzer" in CANONICAL_ANALYZER_SOURCE
    assert "not the live interactive desktop dashboard" in CANONICAL_ANALYZER_SOURCE
    assert '.replace(os.sep, "/")' in CANONICAL_ANALYZER_SOURCE
