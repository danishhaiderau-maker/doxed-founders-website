"""Option 1 two-tier live copy: gate truth table, fail-closed, persistence,
signed intents, retired direct path, operator eligibility, outbox, monitor.

Nothing here touches an exchange, a network, or the real /app/data volume.
"""
from __future__ import annotations

import itertools
import json
import re
from pathlib import Path

import pytest

import live_copy_control as lc
import live_copy_monitor as mon
from bitfinex_live_switch import BitfinexLiveSwitch, OPERATOR_WAIVABLE_CAPABILITIES
from combo_pathway_config import ACTIVE_TILE_ORDER, ACTIVE_TILE_REGISTRY

HERE = Path(__file__).resolve().parent
SECRET = "test-secret-not-real"
LANE = ACTIVE_TILE_ORDER[0]


def spec(**over):
    base = {"platform_relay_eligible": False, "relay_capability": "BLOCKED_UNQUALIFIED",
            "policy_signature": "sig", "exit_policy": {"hard_stop_bps": 40}}
    base.update(over)
    return base


ON_OUT = {"enabled": True, "enabled_at_ts": 100.0}
ON_TILE = {"bitfinex_live_orders": True, "last_allow_ts": 110.0}
OK_EVAL = {"eligible": True, "denials": []}


# ---------------------------------------------------------------- truth table
@pytest.mark.parametrize("force_paper,out_on,tile_on,op_elig,ready,after", list(
    itertools.product([False, True], repeat=6)))
def test_entry_gate_truth_table(force_paper, out_on, tile_on, op_elig, ready, after):
    created = 200.0 if after else 105.0  # after output ON (100) and tile ON (110)?
    ok, reasons = lc.entry_gate(
        lane=LANE, created_at_ts=created, spec=spec(),
        output=ON_OUT if out_on else {"enabled": False},
        tile_row=ON_TILE if tile_on else {"bitfinex_live_orders": False},
        tile_eval=OK_EVAL if ready else {"eligible": False, "denials": ["X"]},
        force_paper_mode=force_paper, operator_eligible=op_elig)
    expected = (not force_paper) and out_on and tile_on and op_elig and ready and after
    assert ok is expected, reasons
    if not ok:
        assert reasons


def test_entry_gate_fail_closed_on_garbage():
    ok, reasons = lc.entry_gate(lane=LANE, created_at_ts=None, spec=None, output=None,
                                tile_row=None, tile_eval=None, force_paper_mode=False)
    assert not ok and lc.DENY_OUTPUT_OFF in reasons and lc.DENY_TILE_SWITCH_OFF in reasons


def test_partial_reduction_block_is_not_operator_waivable():
    ok, why = lc.effective_eligibility(spec(relay_capability="BLOCKED_PARTIAL_REDUCTION_UNPROVEN"), True)
    assert not ok and "PARTIAL" in why
    assert lc.effective_eligibility(spec(), True) == (True, None)
    assert lc.effective_eligibility(spec(), False)[0] is False
    assert lc.effective_eligibility({}, True)[0] is False
    assert OPERATOR_WAIVABLE_CAPABILITIES == lc.OPERATOR_WAIVABLE_CAPABILITIES


def test_liquidation_distance_100x_isolated_bitfinex():
    # 1/100 initial margin - 0.5% maintenance margin = 50 bp; cap = 50 - 15.
    assert lc.liquidation_distance_bp(100) == 50.0
    assert lc.exchange_stop_cap_bp(100) == 35.0
    assert lc.liquidation_distance_bp(50) == 150.0


