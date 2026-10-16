"""HM D-B: reserved EMERGENCY admission class (pause/disarm/flatten POSTs never 503).

Runs under pytest and as ``python test_http_emergency_admission.py``.
"""
from __future__ import annotations

import http.client
import os
import socket
import threading
import time

os.environ.setdefault("FORCE_PAPER_MODE", "1")
os.environ.setdefault("SKIP_EXCHANGE_MARKET_LOAD", "1")

import http_emergency_routes as her
import monitor_checks as mc


# ---------------------------------------------------------------- pure classifier
def test_emergency_routes_are_post_only_and_exact():
    for p in (b"/api/pause", b"/api/resume", b"/api/live_arm", b"/api/bitfinex_live",
              b"/api/positions/close", b"/api/live-copy/output", b"/api/live-copy/protection-bootstrap",
              b"/api/live-copy-coordination", b"/api/live-copy/executor-capability",
              b"/api/admin/emergency-wal/clear-alarms", b"/api/toggle_research_lane"):
        assert her.emergency_label(b"POST", p) == p.decode()
        assert her.emergency_label(b"GET", p) is None
    assert her.emergency_label(b"POST", b"/api/live-copy/tiles/B1/eligibility") == "/api/live-copy/tiles/*/eligibility"
    assert her.emergency_label(b"POST", b"/api/bitfinex/tiles/DISLOC/live-orders") == "/api/bitfinex/tiles/*/live-orders"
    assert her.emergency_label(b"POST", b"/api/live-copy/tiles/a/b/eligibility") is None
    assert her.emergency_label(b"POST", b"/api/live-copy/tiles//eligibility") is None


def test_status_health_and_wipe_routes_stay_out_of_emergency():
    for p in (b"/api/status", b"/status", b"/health", b"/ready", b"/api/ping", b"/api/state",
              b"/api/wipe_fly_only", b"/api/fresh_epoch_reset", b"/api/live-copy/execution-report"):
        for m in (b"GET", b"POST"):
            assert her.emergency_label(m, p) is None, (m, p)


def test_parse_request_line_strips_query_and_is_safe():
    assert her.parse_request_line(b"POST /api/pause?token=x HTTP/1.1\r\nHost: a\r\n") == (b"POST", b"/api/pause")
    assert her.parse_request_line(b"") == (b"", b"")
    assert her.parse_request_line(b"garbage") == (b"", b"")
    assert her.parse_request_line(None) == (b"", b"")
    assert her.emergency_label(*her.parse_request_line(b"post /api/pause HTTP/1.1\r\n")) == "/api/pause"


# ---------------------------------------------------------------- monitor check
def test_emergency_admission_check_is_red_on_any_rejection():
    now = 1_000_000.0
    ok = mc.emergency_admission({"rejected_by_cap": {"control": {"total": 70, "last_ts": now - 5}}}, now=now)
    assert ok["status"] == "GREEN" and ok["control_probe_rejected_total"] == 70
    bad = mc.emergency_admission({"rejected_by_cap": {"emergency": {"total": 1, "by_reason": {"class_cap_full": 1},
                                                                    "last_ts": now - 60}}}, now=now)
    assert bad["status"] == "RED" and bad["alert"] is True and bad["emergency_rejected_total"] == 1
    old = mc.emergency_admission({"rejected_by_cap": {"emergency": {"total": 1, "last_ts": now - 90000}}}, now=now)
    assert old["status"] == "GREEN"
    assert mc.emergency_admission(None, now=now)["status"] == "GREEN"


# ---------------------------------------------------------------- real server saturation
def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


class _Harness:
    def __init__(self):
        import bot
        self.bot = bot
        self.release = threading.Event()
        self.entered = threading.Semaphore(0)
        outer = self

        def wsgi(environ, start_response):
            path = environ.get("PATH_INFO", "")
            if path.startswith("/slow"):
                outer.entered.release()
                outer.release.wait(15)
            start_response("200 OK", [("Content-Type", "application/json")])
            return [b'{"ok":true}']

        self._saved = (bot.DASHBOARD_PORT, bot.DASHBOARD_BIND_HOST, bot._mount_research_segment_server,
                       bot._ensure_flask_port_available)
        self.port = _free_port()
        bot.DASHBOARD_PORT, bot.DASHBOARD_BIND_HOST = self.port, "127.0.0.1"
        bot._mount_research_segment_server = lambda _app: wsgi
        bot._ensure_flask_port_available = lambda _p: None
        self.server = bot._create_dashboard_server()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        with bot._dashboard_handler_lock:
            bot._dashboard_overload_rejections.clear()

    def hold(self, path, n):
        """Start n requests that block inside the app until release is set."""
        threads = []
        for _ in range(n):
            t = threading.Thread(target=lambda: self.req("GET", path), daemon=True)
            t.start(); threads.append(t)
        for _ in range(n):
            assert self.entered.acquire(timeout=5), "held request never reached the app"
        return threads

    def req(self, method, path):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        try:
            c.request(method, path, body=b"{}" if method == "POST" else None,
                      headers={"Content-Type": "application/json"})
            r = c.getresponse(); r.read(); return r.status
        finally:
            c.close()

    def close(self):
        self.release.set()
        self.server.shutdown(); self.server.server_close()
        (self.bot.DASHBOARD_PORT, self.bot.DASHBOARD_BIND_HOST, self.bot._mount_research_segment_server,
         self.bot._ensure_flask_port_available) = self._saved

    def rejected(self, cap):
        with self.bot._dashboard_handler_lock:
            return dict(self.bot._dashboard_overload_rejections.get(cap) or {})


