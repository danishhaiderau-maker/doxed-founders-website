"""RK ruling 25 Oct EXIT_LAG arm gate (+ option (a)) and the live-protection checks (3)/(4)."""
from __future__ import annotations

import exit_lag_regression as elr
import live_copy_monitor as lcm
import live_guardian as g
import r17_exclusion as r17

S = elr.GATE_SCOPE_START_TS


def _row(tid, dec, rx=None, reason="TP", **extra):
    ev = {"exit_decision_ts": dec, "exit_fill_ts": dec + 0.5, "exit_trigger_ts": dec - 0.01,
          "exit_trigger_receive_ts": rx, "exit_trigger_receive_ts_source": "WS_FRAME" if rx else None, **extra}
    return {"trade_id": tid, "exit_reason": reason, "event_timestamps": repr(ev)}


def _xchain(cid, trig, fill, cls="EXCHANGE_RESIDENT"):
    return {"correlation_id": cid, "closed": True, "exit_order_class": cls,
            "exit_trigger_print_at_ts": trig, "exit_fill_at_ts": fill}


def test_scope_constant_is_the_868_deploy_with_a_reason():
    assert S == 1792854720.0 and "#868" in elr.GATE_SCOPE_REASON
    assert elr.gate_status(_row("p", S - 10, rx=S - 10.01)) == elr.PRE_DEPLOY
    assert elr.gate_status(_row("q", S + 10, rx=S + 9.99)) == elr.MEASURED


def test_admin_closes_excluded_noncountable_and_listed():
    for reason in ("ADMIN_MANUAL_CLOSE", "ADMIN_FORCE_FLAT", "DEPLOY_FORCE_FLAT"):
        r = _row("adm", S + 10, reason=reason)
        assert elr.gate_status(r) == elr.ADMIN_EXCLUDED
        assert elr.ADMIN_EXCLUDED_REASON in elr.reasons(r)
    gate = elr.rk_gate([_row("adm", S + 10, reason="ADMIN_FORCE_FLAT"), _row("m", S + 20, rx=S + 19.99)])
    assert gate["admin_excluded_ids"] == ["adm"] and gate["run_broken_by"] is None


def test_exchange_stop_is_exchange_filled_not_unmeasured_and_skipped():
    r = _row("xs", S + 10, reason="EXCHANGE_STOP_30BP")
    assert elr.gate_status(r) == elr.EXCHANGE_FILLED and elr.UNMEASURED not in elr.reasons(r)
    assert elr.reasons(r) == [elr.UNPROVEN_REASON]   # only option (a)'s UNPROVEN, no lag reason of its own
    gate = elr.rk_gate([_row("m", S + 5, rx=S + 4.99), r])
    assert gate["exchange_filled_ids"] == ["xs"] and gate["run_broken_by"] is None
    assert gate["consecutive_measured_unjudged"] == 1


def test_other_exit_without_receive_ts_breaks_the_run():
    gate = elr.rk_gate([_row("m1", S + 5, rx=S + 4.99), _row("u", S + 10), _row("m2", S + 20, rx=S + 19.99)])
    assert gate["run_broken_by"]["trade_id"] == "u" and gate["run_broken_by"]["why"] == "NO_RECEIVE_TS"
    assert elr.reasons(_row("u", S + 10))[0] == elr.UNMEASURED


def test_unproven_while_n_lt_10_no_gs01_block_and_noncountable(monkeypatch):
    rows = [_row(f"m{i}", S + 10 * i, rx=S + 10 * i - 0.01) for i in range(1, 30)]
    chains = [_xchain(f"x{i}", S + 100 * i, S + 100 * i + 0.2) for i in range(9)]
    gate = elr.rk_gate(rows, chains)
    assert gate["gate"] == elr.GATE_UNPROVEN and gate["arm_eligible"] is False
    assert gate["gs01_arm_blocked_by_exit_lag"] is False and gate["threshold_s"] is None
    assert gate["n_exchange_stops"] == 9 and gate["real_exchange_figure"]["status"] == "INSUFFICIENT_N"
    assert gate["real_exchange_figure"]["source_ids"] == [f"x{i}" for i in range(9)]
    assert 0.062 not in (gate["threshold_s"],)
    r = rows[0]
    assert elr.reasons(r) == [elr.UNPROVEN_REASON]
    r.update(non_countable=False)
    assert elr.UNPROVEN_REASON in r17.proof_failures(r)
    # 10 samples is only a CANDIDATE for HM: the gate stays UNPROVEN until the constant is set (and RK reviews)
    gate = elr.rk_gate(rows, chains + [_xchain("x9", S + 2000, S + 2000.3)])
    assert gate["n_exchange_stops"] == 10 and gate["real_exchange_figure"]["status"] == "CANDIDATE_FOR_HM"
    assert gate["gate"] == elr.GATE_UNPROVEN and gate["real_exchange_figure"]["max_s"] == 0.3


def test_real_exchange_samples_need_real_timestamps_and_epoch():
    chains = [_xchain("ok", S + 1, S + 1.2), _xchain("bot", S + 1, S + 1.2, cls="BOT_SENT"),
              {**_xchain("nofill", S + 1, None)}, _xchain("old", S - 100, S - 99), {**_xchain("open", S, S + 1), "closed": False}]
    assert [x["id"] for x in elr.real_exchange_stop_samples(chains, epoch_ts=S)] == ["ok"]


