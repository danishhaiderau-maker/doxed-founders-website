"""AMEND4 final (sha 7073ed25) + HM N1/N3/N5 protection rules: one shared implementation (live_tile_gate), R3 book prices only."""
from __future__ import annotations

import json
import logging
import pathlib
import time as _t

import live_tile_gate as ltg
import live_guardian as g

NOW = 1792800000.0 + 43200.0   # 12:00 UTC


def book(age=0.5, bid=100000.0, ask=100010.0, size=1.0):
    return {"s": NOW - age - 1.0, "b": [[bid, size]], "a": [[ask, size]]}


def mirror(positions=(), orders=(), age=1.0):
    return {"ok": True, "read_age_s": age, "fetched_at_ts": NOW, "positions": list(positions), "open_orders": list(orders),
            "orphans": [], "balance": {"derivatives_available_usd": 206.0}}


LONG = {"symbol": "tBTCF0:USTF0", "amount": 0.0001, "base_price": 100500.0, "liq_price": 90000.0, "liq_read_at": NOW - 1}
STOP_L = {"type": "STOP", "reduce_only": True, "price": 100000.0 * (1 + 5 / 1e4)}


def codes(res, key="disarm"):
    return [(v["code"], v.get("cause")) for v in res[key]]


def test_book_age_and_full_size_exit_walk_use_the_shared_helper():
    assert abs(ltg.book_age_s(book(0.5), NOW) - 0.5) < 1e-9 and ltg.book_age_s(None, NOW) is None
    w = ltg.book_exit_walk("LONG", 0.5, {"s": NOW - 1.5, "b": [[100.0, 0.2], [99.0, 1.0]], "a": [[101.0, 1.0]]}, NOW)
    assert w["ok"] and abs(w["vwap"] - (0.2 * 100 + 0.3 * 99) / 0.5) < 1e-9          # exit LONG sells into the bid, full size
    assert ltg.book_exit_walk("SHORT", 0.5, {"s": NOW - 1.5, "b": [], "a": [[101.0, 1.0]]}, NOW)["vwap"] == 101.0
    assert ltg.book_exit_walk("LONG", 5.0, book(), NOW)["error"] == "PARTIAL_DEPTH"


def test_e7_stale_book_blocks_entries_even_when_flat():
    assert [b["code"] for b in ltg.open_position_protection(mirror(), book(2.5), NOW)["block"]] == [ltg.AMBER_BOOK_STALE]
    assert ltg.open_position_protection(mirror(), book(1.0), NOW)["block"] == []


def test_e7_hold_with_resident_stop_and_book_le_10s_then_flatten_beyond():
    r = ltg.open_position_protection(mirror([LONG], [STOP_L]), book(8.0), NOW)
    assert r["disarm"] == [] and [b["code"] for b in r["block"]] == [ltg.AMBER_BOOK_STALE]
    r = ltg.open_position_protection(mirror([LONG], [STOP_L]), book(10.5), NOW)
    assert (ltg.STOP_FAILURE_FLATTEN, "BOOK_STALE_GT_10S") in codes(r)
    r = ltg.open_position_protection(mirror([LONG], [STOP_L]), None, NOW)
    assert (ltg.STOP_FAILURE_FLATTEN, "BOOK_STALE_GT_10S") in codes(r)


def test_e6_stop_residency_missing_or_unconfirmed_flattens():
    assert (ltg.STOP_FAILURE_FLATTEN, "STOP_NOT_RESIDENT") in codes(ltg.open_position_protection(mirror([LONG]), book(), NOW))
    assert ltg.open_position_protection(mirror([LONG]), book(), NOW, fill_grace=True)["disarm"] == []
    old = mirror([LONG], [STOP_L], age=10.5)
    assert (ltg.STOP_FAILURE_FLATTEN, "STOP_RESIDENCY_UNCONFIRMED") in codes(ltg.open_position_protection(old, book(), NOW))
    # a non-reduce-only STOP is not a protective stop
    assert (ltg.STOP_FAILURE_FLATTEN, "STOP_NOT_RESIDENT") in codes(
        ltg.open_position_protection(mirror([LONG], [dict(STOP_L, reduce_only=False)]), book(), NOW))


