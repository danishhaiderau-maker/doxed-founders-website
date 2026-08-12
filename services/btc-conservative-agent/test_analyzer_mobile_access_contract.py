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


def test_continuous_duplicate_band_is_fixed_twenty_dollars_without_percentage_fallback():
    helper = _route("_limit_prices_near", "def _signal_direction")
    duplicate_scan = _route("_find_duplicate_limit_exposure", "def _reject_duplicate_limit_order")
    assert "allow_percentage_fallback: bool = True" in helper
    assert "if not allow_percentage_fallback:" in helper
    assert "return False" in helper
    assert "RESEARCH_LANE_CONTINUOUS: 20.0" in SOURCE
    # The caller uses the explicit twenty-dollar Continuous policy rather than
    # the broad 0.25% BTC price-cluster fallback.
    assert "continuous_pair = lane == RESEARCH_LANE_CONTINUOUS" in duplicate_scan
    assert "allow_percentage_fallback=not continuous_pair" in duplicate_scan


def test_final_hard_stop_is_thirteen_percent_margin_loss():
    assert "MAX_SL_MARGIN_PCT = 13.0" in SOURCE
    helper = _route("sl_price_pct", "SL_PCT = sl_price_pct")
    assert "MAX_SL_MARGIN_PCT / (lev * 100.0)" in helper


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
