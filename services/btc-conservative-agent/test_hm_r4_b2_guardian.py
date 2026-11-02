"""HM R4 (#852) Fly-side: B2 relay boot-reconcile RED, N3 (flat evidence = READ time, newer than every stacked code),
N4 (rolling-24h cap unreadable -> AMBER block), N5 (boot re-derive read postdates the boot) and the RK 07:57 balance rules
(funding / deposit / withdrawal / unreadable balance never gate; day_baseline / flat_hist never produce a code; every D3
code is an ENTRY_BLOCK_CODE)."""
from __future__ import annotations

import copy
import datetime as _dtm
import json
import logging
import pathlib

import live_guardian as g
import live_tile_gate as ltg

import pytest as _pytest_r20
pytestmark = _pytest_r20.mark.usefixtures("small_loss_limit")   # R20: legacy logic at a lowered limit (see conftest)

_SRC = pathlib.Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
NOW = 1_800_000_000.0
MIR = {"ok": True, "positions": [], "open_orders": [], "orphans": [], "balance": {"derivatives_available_usd": 206.79}}


def _codes(v):
    return [x["code"] for x in v]


def _real(pnl, **k):
    return {"correlation_id": "GS01-1", "lane": "FAMILY_GS01_XV", "closed": True, "fill_price": 100_000.0, "fill_qty": 0.01,
            "side": "LONG", "exit_fill_price": 100_000.0 + pnl / 0.01, "exit_price_source": "exchange_fill", "fill_price_source": "exchange_trades", **k}


def _nofill(**k):
    return {"correlation_id": "GS01-2", "lane": "FAMILY_GS01_XV", "closed": True, "fill_price": 100_000.0, "fill_qty": 0.01,
            "side": "LONG", "exit_price_source": "mark", "reported_exit_price": 99_000.0, "pnl_usd": 0.0, **k}


def _fn_src(name):
    a = _SRC.index(f"def {name}(")
    return _SRC[a:_SRC.index("\ndef ", a + 10)]


# ---- B2: the relay boot-reconcile stamp -> RED RELAY_BOOT_RECONCILE ------------------------------------------------------
STAMP_RED = {"verdict": "UNREADABLE", "allowed": False, "red": True, "detail": "E6_READ_TIMEOUT",
             "process_start_at": "2027-01-15T08:00:00.000Z", "read_sent_at": "2027-01-15T08:00:01.000Z"}


def test_b2_boot_reconcile_red_is_a_red_code_on_a_readable_and_on_an_unreadable_mirror():
    for m in (dict(MIR, boot_reconcile=STAMP_RED), {"ok": False, "error": "timeout", "boot_reconcile": STAMP_RED}):
        v = g.evaluate([], m, {}, NOW)
        assert "RELAY_BOOT_RECONCILE" in _codes(v), m
        dis, amber = g.split_violations(v)
        assert "RELAY_BOOT_RECONCILE" in _codes(dis) and "RELAY_BOOT_RECONCILE" not in g.ENTRY_BLOCK_CODES
        assert [x for x in v if x["code"] == "RELAY_BOOT_RECONCILE"][0]["cause"] == "UNREADABLE"
    for verdict in ("MISMATCH", "STOP_NOT_RESIDENT", "STALE_READ"):
        v = g.evaluate([], dict(MIR, boot_reconcile=dict(STAMP_RED, verdict=verdict)), {}, NOW)
        assert [x["cause"] for x in v if x["code"] == "RELAY_BOOT_RECONCILE"] == [verdict]


def test_b2_a_passing_or_absent_stamp_raises_nothing():
    for br in (None, {"verdict": "FLAT_NO_STOPS", "allowed": True, "red": False},
               {"verdict": "MATCH_STOPS_RESIDENT", "allowed": True, "red": False}, "garbage"):
        m = dict(MIR) if br is None else dict(MIR, boot_reconcile=br)
        assert "RELAY_BOOT_RECONCILE" not in _codes(g.evaluate([], m, {}, NOW))


