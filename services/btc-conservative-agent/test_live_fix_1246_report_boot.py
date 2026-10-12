import bot

def test_signed_reports_pass_boot_guard(monkeypatch):
    monkeypatch.setattr(bot, "_DASHBOARD_BOOTSTRAP_COMPLETE", False)
    c = bot.app.test_client()
    r = c.post("/api/live-copy/execution-report", data=b"{}", headers={"X-Live-Report-Signature": "bad"})
    assert r.status_code != 503  # reaches the handler (signature check), not the restore 503
    assert c.get("/api/state").status_code == 503
