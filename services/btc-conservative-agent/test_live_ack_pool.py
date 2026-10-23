"""RK ruling A: POOLED_LIVE_ACK_CROSS_LANE - one shared pool (extends B1) for paper-only lanes.
Keys separate, exclusions, #847 gap rule (fail closed without it), n<10 fail closed / MAX / q0.90, own signal->send
added per row, stamps, per-epoch persistence + reset, and the r17 contract accepting only measured labels."""
import re
import sys
import types

import pytest

import live_ack_pool as lap
import paper_fill as pf
import r17_fields as F
from test_delay_pool_f1 import EPOCH, _fresh

BOOK = {"s": 999, "b": [[100.0, 1.0]], "a": [[101.0, 1.0]]}


def _book_at(t):
    return BOOK


def _no_miss(a, b):
    return 0


class _Log:
    def __init__(self, boot=False, readable=True):
        self.boot, self.r = boot, readable

    def readable(self, a, b):
        return self.r

    def boot_in(self, a, b):
        return self.boot


@pytest.fixture
def gd(monkeypatch):
    m = types.ModuleType("r17_gap_detector")
    m.state = {"log": _Log()}
    m.missing_seconds_are_a_hole = lambda n: n >= 3
    m.BootLog = types.SimpleNamespace(load=lambda paths: m.state["log"])
    monkeypatch.setitem(sys.modules, "r17_gap_detector", m)
    return m


def _entry(i, price=101.0, side="LONG", sent=1000.0, ack=1000.3, account="acct-A", **kw):
    # HM 01:43 blocker 1: the sample starts at the Fly intent (sent), Railway receives inside [intent, ack]
    return {"type": "ORDER_PLACED", "correlation_id": f"e{i}", "lane": "GS-01", "account": account, "schema": "v2",
            "order": {"type": "LIMIT", "tif": "GTC", "side": side, "price": price},
            "timeline": {"fly_intent_emitted_at_ts": sent, "railway_received_at_ts": sent + 0.01,
                         "order_sent_at_ts": sent + 0.02, "exchange_ack_at_ts": ack}, **kw}


def _close(i, reason="TP", xs=2000.0, xa=2000.2):
    return {"type": "POSITION_CLOSED", "correlation_id": f"x{i}", "lane": "GS-01", "account": "acct-A", "schema": "v2",
            "close": {"reason": reason}, "timeline": {"exit_sent_at_ts": xs + 0.05, "exit_ack_at_ts": xa}}


def _fly_exit(xs=2000.0, rtt=0.04, rx=None, **kw):
    """The bot's own Fly exit-send record (relay outbox ack): intent xs, POST at xs + 0.001, Railway rx inside RTT."""
    rec = {"fly_intent_at_ts": xs, "fly_send_started_at_ts": xs + 0.001, "fly_send_rtt_s": rtt,
           "railway_received_at_ts": xs + 0.02 if rx is None else rx, "attempts": 1}
    rec.update(kw)
    return lambda trade_id: rec


def _fill(pool, reports, now=10_000.0, fly_exit_send=None):
    for r in reports:
        pool.observe(r, book_at=_book_at, missing_seconds=_no_miss, fly_exit_send=fly_exit_send or _fly_exit())
    return pool.admit_pending(now, "boot.jsonl")


def test_keys_from_order_model():
    import order_model as om
    assert lap.key_for("entry", entry_order_model=om.ENTRY_MAKER_REST) == "MAKER_REST"
    assert lap.key_for("entry", entry_order_model=om.ENTRY_IOC_MARKET) == "ENTRY_MKT_GTC"
    assert lap.key_for("exit", exit_order_class=om.BOT_SENT) == "EXIT_RO_MKT"
    assert lap.key_for("exit", exit_order_class=om.EXCHANGE_RESIDENT) is None  # resident stop: no delay, never pooled


def test_marketable_vs_resting_kept_separate(gd):
    p = lap.CrossLanePool()
    assert _fill(p, [_entry(i, price=101.0) for i in range(10)] + [_entry(100 + i, price=100.5) for i in range(3)]) == 13
    assert p.pooled("ENTRY_MKT_GTC")["pool_n"] == 10
    assert p.pooled("MAKER_REST")["pool_n"] == 3
    assert p.pooled("EXIT_RO_MKT")["pool_n"] == 0