def test_b2_fly_mirror_carries_the_stamp_even_when_the_read_failed_or_is_stale():
    for acct_mirror in ({"ok": False, "error": "x", "read_age_s": 0.5, "boot_reconcile": STAMP_RED},
                        dict(MIR, read_age_s=99.0, boot_reconcile=STAMP_RED)):
        ns = {"_live_copy_website_cached_view": lambda m=acct_mirror: {"fetched_at_ts": NOW, "accounts": [
            {"armed": True, "exchange_mirror": m}]}}
        exec(_fn_src("_live_guardian_mirror"), ns)
        out = ns["_live_guardian_mirror"]()
        assert out["ok"] is False and out["boot_reconcile"] == STAMP_RED
        assert "RELAY_BOOT_RECONCILE" in _codes(g.evaluate([], out, {}, NOW))


# ---- N4: the rolling-24h ledger cap blocks (AMBER) on an unreadable closed row ------------------------------------------
def test_n4_rolling_24h_cap_blocks_on_a_closed_row_without_a_real_exit_fill():
    # closed yesterday (UTC) but inside the rolling 24 h: only the rolling cap sees it
    now = _dtm.datetime(2027, 1, 15, 2, 0, tzinfo=_dtm.timezone.utc).timestamp()
    rows = [_nofill(fill_at_ts=now - 7300, closed_at_ts=now - 7200, exit_fill_at_ts=now - 7200)]
    v = g.evaluate(rows, MIR, {}, now)
    rolling = [x for x in v if x["code"] == ltg.VIOL_PNL_UNREADABLE and x.get("window") == "rolling_24h"]
    assert len(rolling) == 1 and "GS01-2" in rolling[0]["detail"]
    dis, amber = g.split_violations(v)
    assert dis == [] and ltg.VIOL_PNL_UNREADABLE in _codes(amber)        # AMBER entry block, never a disarm
    # HM R6 B1 / RK 10:26: a row without a real exit fill has no Bitfinex fill time, so it never ages out on a
    # report / close-record time: it blocks until the relay's clearing read re-stamps it with the real fill
    old = [_nofill(fill_at_ts=now - 90_100, closed_at_ts=now - 90_000)]
    assert [x for x in g.evaluate(old, MIR, {}, now) if x.get("window") == "rolling_24h"]
    # a real exit fill: readable, nothing
    assert not [x for x in g.evaluate([_real(-0.2, fill_at_ts=now - 7300, closed_at_ts=now - 7200, exit_fill_at_ts=now - 7200)], MIR, {}, now)
                if x["code"] == ltg.VIOL_PNL_UNREADABLE]
    # also on a blind mirror (evaluated before the blind return)
    assert [x for x in g.evaluate(rows, {"ok": False}, {}, now) if x.get("window") == "rolling_24h"]


# ---- N3: flat evidence = READ time, newer than the RED and every stacked code --------------------------------------------
def _clear_ns(red, fetched=NOW, age=2.0):
    a = _SRC.index("_LIVE_GUARDIAN_RED_CLEAR_MAX_AGE_SEC =")
    b = _SRC.index("_LIVE_GUARDIAN_EXPOSED_FETCH_SEC =")
    ns = {"_LIVE_GUARDIAN_STATE": {"red_alert": red}, "logger": logging.getLogger("t"), "PERSISTS": [],
          "_live_copy_website_cached_view": lambda: {"fetched_at_ts": fetched},
          "_live_guardian_mirror": lambda: dict(MIR, read_age_s=age, fetched_at_ts=fetched)}
    ns["_live_guardian_persist"] = lambda: ns["PERSISTS"].append(1) or True
    exec(_SRC[a:b], ns)
    return ns


def test_n3_red_latest_ts_covers_also():
    assert g.red_latest_ts({"ts": 5, "also": [{"ts": 9}, {"ts": 7}, "x"]}) == 9
    assert g.red_latest_ts({"ts": 5}) == 5 and g.red_latest_ts(None) == 0.0