def test_zero_position_rows_are_ignored():
    assert ltg.open_position_protection(mirror([dict(LONG, amount=0)]), book(), NOW)["disarm"] == []


def test_a2_overshoot_bp_sign_positive_is_worse():
    assert ltg.overshoot_bp("LONG", 100.0, 99.9) == 10.0 and ltg.overshoot_bp("SHORT", 100.0, 100.1) == 10.0
    assert ltg.overshoot_bp("LONG", 100.0, 100.1) == -10.0 and ltg.overshoot_bp("LONG", None, 1.0) is None


def test_e1_open_mark_to_book_counts_against_the_cap_and_unreadable_blocks():
    pos = dict(LONG, base_price=100600.0, amount=0.001)    # walk at bid 100000 => loss 0.6 USD
    blocks, info = ltg.daily_cap_entry_block([], mirror([pos], [STOP_L]), book(), NOW, "gs1")
    assert {b["code"] for b in blocks} >= {ltg.VIOL_TILE_LOSS_CAP, ltg.VIOL_TOTAL_LOSS_CAP} and info["open_mark_loss"] > 0.5
    blocks, _ = ltg.daily_cap_entry_block([], mirror([pos], [STOP_L]), book(size=0.0001), NOW, "gs1")
    assert "OPEN_MARK_UNREADABLE" in {b["code"] for b in blocks}
    assert ltg.cap_breach_response() == ltg.CAP_RESPONSE_ENTRY_BLOCK


def test_n1_closed_trade_with_missing_pnl_blocks_entries_never_skipped():
    c = {"correlation_id": "gs1-x", "closed": True, "closed_at_ts": NOW - 60, "pnl_usd": None}
    blocks, _ = ltg.daily_cap_entry_block([c], mirror(), book(), NOW, "gs6")
    assert ltg.VIOL_PNL_UNREADABLE in {b["code"] for b in blocks}
    v = g.evaluate([c], mirror(), {}, NOW, lane_needle="", book=book())
    red, amber = g.split_violations(v)
    assert ltg.VIOL_PNL_UNREADABLE in {x["code"] for x in amber} and ltg.VIOL_PNL_UNREADABLE not in {x["code"] for x in red}


def test_e1_funding_counts_no_fees():
    c = {"correlation_id": "gs1-x", "closed": True, "closed_at_ts": NOW - 60, "pnl_usd": -0.03, "funding_usd": -0.025, "fee_usd": -9}
    per, total, n = ltg.daily_realised_loss([c], NOW)
    assert abs(per["gs1"] - 0.055) < 1e-9 and n == 1


def test_skipped_daily_cap_count_and_non_countable_summary():
    day_ts = NOW - 10
    rows = [("gs1-a", {"decided_at_ts": day_ts, "reasons": [ltg.SKIP_DAILY_CAP]}), ("gs6-b", {"decided_at_ts": day_ts, "reasons": ["X"]})]
    assert ltg.skipped_daily_cap_count(rows, NOW) == {"utc_day": ltg.utc_day(NOW), "count": 1, "by_tile": {"gs1": 1}}
    ch = [{"correlation_id": "gs1-a", "closed": True, "exit_reason": "STOP", "stop_price": 99.0, "fill_price": 100.0, "exit_price": 98.9},
          {"correlation_id": "gs1-b", "closed": True, "exit_reason": ltg.STOP_FAILURE_FLATTEN}]
    s = ltg.non_countable_summary(ch, [{"code": ltg.STOP_FAILURE_FLATTEN}])
    assert s["non_countable_trades"] == 1 and s["stop_failure_flatten_red"] and s["stop_overshoot_bp"] == [10.101]


# ---- HM N3: persisted RED survives a restart; an interrupted force-flat is re-launched; boot re-derive ----------------------
_SRC = pathlib.Path(__file__).with_name("bot.py").read_text(encoding="utf-8")


