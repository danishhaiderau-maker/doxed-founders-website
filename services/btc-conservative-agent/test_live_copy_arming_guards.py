"""HM PR880 nit (b): behavioural pins required before arming live copy."""
import bot
import bitfinex_live_switch
import live_copy_control

AUTH = {"X-Bot-Admin-Token": "arm-test-token"}


def test_max_margin_usd_is_quarter_dollar():
    assert live_copy_control.MAX_MARGIN_USD == 0.25
    assert bitfinex_live_switch.MAX_MARGIN_USD == 0.25
    assert bot._live_copy.MAX_MARGIN_USD == 0.25


def test_paper_lock_truth_table(monkeypatch):
    for force, src, want in (("1", "", True), ("true", "0", True), ("1", "1", False),
                             ("", "", False), ("0", "1", False)):
        monkeypatch.setenv("FORCE_PAPER_MODE", force)
        monkeypatch.setenv("LIVE_COPY_SOURCE_ENABLED", src)
        assert bot._live_copy_paper_lock_active() is want, (force, src)


def test_output_on_refused_409_force_paper(monkeypatch, tmp_path):
    monkeypatch.setattr(bot, "_BOT_ADMIN_TOKEN", "arm-test-token")
    monkeypatch.setattr(bot, "_DASHBOARD_BOOTSTRAP_COMPLETE", True)
    monkeypatch.setattr(bot, "_API_RATE_LOG", type(bot._API_RATE_LOG)(bot._API_RATE_LOG.default_factory))
    monkeypatch.setenv("FORCE_PAPER_MODE", "1")
    monkeypatch.delenv("LIVE_COPY_SOURCE_ENABLED", raising=False)

    class _Out:
        enabled = False
        calls = []
        def snapshot(self): return {"enabled": self.enabled}
        def __getattr__(self, n):
            def f(*a, **k): _Out.calls.append(n)
            return f
    out = _Out()
    monkeypatch.setattr(bot, "_get_live_copy_output", lambda: out)
    r = bot.app.test_client().post("/api/live-copy/output", headers=AUTH,
                                   environ_base={"REMOTE_ADDR": "198.51.100.7"}, json={"enabled": True})
    assert r.status_code == 409 and r.get_json()["error"] == "FORCE_PAPER_MODE_ACTIVE"
    assert out.enabled is False and _Out.calls == []