def test_exchange_stop_is_catastrophe_backup_capped_inside_liquidation():
    # hard 40 + 25 = 65 bp is beyond the 50 bp liquidation -> capped at 35.
    plan = lc.exchange_stop_plan(spec())
    assert plan == {"hard_stop_bp": 40.0, "requested_bp": 65.0, "liquidation_bp": 50.0,
                    "cap_bp": 35.0, "exchange_stop_bp": 35.0, "cap_applied": True,
                    "inside_tile_hard_stop": True}
    assert lc.exchange_stop_bp_for_spec(spec()) == 35.0
    # a tight tile keeps hard + 25 when that is >= 15 bp inside liquidation
    tight = lc.exchange_stop_plan({"exit_policy": {"hard_stop_bps": 8}})
    assert tight["exchange_stop_bp"] == 33.0 and tight["cap_applied"] is False
    assert lc.exchange_stop_bp_for_spec({"exit_policy": {"hard_stop_bps": 10}}) == 35.0
    assert lc.exchange_stop_bp_for_spec({"exit_policy": {"hard_stop_bps": 30,
                                         "profiles": {"a": {"hard_bp": 40}}}}) == 35.0
    assert lc.exchange_stop_bp_for_spec({"exit_policy": {}}) is None
    assert lc.exchange_stop_bp_for_spec({"exit_policy": {"hard_stop_bps": 0}}) is None
    for lane in ACTIVE_TILE_ORDER:  # every active tile: computable and inside liquidation
        plan = lc.exchange_stop_plan(ACTIVE_TILE_REGISTRY[lane])
        assert plan is not None, lane
        assert plan["exchange_stop_bp"] <= plan["liquidation_bp"] - lc.LIQUIDATION_SAFETY_BP, lane
        assert plan["exchange_stop_bp"] == min(plan["hard_stop_bp"] + 25, 35.0), lane


# ------------------------------------------------- switch + operator eligibility
def _ga(**over):
    g = {"force_paper_mode": False, "live_armed": True, "bitfinex_live_enabled": True,
         "relay_delivery_block": None, "keys_ok": True,
         "exchange_audit": {"authoritative": True, "fresh": True, "flat": True},
         "market_ready": True, "system_ready": True, "manual_pause": False}
    g.update(over)
    return g


def test_switch_allowlist_needs_operator_eligibility(tmp_path):
    sw = BitfinexLiveSwitch(tmp_path / "sw.json", legacy_sidecar=tmp_path / "legacy.json")
    lane = next(l for l in ACTIVE_TILE_ORDER
                if ACTIVE_TILE_REGISTRY[l].get("relay_capability") == "BLOCKED_UNQUALIFIED")
    off = sw.evaluate(lane, global_arm=_ga(), size_checks={})
    on = sw.evaluate(lane, global_arm=_ga(), size_checks={}, operator_eligible=True)
    assert "LANE_NOT_ALLOWLISTED" in off["denials"] and "LANE_RELAY_CAPABILITY_BLOCKED" in off["denials"]
    assert not any("ALLOWLIST" in d or "CAPABILITY" in d for d in on["denials"]), on["denials"]


def test_switch_flat_gate_only_skipped_when_asked(tmp_path):
    sw = BitfinexLiveSwitch(tmp_path / "sw.json", legacy_sidecar=tmp_path / "legacy.json")
    ga = _ga(exchange_audit={"authoritative": True, "fresh": True, "flat": False})
    a = sw.evaluate(LANE, global_arm=ga, size_checks={}, operator_eligible=True)
    b = sw.evaluate(LANE, global_arm=ga, size_checks={}, operator_eligible=True,
                    exchange_flat_required=False)
    assert any("FLAT" in d for d in a["denials"])
    assert not any("FLAT" in d for d in b["denials"])
    stale = sw.evaluate(LANE, global_arm=_ga(exchange_audit={"fresh": False}), size_checks={},
                        exchange_flat_required=False)
    assert any("AUDIT" in d for d in stale["denials"])


def test_live_eligibility_default_off_persist_and_retired(tmp_path):
    p = tmp_path / "elig.json"
    e = lc.LiveEligibility(path=p)
    assert all(not e.is_eligible(l) for l in ACTIVE_TILE_ORDER)
    assert e.status()["eligible_lanes"] == []
    ok, row = e.set("NOT_A_TILE", True)
    assert not ok and row["error"] == "TILE_NOT_ACTIVE"
    assert not e.is_eligible("NOT_A_TILE")
    ok, _ = e.set(LANE, True)
    assert ok and lc.LiveEligibility(path=p).is_eligible(LANE)  # persisted config
    e.set(LANE, False)
    assert not lc.LiveEligibility(path=p).is_eligible(LANE)


