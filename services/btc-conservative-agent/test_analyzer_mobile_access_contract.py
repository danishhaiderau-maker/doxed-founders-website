from pathlib import Path


SOURCE = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")


def _route(name: str, next_marker: str) -> str:
    return SOURCE.split(f"def {name}(", 1)[1].split(next_marker, 1)[0]


def test_unauthenticated_mobile_analysis_redirects_to_login():
    route = _route("analyzer_mirror_dashboard", "@app.route('/analysis/login'")
    assert "_analyzer_view_authed()" in route
    assert "resp.headers['Location'] = '/analysis/login'" in route
    assert "Cache-Control" in route


def test_fly_dashboard_link_uses_same_origin_mobile_analysis_route():
    helper = _route("research_dashboard_public_url", "DAILY_DRAWDOWN_PAUSE_USD")
    assert 'os.getenv("FLY_APP_NAME")' in helper
    assert 'return "/analysis"' in helper
    assert helper.index('os.getenv("FLY_APP_NAME")') < helper.index('return "http://127.0.0.1:9001/"')


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


def test_final_hard_stop_is_thirteen_percent_margin_loss():
    assert "MAX_SL_MARGIN_PCT = 13.0" in SOURCE
    helper = _route("sl_price_pct", "SL_PCT = sl_price_pct")
    assert "MAX_SL_MARGIN_PCT / (lev * 100.0)" in helper
    strategy_lines = _route("_strategy_detail_lines", "def _annotate_lanes_with_exec_mode")
    assert "Final hard stop:" in strategy_lines
    assert "hard_stop_margin_pct" in strategy_lines


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
