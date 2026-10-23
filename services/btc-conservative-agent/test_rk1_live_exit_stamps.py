"""RK ruling (1), 24 Oct 2026: measured exit trigger / Fly send stamps on the ONE Fly->relay path (additive only)."""
import ast
import hashlib
import hmac
from pathlib import Path

import live_ack_pool as lap
import order_model as om
from relay_event_outbox import FLY_SEND_SIGNATURE_HEADER, FLY_SEND_STARTED_AT_HEADER, fly_send_stamp_headers

HERE = Path(__file__).resolve().parent


def test_fly_send_stamp_headers_sign_event_id_and_ts_like_railway():
    h = fly_send_stamp_headers("k", "e1", 1761300000.1234567)
    assert h[FLY_SEND_STARTED_AT_HEADER] == "1761300000.123457"
    want = hmac.new(b"k", b"e1|1761300000.123457", hashlib.sha256).hexdigest()
    assert h[FLY_SEND_SIGNATURE_HEADER] == f"sha256={want}"
    ts_src = (HERE.parent.parent / "apps/api/src/trading-agents/fly-send-stamp.ts").read_text()
    assert "`${eventId}|${sentAtRaw}`" in ts_src


def test_fly_send_stamp_headers_fail_closed_to_no_header():
    for args in (("", "e1", 1.0), ("k", "", 1.0), ("k", "e1", 0), ("k", "e1", None)):
        assert fly_send_stamp_headers(*args) == {}


def _src_fn(name):
    tree = ast.parse((HERE / "bot.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(name)


def _relay_close_exit_stamps():
    node = _src_fn("_relay_close_exit_stamps")
    mod = ast.Module(body=[node], type_ignores=[])
    ns = {"math": __import__("math")}
    exec(compile(mod, "bot.py", "exec"), ns)
    return ns["_relay_close_exit_stamps"]


def test_relay_close_exit_stamps_measured_or_omitted():
    f = _relay_close_exit_stamps()
    assert f({"exit_trigger_ts": 100.1234567}, "TP") == {"exit_order_class": om.BOT_SENT, "exit_trigger_ts": 100.123457}
    assert f({}, "TP") == {"exit_order_class": om.BOT_SENT}  # unmeasured -> omitted, never invented
    assert f({"exit_trigger_ts": float("nan")}, "TP") == {"exit_order_class": om.BOT_SENT}
    assert f({}, "EXCHANGE_STOP_25BP")["exit_order_class"] == om.EXCHANGE_RESIDENT
    assert f({"exit_order_class": om.EXCHANGE_RESIDENT}, "TP")["exit_order_class"] == om.EXCHANGE_RESIDENT


def test_bot_wires_stamps_on_the_one_relay_path():
    src = (HERE / "bot.py").read_text()
    assert "headers.update(_fly_send_stamp_headers(" in src
    assert "**_relay_close_exit_stamps(pos, exit_reason)" in src


def test_live_legs_resident_exit_requires_print_and_fill_record():
    entry = {"fly_signal_at_ts": 1.0, "fly_intent_emitted_at_ts": 1.1, "railway_received_at_ts": 1.2, "exchange_ack_at_ts": 1.3}
    r = lap.live_legs(entry, None, closed=True, exit_resident=True)
    assert r["non_countable"] is True
    assert "LIVE_LEG_MISSING:exit_resident.signal" in (r["non_countable_reasons"] or [])
    ok = lap.live_legs({**entry, "exit_trigger_print_at_ts": 5.0, "exit_fill_record_at_ts": 5.2}, None,
                       closed=True, exit_resident=True)
    assert ok["non_countable"] is False, ok


def test_fly_chain_carries_exit_order_class_and_exit_stamps_into_the_leg_check():
    """HM 02:55: exit_order_class production (Railway reporter -> Fly chain): a resident stop exit is classified as
    EXCHANGE_RESIDENT (print -> BFX fill record legs) instead of failing the bot-sent send legs."""
    import live_copy_monitor as lcm
    import live_vs_paper as lv
    tl = {"fly_signal_at_ts": 1.0, "fly_intent_emitted_at_ts": 1.1, "railway_received_at_ts": 1.2,
          "exchange_ack_at_ts": 1.3, "fill_at_ts": 1.4}
    rep = [{"type": "ORDER_FILLED", "correlation_id": "c1", "account": "a", "timeline": tl, "fill": {"price": 100.0}},
           {"type": "POSITION_CLOSED", "correlation_id": "c1", "account": "a",
            "timeline": {**tl, "exit_trigger_print_at_ts": 5.0, "exit_fill_record_at_ts": 5.2},
            "close": {"price": 99.0, "reason": "EXCHANGE_STOP_25BP", "exit_order_class": "EXCHANGE_RESIDENT"}}]
    appr = [{"correlation_id": "c1", "signal_at_ts": 1.0, "created_at_ts": 1.1, "entry_allowed": True}]
    ch = next(iter(lcm.build_chains(appr, rep)["accounts"].values()))
    assert ch["exit_order_class"] == "EXCHANGE_RESIDENT" and ch["exit_fill_record_at_ts"] == 5.2
    fund = {"funding_model": "LIVE_LEDGER_V1", "funding_status": "NO_CROSSING", "funding_verified": True,
            "funding_ts_fallbacks": []}  # #849: a closed live row also needs a verified funding read-back
    pc = lv.pair_countability(ch, None, live_funding=fund)
    assert pc["live_non_countable"] is False, pc
    assert lv.pair_countability(ch, None)["live_non_countable_reason"] == "LIVE_FUNDING_UNVERIFIED:NO_READBACK"
    rep[1]["timeline"].pop("exit_trigger_print_at_ts")  # print not carried -> non-countable (R17)
    ch = next(iter(lcm.build_chains(appr, rep)["accounts"].values()))
    assert lv.pair_countability(ch, None)["live_non_countable"] is True