def test_stale_book_entry_not_pooled(gd):
    p = lap.CrossLanePool()
    p.observe(_entry(1, sent=1010.0, ack=1010.3), book_at=_book_at, missing_seconds=_no_miss)
    assert p.excluded == {"ENTRY_UNCLASSIFIED": 1}


@pytest.mark.parametrize("reason", ["ADMIN_FORCE_FLAT", "USER_RELAY_STOP", "DEPLOY_FLATTEN", "KILL_SWITCH",
                                    "RECONCILE_CLOSE", "MANUAL_OR_EXCHANGE_CLOSE", "EXCHANGE_STOP", "EXCHANGE_ALREADY_FLAT"])
def test_forced_admin_kill_reconcile_closes_excluded(gd, reason):
    p = lap.CrossLanePool()
    assert _fill(p, [_close(1, reason=reason)]) == 0
    assert p.pooled("EXIT_RO_MKT")["pool_n"] == 0 and list(p.excluded)[0].startswith("REASON:")


def test_gap_rule_flags_exclude(gd):
    p = lap.CrossLanePool()
    p.observe(_entry(1), book_at=_book_at, missing_seconds=lambda a, b: 5)
    p.observe(_entry(2), book_at=_book_at, missing_seconds=lambda a, b: None)
    gd.state["log"] = _Log(boot=True)
    _fill(p, [_entry(3)])
    gd.state["log"] = _Log(readable=False)
    _fill(p, [_entry(4)])
    assert p.excluded == {"C2:BOOK_HOLE": 1, "C2:BOOK_MISSING": 1, "C2:BOOT": 1, "C2:BOOT_LOG_MISSING": 1}
    assert p.pooled("ENTRY_MKT_GTC")["pool_n"] == 0


def test_boot_check_deferred_until_log_can_show_it(gd):
    p = lap.CrossLanePool()
    p.observe(_entry(1), book_at=_book_at, missing_seconds=_no_miss)
    assert p.admit_pending(1000.3 + 10, "boot.jsonl") == 0 and p.pooled("ENTRY_MKT_GTC")["pool_n"] == 0
    assert p.admit_pending(1000.3 + lap.BOOT_ADMIT_AFTER_S, "boot.jsonl") == 1


def test_without_847_gap_rule_nothing_is_admitted(monkeypatch):
    monkeypatch.setitem(sys.modules, "r17_gap_detector", None)  # import fails -> fail closed
    p = lap.CrossLanePool()
    assert _fill(p, [_entry(i) for i in range(12)]) == 0
    assert p.excluded == {"GAP_RULE_UNAVAILABLE": 12}


def test_rule_fail_closed_max_q90_and_own_s2s_added(gd):
    p = lap.CrossLanePool()
    _fill(p, [_entry(i, ack=1000.0 + 0.1 * (i + 1)) for i in range(9)])
    d, src, meta = p.total("ENTRY_MKT_GTC", 0.05)
    assert d == pf.DEFAULT_DELAY_S and "default" in src and pf.delay_stamp(src)["delay_unverified"]
    _fill(p, [_entry(9, ack=1001.0)])
    d, src, meta = p.total("ENTRY_MKT_GTC", 0.05)
    assert src == "POOLED_LIVE_ACK_CROSS_LANE:ENTRY_MKT_GTC_max_n10" and d == pytest.approx(1.05)
    _fill(p, [_entry(10 + i, ack=1000.2) for i in range(10)])
    d, src, meta = p.total("ENTRY_MKT_GTC", 0.05)
    assert src == "POOLED_LIVE_ACK_CROSS_LANE:ENTRY_MKT_GTC_q90_n20" and meta["quantile_method"] == pf.quantile_method_of(src)
    assert d == pytest.approx(0.05 + meta["pooled_send_to_ack_s"])
    d, src, _ = p.total("ENTRY_MKT_GTC", None)  # own signal->send not measured: fail closed
    assert d == pf.DEFAULT_DELAY_S and "default" in src and pf.delay_stamp(src)["delay_unverified"]