def test_live_eligibility_fails_closed_when_unpersistable(tmp_path, monkeypatch):
    e = lc.LiveEligibility(path=tmp_path / "elig.json")
    monkeypatch.setattr(lc, "_atomic_write_json", lambda *a, **k: False)
    ok, row = e.set(LANE, True)
    assert not ok and not e.is_eligible(LANE)


# -------------------------------------------------------- output persistence
def test_output_default_off_and_restart_fails_closed(tmp_path):
    p = tmp_path / "out.json"
    o = lc.LiveCopyOutput(path=p)
    assert o.enabled is False
    o.set(True, by="t", reason="test")
    assert o.enabled and o.enabled_at_ts
    o2 = lc.LiveCopyOutput(path=p)  # process restart
    assert o2.enabled is False
    snap = o2.snapshot()
    assert lc.RESTART_RESET_REASON in json.dumps(snap)


def test_output_on_refused_when_unpersistable(tmp_path, monkeypatch):
    o = lc.LiveCopyOutput(path=tmp_path / "out.json")
    monkeypatch.setattr(lc, "_atomic_write_json", lambda *a, **k: False)
    o.set(True, by="t", reason="x")
    assert o.enabled is False


# --------------------------------------------------- signing / unsigned intents
def _approval(**over):
    a = lc.build_approval(event="ORDER_PLACED", trade_id="t-1", lane=LANE, spec=spec(),
                          output=ON_OUT, tile_row=ON_TILE, entry_allowed=True,
                          trade_approved_at_ts=200.0, created_at_ts=200.0, signal_at_ts=199.0,
                          operator_eligible=True)
    a.update(over)
    return lc.sign_approval(a, SECRET)


def test_unsigned_or_tampered_approval_rejected_at_delivery():
    payload = {"event": "ORDER_PLACED", "trade_id": "t-1", "live_copy_approval": _approval()}
    assert lc.delivery_check(payload, secret=SECRET, now=201, tile_row=ON_TILE, output=ON_OUT) == (True, None)
    unsigned = dict(payload, live_copy_approval={k: v for k, v in _approval().items() if k != "signature"})
    assert lc.delivery_check(unsigned, secret=SECRET, now=201, tile_row=ON_TILE, output=ON_OUT)[0] is False
    tampered = dict(payload, live_copy_approval=dict(_approval(), entry_allowed=True, max_margin_usd=99))
    assert lc.delivery_check(tampered, secret=SECRET, now=201, tile_row=ON_TILE, output=ON_OUT)[0] is False
    wrong_key = dict(payload, live_copy_approval=lc.sign_approval(_approval(), "other"))
    assert lc.delivery_check(wrong_key, secret=SECRET, now=201, tile_row=ON_TILE, output=ON_OUT)[0] is False
    assert lc.delivery_check({"event": "ORDER_PLACED", "trade_id": "t-1"}, secret=SECRET, now=1,
                             tile_row=ON_TILE, output=ON_OUT)[0] is False
    other_trade = dict(payload, trade_id="t-2")
    assert lc.delivery_check(other_trade, secret=SECRET, now=201, tile_row=ON_TILE, output=ON_OUT)[1] \
        == "APPROVAL_IDENTITY_MISMATCH"


def test_entry_withheld_when_switch_turned_off_after_emit():
    payload = {"event": "ORDER_PLACED", "trade_id": "t-1", "live_copy_approval": _approval()}
    assert lc.delivery_check(payload, secret=SECRET, now=201, tile_row={"bitfinex_live_orders": False},
                             output=ON_OUT) == (False, lc.DENY_TILE_SWITCH_OFF)
    assert lc.delivery_check(payload, secret=SECRET, now=201, tile_row=ON_TILE,
                             output={"enabled": False}) == (False, lc.DENY_OUTPUT_OFF)


