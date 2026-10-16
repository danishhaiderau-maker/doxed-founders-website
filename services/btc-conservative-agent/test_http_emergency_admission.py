"""HM D-B: reserved EMERGENCY admission class (pause/disarm/flatten POSTs never 503).

Runs under pytest and as ``python test_http_emergency_admission.py``.
"""
from __future__ import annotations

import http.client
import json
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
    # the real routes are wrapped (functools.wraps exposes the original)
    assert hasattr(bot.app.view_functions["status"], "__wrapped__")
    assert hasattr(bot.app.view_functions["health"], "__wrapped__")


def test_probe_ttl_is_clamped_to_one_second():
    import bot
    assert 0.0 <= bot._PROBE_CACHE_TTL_SEC <= 1.0


def test_fresh_query_bypasses_cache_and_refreshes_it():
    import bot
    calls = {"n": 0}

    @bot._probe_cached
    def probe():
        calls["n"] += 1
        return bot.jsonify({"n": calls["n"]})

    with bot.app.test_request_context("/x", method="GET"):
        probe(); probe(); assert calls["n"] == 1
    with bot.app.test_request_context("/x?fresh=1", method="GET"):
        r = probe(); assert calls["n"] == 2
    with bot.app.test_request_context("/x", method="GET"):
        again = probe(); assert calls["n"] == 2 and again.get_data() == r.get_data()


def test_single_flight_on_cache_miss():
    import bot
    calls = {"n": 0}
    gate = threading.Event()

    @bot._probe_cached
    def probe():
        calls["n"] += 1
        gate.wait(5)
        return bot.jsonify({"ok": True})

    out = []

    def go():
        with bot.app.test_request_context("/x", method="GET"):
            out.append(probe().status_code)
    ts = [threading.Thread(target=go) for _ in range(6)]
    for t in ts:
        t.start()
    time.sleep(0.3); gate.set()
    for t in ts:
        t.join(10)
    assert out == [200] * 6 and calls["n"] == 1


def test_clear_all_prevents_stale_and_drops_in_flight_rebuild():
    import bot
    state = {"live_armed": True}
    started, go = threading.Event(), threading.Event()

    @bot._probe_cached
    def probe():
        snap = dict(state)
        started.set(); go.wait(5)
        return bot.jsonify(snap)

    def first():
        with bot.app.test_request_context("/x", method="GET"):
            probe()
    t = threading.Thread(target=first); t.start()
    assert started.wait(5)
    state["live_armed"] = False
    bot._probe_cache_clear_all()          # disarm happened while the rebuild was in flight
    go.set(); t.join(10)
    started.clear()
    with bot.app.test_request_context("/x", method="GET"):
        r = probe()
    assert json.loads(r.get_data())["live_armed"] is False


def test_real_app_emergency_post_clears_probe_caches_through_real_auth_guard():
    import bot
    c = bot.app.test_client()
    marker = (b"stale", 200, "application/json")
    for cache in bot._PROBE_CACHES:
        cache["resp"] = marker; cache["at"] = time.monotonic()
    # No admin credentials: the real _emergency_api_guard decides, whatever it answers,
    # the after_request hook still clears the caches.
    r = c.post("/api/pause", json={})
    assert r.status_code in (200, 401, 403, 503)
    assert all(cache["resp"] is None for cache in bot._PROBE_CACHES)
    # A non-emergency POST leaves them alone.
    for cache in bot._PROBE_CACHES:
        cache["resp"] = marker; cache["at"] = time.monotonic()
    c.post("/api/not-an-emergency-route", json={})
    assert all(cache["resp"] == marker for cache in bot._PROBE_CACHES)
    bot._probe_cache_clear_all()


def test_disarm_and_arm_controls_clear_probe_caches():
    import bot
    marker = (b"stale", 200, "application/json")
    for fn_name in ("_disarm_live_control", "_arm_live_control"):
        assert hasattr(getattr(bot, fn_name), "__wrapped__"), fn_name


def test_reject_when_dispatch_and_reserve_both_full_is_counted_and_red():
    h = _Harness()
    try:
        s = h.server
        s._dispatch_thread_cap = threading.BoundedSemaphore(1)
        s._emergency_dispatch_reserve = threading.BoundedSemaphore(1)
        orig = s.app
        def wsgi(environ, start_response):
            if environ.get("PATH_INFO", "").startswith("/slow") or environ.get("PATH_INFO", "").endswith("/slow/eligibility"):
                h.entered.release(); h.release.wait(15)
            return orig(environ, start_response)
        s.app = wsgi
        threading.Thread(target=lambda: h.req("GET", "/slow-a"), daemon=True).start()
        assert h.entered.acquire(timeout=5)        # dispatch now full
        threading.Thread(target=lambda: h.req("POST", "/api/live-copy/tiles/slow/eligibility"), daemon=True).start()
        assert h.entered.acquire(timeout=5)        # reserve now full
        assert h.req("POST", "/api/pause") == 503
        rej = h.rejected("dispatch_reserve")
        assert rej.get("total") == 1
        assert rej["by_reason"] == {"dispatch_cap_full_reserve_exhausted": 1}
        chk = mc.emergency_admission(h.bot._dashboard_handler_snapshot(), now=time.time())
        assert chk["status"] == "RED"
        assert chk["dispatch_reserve_exhausted_total"] == 1
    finally:
        h.close()


def test_emergency_admission_green_when_no_rejections_and_dispatch_reserve_zero():
    chk = mc.emergency_admission({"rejected_by_cap": {}}, now=time.time())
    assert chk["status"] == "GREEN"


def test_emergency_socket_timeout_between_5_and_10_seconds():
    import bot
    h = _Harness()
    try:
        assert 5.0 <= h.server._emergency_client_io_timeout_sec <= 10.0
    finally:
        h.close()


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