def test_two_accounts_without_config_fail_closed(gd):
    p = lap.CrossLanePool()
    _fill(p, [_entry(i) for i in range(10)] + [_entry(50 + i, account="acct-B") for i in range(10)])
    assert "default" in p.total("ENTRY_MKT_GTC", 0.1)[1]
    assert "_max_n10" in p.total("ENTRY_MKT_GTC", 0.1, ident="acct-A@v2")[1]


def test_r17_accepts_only_measured_pooled_labels():
    rx = re.compile(F.MEASURED_DELAY_PATTERN)
    assert rx.fullmatch("POOLED_LIVE_ACK_CROSS_LANE:EXIT_RO_MKT_q90_n25")
    assert rx.fullmatch("lane_max_n12")
    assert not rx.fullmatch("POOLED_LIVE_ACK_CROSS_LANE:ENTRY_MKT_GTC_default_until_n10_n4")
    assert not rx.fullmatch("POOLED_LIVE_ACK_CROSS_LANE:STOP_max_n12")
    assert all(rx.fullmatch(f"{lap.DELAY_SOURCE}:{k}_q90_n20") for k in lap.KEYS)


def test_stamp(gd):
    p = lap.CrossLanePool()
    _fill(p, [_close(i) for i in range(10)])
    d, src, _ = p.total("EXIT_RO_MKT", 0.3)
    row = p.stamp({"delay_s": d, "delay_source": src})
    assert row["delay_source_kind"] == "POOLED_LIVE_ACK_CROSS_LANE" and row["pool_n"] == 10
    assert row["pool_ts"] == 2000.2 and row["quantile_method"] == pf.quantile_method_of(src)
    assert row["own_signal_to_send_s"] == pytest.approx(0.3) and row["delay_unverified"] is False
    # HM 01:43 item 5: per-leg component stamps, no double count: total = own + pooled
    pooled = p.pooled("EXIT_RO_MKT")["pooled_send_to_ack_s"]
    assert row["sig_to_send_ms"] == 300 and row["pool_send_to_ack_ms"] == round(pooled * 1000)
    assert row["pool_def"] == lap.POOL_DEF and row["delay_span"] == lap.DELAY_SPAN and d == pytest.approx(0.3 + pooled)


def test_entry_sample_is_fly_intent_to_ack_not_order_sent(gd):
    p = lap.CrossLanePool()
    _fill(p, [_entry(i, sent=1000.0, ack=1000.5) for i in range(10)])
    assert p.pooled("ENTRY_MKT_GTC")["pooled_send_to_ack_s"] == pytest.approx(0.5)  # not 0.48 (order_sent->ack)


@pytest.mark.parametrize("tl,why", [
    ({"order_sent_at_ts": 1000.02, "exchange_ack_at_ts": 1000.3}, "FLY_SEND_TS_MISSING"),
    ({"fly_intent_emitted_at_ts": 1000.0, "exchange_ack_at_ts": 1000.3}, "RAILWAY_RECEIVE_TS_MISSING"),
    ({"fly_intent_emitted_at_ts": 1000.0, "railway_received_at_ts": 999.5, "exchange_ack_at_ts": 1000.3}, "CLOCK_SKEW"),
    ({"fly_intent_emitted_at_ts": 1000.0, "railway_received_at_ts": 1000.9, "exchange_ack_at_ts": 1000.3}, "CLOCK_SKEW"),
])
def test_entry_without_fly_stamp_or_with_skew_fails_closed(gd, tl, why):
    p = lap.CrossLanePool()
    r = _entry(1)
    r["timeline"] = tl
    assert _fill(p, [r]) == 0 and p.excluded == {why: 1}


@pytest.mark.parametrize("fly,why", [
    (lambda tid: None, "FLY_SEND_TS_MISSING"),  # no Fly exit-send stamp: EXIT_RO_MKT fails closed
    (_fly_exit(fly_send_started_at_ts=None), "FLY_SEND_TS_MISSING"),
    (_fly_exit(railway_received_at_ts=None), "RAILWAY_RECEIVE_TS_MISSING"),
    (_fly_exit(rx=2000.5), "CLOCK_SKEW"),  # Railway receive outside the POST round trip
])
def test_exit_needs_the_fly_exit_send_stamp(gd, fly, why):
    p = lap.CrossLanePool()
    assert _fill(p, [_close(1)], fly_exit_send=fly) == 0 and p.excluded == {why: 1}
    p2 = lap.CrossLanePool()
    p2.observe(_close(2), book_at=_book_at, missing_seconds=_no_miss)  # no lookup at all
    assert p2.excluded == {"FLY_SEND_TS_MISSING": 1}