def test_n3_a_flat_read_older_than_a_stacked_code_neither_clears_nor_stamps():
    # read at NOW-2; the RED is older but a stacked code (also[].ts) is newer than the read
    for code in ("FORCE_FLAT_UNCONFIRMED", "LIQ_CLAMP_FLATTEN_UNCONFIRMED"):
        red = {"ts": NOW - 100, "code": code, "also": [{"ts": NOW - 1, "code": "STOP_FAILURE_FLATTEN"}]}
        ns = _clear_ns(red)
        assert ns["_live_guardian_red_clear_if_flat"](NOW) is False
        assert ns["_LIVE_GUARDIAN_STATE"]["red_alert"].get("flat_evidence_ts") is None
    # a read newer than everything: a plain RED clears and records the read time
    ns = _clear_ns({"ts": NOW - 100, "code": "FORCE_FLAT_UNCONFIRMED", "also": [{"ts": NOW - 50}]})
    assert ns["_live_guardian_red_clear_if_flat"](NOW) is True
    assert ns["_LIVE_GUARDIAN_STATE"]["red_alert_cleared"]["evidence_read_ts"] == NOW - 2.0


def test_n3_ack_only_red_stamps_the_read_time_not_the_tick_time():
    ns = _clear_ns({"ts": NOW - 100, "code": "LIQ_CLAMP_FLATTEN_UNCONFIRMED", "also": [{"ts": NOW - 50}]}, age=3.0)
    assert ns["_live_guardian_red_clear_if_flat"](NOW) is False
    assert ns["_LIVE_GUARDIAN_STATE"]["red_alert"]["flat_evidence_ts"] == NOW - 3.0
    ack = _fn_src("_live_guardian_red_ack")
    assert "_lgm.red_latest_ts(red)" in ack


# ---- N5: the boot re-derive only accepts a mirror READ taken after this boot ----------------------------------------------
def _rederive_ns(boot_ts, read_age, fetched=NOW):
    import collections
    a = _SRC.index("_LIVE_GUARDIAN_PERSIST_KEYS =")
    b = _SRC.index("def _live_guardian_tick")
    view = {"fetched_at_ts": fetched, "accounts": [{"armed": False}]}
    m = dict(MIR, read_age_s=read_age, fetched_at_ts=fetched)
    ns = {"json": json, "logger": logging.getLogger("t"), "collections": collections, "FF": [],
          "_LIVE_GUARDIAN_STATE": {"boot_rederive_pending": {"ts": boot_ts}},
          "_live_copy_website_cached_view": lambda: view, "_live_guardian_mirror": lambda: m}
    exec(_SRC[a:b], ns)
    ns["_live_guardian_force_flat"] = lambda reason, now=None, spawn=None, **kw: ns["FF"].append(reason)
    ns["_live_guardian_persist"] = lambda: True
    return ns


def test_n5_a_cached_read_from_before_the_boot_is_not_evidence():
    ns = _rederive_ns(boot_ts=NOW - 5, read_age=8.0)          # read at NOW-8 < boot NOW-5
    assert ns["_live_guardian_boot_rederive"](NOW) is None
    assert ns["_LIVE_GUARDIAN_STATE"]["boot_rederive_pending"] == {"ts": NOW - 5}
    ns = _rederive_ns(boot_ts=NOW - 5, read_age=1.0)          # read at NOW-1 > boot
    assert ns["_live_guardian_boot_rederive"](NOW) == "FLAT_NO_STOPS"
    rec = ns["_LIVE_GUARDIAN_STATE"]["boot_rederive"]
    assert rec["read_ts"] == NOW - 1.0 and rec["boot_ts"] == NOW - 5 and "boot_rederive_pending" not in ns["_LIVE_GUARDIAN_STATE"]
    ns = _rederive_ns(boot_ts=NOW - 5, read_age=None)         # no read age: not evidence
    assert ns["_live_guardian_boot_rederive"](NOW) is None


# ---- RK 07:57 balance rules -----------------------------------------------------------------------------------------------
def _bk(t):
    return {"s": t - 1.5, "b": [[100000.0, 1.0]], "a": [[100010.0, 1.0]]}


def _run_balance(seq, chains=()):
    st, outs = {}, []
    frozen = copy.deepcopy(list(chains))
    for i, bal in enumerate(seq):
        t = NOW + 60 * i
        m = dict(MIR, balance={"derivatives_available_usd": bal}) if bal != "UNREADABLE" else dict(MIR, balance={})
        outs.append(g.evaluate(list(chains), m, st, t, book=_bk(t)))
    assert list(chains) == frozen, "the balance path never mutates a chain (countability unchanged)"
    return st, outs