def _persist_ns(tmp, view=None, mirror_=None):
    import collections, os, threading
    a = _SRC.index("_LIVE_GUARDIAN_PERSIST_KEYS =")
    b = _SRC.index("def _live_guardian_tick")
    ns = {"os": os, "json": json, "time": _t, "threading": threading, "collections": collections, "logger": logging.getLogger("t"),
          "_LIVE_GUARDIAN_STATE": {}, "_LIVE_GUARDIAN_FORCE_FLAT_LOG": collections.deque(maxlen=50),
          "_fresh_start_epoch_path": lambda: str(tmp / "epoch.json"), "FF": [],
          "_live_copy_website_cached_view": lambda: view or {}, "_live_guardian_mirror": lambda: mirror_ or {"ok": False}}
    ns["_live_guardian_force_flat"] = lambda reason, now=None, spawn=None: ns["FF"].append(reason) or {"requested": True}
    exec(_SRC[a:b], ns)
    return ns


def test_n3_red_and_latch_persist_and_restore(tmp_path):
    ns = _persist_ns(tmp_path)
    ns["_LIVE_GUARDIAN_STATE"].update(red_alert={"ts": NOW, "code": "UNTAGGED_ORDER"}, auto_disarm_latched={"ts": NOW, "reason": "x"})
    ns["_LIVE_GUARDIAN_FORCE_FLAT_LOG"].append({"ts": NOW, "reason": "AUTO_DISARM:STOP_FAILURE_FLATTEN", "requested": True})
    assert ns["_live_guardian_persist"]()
    ns2 = _persist_ns(tmp_path)
    out = ns2["_live_guardian_restore"](NOW + 30)
    st = ns2["_LIVE_GUARDIAN_STATE"]
    assert out["red"] and st["red_alert"]["code"] == "UNTAGGED_ORDER" and st["auto_disarm_latched"]
    assert ns2["FF"] == ["BOOT_RESUME:AUTO_DISARM:STOP_FAILURE_FLATTEN"] and st["boot_rederive_pending"]


def test_n3_unreadable_state_file_fails_closed(tmp_path):
    (tmp_path / "live_guardian_state.json").write_text("{not json")
    ns = _persist_ns(tmp_path)
    ns["_live_guardian_restore"](NOW)
    assert ns["_LIVE_GUARDIAN_STATE"]["red_alert"]["code"] == "GUARDIAN_STATE_UNREADABLE"


def test_n3_boot_rederive_paused_relay_not_flat_is_red(tmp_path):
    view = {"fetched_at_ts": NOW, "accounts": [{"armed": False}]}
    ns = _persist_ns(tmp_path, view, mirror([LONG], [STOP_L]))
    ns["_LIVE_GUARDIAN_STATE"]["boot_rederive_pending"] = {"ts": NOW}
    assert ns["_live_guardian_boot_rederive"](NOW) == "BOOT_REDERIVED_NOT_FLAT"
    assert ns["_LIVE_GUARDIAN_STATE"]["red_alert"]["code"] == "BOOT_REDERIVED_NOT_FLAT" and ns["FF"]
    ns = _persist_ns(tmp_path, view, mirror())
    ns["_LIVE_GUARDIAN_STATE"]["boot_rederive_pending"] = {"ts": NOW}
    assert ns["_live_guardian_boot_rederive"](NOW) == "FLAT_NO_STOPS" and not ns["_LIVE_GUARDIAN_STATE"].get("red_alert")


def test_n5_red_clear_needs_fresh_evidence_after_the_red():
    a = _SRC.index("_LIVE_GUARDIAN_RED_CLEAR_MAX_AGE_SEC =")
    b = _SRC.index("_LIVE_GUARDIAN_EXPOSED_FETCH_SEC =")
    for fetched, age, expect in ((NOW, 2.0, True), (NOW, 11.0, False), (NOW - 9, 2.0, False), (NOW - 200, 1.0, False)):
        st = {"red_alert": {"ts": NOW - 100, "code": "X"}}
        ns = {"_LIVE_GUARDIAN_STATE": st, "logger": logging.getLogger("t"), "_live_guardian_persist": lambda: True,
              "_live_copy_website_cached_view": lambda: {"fetched_at_ts": fetched},
              "_live_guardian_mirror": lambda: mirror(age=age)}
        exec(_SRC[a:b], ns)
        assert ns["_live_guardian_red_clear_if_flat"](NOW) is expect, (fetched, age)