def test_pass_needs_20_consecutive_measured_at_or_under_real_figure(monkeypatch):
    monkeypatch.setattr(elr, "REAL_EXCHANGE_THRESHOLD_S", 0.05)
    monkeypatch.setattr(elr, "REAL_EXCHANGE_THRESHOLD_SOURCE_IDS", tuple(f"x{i}" for i in range(10)))
    ok = [_row(f"m{i}", S + 10 * i, rx=S + 10 * i - 0.04, exit_book_row_rule="LATEST") for i in range(1, 21)]
    gate = elr.rk_gate(ok)
    assert gate["gate"] == elr.GATE_PASS and gate["consecutive_measured_within_threshold"] == 20
    assert gate["arm_eligible"] and elr.reasons(ok[0]) == []
    slow = _row("slow", S + 500, rx=S + 499.9)
    gate = elr.rk_gate(ok + [slow])
    assert gate["gate"] == elr.GATE_FAIL and gate["run_broken_by"]["why"] == "LAG_ABOVE_REAL_EXCHANGE"
    assert elr.reasons(slow) == [elr.REASON]
    assert elr.rk_gate(ok[:19])["gate"] == elr.GATE_FAIL


def test_stop_fill_first_row_after_trigger_ok_else_violation():
    base = dict(exit_trigger_ts=S + 10.0, exit_book_receive_ts=S + 10.2)
    ok = {"trade_id": "s", "exit_reason": "HARD_STOP", "event_timestamps": repr({**base, "exit_book_row_rule": elr.FIRST_AFTER_RULE})}
    assert elr.stop_fill_row_check(ok)["status"] == elr.STOP_ROW_OK
    dec_row = {**ok, "event_timestamps": repr({**base, "exit_book_row_rule": "LATEST_ROW_AT_OR_BEFORE_FILL_TS"})}
    assert elr.stop_fill_row_check(dec_row)["status"] == elr.STOP_ROW_VIOLATION
    early = {**ok, "event_timestamps": repr({**base, "exit_book_receive_ts": S + 9.9, "exit_book_row_rule": elr.FIRST_AFTER_RULE})}
    assert elr.stop_fill_row_check(early)["reason"] == "BOOK_ROW_RECEIVED_BEFORE_TRIGGER"
    assert elr.stop_fill_row_check(_row("tp", S + 1))["status"] == elr.STOP_ROW_NA
    r = _row("hs", S + 20, rx=S + 19.99, reason="HARD_STOP", exit_book_row_rule="LATEST_ROW_AT_OR_BEFORE_FILL_TS")
    gate = elr.rk_gate([r])
    assert gate["stop_fill_row_violations"] == ["hs"] and gate["stop_fill_row_checks"][0]["status"] == elr.STOP_ROW_VIOLATION


def test_live_exit_stamp_check_bot_driven_only():
    full = _row("t", S + 10, rx=S + 9.99, exit_sent_ts=S + 10.01)
    full["exit_ack_at_ts"] = S + 10.2
    assert elr.live_exit_stamp_check(full) == {"applies": True, "missing": [], "ok": True}
    miss = _row("t2", S + 10, reason="TIME_EXIT")
    assert set(elr.live_exit_stamp_check(miss)["missing"]) == {"exit_trigger_receive_ts", "exit_sent_ts", "exit_ack_ts"}
    assert elr.live_exit_stamp_check(_row("x", S + 1, reason="EXCHANGE_STOP_30BP"))["applies"] is False


def _reports(stop):
    return [{"type": "ORDER_FILLED", "correlation_id": "gs1-a", "account": "A", "timeline": {"fill_at_ts": 100.0},
             "fill": {"price": 1.0, "qty": 1.0}},
            {"type": "STOP_CONFIRMED", "correlation_id": "gs1-a", "account": "A", "stop": stop}]


def test_stop_confirmed_requires_order_id_read_back():
    ch = next(iter(lcm.build_chains([], _reports({"price": 0.99}))["accounts"].values()))
    assert not ch.get("stop_confirmed") and ch["stop_unverified_report"]
    ch = next(iter(lcm.build_chains([], _reports({"price": 0.99, "exchange_order_id": 7}))["accounts"].values()))
    assert not ch.get("stop_confirmed")
    ch = next(iter(lcm.build_chains([], _reports({"price": 0.99, "exchange_order_id": 7, "verified_on_exchange": True}))["accounts"].values()))
    assert ch["stop_confirmed"] and ch["stop_order_id"] == "7"


def test_missing_or_unread_stop_flattens_and_is_red():
    assert {"STOP_MISSING_GT_5S", "STOP_PLACEMENT_FAILED", g.STOP_ORDER_ID_NOT_RESIDENT} <= g.RED_CODES
    now = 1_000.0
    c = {"correlation_id": "gs1-a", "lane": "GS01", "fill_at_ts": now - 60, "stop_confirmed": True, "stop_order_id": "7"}
    m = {"ok": True, "positions": [], "open_orders": [{"id": 8, "type": "STOP", "reduce_only": True}], "orphans": []}
    codes = {v["code"] for v in g.evaluate([c], m, {}, now)}
    assert g.STOP_ORDER_ID_NOT_RESIDENT in codes
    m["open_orders"][0]["id"] = 7
    assert g.STOP_ORDER_ID_NOT_RESIDENT not in {v["code"] for v in g.evaluate([c], m, {}, now)}