def test_continuation_of_approved_trade_always_flows():
    a = lc.sign_approval(dict(_approval(), event="POSITION_CLOSED", entry_allowed=False), SECRET)
    p = {"event": "POSITION_CLOSED", "trade_id": "t-1", "live_copy_approval": a}
    assert lc.delivery_check(p, secret=SECRET, now=1, tile_row={}, output={"enabled": False})[0]


def test_stamp_or_block_first_entry_decides(tmp_path):
    dec = lc.TradeDecisions(path=tmp_path / "d.json")
    kw = dict(lane=LANE, signal_at_ts=None, spec=spec(), tile_row=ON_TILE, tile_eval=OK_EVAL,
              force_paper_mode=False, decisions=dec, secret=SECRET, operator_eligible=True)
    # Output OFF at first entry -> trade denied forever, continuations blocked.
    a, r = lc.stamp_or_block(event="ORDER_PLACED", trade_id="d-1", now=200, output={"enabled": False}, **kw)
    assert a is None and r
    a, r = lc.stamp_or_block(event="ORDER_PLACED", trade_id="d-1", now=300, output=ON_OUT, **kw)
    assert a is None
    a, r = lc.stamp_or_block(event="POSITION_CLOSED", trade_id="d-1", now=300, output=ON_OUT, **kw)
    assert a is None
    # Continuation without a decided entry never emits.
    assert lc.stamp_or_block(event="POSITION_OPENED", trade_id="d-x", now=300, output=ON_OUT, **kw)[0] is None
    # Approved trade.
    a, r = lc.stamp_or_block(event="ORDER_PLACED", trade_id="d-2", now=300, output=ON_OUT, **kw)
    assert a and lc.verify_approval(a, SECRET) and a["entry_allowed"] and a["order_type"] == "LIMIT"
    assert a["exchange_stop_bp"] == 35.0 and a["exchange_stop_role"] == "CATASTROPHE_BACKUP"
    assert a["liquidation_bp"] == 50.0 and a["exchange_stop_cap_applied"] is True
    assert a["exchange_stop_requested_bp"] == 65.0 and a["hard_stop_bp"] == 40.0
    assert a["exchange_stop_never_moved"] is True and a["eligibility_source"] == "OPERATOR"
    a2, _ = lc.stamp_or_block(event="POSITION_CLOSED", trade_id="d-2", now=400,
                              output={"enabled": False}, **kw)
    assert a2 and not a2["entry_allowed"] and a2["continuation"]
    # Decision survives restart.
    assert lc.TradeDecisions(path=tmp_path / "d.json").get("d-2")["approved"]
    # Missing secret -> nothing emitted.
    assert lc.stamp_or_block(event="ORDER_PLACED", trade_id="d-3", now=300, output=ON_OUT,
                             **{**kw, "secret": ""})[0] is None


def test_operator_ineligible_tile_never_stamps(tmp_path):
    dec = lc.TradeDecisions(path=tmp_path / "d.json")
    a, r = lc.stamp_or_block(event="ORDER_PLACED", trade_id="e-1", lane=LANE, now=300, signal_at_ts=None,
                             spec=spec(), output=ON_OUT, tile_row=ON_TILE, tile_eval=OK_EVAL,
                             force_paper_mode=False, decisions=dec, secret=SECRET, operator_eligible=False)
    assert a is None and lc.DENY_TILE_NOT_ELIGIBLE in r


# --------------------------------------------------------- protection evidence
def test_protection_flags_only_from_verified_stop(tmp_path):
    pe = lc.ProtectionEvidence(path=tmp_path / "p.json")
    assert not pe.flags()["stop_coverage_verified"]
    assert not pe.record_stop_confirmation({"type": "STOP_PLACED", "stop": {}})
    assert not pe.record_stop_confirmation({"type": "STOP_CONFIRMED", "stop": {
        "reduce_only": False, "exchange_order_id": "1", "verified_on_exchange": True, "price": 1, "qty": 1}})
    assert pe.record_stop_confirmation({"type": "STOP_CONFIRMED", "lane": LANE, "stop": {
        "reduce_only": True, "exchange_order_id": "1", "verified_on_exchange": True, "price": 1, "qty": 1}})
    f = lc.ProtectionEvidence(path=tmp_path / "p.json").flags(LANE)
    assert f["stop_coverage_verified"] and f["reduce_only_supported"]


