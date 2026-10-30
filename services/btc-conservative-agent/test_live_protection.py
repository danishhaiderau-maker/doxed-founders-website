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


def test_e1_open_mark_to_book_counts_against_the_cap_and_unreadable_blocks(small_loss_limit):   # R20: logic at a lowered limit
    pos = dict(LONG, base_price=100600.0, amount=0.001)    # walk at bid 100000 => loss 0.6 USD
    blocks, info = ltg.daily_cap_entry_block([], mirror([pos], [STOP_L]), book(), NOW, "gs1")
    assert {b["code"] for b in blocks} >= {ltg.VIOL_TILE_LOSS_CAP, ltg.VIOL_TOTAL_LOSS_CAP} and info["open_mark_loss"] > 0.5
    blocks, _ = ltg.daily_cap_entry_block([], mirror([pos], [STOP_L]), book(size=0.0001), NOW, "gs1")
    assert "OPEN_MARK_UNREADABLE" in {b["code"] for b in blocks}
    assert ltg.cap_breach_response() == ltg.CAP_RESPONSE_DISARM_FLATTEN   # R20 supersedes AMEND4 E1


def test_n1_closed_trade_with_missing_pnl_blocks_entries_never_skipped():
    c = {"correlation_id": "gs1-x", "closed": True, "closed_at_ts": NOW - 60, "exit_fill_at_ts": NOW - 60, "pnl_usd": None}
    blocks, _ = ltg.daily_cap_entry_block([c], mirror(), book(), NOW, "gb2")
    assert ltg.VIOL_PNL_UNREADABLE in {b["code"] for b in blocks}
    v = g.evaluate([c], mirror(), {}, NOW, lane_needle="", book=book())
    red, amber = g.split_violations(v)
    assert ltg.VIOL_PNL_UNREADABLE in {x["code"] for x in amber} and ltg.VIOL_PNL_UNREADABLE not in {x["code"] for x in red}


def test_d3_trade_pnl_only_no_funding_no_fees():
    # RK D3 (24 Oct 06:26) supersedes AMEND4 E1's funding term: trade P&L only, no funding term, no fee term.
    c = {"correlation_id": "gs1-x", "closed": True, "closed_at_ts": NOW - 60, "exit_fill_at_ts": NOW - 60, "pnl_usd": -0.03, "funding_usd": -0.025, "fee_usd": -9,
         "fill_price": 100_000.0, "fill_qty": 0.01, "side": "LONG", "exit_fill_price": 99_997.0, "exit_price_source": "stop_fill",
         "fill_price_source": "exchange_trades"}   # HM R5 nit 6: the entry price is the real entry fill
    per, total, n = ltg.daily_realised_loss([c], NOW)
    assert abs(per["gs1"] - 0.03) < 1e-9 and n == 1


