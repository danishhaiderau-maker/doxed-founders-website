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
    return {"type": "ORDER_PLACED", "correlation_id": f"e{i}", "lane": "GS-01", "account": account, "schema": "v2",
            "order": {"type": "LIMIT", "tif": "GTC", "side": side, "price": price},
            "timeline": {"order_sent_at_ts": sent, "exchange_ack_at_ts": ack}, **kw}


def _close(i, reason="TP", xs=2000.0, xa=2000.2):
    return {"type": "POSITION_CLOSED", "correlation_id": f"x{i}", "lane": "GS-01", "account": "acct-A", "schema": "v2",
            "close": {"reason": reason}, "timeline": {"exit_sent_at_ts": xs, "exit_ack_at_ts": xa}}


def _fill(pool, reports, now=10_000.0):
    for r in reports:
        pool.observe(r, book_at=_book_at, missing_seconds=_no_miss)
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
    p.observe(_entry(1, sent=1010.0), book_at=_book_at, missing_seconds=_no_miss)
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
