"""Option 1 signed live-copy outbox delivers regardless of RELAY_STACK_MODE=research_only."""
import hashlib
import hmac
import types

import relay_stack_mode as mode_mod
from test_relay_stack_mode import load_functions


class Outbox:
    def __init__(self, rows):
        self.rows, self.acked, self.failed = rows, [], []

    def due(self, now):
        return list(self.rows)

    def ack(self, eid):
        self.acked.append(eid)

    def fail(self, eid, err):
        self.failed.append((eid, str(err)))


class Session:
    def __init__(self):
        self.posts = []

    def post(self, url, data=None, headers=None, timeout=None):
        self.posts.append(url)
        return types.SimpleNamespace(raise_for_status=lambda: None, content=b"{}",
                                     json=lambda: {"durable_ack": {"platform_received_at": "x"}})


class Switch:
    def snapshot(self, lane):
        return {"lane": lane, "bitfinex_live_orders": True}

    def delivery_gate(self, lane, created_at_unix=None, now=None):
        return True, None


def _run(*, output_on, source_enabled=True, research_only=True):
    rec = {"event_id": "gs1-a:ORDER_PLACED:1:lc", "created_at_unix": 1.0,
           "payload": {"event": "ORDER_PLACED", "trade_id": "gs1-a",
                       "research_lane": "FAMILY_GS01_XV_PREMIUM_ATR_TP"}}
    box, sess, denials = Outbox([rec]), Session(), []
    live_copy = types.SimpleNamespace(ENTRY_EVENTS={"ORDER_PLACED", "LIMIT_UPDATED"},
                                      delivery_check=lambda *a, **k: (True, None))
    import os
    os.environ["SHOWCASE_RELAY_WEBHOOK_URL"] = "https://example.invalid/relay"
    os.environ["SHOWCASE_WEBHOOK_SECRET"] = "s"
    ns = {
        "time": __import__("time"), "os": os, "hmac": hmac, "hashlib": hashlib,
        "RELAY_STACK_RESEARCH_ONLY": research_only, "_relay_stack_mode": mode_mod,
        "_get_live_copy_outbox": lambda: box, "_get_bfx_live_switch": lambda: Switch(),
        "_get_live_copy_output": lambda: types.SimpleNamespace(snapshot=lambda: {"enabled": output_on}),
        "_live_copy_source_enabled": lambda: source_enabled, "_live_copy": live_copy,
        "_record_bitfinex_delivery_denial": lambda lane, reason, eid=None: denials.append(reason),
        "RelayEventOutbox": types.SimpleNamespace(canonical_body=lambda p: b"{}"),
        "_relay_http_session": sess, "_relay_response_has_durable_receipt": lambda r, p: True,
        "_record_relay_delivery": lambda *a, **k: None,
        "_live_copy_last_delivery": {"skips": {}, "delivered": None},
    }
    load_functions(["_live_copy_deliver_once", "_note_live_copy_delivery_skip"], ns)
    ns["_live_copy_deliver_once"](now=10.0)
    return box, sess, denials, ns


def test_output_off_means_no_delivery():
    box, sess, denials, ns = _run(output_on=False)
    assert sess.posts == []
    assert denials == [mode_mod.LIVE_COPY_SKIP_OUTPUT_OFF]
    assert ns["_live_copy_last_delivery"]["skips"]["FAMILY_GS01_XV_PREMIUM_ATR_TP"]["reason"] == "LIVE_COPY_OUTPUT_OFF"


def test_source_disabled_means_no_delivery():
    _, sess, denials, _ = _run(output_on=True, source_enabled=False)
    assert sess.posts == [] and denials == [mode_mod.LIVE_COPY_SKIP_SOURCE_DISABLED]


def test_output_on_eligible_lane_delivers_under_research_only():
    box, sess, denials, ns = _run(output_on=True, research_only=True)
    assert sess.posts == ["https://example.invalid/relay"]
    assert box.acked == ["gs1-a:ORDER_PLACED:1:lc"] and denials == []
    assert ns["_live_copy_last_delivery"]["delivered"]["lane"] == "FAMILY_GS01_XV_PREMIUM_ATR_TP"


def test_policy_is_independent_of_relay_stack_mode():
    assert mode_mod.live_copy_delivery_allowed(output_on=True, source_enabled=True) == (True, None)
    assert mode_mod.research_only({"RELAY_STACK_MODE": "research_only"})


def test_legacy_relay_still_blocked_under_research_only():
    posts = []
    ns = load_functions(["_deliver_relay_outbox_record"], {
        "RELAY_STACK_RESEARCH_ONLY": True, "_relay_push_state": {},
        "_relay_event_outbox": object(), "os": __import__("os"),
        "_relay_http_session": types.SimpleNamespace(post=lambda *a, **k: posts.append(1)),
    })
    rec = {"event_id": "T-1:0", "payload": {"event": "ORDER_PLACED", "trade_id": "T-1"}}
    assert ns["_deliver_relay_outbox_record"](rec) is False and posts == []
