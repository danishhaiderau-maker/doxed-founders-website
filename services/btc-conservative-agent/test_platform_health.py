from collections import deque
import platform_health as ph


def _snap(**kw):
    base = {"platform": "fly", "state": "up", "revision": "a", "boot_id": "b", "latency_ms": 100.0, "volumes": {}}
    base.update(kw)
    return base


def test_state_and_revision_change_alert():
    a = ph.diff_alerts(_snap(), _snap(state="down", revision="c"), deque(), 1.0)
    kinds = {x["kind"]: x["severity"] for x in a}
    assert kinds["state_change"] == "red" and kinds["revision_change"] == "info"


def test_latency_rise_alert():
    hist = deque([100.0] * 10)
    a = ph.diff_alerts(_snap(), _snap(latency_ms=2000.0), hist, 1.0)
    assert any(x["kind"] == "latency_rise" for x in a)
    assert not ph.diff_alerts(_snap(), _snap(latency_ms=150.0), hist, 1.0)


def test_probe_exception_is_unknown_not_crash(monkeypatch):
    def boom(session, ctx):
        raise RuntimeError("x")
    monkeypatch.setattr(ph, "PROBES", {"fly": boom})
    m = ph.PlatformMonitor()
    m.lat = {"fly": deque()}
    snap = m.run_check(None, {})
    assert snap["fly"]["state"] == "unknown"
    assert m.view()["alerts"][0]["kind"] == "error"


def test_no_credential_reported(monkeypatch):
    for k in ("NEON_API_KEY", "DATABASE_URL"):
        monkeypatch.delenv(k, raising=False)

    class S:
        def request(self, *a, **k):
            raise OSError("offline")
    out = ph.probe_neon(S(), {})
    assert out["storage"] == ph.NO_CRED and out["direct_connection"] == ph.NO_CRED
