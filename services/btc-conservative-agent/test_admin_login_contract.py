from pathlib import Path


SOURCE = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")


def test_mobile_admin_login_uses_password_post_and_http_only_cookie():
    assert "@app.route('/admin/login', methods=['GET', 'POST'])" in SOURCE
    assert "request.form.get('token', '')" in SOURCE
    assert "hmac.compare_digest(token, _BOT_ADMIN_TOKEN)" in SOURCE
    assert "httponly=True, samesite='Lax'" in SOURCE
    assert "resp.headers['Location'] = '/'" in SOURCE


def test_mobile_admin_login_never_reflects_token_into_redirect_url():
    route = SOURCE.split("def dashboard_admin_login():", 1)[1].split("@app.route('/')", 1)[0]
    assert "admin_token=" not in route
    assert "Location'] = '/'" in route
