"""An invalid legacy v22 seal index degrades the bot instead of crash-looping it.

Regression for the 4 Oct 2026 07:49-08:35 AEDT loop: every boot raised
V22_SEAL_RECEIPT_INVALID:2 from _restore_collector_v22_provisionals and the
entrypoint restarted the bot 407 times.
"""
import ast
import logging
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
BOT_SRC = (HERE / "bot.py").read_text(encoding="utf-8")


def load(names, ns):
    tree = ast.parse(BOT_SRC)
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in nodes} == set(names)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "bot.py", "exec"), ns)
    return ns


def make_ns(merge):
    calls = []
    ns = {
        "time": time, "logger": logging.getLogger("t"), "_collector_v22_seal_degraded": {},
        "_collector_v22_last_merge": 0.0, "_merge_collector_v22_provisionals": merge,
        "_replay_preentry_evidence_handoffs": lambda: calls.append("preentry"),
        "_replay_cancellation_evidence_handoffs": lambda: calls.append("cancel"),
        "_replay_fill_evidence_handoffs": lambda: calls.append("fill"),
    }
    load(["_merge_collector_v22_provisionals_guarded", "_restore_collector_v22_provisionals"], ns)
    # The helpers read globals from their own namespace.
    ns["globals"] = lambda: ns
    return ns, calls


def test_startup_survives_invalid_seal_and_still_replays_handoffs():
    def boom(*, reason):
        raise RuntimeError("V22_SEAL_RECEIPT_INVALID:2")

    ns, calls = make_ns(boom)
    assert ns["_restore_collector_v22_provisionals"]() == 0
    degraded = ns["_collector_v22_seal_degraded"]
    assert degraded["error"] == "V22_SEAL_RECEIPT_INVALID:2"
    assert degraded["reason"] == "STARTUP" and degraded["occurrences"] == 1
    assert calls == ["preentry", "cancel", "fill"]
    # Repeated failure keeps the original since_ts and counts occurrences.
    since = degraded["since_ts"]
    ns["_merge_collector_v22_provisionals_guarded"](reason="POLL")
    assert ns["_collector_v22_seal_degraded"]["since_ts"] == since
    assert ns["_collector_v22_seal_degraded"]["occurrences"] == 2


def test_success_clears_degraded_state():
    ns, _ = make_ns(lambda *, reason: 3)
    ns["_collector_v22_seal_degraded"] = {"error": "V22_SEAL_RECEIPT_INVALID:2"}
    assert ns["_merge_collector_v22_provisionals_guarded"](reason="POLL") == 3
    assert ns["_collector_v22_seal_degraded"] == {}


def test_other_errors_still_propagate():
    def boom(*, reason):
        raise RuntimeError("SOMETHING_ELSE")

    ns, _ = make_ns(boom)
    with pytest.raises(RuntimeError, match="SOMETHING_ELSE"):
        ns["_restore_collector_v22_provisionals"]()


def test_degraded_state_is_a_collection_alarm_and_periodic_merge_is_guarded():
    assert 'alarms.append("COLLECTOR_V22_SEAL_DEGRADED")' in BOT_SRC
    start = BOT_SRC.index("def _schedule_collector_v22_provisional_merge")
    body = BOT_SRC[start:BOT_SRC.index("\ndef ", start + 10)]
    assert "_merge_collector_v22_provisionals_guarded(reason=reason)" in body
    assert "_merge_collector_v22_provisionals(reason=reason)" not in body


def test_entrypoint_backs_off_a_crash_loop_but_keeps_the_exit_marker():
    entry = (HERE / "fly-entrypoint.sh").read_text(encoding="utf-8")
    assert "[fly-entrypoint] bot exited rc=$rc" in entry  # v22_seal_repair.EXIT_MARKER parses this
    assert 'FAST_FAIL_THRESHOLD="${BOT_FAST_FAIL_THRESHOLD:-5}"' in entry
    assert 'MAX_DELAY_SEC="${BOT_RESTART_MAX_DELAY_SEC:-300}"' in entry
    assert "bot_crash_loop_v1" in entry