def test_report_signature_and_validation():
    body = json.dumps({"a": 1}).encode()
    sig = lc.sign_report({"a": 1}, SECRET) if False else None  # report signs raw body below
    import hashlib, hmac
    good = hmac.new(lc.derive_key(SECRET, lc.REPORT_KEY_DOMAIN), body, hashlib.sha256).hexdigest()
    assert lc.verify_report_signature(body, good, SECRET)
    assert lc.verify_report_signature(body, "sha256=" + good, SECRET)
    assert not lc.verify_report_signature(body, "", SECRET)
    assert not lc.verify_report_signature(body + b" ", good, SECRET)
    ok, why = lc.validate_report({"schema": lc.REPORT_SCHEMA, "report_id": "r", "type": "ORDER_FILLED",
                                  "correlation_id": "c", "sent_at_ts": 10_000}, now=10_000 + 5000)
    assert not ok and why == "REPORT_STALE_OR_FUTURE"


# ----------------------------------------------------------------- outbox
def test_outbox_per_trade_order_ack_and_restart(tmp_path):
    p = tmp_path / "ob.json"
    ob = lc.LiveCopyOutbox(path=p, clock=lambda: 1000.0)
    r1 = ob.enqueue({"trade_id": "a", "event": "ORDER_PLACED"})
    r2 = ob.enqueue({"trade_id": "a", "event": "POSITION_OPENED"})
    ob.enqueue({"trade_id": "b", "event": "ORDER_PLACED"})
    heads = {r["event_id"] for r in ob.due()}
    assert r1["event_id"] in heads and r2["event_id"] not in heads and len(heads) == 2
    ob.fail(r1["event_id"], "boom")
    assert r1["event_id"] not in {r["event_id"] for r in ob.due(1000.0)}
    ob2 = lc.LiveCopyOutbox(path=p, clock=lambda: 2000.0)  # restart keeps pending
    assert ob2.status()["pending"] == 3
    assert ob2.ack(r1["event_id"])
    assert r2["event_id"] in {r["event_id"] for r in ob2.due()}
    assert r1["event_id"].endswith(":lc")


# ---------------------------------------------------------- retired direct path
def test_direct_entry_helpers_never_reach_exchange():
    import bitfinex_live_executor as bx
    calls = []
    retry = lambda fn, **k: calls.append(fn) or {"id": 1}  # noqa: E731

    class Ex:
        def create_order(self, *a, **k):
            calls.append(a)
            return {"id": 1}

    assert bx.DIRECT_ENTRY_PATH_RETIRED is True
    assert bx.submit_limit_entry(Ex(), retry, "tBTCF0:USTF0", "LONG", 0.001, 60000, 100, "x") is None
    assert bx.submit_market_entry(Ex(), retry, "tBTCF0:USTF0", "LONG", 0.001, 100, "x") is None
    assert calls == []


def test_bot_never_returns_direct_live_mode_and_arm_routes_retired():
    src = (HERE / "bot.py").read_text(encoding="utf-8")
    m = re.search(r"def execution_mode_for_lane\(.*?\n(?=def )", src, re.S)
    assert m and "return EXEC_MODE_LIVE" not in m.group(0)
    for fn in ("_maybe_bitfinex_limit_entry_locked", "_maybe_bitfinex_market_entry_locked"):
        body = re.search(rf"def {fn}\(.*?\n(.*?)\n", src, re.S).group(1)
        assert "Option 1" in body, fn
    for route in ("def live_arm():", "def api_bitfinex_live():"):
        seg = src[src.index(route): src.index(route) + 600]
        assert "_retired_direct_arm_response()" in seg
    # Reconcile never adopts or cancels on Fly's key account.
    loop = src[src.index("def bitfinex_live_reconcile_loop"):]
    loop = loop[: loop.index("\ndef ")]
    assert "rebuilt = None" in loop and "if False and not _bitfinex_live_active()" in loop


