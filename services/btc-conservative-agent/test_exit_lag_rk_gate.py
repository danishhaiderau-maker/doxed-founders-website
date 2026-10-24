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


# --------------------------------------------------------------------------------------------------- HM R2 (a) / (b)
def _gate(**over):
    g_ = elr.rk_gate([])
    g_.update(over)
    return g_


def test_arm_block_reason_reads_the_flags_and_fails_closed():
    # UNPROVEN today: GS-01 allowed (option (a)), every other tile refused (arm_eligible False)
    g0 = elr.rk_gate([])
    assert g0["gate"] == "UNPROVEN" and elr.arm_block_reason(g0, "gs1") is None
    assert elr.arm_block_reason(g0, "gb1") == elr.ARM_BLOCK_NOT_ELIGIBLE
    # a blocked flag refuses GS-01
    assert elr.arm_block_reason(_gate(gs01_arm_blocked_by_exit_lag=True), "gs1") == elr.ARM_BLOCK_GS01
    assert elr.arm_block_reason(_gate(arm_eligible=True), "gb1") is None
    # unreadable -> refused
    for bad in (None, {}, "x", {"schema": "exit_lag_arm_gate_v1"}):
        assert elr.arm_block_reason(bad, "gs1") == elr.ARM_BLOCK_UNREADABLE
        assert elr.arm_block_reason(bad, "gb1") == elr.ARM_BLOCK_UNREADABLE
    assert elr.arm_block_reason(_gate(gs01_arm_blocked_by_exit_lag=None), "gs1") == elr.ARM_BLOCK_UNREADABLE
    assert elr.arm_block_reason(_gate(gs01_arm_blocked_by_exit_lag="false"), "gs1") == elr.ARM_BLOCK_UNREADABLE
    assert elr.arm_block_reason(_gate(arm_eligible=1), "gb1") == elr.ARM_BLOCK_UNREADABLE
    assert elr.arm_block_reason(g0, None) == elr.ARM_BLOCK_NOT_ELIGIBLE


def test_gs01_margin_cap_enforced():
    assert elr.GS01_MAX_NOTIONAL_USD == 0.25
    assert elr.gs01_size_block_reason("gs1", 0.25) is None
    assert elr.gs01_size_block_reason("gs1", 0.26) == elr.GS01_SIZE_BLOCK
    assert elr.gs01_size_block_reason("gs1", 25.0) == elr.GS01_SIZE_BLOCK
    for bad in (None, "", "x", 0, -1, float("nan")):
        assert elr.gs01_size_block_reason("gs1", bad) == elr.GS01_SIZE_UNREADABLE
    assert elr.gs01_size_block_reason("gb1", 5.0) is None   # cap is GS-01's (other tiles refused by arm_eligible)


def test_untimed_in_scope_exit_breaks_the_run():
    r = {"trade_id": "nots", "exit_reason": "TP", "event_timestamps": repr({"exit_trigger_receive_ts": None})}
    assert elr.gate_status(r) == elr.UNTIMED_STATUS
    assert elr.UNMEASURED in elr.reasons(r) and elr.UNPROVEN_REASON in elr.reasons(r)
    rows = [_row("m1", S + 10, rx=S + 9.99), r, _row("m2", S + 20, rx=S + 19.99)]
    gate = elr.rk_gate(rows)
    assert gate["run_broken_by"]["trade_id"] == "nots" and gate["run_broken_by"]["why"] == "NO_EXIT_TS"
    assert gate["consecutive_measured_unjudged"] == 0 and gate["counts"][elr.UNTIMED_STATUS] == 1
    # even with a proven threshold it can never pass
    import pytest
    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(elr, "REAL_EXCHANGE_THRESHOLD_S", 1.0)
        mp.setattr(elr, "REAL_EXCHANGE_THRESHOLD_SOURCE_IDS", tuple(f"x{i}" for i in range(10)))
        many = [_row(f"m{i}", S + 100 + i, rx=S + 99.99 + i) for i in range(30)] + [r]
        g2 = elr.rk_gate(many)
        assert g2["gate"] == "FAIL" and g2["run_broken_by"]["trade_id"] == "nots"
    finally:
        mp.undo()


def test_scope_reason_names_the_v724_release_and_keeps_0212():
    assert elr.GATE_SCOPE_START_TS == 1792854720.0
    assert "02:30:20" in elr.GATE_SCOPE_REASON and "v724" in elr.GATE_SCOPE_REASON and "02:12" in elr.GATE_SCOPE_REASON


def test_exchange_resident_class_needs_bot_provenance():
    base = _row("c", S + 10, reason="TP", exit_order_class="EXCHANGE_RESIDENT")
    assert elr.gate_status(base) == elr.UNMEASURED_STATUS     # class alone (no bot stamp) is NOT excluded
    bot_fill = _row("c", S + 10, reason="TP", exit_order_class="EXCHANGE_RESIDENT",
                    exit_stop_flags=["EXCHANGE_RESIDENT", "STOP_BASIS_UNVERIFIED"])
    race = _row("c", S + 10, reason="TP", exit_order_class="EXCHANGE_RESIDENT", exit_race_lost_by="TP")
    assert elr.gate_status(bot_fill) == elr.EXCHANGE_FILLED and elr.gate_status(race) == elr.EXCHANGE_FILLED


def test_only_bot_code_sets_exchange_resident():
    """HM R2 (b): on the PAPER rows the gate run reads, EXCHANGE_RESIDENT is assigned only by order_model / bot.py
    (exchange-stop fill + paper race), and is_exchange_stop also needs the bot's provenance stamp. The only non-bot
    emitter is the website's residentExitLegStamps (live resident-stop fill -> signed POSITION_CLOSED -> reporter
    whitelist -> live_copy_monitor whitelist): that reaches live CHAINS only (real_exchange_stop_samples candidate figure
    for HM, never a gate input), never the paper rows."""
    import pathlib
    import re
    here = pathlib.Path(__file__).resolve().parent
    repo = here.parent.parent
    pat = re.compile(r"""exit_order_class["']?\]?\s*(?::|(?<![=!<>])=(?!=))\s*[^\n=]*EXCHANGE_RESIDENT|EXCHANGE_RESIDENT\s*=\s*["']""")
    import os
    hits = set()
    skip_dirs = {"node_modules", ".git", "fixtures", "dist", "build", ".next", "__pycache__", ".venv", "venv"}
    for root, dirs, files in os.walk(repo, followlinks=False):   # pruned walk (never descends node_modules)
        dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]
        for name in files:
            if (not name.endswith((".py", ".ts", ".tsx", ".js", ".mjs")) or name.startswith("test_")
                    or ".test." in name or ".spec." in name):
                continue
            p = pathlib.Path(root) / name
            try:
                txt = p.read_text(errors="ignore")
            except OSError:
                continue
            if pat.search(txt):
                hits.add(str(p.relative_to(repo)))
    svc = "services/btc-conservative-agent/"
    web = "apps/api/src/trading-agents/signal-subscriber-execution.service.ts"
    assert hits <= {svc + "order_model.py", svc + "bot.py", web}, hits
    assert svc + "order_model.py" in hits and svc + "bot.py" in hits
    src = (here / "live_copy_monitor.py").read_text()
    assert 'if cl.get("exit_order_class") in ("BOT_SENT", "EXCHANGE_RESIDENT"):' in src