def test_balance_funding_debit_deposit_withdrawal_and_unreadable_never_gate():
    cases = {"funding_debit_while_flat": (206.79, 206.70, 206.61),
             "deposit": (206.79, 1206.79),
             "withdrawal": (206.79, 6.79),
             "unreadable_balance": (206.79, "UNREADABLE", "UNREADABLE")}
    ledger = [_real(0.05, fill_at_ts=NOW - 900, closed_at_ts=NOW - 600, exit_fill_at_ts=NOW - 600)]   # a small win: under the $0.10 D3 cap
    pnl_before = [ltg.trade_realised_pnl_usd(c) for c in ledger]
    for name, seq in cases.items():
        st, outs = _run_balance(seq, ledger)
        for v in outs:
            assert v == [], (name, v)                                   # no block, no disarm, no flatten, nothing appended
            dis, amber = g.split_violations(v)
            assert dis == [] and amber == [] and g.position_flatten_violations(v) == []
        assert st["balance_reconcile"]["gating"] is False, name
        assert [ltg.trade_realised_pnl_usd(c) for c in ledger] == pnl_before, name
        assert st["ledger_loss_usd"] == -0.05, name                     # D3 alone defines the cap figure
    st, _ = _run_balance((206.79, "UNREADABLE"))
    assert st["balance_reconcile"]["ts"] == NOW                         # an unreadable balance records nothing new


def test_day_baseline_and_flat_hist_never_produce_a_code():
    for seed in ({"day_baseline": 10_000.0, "day_baseline_ts": NOW - 60},
                 {"flat_hist": [[NOW - 3600, 10_000.0], [NOW - 60, 9_000.0]]},
                 {"day_baseline": 10_000.0, "flat_hist": [[NOW - 100, 1e9]], "last_flat_balance": 1e9, "last_flat_ts": NOW - 10}):
        st = dict(seed)
        assert g.evaluate([], MIR, st, NOW, book=_bk(NOW)) == [], seed
        assert st["balance_reconcile"]["warning"] is True and st["balance_reconcile"]["gating"] is False
    src = pathlib.Path(g.__file__).read_text(encoding="utf-8")
    a = src.index("bal = _f((m.get(\"balance\")")
    tail = src[a:src.index("return out", a)]
    assert "out.append" not in tail and "BALANCE_DRIFT\"" not in tail


def test_every_d3_code_is_an_entry_block_code():
    d3 = {"DAILY_LOSS_CAP", ltg.VIOL_TILE_LOSS_CAP, ltg.VIOL_TOTAL_LOSS_CAP, ltg.VIOL_TOTAL_LOSS_CAP + "_ERROR",
          ltg.VIOL_PNL_UNREADABLE}
    assert d3 <= g.ENTRY_BLOCK_CODES
    now = _dtm.datetime(2027, 1, 15, 12, 0, tzinfo=_dtm.timezone.utc).timestamp()
    rows = [_real(-6.0, fill_at_ts=now - 900, closed_at_ts=now - 600, exit_fill_at_ts=now - 600), _nofill(fill_at_ts=now - 500, closed_at_ts=now - 400, exit_fill_at_ts=now - 400)]
    seen = set(_codes(g.evaluate(rows, MIR, {}, now))) | set(_codes(ltg.daily_loss_violations(rows, now)))
    assert {"DAILY_LOSS_CAP", ltg.VIOL_PNL_UNREADABLE, ltg.VIOL_TOTAL_LOSS_CAP} <= seen
    # R20 (Danish 25 Oct 06:21): the TOTAL (all-time) live-loss cap is NOT a D3 code: it is RED (disarm ALL + force-flat)
    assert g.VIOL_LIVE_TOTAL_LOSS_CAP in seen and g.VIOL_LIVE_TOTAL_LOSS_CAP not in g.ENTRY_BLOCK_CODES
    produced_d3 = seen & (d3 | {c for c in seen if "LOSS" in c or "PNL" in c}) - g.LIVE_TOTAL_LOSS_RED_CODES
    assert produced_d3 <= g.ENTRY_BLOCK_CODES, produced_d3
