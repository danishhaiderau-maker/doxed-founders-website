from pathlib import Path


SOURCE = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")


def test_mobile_admin_login_uses_password_post_and_http_only_cookie():
    assert "@app.route('/admin/login', methods=['GET', 'POST'])" in SOURCE
    assert "request.form.get('token', '')" in SOURCE
    assert "hmac.compare_digest(token, _BOT_ADMIN_TOKEN)" in SOURCE
    assert "httponly=True, samesite='Lax'" in SOURCE
    assert "resp.headers['Location'] = '/'" in SOURCE
    assert 'autocomplete="on"' in SOURCE
    assert 'autocomplete="username"' in SOURCE
    assert 'autocomplete="current-password"' in SOURCE


def test_mobile_admin_login_never_reflects_token_into_redirect_url():
    route = SOURCE.split("def dashboard_admin_login():", 1)[1].split("@app.route('/')", 1)[0]
    assert "admin_token=" not in route
    assert "Location'] = '/'" in route


def test_dashboard_rejects_legacy_url_token_authentication():
    route = SOURCE.split("def dashboard():", 1)[1].split("def _relay_mirror", 1)[0]
    assert 'request.args.get("admin_token"' not in route
    assert "resp.headers['Cache-Control'] = 'no-store'" in route


def test_dashboard_exposes_login_status_and_secure_logout():
    assert '__ADMIN_ACCESS_CONTROLS__' in SOURCE
    assert 'Admin unlocked' in SOURCE
    assert 'Read-only' in SOURCE
    assert "@app.route('/admin/logout', methods=['POST'])" in SOURCE
    logout = SOURCE.split("def dashboard_admin_logout():", 1)[1].split("@app.route('/')", 1)[0]
    assert "resp.delete_cookie(" in logout
    assert "httponly=True" in logout
    assert "samesite='Lax'" in logout