def _sem_value(sem):
    n = 0
    got = []
    while sem.acquire(blocking=False):
        got.append(1); n += 1
    for _ in got:
        sem.release()
    return n


def test_pause_live_arm_and_close_survive_full_dispatch_and_class_caps():
    h = _Harness()
    try:
        s = h.server
        s._dispatch_thread_cap = threading.BoundedSemaphore(3)   # dispatch full after 3 held requests
        s._general_thread_cap = threading.BoundedSemaphore(3)
        h.hold("/slow-general", 3)
        assert _sem_value(s._dispatch_thread_cap) == 0
        # non-emergency requests are rejected, emergency POSTs are not
        assert h.req("GET", "/api/status") == 503
        assert h.req("GET", "/anything") == 503
        assert h.req("POST", "/api/pause") == 200
        assert h.req("POST", "/api/live_arm") == 200
        assert h.req("POST", "/api/positions/close") == 200
        assert h.req("POST", "/api/bitfinex_live") == 200
        assert h.req("POST", "/api/live-copy/tiles/B1/eligibility") == 200
        assert not h.rejected("emergency")
        assert h.rejected("unclassified").get("by_reason", {}).get("dispatch_cap_full", 0) == 0
        # reserve slots are all returned
        deadline = time.time() + 5
        while _sem_value(s._emergency_dispatch_reserve) != 4 and time.time() < deadline:
            time.sleep(0.05)
        assert _sem_value(s._emergency_dispatch_reserve) == 4
        assert _sem_value(s._emergency_thread_cap) == 4
    finally:
        h.close()


def test_emergency_not_starved_by_full_general_and_control_classes():
    h = _Harness()
    try:
        s = h.server
        s._general_thread_cap = threading.BoundedSemaphore(2)
        t = h.hold("/slow-g", 2)
        assert h.req("GET", "/dashboard") == 503                 # general full
        assert h.req("POST", "/api/pause") == 200                # emergency unaffected
        assert h.req("POST", "/api/resume") == 200
        assert not h.rejected("emergency")
    finally:
        h.close()


def test_emergency_rejection_is_counted_only_when_its_own_reserve_is_exhausted():
    h = _Harness()
    try:
        s = h.server
        s._emergency_thread_cap = threading.BoundedSemaphore(1)
        # hold the single emergency slot with a blocking emergency POST (path starts /slow? use eligibility pattern)
        results = []
        def slow_post():
            results.append(h.req("POST", "/api/live-copy/tiles/slow/eligibility"))
        # the stub only blocks on paths starting with /slow; make the app block this route too
        orig = s.app
        def wsgi(environ, start_response):
            if environ.get("PATH_INFO", "").endswith("/slow/eligibility"):
                h.entered.release(); h.release.wait(15)
            return orig(environ, start_response)
        s.app = wsgi
        t = threading.Thread(target=slow_post, daemon=True); t.start()
        assert h.entered.acquire(timeout=5)
        assert h.req("POST", "/api/pause") == 503
        assert h.rejected("emergency").get("total") == 1
        chk = mc.emergency_admission(h.bot._dashboard_handler_snapshot(), now=time.time())
        assert chk["status"] == "RED"
    finally:
        h.close()


def test_status_and_health_are_probe_cached_for_one_second():
    import bot
    calls = {"n": 0}

    @bot._probe_cached
    def probe():
        calls["n"] += 1
        return bot.jsonify({"n": calls["n"]}), 503

    with bot.app.test_request_context("/x", method="GET"):
        a = probe(); b = probe()
        assert calls["n"] == 1
        assert a.status_code == 503 and b.status_code == 503 and a.get_data() == b.get_data()
    with bot.app.test_request_context("/x", method="POST"):
        probe(); assert calls["n"] == 2
    time.sleep(1.1)
    with bot.app.test_request_context("/x", method="GET"):
        c = probe(); assert calls["n"] == 3
    src = open(os.path.join(os.path.dirname(__file__), "bot.py"), encoding="utf-8").read()
    assert "@_probe_cached\ndef status():" in src and "@_probe_cached\ndef health():" in src


def test_dispatch_reserve_is_wired_before_classification():
    import inspect
    src = inspect.getsource(__import__("bot")._create_dashboard_server)
    assert "_emergency_dispatch_reserve" in src and "dispatch_reserved and cap_name != \"emergency\"" in src
    assert src.index("_dispatch_thread_cap.acquire(blocking=False)") < src.index("_emergency_dispatch_reserve.acquire(blocking=False)")


if __name__ == "__main__":
    import sys
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print("ok  ", name)
            except Exception as e:  # noqa: BLE001
                fails += 1; print("FAIL", name, repr(e))
    sys.exit(1 if fails else 0)