# ----------------------------------------------------------------- monitor
def test_monitor_chain_lags_gaps_and_summary():
    approvals = [{"correlation_id": "c1", "research_lane": LANE, "event": "ORDER_PLACED",
                  "entry_allowed": True, "signal_at_ts": 99.0, "created_at_ts": 100.0}]
    reports = [
        {"correlation_id": "c1", "account": "bitbro4crypto", "type": "ORDER_PLACED",
         "order": {"type": "LIMIT", "qty": 0.0004, "price": 60000},
         "timeline": {"railway_received_at_ts": 100.4, "order_sent_at_ts": 100.6,
                      "exchange_ack_at_ts": 101.0}},
        {"correlation_id": "c1", "account": "bitbro4crypto", "type": "ORDER_FILLED",
         "fill": {"price": 60030, "qty": 0.0004}, "fly_received_at_ts": 103.0,
         "timeline": {"fill_at_ts": 102.0}},
    ]
    chains = mon.build_chains(approvals, reports)
    ch = chains["accounts"][("c1", "bitbro4crypto")]
    assert ch["intent_to_ack_sec"] == 1.0
    gaps = mon.detect_gaps(chains, {"c1": {"entry_price": 60000, "side": "LONG", "qty": 0.0004}},
                           now=200.0, known_accounts=["bitbro4crypto", "other"])
    codes = {g["code"] for g in gaps}
    assert {"MISSING_STOP", "PRICE_DRIFT_VS_PAPER", "ACCOUNT_DID_NOT_COPY"} <= codes
    assert "FILL_NOT_REPORTED" not in codes and "INTENT_NO_ACCOUNT_ORDER" not in codes
    s = mon.summary(output={"enabled": False}, force_paper_mode=True, relay_stack_mode="research_only",
                    switch_rows=[], gaps=gaps, lags=mon.lag_stats(chains, since_ts=0),
                    executor_last_report_ts=None, website=None, rejects_1h=0,
                    unsigned_rejects_1h=1, now=200.0)
    assert s["verdict"] == "RED" and "MISSING_STOP" in s["causes"]
    assert "INTENT_REJECTED_UNSIGNED" in s["causes"] and s["state"] == "IDLE_DISARMED"


def test_monitor_idle_is_green():
    s = mon.summary(output={"enabled": False}, force_paper_mode=True, relay_stack_mode="research_only",
                    switch_rows=[{"lane": LANE, "bitfinex_live_orders": False}], gaps=[], lags={},
                    executor_last_report_ts=None, website=None, rejects_1h=0, unsigned_rejects_1h=0,
                    now=1.0)
    assert s["verdict"] == "GREEN" and s["state"] == "IDLE_DISARMED"


def test_new_modules_never_place_orders():
    for name in ("live_copy_control.py", "live_copy_monitor.py", "live_copy_monitor_api.py"):
        src = (HERE / name).read_text(encoding="utf-8")
        for forbidden in ("create_order", "submit_limit_entry", "submit_market_entry", "privatePostAuthWOrder"):
            assert forbidden not in src, (name, forbidden)


def test_website_tile_prefix_map_matches_active_registry():
    """The executor's lane->prefix map must equal Fly's active registry (no drift)."""
    import re
    from pathlib import Path
    ts = Path(__file__).resolve().parents[2] / "apps/api/src/trading-agents/live-copy-approval.ts"
    if not ts.exists():
        pytest.skip("website source not present (image build)")
    text = ts.read_text(encoding="utf-8")
    block = text[text.index("LIVE_COPY_TILE_PREFIXES"):]
    block = block[:block.index("});")]
    website = dict(re.findall(r"(FAMILY_[A-Z0-9_]+):\s*'([a-z0-9]+)'", block))
    fly = {lane: str(ACTIVE_TILE_REGISTRY[lane].get("id_prefix")) for lane in ACTIVE_TILE_ORDER}
    assert website == fly