def test_exit_sample_is_fly_exit_intent_to_exit_ack(gd):
    p = lap.CrossLanePool()
    _fill(p, [_close(i, xs=2000.0, xa=2000.3) for i in range(10)], fly_exit_send=_fly_exit(xs=2000.0))
    assert p.pooled("EXIT_RO_MKT")["pooled_send_to_ack_s"] == pytest.approx(0.3)


def test_v1_order_sent_pool_is_dropped_on_load(gd):
    p = lap.CrossLanePool()
    _fill(p, [_entry(i) for i in range(10)])
    blob = p.to_json(EPOCH)
    assert blob["pool_def"] == lap.POOL_DEF
    assert lap.CrossLanePool().load(blob, EPOCH) is True
    old = {k: v for k, v in blob.items() if k != "pool_def"}
    q = lap.CrossLanePool()
    assert q.load(old, EPOCH) is False and q.pooled("ENTRY_MKT_GTC")["pool_n"] == 0


def test_bot_paper_only_lane_uses_pool_persists_and_resets(gd, tmp_path, monkeypatch):
    monkeypatch.setattr(pf, "book10l_at", _book_at)
    monkeypatch.setattr(pf, "book10l_missing_seconds", _no_miss)
    ns = _fresh(tmp_path, monkeypatch)
    for i in range(10):
        ns["_paper_delay_observe_report"](_entry(i))
    pool = ns["_cross_lane_pool"]()
    assert pool.admit_pending(10_000.0, "boot.jsonl") == 10
    ns["_paper_delay_pool_persist"]()
    d, src = ns["_disloc_delay"]("GSB1", "entry", pool_key="ENTRY_MKT_GTC", own_signal_to_send_s=0.2)
    assert src == "POOLED_LIVE_ACK_CROSS_LANE:ENTRY_MKT_GTC_max_n10" and d == pytest.approx(0.5)
    row = ns["_paper_stamp_pool"]({"delay_s": d, "delay_source": src}, "GSB1", "entry")
    assert row["pool_key"] == "ENTRY_MKT_GTC" and row["pool_n"] == 10
    # restart: restored from the same per-epoch file
    ns2 = _fresh(tmp_path, monkeypatch)
    ns2["_paper_delay_sampler"]()
    assert ns2["_cross_lane_pool"]().pooled("ENTRY_MKT_GTC")["pool_n"] == 10
    ns2["_paper_delay_pool_reset"]()
    assert ns2["_cross_lane_pool"]().pooled("ENTRY_MKT_GTC")["pool_n"] == 0


def test_bot_fly_exit_send_record_and_paper_exit_own_s2s_fails_closed(gd, tmp_path, monkeypatch):
    ns = _fresh(tmp_path, monkeypatch)

    class _Box:
        def acknowledged_events(self, trade_id):
            return [{"event_type": "ORDER_PLACED"},
                    {"event_type": "POSITION_CLOSED", "fly_intent_at_unix": 2000.0, "fly_send_started_at_unix": 2000.001,
                     "fly_send_rtt_s": 0.04, "platform_received_at": "1970-01-01T00:33:20.020Z", "fly_attempts": 1}]
    monkeypatch.setitem(ns, "_relay_event_outbox", _Box())
    rec = ns["_fly_exit_send_record"]("x1")
    assert rec["fly_intent_at_ts"] == 2000.0 and rec["railway_received_at_ts"] == pytest.approx(2000.02)
    p = lap.CrossLanePool()
    assert _fill(p, [_close(1)], fly_exit_send=ns["_fly_exit_send_record"]) == 1
    # HM 01:43 item 4: the bare exit (_tp1_gate) no longer assumes own signal->send = 0.0 -> fail closed
    pool = ns["_cross_lane_pool"]()
    _fill(pool, [_close(10 + i) for i in range(10)])
    d, src = ns["_disloc_delay"]("GSB1", "exit")
    assert "default" in src and pf.delay_stamp(src)["delay_unverified"]
