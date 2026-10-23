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
    assert ltg.AMEND5_BINDING == ("F1", "F2", "F4", "G1", "G2", "G3", "G4(a)", "G4(b)", "G4(c')", "G5", "G6")


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


def test_g4c_and_g6_arm_gate():
    c = {"outcome": ltg.G4_C}
    assert ltg.arm_gate_block("gs1", None) is None                          # GS-01 arms first (its L is unknown until it trades)
    assert ltg.arm_gate_block("gs1", c) == ltg.DENY_G4_STOP_REEVAL and ltg.arm_gate_block("gs6", c, "PASS:x") == ltg.DENY_G4_STOP_REEVAL
    assert ltg.arm_gate_block("gs6", None, "PASS:hm") == ltg.DENY_G6_NO_FIRST_L
    for o in (ltg.G4_A, ltg.G4_B):
        assert ltg.arm_gate_block("gs6", {"outcome": o}, None) == ltg.DENY_G6_HM_GATE
        assert ltg.arm_gate_block("gs6", {"outcome": o}, "FAIL") == ltg.DENY_G6_HM_GATE
        assert ltg.arm_gate_block("gs6", {"outcome": o}, "PASS:hm-20261024") is None
        assert ltg.arm_gate_block("gs1", {"outcome": o}) is None
    assert ltg.arm_gate_block("gb1", c) is None                             # (c') names GS-01 and FADE3 only


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
    ns, st = _rec_ns({"tile": "gs6", "positions": [row]})
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
