"""Two-tile live gate (HM N1-N3 / G6; RK 24 Oct 2026): mutex, tile key in cid, $ caps, 20 entries/UTC day,
per-entry net=0, fail-closed admission wired into stamp_or_block, and the TS<->Python code-table contract.

Pure: no exchange, no network, no /app/data.
"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

import pytest

import live_copy_control as lc
import live_tile_gate as ltg
from combo_pathway_config import ACTIVE_TILE_ORDER, ACTIVE_TILE_REGISTRY

import pytest as _pytest_r20
pytestmark = _pytest_r20.mark.usefixtures("small_loss_limit")   # R20: legacy logic at a lowered limit (see conftest)

HERE = Path(__file__).resolve().parent
TS = HERE.parent.parent / "apps" / "api" / "src" / "trading-agents" / "live-tile-gate.ts"
GS1 = "FAMILY_GS01_XV_PREMIUM_ATR_TP"
GB2 = "FAMILY_GSB2_REGIME_SWITCHER"  # retired 2026-10-30; reserved key only
NOW = dt.datetime(2026, 10, 24, 12, 0, tzinfo=dt.timezone.utc).timestamp()
FLAT = {"ok": True, "read_age_s": 1.0, "positions": [], "open_orders": [], "orphans": []}
CAPS = {"per_tile_usd": {}, "total_usd": None, "max_entries_per_day": 20}


BOOK = {"s": NOW - 1.5, "b": [[100000.0, 1.0]], "a": [[100010.0, 1.0]]}   # fresh (complete at s+1 => 0.5 s old)


def admit(**over):
    kw = dict(lane=GS1, trade_id="gs1-new", now=NOW, chains=[], mirror=FLAT, decisions=[],
              armed_lanes=[GS1], entry_margin_usd=0.25, caps=CAPS, book=BOOK)
    kw.update(over)
    return ltg.admit_entry(**kw)


# ------------------------------------------------------------------ tile key + cid
def test_every_active_tile_has_a_stable_unique_code():
    for lane in ACTIVE_TILE_ORDER:
        key = ACTIVE_TILE_REGISTRY[lane]["id_prefix"]
        assert key in ltg.TILE_KEY_CODES, f"{lane} ({key}) has no live tile code"
        assert ltg.tile_key_for_lane(lane) == key
    codes = list(ltg.TILE_KEY_CODES.values()) + list(ltg.RESERVED_TILE_KEY_CODES.values())
    assert len(codes) == len(set(codes)) and all(1 <= c <= 127 for c in codes)
    assert not set(ltg.TILE_KEY_CODES) & set(ltg.RESERVED_TILE_KEY_CODES)


def test_ts_and_python_code_tables_match():
    src = TS.read_text(encoding="utf-8")

    def table(name):
        m = re.search(name + r"[^=]*=\s*Object\.freeze\(\{(.*?)\}\)", src, re.S)
        assert m, name
        return {k: int(v) for k, v in re.findall(r"(\w+):\s*(\d+)", m.group(1))}
    assert table("LIVE_TILE_KEY_CODES") == ltg.TILE_KEY_CODES
    assert table("RESERVED_LIVE_TILE_KEY_CODES") == ltg.RESERVED_TILE_KEY_CODES
    assert f"TILE_CID_MARKER = 0b{ltg.CID_MARKER:b}" in src
    assert f"TILE_CID_MARKER_SHIFT = {ltg.CID_MARKER_SHIFT}" in src
    assert f"TILE_CID_CODE_SHIFT = {ltg.CID_CODE_SHIFT}" in src
    assert f"LIVE_SKIPPED_MUTEX = '{ltg.SKIP_MUTEX}'" in src and f"ACCOUNT_NOT_FLAT = '{ltg.DENY_NOT_FLAT}'" in src


@pytest.mark.parametrize("key", sorted(ltg.TILE_KEY_CODES))
def test_cid_round_trip_positive_int32(key):
    for h in (0, 1, ltg.CID_HASH_MASK, 0x7FFFFFFF, 987654321):
        cid = ltg.encode_cid(key, h)
        assert 0 < cid <= 0x7FFFFFFF and ltg.decode_cid(cid) == key and ltg.decode_cid(str(cid)) == key


def test_decode_cid_legacy_and_foreign_is_none():
    for c in (None, "x", 0, -1, 0x80000000, (0b100 << 28) | (2 << 21), (0b101 << 28) | (6 << 21), (0b101 << 28)):
        assert ltg.decode_cid(c) is None


def test_trade_id_and_chain_tile_key():
    assert ltg.tile_key_for_trade_id("gs1-abc") == "gs1"
    assert ltg.tile_key_for_trade_id("adopt:gs6-1") is None  # gs6 reserved/retired
    assert ltg.tile_key_for_trade_id("dsl-1") is None and ltg.tile_key_for_trade_id("gs1-") is None
    assert ltg.chain_tile_key({"lane": GB2, "correlation_id": "gb2-1"}) is None  # retired
    assert ltg.chain_tile_key({"correlation_id": "gb2-9"}) is None
    assert ltg.chain_tile_key({"lane": GS1, "correlation_id": "gs1-9"}) == "gs1"
    assert ltg.tile_key_for_lane("FAMILY_UNKNOWN") is None


def test_unknown_tile_or_trade_prefix_mismatch_refused():
    ok, r, _ = admit(lane="FAMILY_UNKNOWN")
    assert not ok and r == [ltg.DENY_TILE_KEY_UNKNOWN]
    ok, r, _ = admit(trade_id="gb2-1")
    assert not ok and r == [ltg.DENY_TILE_KEY_UNKNOWN]


# ------------------------------------------------------------------ N1 mutex
def test_clean_entry_admitted():
    ok, r, info = admit()
    assert ok and r == [] and info["tile_key"] == "gs1" and info["day_basis"] == "UTC"


@pytest.mark.parametrize("chain", [
    {"correlation_id": "gs1-a", "lane": GS1, "fill_at_ts": NOW - 50},             # open on another tile
    {"correlation_id": "gs1-a", "lane": GS1, "order_sent_at_ts": NOW - 30},       # pending on the same tile
])
def test_second_signal_refused_while_another_live_lot_exists(chain):
    ok, r, info = admit(chains=[chain])
    assert not ok and ltg.SKIP_MUTEX in r and info["exposures"]


def test_closed_or_stale_pending_chain_does_not_block():
    chains = [{"correlation_id": "gs1-a", "lane": GS1, "fill_at_ts": NOW - 500, "closed": True, "pnl_usd": 0.0, "closed_at_ts": NOW - 100, "exit_fill_at_ts": NOW - 100,
               "fill_price": 100_000.0, "fill_qty": 0.01, "side": "LONG", "exit_fill_price": 100_000.0, "exit_price_source": "exchange_fill", "fill_price_source": "exchange_trades"},
              {"correlation_id": "gs1-b", "lane": GS1, "order_sent_at_ts": NOW - ltg.PENDING_EXPOSURE_SEC - 1}]
    assert admit(chains=chains)[0]


def test_approved_but_unreported_trade_is_in_flight_and_blocks():
    rows = [("gs1-x", {"approved": True, "lane": GS1, "approval_ts": NOW - 10})]
    ok, r, _ = admit(decisions=rows)
    assert not ok and ltg.SKIP_MUTEX in r
    # a skipped/rejected intent or an expired approval no longer blocks
    skipped = [{"correlation_id": "gs1-x", "errors": [{"type": "INTENT_REJECTED"}]}]
    assert admit(decisions=rows, chains=skipped)[0]
    old = [("gs1-x", {"approved": True, "lane": GS1, "approval_ts": NOW - ltg.INFLIGHT_APPROVAL_SEC - 1})]
    assert admit(decisions=old)[0]
    denied = [("gs1-x", {"approved": False, "lane": GS1, "approval_ts": None})]
    assert admit(decisions=denied)[0]


# ------------------------------------------------------------------ N5 net = 0
@pytest.mark.parametrize("mirror,reason", [
    (None, ltg.DENY_MIRROR_BLIND),
    ({"ok": False}, ltg.DENY_MIRROR_BLIND),
    (dict(FLAT, read_age_s=ltg.MIRROR_MAX_AGE_SEC + 1), ltg.DENY_MIRROR_BLIND),
    (dict(FLAT, positions=[{"amount": 0.0004}]), "ACCOUNT_NOT_FLAT:POSITION"),
    (dict(FLAT, positions=[{"id": 1}]), "ACCOUNT_NOT_FLAT:POSITION"),
    (dict(FLAT, open_orders=[{"type": "LIMIT", "reduce_only": False}]), "ACCOUNT_NOT_FLAT:ENTRY_ORDER"),
])
def test_net_zero_before_every_entry(mirror, reason):
    ok, r, _ = admit(mirror=mirror)
    assert not ok and reason in r


def test_reduce_only_or_stop_orders_and_zero_rows_are_flat():
    m = dict(FLAT, positions=[{"amount": 0}], open_orders=[{"type": "STOP", "reduce_only": True}, {"type": "LIMIT", "reduce_only": True}])
    assert admit(mirror=m)[0]


# ------------------------------------------------------------------ $ caps
def test_default_caps_and_total_is_sum_of_armed_tiles():
    assert ltg.tile_cap_usd("gs1", CAPS) == ltg.DEFAULT_TILE_MARGIN_CAP_USD == 0.25
    assert ltg.total_cap_usd({"gs1", "gb2"}, CAPS) == 0.25  # gb2 retired → ignored
    assert ltg.total_cap_usd({"gs1"}, CAPS) == 0.25
    assert ltg.total_cap_usd({"gs1", "nope"}, CAPS) == 0.25


def test_tile_cap_checked_every_entry():
    ok, r, info = admit(entry_margin_usd=0.30)
    assert not ok and ltg.DENY_TILE_CAP in r and info["tile_cap_usd"] == 0.25
    caps = dict(CAPS, per_tile_usd={"gs1": 0.10})
    ok, r, _ = admit(caps=caps)
    assert not ok and ltg.DENY_TILE_CAP in r


def test_total_cap_override_can_only_lower():
    caps = dict(CAPS, total_usd=0.20)
    ok, r, info = admit(caps=caps)
    assert not ok and ltg.DENY_TOTAL_CAP in r and info["total_cap_usd"] == 0.20
    assert ltg.total_cap_usd({"gs1"}, dict(CAPS, total_usd=5.0)) == 0.25


def test_entered_tile_counts_as_armed_even_if_list_is_stale():
    ok, _, info = admit(armed_lanes=[])
    assert ok and info["total_cap_usd"] == 0.25


def test_load_caps_from_env_and_bad_values_fall_back():
    caps = ltg.load_caps({"LIVE_TILE_MARGIN_CAPS_USD_JSON": '{"gs1": 0.2, "gb2": 0.15, "zzz": 9, "gb1": -1}',
                          "LIVE_TOTAL_MARGIN_CAP_USD": "0.3", "LIVE_TILE_MAX_ENTRIES_PER_DAY": "50"})
    assert caps == {"per_tile_usd": {"gs1": 0.2}, "total_usd": 0.3, "max_entries_per_day": 20}
    bad = ltg.load_caps({"LIVE_TILE_MARGIN_CAPS_USD_JSON": "{nope", "LIVE_TOTAL_MARGIN_CAP_USD": "x",
                         "LIVE_TILE_MAX_ENTRIES_PER_DAY": "x"})
    assert bad == {"per_tile_usd": {}, "total_usd": None, "max_entries_per_day": 20}
    assert ltg.load_caps({})["max_entries_per_day"] == 20


# ------------------------------------------------------------------ 20 entries / tile / UTC day
def _rows(n, tile="gs1", lane=GS1, ts=NOW - 3600):
    return [(f"{tile}-{i}", {"approved": True, "lane": lane, "approval_ts": ts}) for i in range(n)]


def test_twenty_entries_per_tile_per_utc_day():
    assert admit(decisions=_rows(19))[0]
    ok, r, info = admit(decisions=_rows(20))
    assert not ok and ltg.DENY_DAILY_LIMIT in r and info["entries_today"] == 20
    # other tile's entries and denied rows don't count
    ok20, r20, _ = admit(decisions=_rows(20, "gs1", GS1))
    assert not ok20  # 20 prior gs1 entries hit daily cap


def test_daily_limit_resets_at_utc_midnight_not_local():
    midnight = dt.datetime(2026, 10, 24, tzinfo=dt.timezone.utc).timestamp()
    yesterday = _rows(20, ts=midnight - 600)   # older than the in-flight window
    assert admit(decisions=yesterday, now=midnight + 60)[0]
    assert ltg.DENY_DAILY_LIMIT in admit(decisions=yesterday, now=midnight - 30)[1]
    assert ltg.utc_day(midnight - 1) == "2026-10-23" and ltg.utc_day(midnight) == "2026-10-24"


def test_lower_daily_limit_from_config():
    ok, r, _ = admit(decisions=_rows(3), caps=dict(CAPS, max_entries_per_day=3))
    assert not ok and ltg.DENY_DAILY_LIMIT in r


def test_admission_fails_closed_on_garbage():
    ok, r, _ = admit(entry_margin_usd="nan-ish")
    assert not ok and r[0].startswith(ltg.DENY_GATE_ERROR)


# ------------------------------------------------------------------ stamp_or_block wiring
ON_OUT = {"enabled": True, "enabled_at_ts": 100.0}
ON_TILE = {"bitfinex_live_orders": True, "last_allow_ts": 110.0}
OK_EVAL = {"eligible": True, "denials": []}
SECRET = "test-secret-not-real"


def _spec():
    return {"platform_relay_eligible": False, "relay_capability": "BLOCKED_UNQUALIFIED",
            "policy_signature": "sig", "exit_policy": {"hard_stop_bps": 40}}


def _stamp(dec, event, trade_id, gate, now=300):
    return lc.stamp_or_block(event=event, trade_id=trade_id, lane=GS1, now=now, signal_at_ts=None, spec=_spec(),
                             output=ON_OUT, tile_row=ON_TILE, tile_eval=OK_EVAL, force_paper_mode=False,
                             decisions=dec, secret=SECRET, operator_eligible=True, admission_gate=gate)


def test_refusal_is_persisted_and_trade_stays_paper_only(tmp_path):
    dec = lc.TradeDecisions(path=tmp_path / "d.json")
    calls = []

    def refuse():
        calls.append(1)
        return False, [ltg.SKIP_MUTEX], {}
    a, r = _stamp(dec, "ORDER_PLACED", "gs1-1", refuse)
    assert a is None and ltg.SKIP_MUTEX in r
    row = lc.TradeDecisions(path=tmp_path / "d.json").get("gs1-1")
    assert row and not row["approved"] and ltg.SKIP_MUTEX in row["reasons"]
    # later events of the refused trade never emit and never re-run the gate
    assert _stamp(dec, "ORDER_PLACED", "gs1-1", refuse)[0] is None
    assert _stamp(dec, "POSITION_CLOSED", "gs1-1", refuse)[0] is None
    assert len(calls) == 1


def test_admitted_trade_carries_tile_key_and_reprices_skip_the_gate(tmp_path):
    dec = lc.TradeDecisions(path=tmp_path / "d.json")
    calls = []

    def admit_ok():
        calls.append(1)
        return True, [], {}
    a, r = _stamp(dec, "ORDER_PLACED", "gs1-2", admit_ok)
    assert a and lc.verify_approval(a, SECRET) and a["tile_key"] == "gs1" and a["tile_key_code"] == 2
    a2, _ = _stamp(dec, "ORDER_PLACED", "gs1-2", lambda: (_ for _ in ()).throw(AssertionError("re-admitted")), now=310)
    assert a2 and a2["entry_allowed"] and len(calls) == 1


def test_gate_exception_refuses(tmp_path):
    dec = lc.TradeDecisions(path=tmp_path / "d.json")

    def boom():
        raise RuntimeError("chains unavailable")
    a, r = _stamp(dec, "ORDER_PLACED", "gs1-3", boom)
    assert a is None and any(x.startswith("LIVE_TILE_GATE_ERROR:RuntimeError") for x in r)


def test_gate_not_run_when_entry_gate_already_denies(tmp_path):
    dec = lc.TradeDecisions(path=tmp_path / "d.json")
    calls = []
    a, _ = lc.stamp_or_block(event="ORDER_PLACED", trade_id="gs1-4", lane=GS1, now=300, signal_at_ts=None, spec=_spec(),
                             output={"enabled": False}, tile_row=ON_TILE, tile_eval=OK_EVAL, force_paper_mode=False,
                             decisions=dec, secret=SECRET, operator_eligible=True,
                             admission_gate=lambda: calls.append(1) or (True, [], {}))
    assert a is None and calls == []


def test_bot_wires_admission_into_stamp_and_reports_tile_gate():
    src = (HERE / "bot.py").read_text(encoding="utf-8")
    assert "admission_gate=(lambda: _live_tile_admission(lane, trade_id, now))" in src
    assert "def _live_tile_admission(" in src and "_ltg.admit_entry(" in src
    assert '"tile_gate"' in src and 'lane_needle=""' in src


# ------------------------------------------------------------------ HM R2 C4 / C5 / C6 / C1-admission
def test_c4_env_per_tile_cap_is_clamped_to_the_default():
    caps = ltg.load_caps({"LIVE_TILE_MARGIN_CAPS_USD_JSON": '{"gs1": 5.0, "gb2": 0.10}'})
    assert caps["per_tile_usd"] == {"gs1": ltg.DEFAULT_TILE_MARGIN_CAP_USD}  # gb2 retired → ignored
    assert ltg.total_cap_usd({"gs1"}, caps) <= ltg.DEFAULT_TILE_MARGIN_CAP_USD


@pytest.mark.parametrize("age", [None, "x", -1])
def test_c5_missing_or_bad_read_age_is_blind(age):
    m = {k: v for k, v in FLAT.items() if k != "read_age_s"}
    if age is not None:
        m["read_age_s"] = age
    ok, r, _ = admit(mirror=m)
    assert not ok and ltg.DENY_MIRROR_BLIND in r


@pytest.mark.parametrize("order", [{"type": "STOP", "reduce_only": False}, {"type": "STOP"}, {"type": "STOP", "reduce_only": None}])
def test_c6_only_reduce_only_orders_are_skipped(order):
    ok, r, _ = admit(mirror=dict(FLAT, open_orders=[order]))
    assert not ok and "ACCOUNT_NOT_FLAT:ENTRY_ORDER" in r


def test_c1_admission_refuses_while_a_daily_loss_cap_is_breached():
    def rf(pnl):   # HM R4 B1: a real Bitfinex exit fill backs the loss (pnl_usd alone is unreadable)
        return {"pnl_usd": pnl, "fill_price": 100_000.0, "fill_qty": 0.01, "side": "LONG",
                "exit_fill_price": 100_000.0 + pnl / 0.01, "exit_price_source": "exchange_fill", "fill_price_source": "exchange_trades"}
    # Only GS-01 remains live-eligible after 2026-10-30 R22; exercise the same-tile loss path.
    small = {"correlation_id": "gs1-small", "lane": GS1, "closed": True, "closed_at_ts": NOW - 60,
             "exit_fill_at_ts": NOW - 60, **rf(-0.01)}
    assert admit(chains=[small])[0]
    lost = {"correlation_id": "gs1-old", "lane": GS1, "closed": True, "closed_at_ts": NOW - 60,
            "exit_fill_at_ts": NOW - 60, **rf(-0.06)}
    ok, r, info = admit(chains=[lost], lane=GS1, trade_id="gs1-new")
    assert not ok and ltg.DENY_DAILY_LOSS_CAP in r and ltg.VIOL_TILE_LOSS_CAP in info["daily_loss_violations"]
    ok, r, info = admit(chains=[lost, dict(lost, correlation_id="gs1-old2", **rf(-0.05))])
    assert not ok and ltg.DENY_DAILY_LOSS_CAP in r and info["daily_loss_violations"]


def test_force_flattened_trades_are_non_countable():
    assert ltg.force_flat_non_countable({"close_reason": "AUTHENTICATED_EMERGENCY_CLOSE"})
    assert ltg.force_flat_non_countable({"exit_source": "guardian_force_flat"})
    assert not ltg.force_flat_non_countable({"close_reason": "TP"}) and not ltg.force_flat_non_countable(None)


# RK final ruling (1) (24 Oct 06:26): D3 loss cap = realised trade P&L from the REAL Bitfinex fill prices + the open
# position marked to the book (full-size walk, book <= 2 s else BLOCK). No funding term, no fee term. ONE shared helper (R4).
def test_d3_realised_pnl_is_from_real_fills_with_no_funding_or_fee_term():
    row = {"correlation_id": "gs1-a", "lane": GS1, "closed": True, "closed_at_ts": NOW - 60, "exit_fill_at_ts": NOW - 60,
           "fill_price": 100_000.0, "exit_fill_price": 99_800.0, "exit_price_source": "exchange_fill", "fill_price_source": "exchange_trades",
           "exit_price": 99_900.0, "fill_qty": 0.0003, "side": "LONG",
           "pnl_usd": 123.0, "funding_usd": -50.0, "fee_usd": -9.0}
    assert ltg.trade_realised_pnl_usd(row) == pytest.approx(-0.06)              # fills win over the reported pnl
    assert ltg.trade_realised_pnl_usd(dict(row, exit_price_source="stop_fill")) == pytest.approx(-0.06)
    assert ltg.trade_realised_pnl_usd(dict(row, side="SHORT")) == pytest.approx(0.06)
    per, total, n = ltg.daily_realised_loss([row], NOW)
    assert total == pytest.approx(0.06) and n == 1                               # funding/fee fields are ignored
    # a missing/unknown funding value never blocks, never gates countability
    assert not ltg.daily_loss_violations([dict(row, side="SHORT", funding_usd=None, funding_status="PENDING")], NOW)
    # HM R4 B1: NO pnl_usd fallback. Without a real exit fill the trade is UNREADABLE (AMBER), never a $0 loss
    bare = {"correlation_id": "gs1-b", "lane": GS1, "closed": True, "closed_at_ts": NOW - 60, "exit_fill_at_ts": NOW - 60, "pnl_usd": -0.02,
            "funding_usd": -5.0}
    assert ltg.trade_realised_pnl_usd(bare) is None
    assert ltg.daily_realised_loss([bare], NOW)[2] == 0
    assert {v["code"] for v in ltg.daily_loss_violations([bare], NOW)} == {ltg.VIOL_PNL_UNREADABLE}
    assert {v["code"] for v in ltg.daily_loss_violations([dict(bare, pnl_usd=None)], NOW)} == {ltg.VIOL_PNL_UNREADABLE}


def test_d3_one_helper_no_funding_term_in_gate_or_guardian_source():
    here = Path(__file__).resolve().parent
    gate = (here / "live_tile_gate.py").read_text()
    guardian = (here / "live_guardian.py").read_text()
    for name in ("FUNDING_UNVERIFIED", "funding_usd_countable", "unverified_funding_rows", 'c.get("funding_usd")'):
        assert name not in gate and name not in guardian
    assert gate.count("pnl = trade_realised_pnl_usd(c)") == 1                   # G6 / admit_entry
    assert "_ltg.trade_realised_pnl_usd(c)" in guardian                          # guardian rolling ledger cap
    assert '_f(c.get("pnl_usd")) is not None' not in gate


def test_r5_admit_entry_none_chains_fails_closed():
    """HM R5 nit: chains=None is an unreadable source, refused (no inert `chains or []`)."""
    ok, reasons, _ = admit(chains=None)
    assert not ok and reasons == ["LIVE_TOTAL_LOSS_UNREADABLE"]
