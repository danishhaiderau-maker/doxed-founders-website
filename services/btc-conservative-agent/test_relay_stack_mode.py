"""RELAY_STACK_MODE=research_only disables relay delivery/pusher/refresher by config only."""
import ast
import re
import threading
from pathlib import Path

import relay_stack_mode as mode_mod

HERE = Path(__file__).resolve().parent
BOT = HERE / "bot.py"
BOT_SRC = BOT.read_text(encoding="utf-8")


def load_functions(names, ns):
    tree = ast.parse(BOT_SRC)
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in nodes} == set(names)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "bot.py", "exec"), ns)
    return ns


def test_mode_parsing_fails_safe_to_active():
    assert mode_mod.mode({}) == "active"
    assert mode_mod.mode({"RELAY_STACK_MODE": "bogus"}) == "active"
    assert mode_mod.mode({"RELAY_STACK_MODE": " Research_Only "}) == "research_only"
    assert mode_mod.research_only({"RELAY_STACK_MODE": "research_only"})
    status = mode_mod.status({"RELAY_STACK_MODE": "research_only"})
    assert status["status"] == "RELAY_DISABLED_RESEARCH_ONLY"
    assert status["relay_delivery_enabled"] is False
    assert status["stale_owner_alarm_suppressed"] is True
    assert mode_mod.status({})["relay_delivery_enabled"] is True


def test_research_only_delivery_is_withheld_without_http_or_failure():
    calls = []

    class Outbox:
        def fail(self, *a, **k):
            calls.append(("fail", a))

        def acknowledge(self, *a, **k):
            calls.append(("ack", a))

    class Session:
        def post(self, *a, **k):
            calls.append(("post", a))
            raise AssertionError("research-only must not POST")

    push_state = {}
    ns = load_functions(["_deliver_relay_outbox_record"], {
        "RELAY_STACK_RESEARCH_ONLY": True, "_relay_push_state": push_state,
        "_relay_event_outbox": Outbox(), "_relay_http_session": Session(),
        "os": __import__("os"),
    })
    record = {"event_id": "T-1:0", "payload": {"event": "ORDER_PLACED", "trade_id": "T-1"}}
    assert ns["_deliver_relay_outbox_record"](record) is False
    assert ns["_deliver_relay_outbox_record"](record) is False
    assert calls == []
    assert push_state["research_only_withheld_total"] == 2


def test_research_only_keepalive_observes_locally_on_slow_cadence():
    drains, waits = [], []
    stop = threading.Event()

    class Shutdown:
        def is_set(self):
            return stop.is_set()

        def wait(self, delay):
            waits.append(delay)
            stop.set()

    ns = load_functions(["_platform_relay_connection_keepalive_loop"], {
        "RELAY_STACK_RESEARCH_ONLY": True, "shutdown_event": Shutdown(),
        "_relay_stack_mode": mode_mod,
        "_drain_relay_event_outbox_once": lambda *a, **k: drains.append("drain"),
        "_drain_partial_reduction_outbox_once": lambda: (_ for _ in ()).throw(AssertionError("no relay replay")),
        "_relay_event_outbox": None,
    })
    ns["_platform_relay_connection_keepalive_loop"]()
    assert drains == ["drain"]
    assert waits == [mode_mod.RESEARCH_ONLY_OUTBOX_OBSERVE_SEC]


def test_stale_owner_alarm_is_info_and_background_relay_refresher_is_gated():
    assert 'if not globals().get("RELAY_STACK_RESEARCH_ONLY", False) and _relay_delivery_guard.stale_owner_alarm(now):' in BOT_SRC
    start = BOT_SRC.index("def _start_api_state_cache_refresher")
    body = BOT_SRC[start:BOT_SRC.index("\ndef ", start + 10)]
    assert re.search(r"if not globals\(\)\.get\(\"RELAY_STACK_RESEARCH_ONLY\", False\):\n\s+# .*\n\s+threading\.Thread\(target=_relay_state_cache_refresher_loop", body)
    # The canonical execution snapshot stays (dashboard overlay + deploy flat checks read it).
    assert "threading.Thread(target=_relay_execution_cache_refresher_loop, daemon=True).start()" in body
    route = BOT_SRC[BOT_SRC.index("def api_relay_state("):]
    route = route[:route.index("\n@app.route")]
    assert 'if globals().get("RELAY_STACK_RESEARCH_ONLY", False):' in route and "api_relay_state(force_rebuild=True)" in route
    assert '"relay_stack": _relay_stack_mode.status(),' in BOT_SRC


def test_execution_refresh_is_throttled_not_removed_in_research_only():
    assert 'if RELAY_STACK_RESEARCH_ONLY and not os.getenv("RELAY_EXECUTION_REFRESH_INTERVAL_SEC"):' in BOT_SRC
    assert "_RELAY_EXECUTION_REFRESH_INTERVAL_SEC * 3," in BOT_SRC  # 5 s refresh -> 15 s stale fence
    assert mode_mod.RESEARCH_ONLY_EXECUTION_MAX_STALE_SEC >= 3 * mode_mod.RESEARCH_ONLY_EXECUTION_REFRESH_SEC


def test_fly_config_and_entrypoint_disable_the_pusher():
    toml = (HERE / "fly.toml").read_text(encoding="utf-8")
    assert re.search(r'^\s*RELAY_STACK_MODE = "research_only"$', toml, re.M)
    entry = (HERE / "fly-entrypoint.sh").read_text(encoding="utf-8")
    gate = entry.index('if [ "${RELAY_STACK_MODE:-active}" = "research_only" ]; then')
    pusher = entry.index("python /app/fly_relay_state_pusher.py")
    assert gate < pusher
    assert "elif [ -n \"${BOT_CONTROL_SECRET:-}\" ]; then" in entry[gate:pusher]
    # Disabled by config, not deleted.
    assert (HERE / "fly_relay_state_pusher.py").is_file()


def test_research_only_never_touches_live_flags():
    src = (HERE / "relay_stack_mode.py").read_text(encoding="utf-8")
    for forbidden in ("live_armed", "bitfinex_live_enabled", "API_KEY", "API_SECRET", "requests"):
        assert forbidden not in src