def test_skipped_daily_cap_count_and_non_countable_summary():
    day_ts = NOW - 10
    rows = [("gs1-a", {"decided_at_ts": day_ts, "reasons": [ltg.SKIP_DAILY_CAP]}), ("gs6-b", {"decided_at_ts": day_ts, "reasons": ["X"]})]
    assert ltg.skipped_daily_cap_count(rows, NOW) == {"utc_day": ltg.utc_day(NOW), "count": 1, "by_tile": {"gs1": 1}}
    ch = [{"correlation_id": "gs1-a", "closed": True, "exit_reason": "STOP", "stop_price": 99.0, "fill_price": 100.0, "exit_price": 98.9,
           "exit_fill_price": 98.9, "exit_price_source": "stop_fill", "fill_price_source": "exchange_trades"},   # HM R5 nit 7: real exit fill
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
    ns["_live_guardian_force_flat"] = lambda reason, now=None, spawn=None, **kw: ns["FF"].append(reason) or {"requested": True}
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
    ns["_LIVE_GUARDIAN_STATE"]["boot_rederive_pending"] = {"ts": NOW - 30}   # HM R4 N5: the read (NOW-1) postdates the boot
    assert ns["_live_guardian_boot_rederive"](NOW) == "BOOT_REDERIVED_NOT_FLAT"
    assert ns["_LIVE_GUARDIAN_STATE"]["red_alert"]["code"] == "BOOT_REDERIVED_NOT_FLAT" and ns["FF"]
    ns = _persist_ns(tmp_path, view, mirror())
    ns["_LIVE_GUARDIAN_STATE"]["boot_rederive_pending"] = {"ts": NOW - 30}
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


# ---- AMEND5 rev 4 FINAL (sha 4407d5a2): F1/F2 backstop 18 bp, G3 in-trade liq check, G4 outcomes, G5 source, G6 FADE3 gate --
_AMEND5 = pathlib.Path("/workspace") / ltg.AMEND5_FILE


def test_amend5_constants_pinned():
    import hashlib, pytest
    if not _AMEND5.exists():
        pytest.skip("AMEND5 prereg not present (CI / image build)")
    raw = _AMEND5.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == ltg.AMEND5_SHA256
    lines = raw.decode("utf-8").splitlines()
    at = lambda n: lines[n - 1]
    for tag in ("F1", "F2", "F4", "G1", "G2", "G3", "G5", "G6"):
        assert at(ltg.AMEND5_LINES[tag]).startswith(f"- **{tag}"), tag
    assert at(ltg.AMEND5_LINES["G4"]).startswith("- **G4 (HM 1)")
    assert at(ltg.AMEND5_LINES["G4(a)"]).strip().startswith("- (a) L at least 28 bp past the stop")
    assert at(ltg.AMEND5_LINES["G4(b)"][0]).strip().startswith("- (b) L more than 15 and less than 28 bp")
    assert "L − 10" in at(22) and "LIQ_CLAMP_FLATTEN" in " ".join(lines[21:24]) and "5%" in " ".join(lines[21:24])
    assert at(ltg.AMEND5_LINES["G4(c')"][0]).startswith("- **G4(c') replaces G4(c).**") and "15 bp or less" in at(38)
    assert "Rev 4" in at(36) and "FINAL" in at(36)
    assert "18" in at(6) and "CLOSER" in at(7) and "15 bp or less" in at(18) and "(inclusive)" in at(18)
    assert "#851" in at(13) or "receive" in at(13)
    # void text is cited, never coded
    assert at(8).startswith("- **F3 ") and at(25).strip().startswith("- (c) L 15 bp or less") and at(28).startswith("## Rev 3")
    assert ltg.STOP_BACKSTOP_PAST_STOP_BP == 18.0 and ltg.LIQ_CHECK_MAX_BP == 15.0 and ltg.LIQ_NORMAL_MIN_BP == 28.0
    assert ltg.LIQ_CLAMP_RATE_MAX == 0.05 and ltg.LIQ_CLAMP_MIN_N == 20
    assert ltg.AMEND5_BINDING == ("F1", "F2", "F4", "G1", "G2", "G3", "G4(a)", "G4(b)", "G4(c')", "G5")
    assert "G6" not in ltg.AMEND5_BINDING and "G6" in ltg.AMEND5_VOID_LINES


def test_amend5_void_rules_have_no_code():
    src = pathlib.Path(ltg.__file__).read_text(encoding="utf-8")
    for dead in ("STOP_NEAR_LIQ", "PRE_ARM_LIQ", "LIQ_ARM_MIN", "22.8", "63 bp", "F3_"):
        assert dead not in src, dead


def test_f1_f2_backstop_is_the_closer_of_stop_plus_18_and_l_minus_10():
    s = 100000.0
    far = ltg.backstop_level("LONG", 100500.0, s, s * (1 - 60 / 1e4))          # L 60 bp past stop => stop + 18 binds
    assert far["binding"] == "STOP+18BP" and abs(far["level"] - s * (1 - 18 / 1e4)) < 1e-6
    near = ltg.backstop_level("LONG", 100500.0, s, s * (1 - 20 / 1e4))         # L 20 bp past stop => L - 10 binds
    assert near["binding"] == "LIQ-10BP" and near["level"] > far["level"]
    sh = ltg.backstop_level("SHORT", 99500.0, s, s * (1 + 60 / 1e4))
    assert sh["binding"] == "STOP+18BP" and abs(sh["level"] - s * (1 + 18 / 1e4)) < 1e-6
    assert ltg.backstop_level("LONG", 100500.0, s, None)["binding"] == "STOP+18BP"


def test_g4_outcome_boundaries():
    assert ltg.g4_outcome(None) is None
    assert ltg.g4_outcome(15.0) == ltg.G4_C and ltg.g4_outcome(-3) == ltg.G4_C       # (c') inclusive at 15
    assert ltg.g4_outcome(15.001) == ltg.G4_B and ltg.g4_outcome(27.999) == ltg.G4_B
    assert ltg.g4_outcome(28.0) == ltg.G4_A and ltg.g4_outcome(80) == ltg.G4_A


def _pos(stop_liq_bp, side="LONG", **k):
    s = 100000.0
    liq = s * (1 - stop_liq_bp / 1e4) if side == "LONG" else s * (1 + stop_liq_bp / 1e4)
    p = {"symbol": "tBTCF0:USTF0", "amount": 0.0001 if side == "LONG" else -0.0001, "base_price": 100500.0 if side == "LONG" else 99500.0,
         "liq_price": liq, "liq_read_at": NOW - 1}
    p.update(k)
    return p, {"type": "STOP", "reduce_only": True, "price": s}


def test_g3_liq_le_15bp_inclusive_or_unreadable_flattens_and_disarms_all():
    for d in (15.0, 10.0):
        p, st = _pos(d)
        r = ltg.open_position_protection(mirror([p], [st]), book(), NOW)
        assert (ltg.LIQ_CHECK_FLATTEN, "LIQ_LE_15BP_PAST_STOP") in codes(r) and r["positions"][0]["g4_outcome"] == ltg.G4_C
    p, st = _pos(15.5)
    r = ltg.open_position_protection(mirror([p], [st]), book(), NOW)
    assert ltg.LIQ_CHECK_FLATTEN not in [c for c, _ in codes(r)] and r["positions"][0]["g4_outcome"] == ltg.G4_B
    p, st = _pos(40, liq_price=None)
    assert (ltg.LIQ_CHECK_FLATTEN, "LIQ_UNREADABLE") in codes(ltg.open_position_protection(mirror([p], [st]), book(), NOW))
    p, st = _pos(40, liq_read_at=None)                      # G5: a price with no Bitfinex read time is not a real L
    r = ltg.open_position_protection(mirror([p], [st]), book(), NOW)
    assert (ltg.LIQ_CHECK_FLATTEN, "LIQ_UNREADABLE") in codes(r) and r["positions"][0]["liq_source"] is None
    # the first relay read after a fill (< 5 s) may not carry L yet
    assert ltg.LIQ_CHECK_FLATTEN not in [c for c, _ in codes(ltg.open_position_protection(mirror([p], [st]), book(), NOW, fill_grace=True))]
    assert ltg.LIQ_CHECK_FLATTEN in g.RED_CODES


def test_g4b_clamp_flattens_position_only_but_a_stop_failure_still_disarms():
    p, st = _pos(20)                                         # (b): L - 10 = 10 bp past stop binds
    lvl = 100000.0 * (1 - 12 / 1e4)                          # exit walk 12 bp past the stop: past the clamp, short of stop + 18
    r = ltg.open_position_protection(mirror([p], [st]), book(bid=lvl, ask=lvl + 10), NOW)
    assert codes(r, "flatten_position") == [(ltg.LIQ_CLAMP_FLATTEN, "BACKSTOP_LIQ-10BP")] and r["disarm"] == []
    assert ltg.LIQ_CLAMP_FLATTEN in ltg.FORCE_FLAT_CLOSE_MARKERS and ltg.LIQ_CHECK_FLATTEN in ltg.FORCE_FLAT_CLOSE_MARKERS
    # same walk with stale stop-residency evidence: a real stop failure => disarm ALL
    r = ltg.open_position_protection(mirror([p], [st], age=10.5), book(bid=lvl, ask=lvl + 10), NOW)
    assert r["flatten_position"] == [] and ltg.STOP_FAILURE_FLATTEN in [c for c, _ in codes(r)]
    # (a): stop + 18 binds; a walk past it is a stop failure (disarm ALL), never a clamp
    p, st = _pos(60)
    lvl = 100000.0 * (1 - 19 / 1e4)
    r = ltg.open_position_protection(mirror([p], [st]), book(bid=lvl, ask=lvl + 10), NOW)
    assert (ltg.STOP_FAILURE_FLATTEN, "BACKSTOP_STOP+18BP") in codes(r) and r["flatten_position"] == []
    # guardian split: the clamp is not a disarm violation, but is exposed as a position-only flatten
    v = [{"code": ltg.LIQ_CLAMP_FLATTEN, "tile": "gs1", "trade_id": "t"}]
    disarm, amber = g.split_violations(v)
    assert disarm == [] and amber == [] and g.position_flatten_violations(v) == v


def _ch(tile_code_tid, closed=True):
    return {"correlation_id": tile_code_tid, "closed": closed, "fill_at_ts": NOW - 100, "fill_price": 1.0}


def test_g4b_clamp_rate_gt_5pct_at_n_ge_20_per_tile_disarms_all():
    reg = None
    chains = [_ch(f"gs1-{i}") for i in range(20)]
    tile = ltg.chain_tile_key(chains[0], reg)
    assert tile == "gs1"
    ev = lambda n: [{"code": ltg.LIQ_CLAMP_FLATTEN, "tile": tile, "trade_id": f"x{i}"} for i in range(n)]
    assert ltg.liq_clamp_rate_violations(chains, ev(1), reg) == []          # 1/20 = 5 % is not > 5 %
    out = ltg.liq_clamp_rate_violations(chains, ev(2), reg)
    assert [(v["code"], v["tile"]) for v in out] == [(ltg.LIQ_CLAMP_RATE, "gs1")]
    assert ltg.liq_clamp_rate_violations(chains[:19], ev(5), reg) == []     # n < 20: no rate rule yet
    assert ltg.liq_clamp_rate_violations(chains + [_ch("gs1-open", False)], ev(1), reg) == []
    d, _ = g.split_violations(out)
    assert d and d[0]["code"] == ltg.LIQ_CLAMP_RATE                         # disarm ALL


def test_g4c_arm_gate_no_fade3_g6_path():
    c = {"outcome": ltg.G4_C}
    assert ltg.arm_gate_block("gs1", None) is None                          # GS-01 arms first (its L is unknown until it trades)
    assert ltg.arm_gate_block("gs1", c) == ltg.DENY_G4_STOP_REEVAL
    assert not hasattr(ltg, "G6_GATED_TILE") and not hasattr(ltg, "DENY_G6_NO_FIRST_L")
    for o in (ltg.G4_A, ltg.G4_B):
        assert ltg.arm_gate_block("gs1", {"outcome": o}) is None
        assert ltg.arm_gate_block("gf3", {"outcome": o}, "PASS:hm") is None  # reserved key; no G6 gate
    assert ltg.arm_gate_block("gb1", c) is None                             # (c') names GS-01 only


def _rec_ns(prot, state=None):
    a = _SRC.index("def _live_guardian_record_protection")
    b = _SRC.index("\ndef ", a + 10)
    st = {"calc": {"protection": prot}}
    st.update(state or {})
    ns = {"_LIVE_GUARDIAN_STATE": st, "logger": logging.getLogger("t"), "_live_guardian_persist": lambda: True, "time": _t}
    exec(_SRC[a:b], ns)
    return ns, st


def test_e8_g4_first_l_recorded_once_from_gs01_bitfinex_l_only():
    row = {"side": "LONG", "entry": 100500.0, "stop_level": 100000.0, "liq_price": 99900.0, "liq_read_at": NOW - 1,
           "stop_liq_distance_bp": 10.0, "liq_source": ltg.LIQ_SOURCE}
    ns, st = _rec_ns({"tile": "gb2", "positions": [row]})
    ns["_live_guardian_record_protection"](NOW, [])
    assert not st.get("g4_first_l")                                         # only GS-01's first position decides G4
    ns, st = _rec_ns({"tile": "gs1", "trade_id": "t1", "positions": [dict(row, liq_source=None)]})
    ns["_live_guardian_record_protection"](NOW, [])
    assert not st.get("g4_first_l")                                         # G5: no Bitfinex source => no record
    ns, st = _rec_ns({"tile": "gs1", "trade_id": "t1", "positions": [row]})
    ns["_live_guardian_record_protection"](NOW, [])
    assert st["g4_first_l"]["outcome"] == ltg.G4_C and st["stop_reeval"]["flag"] == ltg.STOP_REEVAL
    st["calc"]["protection"]["positions"] = [dict(row, stop_liq_distance_bp=40.0)]
    ns["_live_guardian_record_protection"](NOW + 5, [])
    assert st["g4_first_l"]["outcome"] == ltg.G4_C                          # recorded once
    ns, st = _rec_ns({"tile": "gs1", "trade_id": "t2", "positions": [dict(row, stop_liq_distance_bp=20.0)]})
    ns["_live_guardian_record_protection"](NOW, [])
    assert st["g4_first_l"]["outcome"] == ltg.G4_B and not st.get("stop_reeval")


def test_liq_events_are_logged_once_per_trade_and_counted():
    ns, st = _rec_ns({})
    v = {"code": ltg.LIQ_CLAMP_FLATTEN, "tile": "gs1", "trade_id": "t1", "cause": "BACKSTOP_LIQ-10BP"}
    for i in range(3):
        ns["_live_guardian_record_protection"](NOW + i, [v])
    ns["_live_guardian_record_protection"](NOW + 4, [dict(v, trade_id="t2"), {"code": ltg.LIQ_CHECK_FLATTEN, "tile": "gs1", "trade_id": "t2"}])
    assert [(e["code"], e["trade_id"]) for e in st["liq_events"]] == [
        (ltg.LIQ_CLAMP_FLATTEN, "t1"), (ltg.LIQ_CLAMP_FLATTEN, "t2"), (ltg.LIQ_CHECK_FLATTEN, "t2")]
    s = ltg.non_countable_summary([], [], st["liq_events"])
    assert s["liq_clamp_flatten_events"] == 2 and s["liq_check_flatten_events"] == 1 and s["amend5_sha256"] == ltg.AMEND5_SHA256


def _pf_ns():
    import collections, threading
    a = _SRC.index("LIVE_GUARDIAN_POSITION_FLATTEN_CONFIRMATION =")
    b = _SRC.index("def _live_guardian_tile_arm_block")
    ns = {"collections": collections, "threading": threading, "time": _t, "logger": logging.getLogger("t"), "DIS": [], "FF": [],
          "_LIVE_GUARDIAN_FORCE_FLAT_RETRY_SEC": (0, 0, 0)}
    ns["_disarm_live_control"] = lambda reason: ns["DIS"].append(reason)
    ns["_live_guardian_force_flat"] = lambda reason, now=None, **kw: ns["FF"].append((reason, kw.get("disarmed", False))) and None
    ns["_LIVE_GUARDIAN_STATE"] = {}
    ns["PERSISTS"] = []
    ns["_live_guardian_persist"] = lambda: ns["PERSISTS"].append(dict(ns["_LIVE_GUARDIAN_STATE"])) or True
    exec(_SRC[a:b], ns)
    return ns


def test_position_flatten_ok_never_disarms_and_unconfirmed_escalates_to_disarm_all():
    ns = _pf_ns()
    rec = ns["_live_guardian_position_flatten"]({"trade_id": "t1", "tile": "gs1"}, NOW, spawn=lambda fn: None)
    assert rec["reason"] == "LIQ_CLAMP_FLATTEN:t1"
    assert ns["_live_guardian_position_flatten"]({"trade_id": "t1"}, NOW + 1, spawn=lambda fn: None) is None   # one in flight
    out = ns["_live_guardian_position_flatten_worker"](rec, post=lambda r: {"ok": True, "verdict": "FLAT_NO_STOPS"}, sleep=lambda s: None)
    assert out["readback"] == "FLAT_NO_STOPS" and ns["DIS"] == [] and ns["FF"] == []
    assert ns["_live_guardian_position_flatten"]({"trade_id": "t1"}, NOW + 10, spawn=lambda fn: None) is None  # 30 s re-fire guard
    ns = _pf_ns()
    rec = ns["_live_guardian_position_flatten"]({"trade_id": "t9"}, NOW, spawn=lambda fn: None)
    calls = []
    out = ns["_live_guardian_position_flatten_worker"](rec, post=lambda r: calls.append(r) or {"ok": False, "verdict": "NOT_FLAT"},
                                                      sleep=lambda s: None)
    assert len(calls) == 3 and out["readback"] == "NOT_FLAT"
    assert ns["DIS"] == ["AUTO_DISARM:LIQ_CLAMP_FLATTEN_UNCONFIRMED"] and ns["FF"] == [("AUTO_DISARM:LIQ_CLAMP_FLATTEN_UNCONFIRMED", True)]
    assert ns["LIVE_GUARDIAN_POSITION_FLATTEN_CONFIRMATION"] == "FLATTEN_POSITION_ONLY"


def test_hm1_fly_admission_refuses_while_a_flatten_runs_and_until_a_post_flatten_read():
    import collections
    ns = _pf_ns()
    ns["_LIVE_GUARDIAN_FORCE_FLAT_LOG"] = collections.deque(maxlen=50)
    fence = ns["_live_flatten_entry_fence"]
    fresh = {"ok": True, "fetched_at_ts": NOW, "read_age_s": 1.0}
    assert fence(NOW, fresh) is None
    rec = ns["_live_guardian_position_flatten"]({"trade_id": "t1", "tile": "gs1"}, NOW, spawn=lambda fn: None)
    assert fence(NOW + 1, fresh) == "FLATTEN_IN_PROGRESS"
    rec["done_ts"] = NOW + 5
    assert fence(NOW + 6, {"ok": True, "fetched_at_ts": NOW + 5.5, "read_age_s": 1.0}) == "FLATTEN_READBACK_PENDING"
    assert fence(NOW + 6, {"ok": False}) == "FLATTEN_READBACK_PENDING"
    assert fence(NOW + 8, {"ok": True, "fetched_at_ts": NOW + 7.5, "read_age_s": 1.0}) is None
    ns["_LIVE_GUARDIAN_FORCE_FLAT_LOG"].append({"requested": True, "ts": NOW + 9})      # a force-flat fences too
    assert fence(NOW + 9, {"ok": True, "fetched_at_ts": NOW + 9, "read_age_s": 0.5}) == "FLATTEN_IN_PROGRESS"


def _fn_src(name):
    a = _SRC.index(f"def {name}(")
    return _SRC[a:_SRC.index("\ndef ", a + 10)]


def test_rk1_fence_expiry_never_clears_red_disarm_or_arm_state():
    import collections
    ns = _pf_ns()
    ns["_LIVE_GUARDIAN_FORCE_FLAT_LOG"] = collections.deque(maxlen=50)
    exec(_fn_src("_live_guardian_arm_block"), ns)
    st = ns["_LIVE_GUARDIAN_STATE"]
    st.update(red_alert={"ts": NOW, "code": "STOP_FAILURE_FLATTEN"}, auto_disarm_latched={"ts": NOW, "reason": "x"})
    before = json.loads(json.dumps(st))
    fence = ns["_live_flatten_entry_fence"]
    rec = ns["_live_guardian_position_flatten"]({"trade_id": "t1", "tile": "gs1"}, NOW, spawn=lambda fn: None)
    rec["done_ts"] = NOW + 5
    # no time-based expiry: hours later, with no post-flatten exchange read, the fence is STILL held
    assert fence(NOW + 7200, {"ok": False}) == "FLATTEN_READBACK_PENDING"
    assert fence(NOW + 7200, {"ok": True, "fetched_at_ts": NOW + 4, "read_age_s": 0.5}) == "FLATTEN_READBACK_PENDING"
    # it lifts only on a read taken after the flatten finished - and lifting it changes NO guardian state
    assert fence(NOW + 7200, {"ok": True, "fetched_at_ts": NOW + 7200, "read_age_s": 1.0}) is None
    for k in ("red_alert", "auto_disarm_latched"):
        assert st[k] == before[k]
    assert ns["_live_guardian_arm_block"]() == "LIVE_GUARDIAN_FORCE_FLAT_RED"
    assert "_LIVE_FLATTEN_FENCE_TAIL_SEC" not in _SRC
    body = _fn_src("_live_flatten_entry_fence")
    assert "pop(" not in body and "_LIVE_GUARDIAN_STATE[" not in body and "output.set" not in body


def test_rk1_restart_reconciles_to_the_exchange_before_any_state_change(tmp_path):
    import collections
    view = {"fetched_at_ts": NOW + 40, "accounts": [{"armed": False}]}
    ns = _persist_ns(tmp_path)
    ns["_LIVE_GUARDIAN_STATE"].update(auto_disarm_latched={"ts": NOW, "reason": "x"})
    assert ns["_live_guardian_persist"]()
    ev = {"view": {}, "mirror": {"ok": False}}
    ns2 = _persist_ns(tmp_path)
    ns2["_live_copy_website_cached_view"] = lambda: ev["view"]
    ns2["_live_guardian_mirror"] = lambda: ev["mirror"]
    ns2["_LIVE_GUARDIAN_POSITION_FLATTEN_LOG"] = collections.deque(maxlen=50)
    ns2["_LIVE_GUARDIAN_FORCE_FLAT_LOG"] = collections.deque(maxlen=50)
    for fn in ("_live_guardian_arm_block", "_live_flatten_entry_fence"):
        exec(_fn_src(fn), ns2)
    ns2["_live_fences_arm_block"] = lambda: None   # live_fences health: test_live_fences_health.py
    ns2["_live_total_loss_arm_block"] = lambda: None
    ns2["_live_guardian_restore"](NOW + 30)
    st = ns2["_LIVE_GUARDIAN_STATE"]
    # before the reconcile: arming AND live entries are refused, the latch stays
    assert ns2["_live_guardian_arm_block"]() == "LIVE_GUARDIAN_BOOT_RECONCILE_PENDING"
    assert ns2["_live_flatten_entry_fence"](NOW + 31, {"ok": True, "fetched_at_ts": NOW + 31, "read_age_s": 0.5}) == "BOOT_RECONCILE_PENDING"
    assert ns2["_live_guardian_boot_rederive"](NOW + 31) is None and st["boot_rederive_pending"]   # no evidence yet: wait
    assert ns2["_live_guardian_arm_block"]() == "LIVE_GUARDIAN_BOOT_RECONCILE_PENDING"
    # exchange evidence arrives: reconcile first, then the gates evaluate normally; the reconcile never unlatches
    ev.update(view=view, mirror=mirror())
    assert ns2["_live_guardian_boot_rederive"](NOW + 41) == "FLAT_NO_STOPS"
    assert "boot_rederive_pending" not in st and st["auto_disarm_latched"]
    assert ns2["_live_guardian_arm_block"]() is None
    assert ns2["_live_flatten_entry_fence"](NOW + 41, {"ok": True, "fetched_at_ts": NOW + 41, "read_age_s": 0.5}) is None
    # the OFF-tick RED clear never runs before the reconcile
    tick = _fn_src("_live_guardian_tick")
    assert 'get("red_alert") and not _LIVE_GUARDIAN_STATE.get("boot_rederive_pending")' in tick
    assert tick.index("_live_guardian_boot_rederive(now)") < tick.index("_live_guardian_red_clear_if_flat(now)")


def test_hm1_admission_checks_the_fence_before_admit_entry():
    body = _SRC[_SRC.index("def _live_tile_admission("):_SRC.index("def _live_copy_stamp_payload")]
    assert body.index("_live_flatten_entry_fence(now, mirror)") < body.index("_ltg.admit_entry(")
    assert "return False, [_ltg.SKIP_MUTEX]" in body


def test_hm2_fly_success_needs_the_relay_ok_db_reconciled_not_just_the_verdict():
    import types
    for name in ("_live_guardian_force_flat_post", "_live_guardian_position_flatten_post"):
        a = _SRC.index(f"def {name}")
        ns = {"_BOT_ADMIN_TOKEN": "t", "LIVE_COPY_OPS_BASE": "b", "LIVE_COPY_OPS_USER_ID": "u",
              "LIVE_GUARDIAN_FORCE_FLAT_CONFIRMATION": "x", "LIVE_GUARDIAN_POSITION_FLATTEN_CONFIRMATION": "y"}
        body = {"verdict": "FLAT_NO_STOPS", "paused": True, "ok": False, "dbReconciled": False, "dbOpenRemaining": 1}
        ns["requests"] = types.SimpleNamespace(post=lambda *a, **k: types.SimpleNamespace(
            status_code=200, content=b"1", json=lambda: dict(body)))
        exec(_SRC[a:_SRC.index("\ndef ", a + 5)], ns)
        out = ns[name]("r")
        assert out["ok"] is False and out["db_reconciled"] is False and out["db_open_remaining"] == 1, name
        body.update(ok=True, dbReconciled=True, dbOpenRemaining=0)
        assert ns[name]("r")["ok"] is True, name


def test_hm3_position_flatten_thread_not_started_disarms_all_first_then_force_flats():
    ns = _pf_ns()
    order = []
    ns["_disarm_live_control"] = lambda reason: order.append(("disarm", reason))
    ns["_live_guardian_force_flat"] = lambda reason, now=None, **kw: order.append(("ff", reason, kw.get("disarmed")))
    def boom(fn):
        raise RuntimeError("can't start new thread")
    rec = ns["_live_guardian_position_flatten"]({"trade_id": "t3"}, NOW, spawn=boom)
    assert rec["ok"] is False and rec["done_ts"] == NOW
    assert order == [("disarm", "AUTO_DISARM:LIQ_CLAMP_FLATTEN_NOT_STARTED"),
                     ("ff", "AUTO_DISARM:LIQ_CLAMP_FLATTEN_NOT_STARTED", True)]


def _ffo_ns():
    import collections, threading, time as _tt
    src = _SRC[_SRC.index("LIVE_GUARDIAN_FORCE_FLAT_CONFIRMATION ="):_SRC.index("def _live_copy_stale_exit_check")]
    ns = {"time": _tt, "threading": threading, "collections": collections, "logger": logging.getLogger("t"), "_LIVE_GUARDIAN_STATE": {},
          "_BOT_ADMIN_TOKEN": "x", "LIVE_COPY_OPS_BASE": "http://relay", "LIVE_COPY_OPS_USER_ID": "u", "requests": None,
          "_live_guardian_persist": lambda: True, "ORDER": []}
    exec(src, ns)
    ns["_disarm_live_control"] = lambda reason: ns["ORDER"].append(("disarm", reason))
    return ns


def test_hm3_force_flat_disarms_all_before_spawning_even_when_the_thread_fails_to_start():
    ns = _ffo_ns()
    def boom(fn):
        ns["ORDER"].append(("spawn",))
        raise RuntimeError("no thread")
    rec = ns["_live_guardian_force_flat"]("BOOT_REDERIVED_NOT_FLAT", 1.0, spawn=boom)
    assert ns["ORDER"] == [("disarm", "BOOT_REDERIVED_NOT_FLAT"), ("spawn",)]
    assert rec["ok"] is False and ns["_LIVE_GUARDIAN_STATE"]["red_alert"]["code"] == "FORCE_FLAT_NOT_STARTED"
    # a disarm that raises still lets the flatten start (never blocks it)
    ns2 = _ffo_ns()
    ns2["_disarm_live_control"] = lambda reason: 1 / 0
    started = []
    ns2["_live_guardian_force_flat"]("X", 1.0, spawn=lambda fn: started.append(1))
    assert started == [1]
    # callers that have just disarmed do not disarm twice
    ns3 = _ffo_ns()
    ns3["_live_guardian_force_flat"]("Y", 1.0, spawn=lambda fn: None, disarmed=True)
    assert ns3["ORDER"] == []


def test_hm3_every_force_flat_caller_disarms_first():
    import re
    calls = [c for c in re.findall(r"_live_guardian_force_flat\(([^\n]*)", _SRC) if not c.startswith("reason: str")]
    assert len([c for c in calls if "disarmed=True" in c]) == 5
    for fn in ("def _live_guardian_tick", "def _live_guardian_on_error", "def _live_guardian_watchdog",
               "def _live_guardian_position_flatten_worker", "def _live_guardian_position_flatten("):
        body = _SRC[_SRC.index(fn):]
        body = body[:body.index("\ndef ", 5)]
        assert body.index("_disarm_live_control(") < body.index("_live_guardian_force_flat("), fn


def test_hm5_unconfirmed_position_flatten_is_persisted_red_even_if_the_force_flat_then_succeeds():
    ns = _pf_ns()
    rec = ns["_live_guardian_position_flatten"]({"trade_id": "t5", "tile": "gs1"}, NOW, spawn=lambda fn: None)
    order = []
    ns["_disarm_live_control"] = lambda reason: order.append(("disarm", dict(ns["_LIVE_GUARDIAN_STATE"]).get("red_alert", {}).get("code")))
    ns["_live_guardian_force_flat"] = lambda reason, now=None, **kw: order.append(("ff",)) or {"ok": True, "readback": "FLAT_NO_STOPS"}
    ns["_live_guardian_position_flatten_worker"](rec, post=lambda r: {"ok": False, "verdict": "NOT_FLAT"}, sleep=lambda s: None)
    red = ns["_LIVE_GUARDIAN_STATE"]["red_alert"]
    assert red["code"] == "LIQ_CLAMP_FLATTEN_UNCONFIRMED" and red["trade_id"] == "t5" and red["readback"] == "NOT_FLAT"
    assert order[0] == ("disarm", "LIQ_CLAMP_FLATTEN_UNCONFIRMED")          # latched before the escalation
    assert ns["PERSISTS"] and ns["PERSISTS"][-1]["red_alert"]["code"] == "LIQ_CLAMP_FLATTEN_UNCONFIRMED"
    # the escalation force-flat "succeeded" - the RED stays
    assert ns["_LIVE_GUARDIAN_STATE"]["red_alert"]["code"] == "LIQ_CLAMP_FLATTEN_UNCONFIRMED"
    # spawn failure is RED too
    ns = _pf_ns()
    def boom(fn):
        raise RuntimeError("no thread")
    ns["_live_guardian_position_flatten"]({"trade_id": "t6"}, NOW, spawn=boom)
    assert ns["_LIVE_GUARDIAN_STATE"]["red_alert"]["code"] == "LIQ_CLAMP_FLATTEN_UNCONFIRMED" and ns["PERSISTS"]
    # a confirmed position flatten is NOT red
    ns = _pf_ns()
    rec = ns["_live_guardian_position_flatten"]({"trade_id": "t7"}, NOW, spawn=lambda fn: None)
    ns["_live_guardian_position_flatten_worker"](rec, post=lambda r: {"ok": True, "verdict": "FLAT_NO_STOPS"}, sleep=lambda s: None)
    assert "red_alert" not in ns["_LIVE_GUARDIAN_STATE"]


def test_hm5_force_flat_worker_success_never_clears_red():
    body = _SRC[_SRC.index("def _live_guardian_force_flat_worker"):_SRC.index("def _live_guardian_force_flat(")]
    assert 'pop("red_alert"' not in body and "red_alert\"] = None" not in body


# ---- HM5 rev 2 (24 Oct, #852): LIQ_CLAMP_FLATTEN_UNCONFIRMED is ack-only; both codes recorded; stamped operator ack ----
def test_hm5r2_latch_red_never_replaces_an_ack_only_code():
    import live_guardian as g
    st = {}
    g.latch_red(st, {"ts": 1, "code": "LIQ_CLAMP_FLATTEN_UNCONFIRMED", "trade_id": "t1"})
    g.latch_red(st, {"ts": 2, "code": "FORCE_FLAT_NOT_STARTED", "error": "spawn failed"})
    g.latch_red(st, {"ts": 3, "code": "UNTAGGED_ORDER", "codes": ["UNTAGGED_ORDER", "UNTAGGED_POSITION"]})
    red = st["red_alert"]
    assert red["code"] == "LIQ_CLAMP_FLATTEN_UNCONFIRMED" and red["trade_id"] == "t1" and red["ts"] == 1
    assert red["codes"] == ["LIQ_CLAMP_FLATTEN_UNCONFIRMED", "FORCE_FLAT_NOT_STARTED", "UNTAGGED_ORDER", "UNTAGGED_POSITION"]
    assert [a["code"] for a in red["also"]] == ["FORCE_FLAT_NOT_STARTED", "UNTAGGED_ORDER"]
    # an ack-only RED arriving after a plain RED takes the primary slot and keeps the old code
    st = {"red_alert": {"ts": 1, "code": "FORCE_FLAT_UNCONFIRMED"}}
    g.latch_red(st, {"ts": 2, "code": "LIQ_CLAMP_FLATTEN_UNCONFIRMED"})
    assert st["red_alert"]["code"] == "LIQ_CLAMP_FLATTEN_UNCONFIRMED"
    assert st["red_alert"]["codes"] == ["LIQ_CLAMP_FLATTEN_UNCONFIRMED", "FORCE_FLAT_UNCONFIRMED"] and g.red_ack_only(st["red_alert"])
    # plain over plain: unchanged behaviour (new code primary), old code still recorded
    st = {"red_alert": {"ts": 1, "code": "UNTAGGED_ORDER"}}
    g.latch_red(st, {"ts": 2, "code": "FORCE_FLAT_UNCONFIRMED"})
    assert st["red_alert"]["codes"] == ["FORCE_FLAT_UNCONFIRMED", "UNTAGGED_ORDER"] and not g.red_ack_only(st["red_alert"])
    assert g.red_ack_only({"code": "GUARDIAN_STATE_UNREADABLE"})   # HM R4 nit: unreadable state file fails closed
    assert g.ACK_ONLY_RED_CODES == (frozenset({"LIQ_CLAMP_FLATTEN_UNCONFIRMED", "GUARDIAN_STATE_UNREADABLE"}) | g.LIVE_TOTAL_LOSS_RED_CODES
                                    | g.DAILY_CAP_DISARM_CODES)   # HM R3 B1 (#876): R20 cap REDs are ack-only


def test_hm5r2_force_flat_not_started_after_liq_clamp_red_records_both_codes():
    ns = _ffo_ns()
    ns["_LIVE_GUARDIAN_STATE"]["red_alert"] = {"ts": NOW, "code": "LIQ_CLAMP_FLATTEN_UNCONFIRMED", "trade_id": "t9"}
    def boom(fn):
        raise RuntimeError("no thread")
    rec = ns["_live_guardian_force_flat"]("AUTO_DISARM:LIQ_CLAMP_FLATTEN_NOT_STARTED", NOW, spawn=boom, disarmed=True)
    red = ns["_LIVE_GUARDIAN_STATE"]["red_alert"]
    assert rec["ok"] is False and red["code"] == "LIQ_CLAMP_FLATTEN_UNCONFIRMED" and red["trade_id"] == "t9"
    assert red["codes"] == ["LIQ_CLAMP_FLATTEN_UNCONFIRMED", "FORCE_FLAT_NOT_STARTED"]
    # and an unconfirmed force-flat worker after it: still the RED code, all three recorded
    ns["_live_guardian_force_flat_worker"]({"reason": "x"}, post=lambda r: {"ok": False, "verdict": "NOT_FLAT"},
                                           sleep=lambda s: None)
    red = ns["_LIVE_GUARDIAN_STATE"]["red_alert"]
    assert red["code"] == "LIQ_CLAMP_FLATTEN_UNCONFIRMED"
    assert red["codes"] == ["LIQ_CLAMP_FLATTEN_UNCONFIRMED", "FORCE_FLAT_NOT_STARTED", "FORCE_FLAT_UNCONFIRMED"]
    # end to end: the position-flatten spawn failure path (RED latched, then the escalation force-flat cannot start)
    import types
    ns = _ffo_ns()
    def no_thread(*a, **kw):
        raise RuntimeError("no thread")
    ns["threading"] = types.SimpleNamespace(Thread=no_thread, Lock=__import__("threading").Lock)
    ns["_live_guardian_position_flatten"]({"trade_id": "t10", "tile": "gs1"}, NOW, spawn=boom)
    red = ns["_LIVE_GUARDIAN_STATE"]["red_alert"]
    assert red["code"] == "LIQ_CLAMP_FLATTEN_UNCONFIRMED" and red["trade_id"] == "t10"
    assert red["codes"] == ["LIQ_CLAMP_FLATTEN_UNCONFIRMED", "FORCE_FLAT_NOT_STARTED"]


def _red_clear_ns(red, fetched=NOW, age=2.0):
    a = _SRC.index("_LIVE_GUARDIAN_RED_CLEAR_MAX_AGE_SEC =")
    b = _SRC.index("_LIVE_GUARDIAN_EXPOSED_FETCH_SEC =")
    ns = {"_LIVE_GUARDIAN_STATE": {"red_alert": red}, "logger": logging.getLogger("t"), "PERSISTS": [],
          "_live_copy_website_cached_view": lambda: {"fetched_at_ts": fetched},
          "_live_guardian_mirror": lambda: mirror(age=age)}
    ns["_live_guardian_persist"] = lambda: ns["PERSISTS"].append(1) or True
    exec(_SRC[a:b], ns)
    return ns


def test_hm5r2_fresh_flat_reading_never_clears_liq_clamp_red():
    for red in ({"ts": NOW - 100, "code": "LIQ_CLAMP_FLATTEN_UNCONFIRMED"},
                {"ts": NOW - 100, "code": "FORCE_FLAT_NOT_STARTED", "codes": ["FORCE_FLAT_NOT_STARTED", "LIQ_CLAMP_FLATTEN_UNCONFIRMED"]}):
        ns = _red_clear_ns(dict(red))
        for _ in range(3):
            assert ns["_live_guardian_red_clear_if_flat"](NOW) is False
        st = ns["_LIVE_GUARDIAN_STATE"]
        # C-R3-1: the plain stacked code is dropped on the FLAT evidence (recorded); the ack-only code stays (never cleared here)
        assert st["red_alert"]["code"] == "LIQ_CLAMP_FLATTEN_UNCONFIRMED" and "red_alert_cleared" not in st
        assert g.red_codes(st["red_alert"]) == ["LIQ_CLAMP_FLATTEN_UNCONFIRMED"]
        assert [c["code"] for c in st["red_alert"].get("flat_cleared_codes") or []] == [c for c in red.get("codes", []) if c != "LIQ_CLAMP_FLATTEN_UNCONFIRMED"]
        # stamped + persisted once; HM R4 N3: the stamp is the relay READ time (fetched NOW - read age 2 s), not the tick time
        assert st["red_alert"]["flat_evidence_ts"] == NOW - 2.0 and ns["PERSISTS"] == [1]
    # a plain RED still clears on the same evidence (unchanged)
    ns = _red_clear_ns({"ts": NOW - 100, "code": "FORCE_FLAT_UNCONFIRMED"})
    assert ns["_live_guardian_red_clear_if_flat"](NOW) is True and "red_alert" not in ns["_LIVE_GUARDIAN_STATE"]


def test_hm5r2_operator_ack_is_the_only_clear_and_is_stamped_and_persisted():
    ns = _ffo_ns()
    ns["_live_guardian_mirror"] = lambda: {}
    persisted = []
    ns["_live_guardian_persist"] = lambda: persisted.append(dict(ns["_LIVE_GUARDIAN_STATE"])) or True
    red = {"ts": NOW - 10, "code": "LIQ_CLAMP_FLATTEN_UNCONFIRMED", "codes": ["LIQ_CLAMP_FLATTEN_UNCONFIRMED"],
           "flat_evidence_ts": NOW - 5}
    st = ns["_LIVE_GUARDIAN_STATE"]
    st["red_alert"] = dict(red)
    ack = ns["_live_guardian_red_ack"]
    assert ack("", "r", red["code"], NOW)[0] == 400 and ack("AUTO", "r", red["code"], NOW)[0] == 400
    assert ack("danish", "", red["code"], NOW)[0] == 400
    code, out = ack("danish", "checked", "FORCE_FLAT_NOT_STARTED", NOW)
    assert code == 409 and out["error"] == "RED_CODE_MISMATCH" and st["red_alert"]["code"] == red["code"]
    # refused while a force-flat is still running
    ns["_LIVE_GUARDIAN_FORCE_FLAT_LOG"].append({"requested": True, "ts": NOW})
    assert ack("danish", "checked", red["code"], NOW)[1]["error"] == "LIVE_GUARDIAN_FORCE_FLAT_IN_PROGRESS"
    ns["_LIVE_GUARDIAN_FORCE_FLAT_LOG"].clear()
    assert persisted == [] and st["red_alert"]["code"] == red["code"]
    # persist failure -> RED restored, nothing stamped
    ns["_live_guardian_persist"] = lambda: False
    code, out = ack("danish", "checked", red["code"], NOW)
    assert code == 500 and st["red_alert"]["code"] == red["code"] and "red_alert_acks" not in st and "red_alert_cleared" not in st
    ns["_live_guardian_persist"] = lambda: persisted.append(dict(ns["_LIVE_GUARDIAN_STATE"])) or True
    code, out = ack("danish", "flat on exchange, checked", red["code"], NOW + 1, auth_method="token")
    assert code == 200 and out["ok"] and "red_alert" not in st
    stamp = st["red_alert_acks"][-1]
    assert stamp["actor"] == "danish" and stamp["ts"] == NOW + 1 and stamp["auth"] == "token" and stamp["was"]["code"] == red["code"]
    assert stamp["codes"] == red["codes"] and st["red_alert_cleared"]["evidence"] == "OPERATOR_ACK"
    assert persisted and "red_alert" not in persisted[-1] and persisted[-1]["red_alert_acks"][-1]["actor"] == "danish"
    assert ack("danish", "again", red["code"], NOW)[1]["error"] == "NO_RED"


def _ack_ns(red, mirror_view=None):
    ns = _ffo_ns()
    ns["_live_guardian_mirror"] = lambda: (mirror_view or {})
    ns["_LIVE_GUARDIAN_STATE"]["red_alert"] = dict(red)
    return ns, ns["_live_guardian_red_ack"]


def test_c_r3_1_ack_refuses_any_stacked_plain_code():
    for codes in (["LIQ_CLAMP_FLATTEN_UNCONFIRMED", "FORCE_FLAT_NOT_STARTED"],
                  ["LIQ_CLAMP_FLATTEN_UNCONFIRMED", "FORCE_FLAT_UNCONFIRMED", "UNTAGGED_ORDER"]):
        ns, ack = _ack_ns({"ts": NOW - 10, "code": "LIQ_CLAMP_FLATTEN_UNCONFIRMED", "codes": codes, "flat_evidence_ts": NOW - 1})
        code, out = ack("danish", "checked", "LIQ_CLAMP_FLATTEN_UNCONFIRMED", NOW)
        assert code == 409 and out["error"] == "RED_NOT_ACK_ONLY" and out["not_ack_only"] == codes[1:]
        assert ns["_LIVE_GUARDIAN_STATE"]["red_alert"]["codes"] == codes and "red_alert_acks" not in ns["_LIVE_GUARDIAN_STATE"]
    # a plain primary RED is never ackable either (it clears only on exchange evidence)
    ns, ack = _ack_ns({"ts": NOW - 10, "code": "FORCE_FLAT_UNCONFIRMED", "flat_evidence_ts": NOW - 1})
    assert ack("danish", "checked", "FORCE_FLAT_UNCONFIRMED", NOW)[1]["error"] == "RED_NOT_ACK_ONLY"


def test_c_r3_1_ack_requires_flat_evidence_newer_than_the_red():
    base = {"ts": NOW - 10, "code": "LIQ_CLAMP_FLATTEN_UNCONFIRMED", "codes": ["LIQ_CLAMP_FLATTEN_UNCONFIRMED"]}
    for red in (dict(base), dict(base, flat_evidence_ts=NOW - 20), dict(base, flat_evidence_ts=NOW - 10),
                dict(base, flat_evidence_ts="bad"),
                # evidence older than a code stacked later (also[].ts) is stale too
                dict(base, flat_evidence_ts=NOW - 5, also=[{"ts": NOW - 2, "code": "LIQ_CLAMP_FLATTEN_UNCONFIRMED"}])):
        ns, ack = _ack_ns(red)
        code, out = ack("danish", "checked", red["code"], NOW)
        assert code == 409 and out["error"] == "NO_FLAT_EVIDENCE" and "red_alert" in ns["_LIVE_GUARDIAN_STATE"]
    # latch_red drops older FLAT evidence when a new code is stacked on an ack-only RED
    import live_guardian as g
    st = {"red_alert": dict(base, flat_evidence_ts=NOW - 5)}
    g.latch_red(st, {"ts": NOW - 1, "code": "FORCE_FLAT_UNCONFIRMED"})
    assert "flat_evidence_ts" not in st["red_alert"]


def test_c_r3_1_ack_refused_while_the_flatten_entry_fence_is_held():
    red = {"ts": NOW - 10, "code": "LIQ_CLAMP_FLATTEN_UNCONFIRMED", "codes": ["LIQ_CLAMP_FLATTEN_UNCONFIRMED"], "flat_evidence_ts": NOW - 5}
    ns, ack = _ack_ns(red)
    ns["_LIVE_GUARDIAN_POSITION_FLATTEN_LOG"].append({"requested": True, "ts": NOW - 3})       # position-flatten running
    code, out = ack("danish", "checked", red["code"], NOW)
    assert code == 409 and out["error"] == "LIVE_FLATTEN_FENCE_HELD" and out["fence"] == "FLATTEN_IN_PROGRESS"
    ns["_LIVE_GUARDIAN_POSITION_FLATTEN_LOG"][-1]["done_ts"] = NOW - 2                          # done, no read-back after it
    code, out = ack("danish", "checked", red["code"], NOW)
    assert code == 409 and out["fence"] == "FLATTEN_READBACK_PENDING" and "red_alert" in ns["_LIVE_GUARDIAN_STATE"]
    # a relay read taken after the flatten finished releases the fence -> the ack is accepted
    ns["_live_guardian_mirror"] = lambda: {"ok": True, "fetched_at_ts": NOW, "read_age_s": 1.0}
    code, out = ack("danish", "checked", red["code"], NOW)
    assert code == 200 and out["ok"] and "red_alert" not in ns["_LIVE_GUARDIAN_STATE"]
    # an unreadable fence fails closed
    ns, ack = _ack_ns(red)
    ns["_live_guardian_mirror"] = lambda: 1 / 0
    assert ack("danish", "checked", red["code"], NOW)[1]["error"] == "LIVE_FLATTEN_FENCE_HELD"


def test_c_r3_1_ack_accepts_an_ack_only_red_with_fresh_flat_evidence_and_no_fence():
    red = {"ts": NOW - 10, "code": "LIQ_CLAMP_FLATTEN_UNCONFIRMED", "codes": ["LIQ_CLAMP_FLATTEN_UNCONFIRMED"], "flat_evidence_ts": NOW - 5}
    ns, ack = _ack_ns(red)
    code, out = ack("danish", "flat, checked", red["code"], NOW, auth_method="token")
    st = ns["_LIVE_GUARDIAN_STATE"]
    assert code == 200 and out["ok"] and "red_alert" not in st and st["red_alert_acks"][-1]["actor"] == "danish"
    assert st.get("auto_disarm_latched") is None   # re-arms nothing


def test_hm5r2_ack_route_wiring_and_persistence_keys():
    route = _SRC[_SRC.index("@app.route('/api/live-copy/guardian/red-ack', methods=['POST'])"):]
    route = route[:route.index("@app.route", 10)]
    assert "_admin_authed_strict()" in route and route.index("_admin_authed_strict()") < route.index("_live_guardian_red_ack(")
    assert '"red_alert_acks"' in _SRC[_SRC.index("_LIVE_GUARDIAN_PERSIST_KEYS ="):_SRC.index("_LIVE_GUARDIAN_PERSIST_FILE =")]
    # every RED write after the first goes through latch_red (no raw overwrite of red_alert anywhere but boot fail-closed paths)
    import re
    raw = [m.start() for m in re.finditer(r'_LIVE_GUARDIAN_STATE\["red_alert"\] = \{', _SRC)]
    for pos in raw:
        fn = _SRC[:pos][_SRC[:pos].rindex("\ndef "):][:60]
        assert "_live_guardian_restore" in fn or "_live_guardian_boot_rederive" in fn, fn


# ------------------------------------------------------------------ B5: live_guardian_state.json is out of every reset/wipe plan
def _wipe_ns():
    import glob as _glob, os
    a = _SRC.index("# B5 (24 Oct, #852): the persisted live-guardian latch")
    b = _SRC.index("def _reset_runtime_log_handlers")
    ns = {"os": os, "glob": _glob, "logger": logging.getLogger("t")}
    exec(_SRC[a:b], ns)
    return ns


def test_b5_fresh_collection_plan_and_delete_never_touch_live_guardian_state(tmp_path):
    ns = _wipe_ns()
    latch = tmp_path / "live_guardian_state.json"
    latch.write_text('{"red_alert": {"code": "LIQ_CLAMP_FLATTEN_UNCONFIRMED"}}')
    other = tmp_path / "signal_replay.jsonl"
    other.write_text("x")
    # every planner feeding all_research_wipe_paths tries to include it -> filtered out of the plan
    for k in ("research_wipe_file_paths", "_research_wipe_rotated_jsonl_paths", "_analyzer_output_wipe_globs",
              "_wipe_reports_recursive", "_pathway_validation_artifact_paths", "_legacy_redundant_log_paths"):
        ns[k] = lambda *a, **kw: [str(latch), str(latch) + ".tmp", str(other)]
    plan = ns["all_research_wipe_paths"](include_validation_artifacts=True, include_legacy_logs=True)
    assert plan == [str(other)]
    # and the deleter refuses it even if a caller passes it directly
    deleted, errors = ns["_delete_paths"]([str(latch), str(other)])
    assert deleted == [str(other)] and errors == [] and latch.exists()
    assert ns["RESET_NEVER_DELETE_BASENAMES"] >= {"live_guardian_state.json"}


def test_b5_the_persist_file_name_matches_the_protected_name_and_wipe_routes_go_through_the_guarded_planner():
    assert '_LIVE_GUARDIAN_PERSIST_FILE = "live_guardian_state.json"' in _SRC
    body = _SRC[_SRC.index("def _delete_paths"):_SRC.index("def _reset_runtime_log_handlers")]
    assert body.index("_reset_never_delete(path)") < body.index("os.remove(path)")
    reset = _SRC[_SRC.index("live_paths = all_research_wipe_paths("):]
    assert reset.index("all_research_wipe_paths(") < reset.index("_delete_paths(live_paths)")


# RK ruling (24 Oct, AMEND4 E6): stop evidence must be <= 5 s old; 10 s is ONLY the E7 book age while holding.
def test_rk_e6_stop_evidence_4_9s_ok_5_1s_and_9s_flatten_disarm():
    def res(age):
        return ltg.open_position_protection(mirror([LONG], [STOP_L], age=age), book(), NOW)
    ok = res(4.9)
    assert ok["disarm"] == [] and ok["flatten_position"] == []
    for age in (5.1, 9.0):
        r = res(age)
        assert (ltg.STOP_FAILURE_FLATTEN, "STOP_RESIDENCY_UNCONFIRMED") in codes(r), age
        assert ltg.STOP_FAILURE_FLATTEN in g.RED_CODES     # RED => reduce-only flatten + disarm ALL (guardian)
    # end-to-end age: relay read age + time since Fly fetched it
    m = mirror([LONG], [STOP_L], age=3.0)
    m["fetched_at_ts"] = NOW - 2.5
    assert (ltg.STOP_FAILURE_FLATTEN, "STOP_RESIDENCY_UNCONFIRMED") in codes(ltg.open_position_protection(m, book(), NOW))
    # the book bound is still 10 s while holding (unchanged): 8 s old book with fresh stop evidence = hold (AMBER only)
    r = ltg.open_position_protection(mirror([LONG], [STOP_L], age=1.0), book(8.0), NOW)
    assert r["disarm"] == []


def test_rk_e6_no_10s_stop_evidence_window_left_in_source():
    src = pathlib.Path(ltg.__file__).read_text()
    assert "2 * STOP_RESIDENT_MAX_AGE_S" not in src and "STOP_RESIDENT_MAX_AGE_S + STOP_RESIDENT_MAX_AGE_S" not in src
    assert ltg.STOP_RESIDENT_MAX_AGE_S == 5.0 and ltg.BOOK_HOLD_MAX_AGE_S == 10.0


# ---- RK ruling (24 Oct) L27: one shared liq check (R4); arm gate refuses on the latest recorded REAL L; no estimate ----------
_L27_ROW = {"side": "LONG", "entry": 100500.0, "stop_level": 100000.0, "liq_price": 99880.0, "liq_read_at": NOW - 1,
            "stop_liq_distance_bp": 12.0, "liq_source": ltg.LIQ_SOURCE}


def test_rk_l27_shared_liq_helper_bounds():
    assert ltg.liq_too_close(15.0) and ltg.liq_too_close(0.0) and not ltg.liq_too_close(15.01)
    assert not ltg.liq_too_close(None)
    assert ltg.g4_outcome(15.0) == ltg.G4_C and ltg.g4_outcome(15.01) == ltg.G4_B


# RK binding ruling (2): only an RK+HM-signed frozen sha with a stamped, persisted pass record lifts L27
_SHA1, _SHA2 = "a1b2c3d4e5f60718", "0f1e2d3c4b5a6978"
_SIGNED = {_SHA1: {"rk": "TRACKER:RK-sig-1", "hm": "TRACKER:HM-sig-1"},
           _SHA2: {"rk": "TRACKER:RK-sig-2", "hm": "TRACKER:HM-sig-2"}}


def _pass(sha=_SHA1, ts=NOW, actor="danish"):
    return {"sha": sha, "actor": actor, "ts": ts}


def test_rk_l27_arm_gate_refuses_until_a_new_amendment_passes(monkeypatch):
    monkeypatch.setattr(ltg, "SIGNED_STOP_AMENDMENTS", _SIGNED)
    rec = {"ts": NOW - 100, "stop_liq_distance_bp": 12.0, "amendment_at_record": ""}
    for tile in ("gs1", "gb2", "gb1"):                                      # every tile, account-wide
        assert ltg.arm_gate_block(tile, {"outcome": ltg.G4_A}, "PASS:hm", rec) == ltg.DENY_L27_LATEST_L
    assert ltg.arm_gate_block("gb1", None, None, rec, "FAIL") == ltg.DENY_L27_LATEST_L
    assert ltg.arm_gate_block("gb1", None, None, rec, "PASS:" + _SHA1) == ltg.DENY_L27_LATEST_L   # env alone: no pass record
    assert ltg.arm_gate_block("gb1", None, None, rec, "PASS:" + _SHA1, _pass()) is None
    assert ltg.arm_gate_block("gb1", None, None, rec, _SHA1, _pass()) is None                    # bare sha form
    old = dict(rec, amendment_at_record="PASS:" + _SHA1)                  # the amendment already in force when L was read
    assert ltg.arm_gate_block("gb1", None, None, old, "PASS:" + _SHA1, _pass()) == ltg.DENY_L27_LATEST_L
    assert ltg.arm_gate_block("gb1", None, None, old, "PASS:" + _SHA2, _pass(_SHA2)) is None
    assert ltg.arm_gate_block("gb1", None, None, dict(rec, stop_liq_distance_bp=16.0)) is None
    assert ltg.arm_gate_block("gb1", None, None, None) is None             # nothing recorded: no estimate, no refusal


def test_rk_l27_admit_entry_carries_the_l27_refusal():
    reasons = ltg.admit_entry(lane="x", trade_id="x", now=NOW, chains=[], mirror=mirror(), decisions=[], armed_lanes=[],
                              entry_margin_usd=1.0, latest_l={"stop_liq_distance_bp": 1.0})[1]
    assert reasons   # unknown tile key fails closed first; the L27 refusal is wired via arm_gate_block (source test below)
    src = pathlib.Path(ltg.__file__).read_text()
    assert "g = arm_gate_block(key, g4_first_l, hm_gate, latest_l, stop_amendment, amendment_pass)" in src


def test_rk2_l27_amendment_refusals_mismatch_unsigned_half_signed_stale_unstamped(monkeypatch):
    monkeypatch.setattr(ltg, "SIGNED_STOP_AMENDMENTS", _SIGNED)
    L = {"ts": NOW - 100, "stop_liq_distance_bp": 12.0, "amendment_at_record": ""}
    r = ltg.l27_amendment_refusal
    assert r("PASS:" + _SHA1, _pass(), L) is None
    assert r("PASS:stop-amend-1", _pass(), L) == ltg.AMENDMENT_ENV_INVALID           # not a sha
    assert r(None, _pass(), L) == ltg.AMENDMENT_ENV_INVALID
    assert r("PASS:deadbeefcafe", _pass("deadbeefcafe"), L) == ltg.AMENDMENT_UNSIGNED  # unsigned sha
    half = {_SHA1: {"rk": "TRACKER:RK-sig-1", "hm": ""}}
    assert r("PASS:" + _SHA1, _pass(), L, signed=half) == ltg.AMENDMENT_UNSIGNED      # RK only: refused
    assert r("PASS:" + _SHA1, None, L) == ltg.AMENDMENT_PASS_MISSING
    assert r("PASS:" + _SHA1, _pass(_SHA2), L) == ltg.AMENDMENT_MISMATCH              # record for another sha
    assert r("PASS:" + _SHA1, _pass(actor=""), L) == ltg.AMENDMENT_UNSTAMPED
    assert r("PASS:" + _SHA1, {"sha": _SHA1, "actor": "danish"}, L) == ltg.AMENDMENT_UNSTAMPED
    assert r("PASS:" + _SHA1, _pass(ts=NOW - 200), L) == ltg.AMENDMENT_PREDATES_L    # pass older than the too-close L
    # the shipped allowlist is EMPTY: nothing lifts L27 until RK and HM sign and a reviewed PR freezes the sha
    monkeypatch.undo()
    assert ltg.SIGNED_STOP_AMENDMENTS == {}
    assert ltg.l27_arm_block(L, "PASS:" + _SHA1, _pass()) == ltg.DENY_L27_LATEST_L


def test_rk2_tracker_signature_parser_needs_both_rk_and_hm():
    txt = ("STOP-AMENDMENT-SIGNED sha=PASS:A1B2C3D4E5F60718 RK=rk-0424 HM=hm-0424\n"
           "STOP-AMENDMENT-SIGNED sha=0f1e2d3c4b5a6978 RK=rk-only\n"
           "STOP-AMENDMENT-SIGNED sha=not-a-sha RK=x HM=y\n")
    assert ltg.parse_signed_stop_amendments(txt) == {_SHA1: {"rk": "rk-0424", "hm": "hm-0424"}}


def _amend_ns(state, env, persist_ok=True):
    import types
    a = _SRC.index("def _live_l27_amendment_pass_set")
    la = _SRC.index("def _live_l27_arm_block")
    ns = {"os": types.SimpleNamespace(environ=env), "time": _t, "logger": logging.getLogger("t"),
          "_LIVE_GUARDIAN_STATE": state, "_live_guardian_persist": lambda: persist_ok}
    exec(_SRC[la:_SRC.index("\ndef ", la + 10)], ns)
    exec(_SRC[a:_SRC.index("\n\n\n", a)], ns)
    return ns


def test_rk2_amendment_pass_route_refuses_mismatch_and_unsigned_and_stamps_who_and_when(monkeypatch):
    L = {"ts": NOW - 100, "stop_liq_distance_bp": 12.0, "too_close": True, "amendment_at_record": ""}
    st = {"liq_latest_l": L}
    env = {ltg.STOP_AMENDMENT_ENV: "PASS:" + _SHA1}
    ns = _amend_ns(st, env)
    assert ns["_live_l27_amendment_pass_set"]("danish", _SHA1, now=NOW)[1]["error"] == ltg.AMENDMENT_UNSIGNED   # empty list
    monkeypatch.setattr(ltg, "SIGNED_STOP_AMENDMENTS", _SIGNED)
    status, out = ns["_live_l27_amendment_pass_set"]("danish", _SHA2, now=NOW)
    assert status == 409 and out["error"] == ltg.AMENDMENT_MISMATCH and "l27_amendment_pass" not in st
    assert ns["_live_l27_amendment_pass_set"]("AUTO", _SHA1, now=NOW)[0] == 400
    assert _amend_ns(st, {})["_live_l27_amendment_pass_set"]("danish", _SHA1, now=NOW)[1]["error"] == ltg.AMENDMENT_ENV_INVALID
    status, out = _amend_ns(st, env, persist_ok=False)["_live_l27_amendment_pass_set"]("danish", _SHA1, now=NOW)
    assert status == 503 and "l27_amendment_pass" not in st                                       # fail closed
    assert ns["_live_l27_arm_block"]() == ltg.DENY_L27_LATEST_L
    status, out = ns["_live_l27_amendment_pass_set"]("danish", _SHA1, now=NOW, auth_method="token")
    p = st["l27_amendment_pass"]
    assert status == 200 and p["sha"] == _SHA1 and p["actor"] == "danish" and p["ts"] == NOW and p["auth"] == "token"
    assert p["rk"] == "TRACKER:RK-sig-1" and p["hm"] == "TRACKER:HM-sig-1" and out["l27_block"] is None
    assert "l27_amendment_pass" in _SRC[_SRC.index("_LIVE_GUARDIAN_PERSIST_KEYS = "):_SRC.index("_LIVE_GUARDIAN_PERSIST_FILE")]
    r = _SRC.index("@app.route('/api/live-copy/guardian/l27-amendment-pass'")
    assert "if not _admin_authed_strict():" in _SRC[r:_SRC.index("\n@app.route(", r + 10)]


def test_rk_l27_refusal_persists_across_a_restart(tmp_path, monkeypatch):
    monkeypatch.delenv(ltg.STOP_AMENDMENT_ENV, raising=False)
    monkeypatch.setattr(ltg, "SIGNED_STOP_AMENDMENTS", _SIGNED)
    ns, st = _rec_ns({"tile": "gs1", "trade_id": "t1", "positions": [_L27_ROW]})
    ns["_live_guardian_record_protection"](NOW, [])
    assert st["liq_latest_l"]["too_close"] and st["liq_latest_l"]["stop_liq_distance_bp"] == 12.0
    p = _persist_ns(tmp_path)
    p["_LIVE_GUARDIAN_STATE"].update(liq_latest_l=st["liq_latest_l"])
    assert p["_live_guardian_persist"]()
    p2 = _persist_ns(tmp_path)                                              # Fly restart
    p2["_live_guardian_restore"](NOW + 60)
    latest = p2["_LIVE_GUARDIAN_STATE"].get("liq_latest_l")
    assert latest and ltg.arm_gate_block("gb1", None, None, latest, None) == ltg.DENY_L27_LATEST_L
    assert ltg.arm_gate_block("gb1", None, None, latest, "PASS:" + _SHA1, _pass(ts=NOW + 1)) is None
    # HM C-R3-2: STICKY - a later live position with a SAFE real L does NOT replace the too-close record ...
    ns, st2 = _rec_ns({"tile": "gs1", "trade_id": "t2", "positions": [dict(_L27_ROW, stop_liq_distance_bp=30.0)]},
                      {"liq_latest_l": latest})
    ns["_live_guardian_record_protection"](NOW + 90, [])
    assert st2["liq_latest_l"]["trade_id"] == "t1" and st2["liq_latest_l"]["too_close"]
    assert st2["liq_latest_l_safe_seen"]["trade_id"] == "t2"
    assert ltg.arm_gate_block("gb1", None, None, st2["liq_latest_l"], None) == ltg.DENY_L27_LATEST_L
    # ... until a persisted amendment-pass record exists (signed sha, actor, ts after the L); then the safe L replaces it
    monkeypatch.setenv(ltg.STOP_AMENDMENT_ENV, "PASS:" + _SHA1)
    ns, st2b = _rec_ns({"tile": "gs1", "trade_id": "t2", "positions": [dict(_L27_ROW, stop_liq_distance_bp=30.0)]},
                       {"liq_latest_l": latest, "l27_amendment_pass": _pass(ts=NOW + 1)})
    ns["_live_guardian_record_protection"](NOW + 90, [])
    assert st2b["liq_latest_l"]["trade_id"] == "t2" and not st2b["liq_latest_l"]["too_close"]
    # a NEWER too-close L always replaces (and the older pass then predates it => refused again)
    ns, st2c = _rec_ns({"tile": "gs1", "trade_id": "t4", "positions": [dict(_L27_ROW, stop_liq_distance_bp=5.0)]},
                       {"liq_latest_l": st2b["liq_latest_l"], "l27_amendment_pass": _pass(ts=NOW + 1)})
    ns["_live_guardian_record_protection"](NOW + 120, [])
    assert st2c["liq_latest_l"]["trade_id"] == "t4" and st2c["liq_latest_l"]["too_close"]
    assert ltg.arm_gate_block("gb1", None, None, st2c["liq_latest_l"], "PASS:" + _SHA1,
                              st2c["l27_amendment_pass"]) == ltg.DENY_L27_LATEST_L
    monkeypatch.delenv(ltg.STOP_AMENDMENT_ENV, raising=False)
    # G5: a non-Bitfinex L is never recorded
    ns, st3 = _rec_ns({"tile": "gs1", "trade_id": "t3", "positions": [dict(_L27_ROW, liq_source=None)]})
    ns["_live_guardian_record_protection"](NOW, [])
    assert "liq_latest_l" not in st3


def test_rk_l27_g3_flatten_disarms_all():
    r = ltg.open_position_protection(mirror([dict(LONG, liq_price=100000.0 * (1 - 10 / 1e4))], [STOP_L]), book(), NOW)
    assert any(c == ltg.LIQ_CHECK_FLATTEN for c, _ in codes(r))
    assert ltg.LIQ_CHECK_FLATTEN in g.RED_CODES                             # RED => reduce-only flatten + disarm ALL
    d, _ = g.split_violations([{"code": ltg.LIQ_CHECK_FLATTEN, "tile": "gs1"}])
    assert d and d[0]["code"] == ltg.LIQ_CHECK_FLATTEN


def test_rk_l27_all_three_call_sites_use_the_one_helper():
    import inspect
    assert "liq_too_close(" in inspect.getsource(ltg.g4_outcome)                   # E5/G4
    assert "liq_too_close(" in inspect.getsource(ltg.open_position_protection)     # G3
    assert "liq_too_close(" in inspect.getsource(ltg.l27_arm_block)                # arm gate
    assert "l27_arm_block(" in inspect.getsource(ltg.arm_gate_block)
    src = pathlib.Path(ltg.__file__).read_text()
    assert src.count("<= LIQ_CHECK_MAX_BP") == 1                                   # only inside liq_too_close
    a = _SRC.index("def _live_guardian_tile_arm_block")
    assert "liq_latest_l" in _SRC[a:_SRC.index("\ndef ", a + 10)]
    assert '"liq_latest_l",' in _SRC[_SRC.index("_LIVE_GUARDIAN_PERSIST_KEYS ="):_SRC.index("_LIVE_GUARDIAN_PERSIST_FILE =")]


def test_852_e6_worst_case_evidence_age_under_normal_cadence_never_false_flattens():
    worst = ltg.stop_evidence_worst_case_age_s()
    assert worst == 3.95 and worst <= 4.0 < ltg.STOP_RESIDENT_MAX_AGE_S
    assert ltg.STOP_EVIDENCE_TIMING["relay_every_s"] <= 2.0
    src = pathlib.Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
    assert "_LIVE_GUARDIAN_EXPOSED_FETCH_SEC = 0.5" in src and "_LIVE_GUARDIAN_INTERVAL_SEC = 1.0" in src
    # Fly computes read_age_s(at response) + (now - fetched_at stamped before the request); worst = 3.45 + 0.5 HTTP
    fetched = NOW - ltg.STOP_EVIDENCE_TIMING["fly_http_s"]
    m = dict(mirror([LONG], [STOP_L]), read_age_s=worst - ltg.STOP_EVIDENCE_TIMING["fly_http_s"], fetched_at_ts=fetched)
    assert abs(ltg.mirror_evidence_age_s(m, NOW) - worst) < 1e-9
    r = ltg.open_position_protection(m, book(), NOW)
    assert "STOP_RESIDENCY_UNCONFIRMED" not in [c for _, c in codes(r)] and r["disarm"] == []
    # the boundary itself still bites (4.9 holds, 5.1 flattens)
    assert ltg.open_position_protection(dict(m, read_age_s=4.9, fetched_at_ts=NOW), book(), NOW)["disarm"] == []
    assert (ltg.STOP_FAILURE_FLATTEN, "STOP_RESIDENCY_UNCONFIRMED") in codes(
        ltg.open_position_protection(dict(m, read_age_s=5.1, fetched_at_ts=NOW), book(), NOW))


# ---- #852 RK L27 on EVERY arm path: the same shared helper (live_tile_gate.l27_arm_block) gates each route ----------------
class _App:
    def route(self, *a, **k):
        return lambda f: f


def _route_ns(route, state, extra=None, body=None, amendment=None):
    """exec one Flask route (and _live_l27_arm_block) from bot.py with stubbed request/jsonify."""
    import os
    import types
    a = _SRC.index(f"@app.route('{route}'")
    b = _SRC.index("\n@app.route(", a + 10)
    la = _SRC.index("def _live_l27_arm_block")
    lb = _SRC.index("\ndef ", la + 10)
    env = {ltg.STOP_AMENDMENT_ENV: amendment} if amendment else {}
    fake_os = types.SimpleNamespace(environ=env)
    calls = []
    ns = {"app": _App(), "os": fake_os, "time": _t, "logger": logging.getLogger("t"), "_LIVE_GUARDIAN_STATE": state,
          "request": types.SimpleNamespace(get_json=lambda silent=True: dict(body or {"enabled": True})),
          "jsonify": lambda *a, **k: dict(*a, **k), "_admin_authed_strict": lambda: True,
          "_strict_json_boolean": lambda d, k: d.get(k) if isinstance(d.get(k), bool) else None,
          "_live_guardian_heartbeat_stale": lambda: False, "_live_guardian_arm_block": lambda: None,
          "_live_fences_arm_block": lambda: None, "_live_total_loss_arm_block": lambda: None, "CALLS": calls}
    ns.update(extra or {})
    sa = _SRC.index("LIVE_ARM_GATE_SCHEMA = ")
    exec(_SRC[sa:_SRC.index("\n", sa)], ns)
    exec(_SRC[la:lb], ns)
    exec(_SRC[a:b], ns)
    return ns, calls


def _resp(r):
    return r if isinstance(r, dict) else r[0], (200 if isinstance(r, dict) else r[1])


_TOO_CLOSE = {"ts": NOW - 100, "stop_liq_distance_bp": 12.0, "too_close": True, "amendment_at_record": ""}


def test_852_l27_fly_output_on_route_refuses_and_new_amendment_unblocks(monkeypatch):
    import types
    monkeypatch.setattr(ltg, "SIGNED_STOP_AMENDMENTS", _SIGNED)
    out = types.SimpleNamespace(snapshot=lambda: {"enabled": False},
                                set=lambda en, by=None, reason=None: {"enabled": en})
    extra = {"_get_live_copy_output": lambda: out, "_live_copy_paper_lock_active": lambda: False,
             "_mirror_arm_block": lambda: None, "_onoff_actor": lambda d: "t", "_onoff_reason": lambda r, d: r,
             "_live_guardian_persist": lambda: True}
    ns, _ = _route_ns("/api/live-copy/output", {"liq_latest_l": _TOO_CLOSE}, extra)
    body, code = _resp(ns["api_live_copy_output"]())
    assert code == 409 and body["error"] == ltg.DENY_L27_LATEST_L
    ns, _ = _route_ns("/api/live-copy/output", {"liq_latest_l": _TOO_CLOSE}, extra, amendment="PASS:" + _SHA1)
    assert _resp(ns["api_live_copy_output"]())[1] == 409                       # env alone (no pass record): refused
    ns, _ = _route_ns("/api/live-copy/output", {"liq_latest_l": _TOO_CLOSE, "l27_amendment_pass": _pass()}, extra,
                      amendment="PASS:" + _SHA1)
    body, code = _resp(ns["api_live_copy_output"]())
    assert code == 200 and body["live_copy_output"]["enabled"] is True
    ns, _ = _route_ns("/api/live-copy/output", {}, extra)                     # nothing recorded: no estimate, no refusal
    assert _resp(ns["api_live_copy_output"]())[1] == 200
    # OFF is always allowed
    ns, _ = _route_ns("/api/live-copy/output", {"liq_latest_l": _TOO_CLOSE},
                      dict(extra, _get_bfx_live_switch=lambda: types.SimpleNamespace(reset_all_off=lambda reason: None),
                           _get_live_copy_bootstrap=lambda: types.SimpleNamespace(disable=lambda reason: None)),
                      body={"enabled": False})
    assert _resp(ns["api_live_copy_output"]())[1] == 200


def test_852_l27_fly_protection_bootstrap_rearm_refuses_per_lane(monkeypatch):
    import types
    # HM C-R3-3: never overwrite the real registry for the rest of the session (monkeypatch restores it)
    import combo_pathway_config as _cpc
    monkeypatch.setattr(_cpc, "ACTIVE_TILE_REGISTRY", {"GS01": {"id_prefix": "gs1"}})
    boot = types.SimpleNamespace(snapshot=lambda: {"lanes": {}}, enable=lambda *a, **k: (True, {}),
                                 disable=lambda **k: {})
    tile_src = _SRC[_SRC.index("def _live_guardian_tile_arm_block"):_SRC.index("\ndef ", _SRC.index("def _live_guardian_tile_arm_block") + 10)]
    extra = {"_get_live_copy_bootstrap": lambda: boot,
             "_get_live_copy_eligibility": lambda: types.SimpleNamespace(is_eligible=lambda lane: True),
             "_live_copy": types.SimpleNamespace(BOOTSTRAP_MAX_ACTIVE_LANES=2, BOOTSTRAP_FILL_REPORT_TYPES=())}
    ns, _ = _route_ns("/api/live-copy/protection-bootstrap", {"liq_latest_l": _TOO_CLOSE}, extra, body={"enabled": True, "lane": "GS01"})
    exec(tile_src, ns)
    body, code = _resp(ns["api_live_copy_protection_bootstrap"]())
    assert code == 409 and body["results"]["GS01"]["error"] == ltg.DENY_L27_LATEST_L
    ns, _ = _route_ns("/api/live-copy/protection-bootstrap", {}, extra, body={"enabled": True, "lane": "GS01"})
    exec(tile_src, ns)
    assert _resp(ns["api_live_copy_protection_bootstrap"]())[1] == 200
    ns, _ = _route_ns("/api/live-copy/protection-bootstrap", {}, dict(extra, _live_guardian_arm_block=lambda: "LIVE_GUARDIAN_FORCE_FLAT_RED"),
                      body={"enabled": True, "lane": "GS01"})
    exec(tile_src, ns)
    assert _resp(ns["api_live_copy_protection_bootstrap"]())[0]["results"]["GS01"]["error"] == "LIVE_GUARDIAN_FORCE_FLAT_RED"


def test_852_l27_fly_arm_gate_endpoint_serves_the_shared_helper_verdict_for_the_relay(monkeypatch):
    monkeypatch.setattr(ltg, "SIGNED_STOP_AMENDMENTS", _SIGNED)
    ns, _ = _route_ns("/api/live-copy/arm-gate", {"liq_latest_l": _TOO_CLOSE})
    body = ns["api_live_copy_arm_gate"]()
    assert body["schema"] == "fly_live_arm_gate_v1" and body["l27_block"] == ltg.DENY_L27_LATEST_L
    ns, _ = _route_ns("/api/live-copy/arm-gate", {"liq_latest_l": _TOO_CLOSE}, amendment="PASS:" + _SHA1)
    assert ns["api_live_copy_arm_gate"]()["l27_block"] == ltg.DENY_L27_LATEST_L       # no pass record
    ns, _ = _route_ns("/api/live-copy/arm-gate", {"liq_latest_l": _TOO_CLOSE, "l27_amendment_pass": _pass()},
                      amendment="PASS:" + _SHA1)
    assert ns["api_live_copy_arm_gate"]()["l27_block"] is None
    ns, _ = _route_ns("/api/live-copy/arm-gate", {})
    assert ns["api_live_copy_arm_gate"]()["l27_block"] is None
    assert "LIVE_ARM_GATE_SCHEMA = \"fly_live_arm_gate_v1\"" in _SRC
    assert ns["api_live_copy_arm_gate"]()["live_fences_block"] is None
    ns, _ = _route_ns("/api/live-copy/arm-gate", {}, {"_live_fences_arm_block": lambda: "LIVE_FENCES_UNREADABLE:acct-b=LIVE_FENCES_OK_FALSE", "_live_total_loss_arm_block": lambda: None})
    assert ns["api_live_copy_arm_gate"]()["live_fences_block"] == "LIVE_FENCES_UNREADABLE:acct-b=LIVE_FENCES_OK_FALSE"


def test_852_l27_every_fly_arm_path_uses_the_one_helper():
    def body(route):
        a = _SRC.index(f"@app.route('{route}'")
        ends = [i for i in (_SRC.find("\n@app.route(", a + 10), _SRC.find("\n\n\ndef ", a + 10)) if i > 0] or [len(_SRC)]
        return _SRC[a:min(ends)]
    assert "_live_l27_arm_block()" in body("/api/live-copy/output")
    assert "_live_guardian_tile_arm_block(lane)" in body("/api/bitfinex/tiles/<lane>/live-orders")   # arm_gate_block => l27
    assert "_live_guardian_tile_arm_block(lane)" in body("/api/live-copy/protection-bootstrap")
    assert "_live_l27_arm_block()" in body("/api/live-copy/arm-gate")
    la = _SRC.index("def _live_l27_arm_block")
    assert "_ltg.l27_arm_block(" in _SRC[la:_SRC.index("\ndef ", la + 10)]
    # the retired direct-arm routes cannot arm at all
    for r in ("/api/live_arm", "/api/bitfinex_live"):
        assert "return _retired_direct_arm_response()" in body(r)


# ---- RK binding ruling (4) (#852 R3): no local / X-Forwarded-For admin bypass; every admin route needs the token ----------
def _auth_ns(token, headers=None, cookies=None, remote="127.0.0.1"):
    import hmac
    import types
    req = types.SimpleNamespace(headers=dict(headers or {}), cookies=dict(cookies or {}), remote_addr=remote)
    req.headers = {k.lower(): v for k, v in req.headers.items()}
    hdr = req.headers
    req.headers = types.SimpleNamespace(get=lambda k, d=None: hdr.get(k.lower(), d))
    ns = {"request": req, "hmac": hmac, "_BOT_ADMIN_TOKEN": token}
    for fn in ("_client_ip", "_is_local_operator", "_is_direct_local_control", "_admin_token_presented", "_admin_authed",
               "_admin_authed_strict", "_trusted_dashboard_operator"):
        exec(_fn_src(fn), ns)
    return ns


def test_rk4_local_or_xff_request_without_the_token_is_refused():
    tok = "t" * 32
    cases = [({}, "127.0.0.1"), ({}, "::1"), ({"X-Forwarded-For": "127.0.0.1"}, "10.0.0.5"),
             ({"X-Forwarded-For": "127.0.0.1, 66.1.2.3"}, "10.0.0.5"), ({"X-Forwarded-For": "::1"}, "127.0.0.1")]
    for configured in (tok, ""):
        for headers, remote in cases:
            ns = _auth_ns(configured, headers, remote=remote)
            assert not ns["_admin_authed"](), (configured, headers, remote)
            assert not ns["_admin_authed_strict"](), (configured, headers, remote)
            assert not ns["_trusted_dashboard_operator"](ns["_client_ip"]()), (configured, headers, remote)
    # a wrong token from loopback is refused; the right token (header or cookie) is the only credential
    assert not _auth_ns(tok, {"X-Bot-Admin-Token": "nope"})["_admin_authed_strict"]()
    assert _auth_ns(tok, {"X-Bot-Admin-Token": tok}, remote="66.1.2.3")["_admin_authed_strict"]()
    assert _auth_ns(tok, cookies={"bot_admin_token": tok}, remote="66.1.2.3")["_admin_authed"]()
    assert not _auth_ns("", {"X-Bot-Admin-Token": ""})["_admin_authed"]()               # unset secret: fail closed


def test_rk4_no_local_bypass_left_on_any_admin_path():
    a = _SRC.index("def _before_request_guard") if "def _before_request_guard" in _SRC else _SRC.index("is_signed_executor_post = ")
    guard = _SRC[a:_SRC.index("def _fast_state_copy")]
    assert 'path in ("/api/pause", "/api/resume")' not in guard                       # home-bridge pause/resume bypass gone
    assert "_is_local_operator" not in _fn_src("_admin_authed_strict")
    assert "_is_direct_local_control" not in _fn_src("_admin_authed") + _fn_src("_admin_token_presented")
    assert "_is_local_operator" not in _fn_src("_trusted_dashboard_operator")
    for route in ("/api/live-copy/output", "/api/live-copy/guardian/red-ack", "/api/live-copy/guardian/l27-amendment-pass",
                  "/api/live-copy/protection-bootstrap"):
        r = _SRC.index(f"@app.route('{route}'")
        body = _SRC[r:_SRC.index("\n@app.route(", r + 10)]
        assert "_is_local_operator" not in body and "_is_direct_local_control" not in body
