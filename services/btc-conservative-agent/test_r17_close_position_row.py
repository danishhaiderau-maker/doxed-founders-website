"""HM (e) cross-PR (#845 r17_exclusion): the closed row is built by the REAL close_position (not a hand-built dict).

A paper book-lane position is held through the REAL hold scan (_realism_family_exit: book-row trigger scan + resident
stop trades-tail scan, no level crossed) and then closed by close_position on a TP, a BE lock and a time exit. Each
row must carry every r17_fields contract field and pass r17_exclusion.proof_failures on a clean book. Only the sinks
after the row is assembled (the WAL / persistence commit) are cut off; pricing, marks and row assembly are real."""
import math
import os
import time

import pytest

os.environ.setdefault("FORCE_PAPER_MODE", "1")
os.environ.setdefault("RESEARCH_DATA_COLLECTION", "1")
os.environ.setdefault("SKIP_EXCHANGE_MARKET_LOAD", "1")

import bot  # noqa: E402
import paper_fill  # noqa: E402
import quote_trigger as qt  # noqa: E402

r17 = pytest.importorskip("r17_exclusion")
r17_fields = pytest.importorskip("r17_fields")

LANE = "FAMILY_GS01_XV_PREMIUM_ATR_TP"
ENTRY = 60000.0
BID, ASK = 60100.0, 60100.5


class _RowBuilt(Exception):
    pass


def _pool():
    """A REAL F1 DelaySampler holding 14 measured live acks per leg (q90 tier, n >= 10)."""
    s = paper_fill.DelaySampler()
    for i in range(14):
        for leg in ("entry", "exit"):
            s.add(LANE, leg, 0.9 + 0.02 * i, measured_ts=1.0 + i, trade_id=f"ack-{leg}-{i}")
    return s


def _cross_lane_pool():
    """HM 02:55 N1: only the shared live_ack_pool prices a paper row. A REAL CrossLanePool holding 14 admitted
    Fly intent->ack samples per order-type key (one account@relay ident): ENTRY_MKT_GTC and EXIT_RO_MKT."""
    import live_ack_pool as lap
    p = lap.CrossLanePool()
    for i in range(14):
        for key in (lap.KEY_ENTRY_MKT, lap.KEY_EXIT_RO_MKT):
            p.add(key, "acct@relay", 0.9 + 0.02 * i, measured_ts=1.0 + i, trade_id=f"ack-{key}-{i}")
    return p


def _clean_book(monkeypatch, entry_ts):
    """Fresh 1 s book rows; the market is ~110 USD lower before the entry has settled (so a TP is in profit)."""
    def book(t):
        s = float(math.floor(float(t)) - 1)
        off = -110.0 if s < entry_ts + 5 else 0.0
        return {"s": s, "b": [[BID + off, 5.0], [BID + off - 1, 5.0]], "a": [[ASK + off, 5.0], [ASK + off + 1, 5.0]],
                # RK/HM #851: collector-measured WS receive time (<= s+1 <= t)
                "receive_ts": s + 0.9, "receive_mono": s, "receive_ts_source": "WS_FRAME"}
    monkeypatch.setattr(paper_fill, "book10l_at", book)


def _pos(now, monkeypatch):
    import tile_fill as tf
    entry_ts = now - 300.0
    _clean_book(monkeypatch, entry_ts)
    pool = _pool()
    monkeypatch.setattr(bot, "_paper_delay_sampler", lambda: pool)
    xpool = _cross_lane_pool()
    monkeypatch.setattr(bot, "_cross_lane_pool", lambda: xpool)
    d, _src = pool.delay(LANE, "entry")
    order = {"trade_id": f"rv2-r17-cp-{int(now * 1000)}", "research_lane": LANE, "dir": "LONG", "side": "buy",
             "signal_dir": "LONG", "qty": 0.01, "signal_created_ts": entry_ts - d, "created_ts": entry_ts - d}
    px = bot.resolve_sim_fill_price(order)  # the REAL paper entry pricer (IOC walk at signal + measured delay)
    efs = order["fill_sim"]
    assert px and efs.get("evidence_status") == "COMPLETE", efs
    return {"trade_id": order["trade_id"], "dir": "LONG", "entry": float(px), "qty": float(order["filled_qty"]),
            "entry_filled_qty": order["filled_qty"], "leverage": 10, "margin_usdt": 60.0,
            "sl": float(px) * 0.98, "tp": float(px) * 1.02, "status": "OPEN",
            "research_lane": LANE, "entry_ts": entry_ts, "signal_price": float(px), "entry_fill_sim": efs}


def _hold_scan(monkeypatch, pos, now):
    """The REAL hold scan over a clean, fresh book with full trades-tail coverage: nothing crosses."""
    monkeypatch.setattr(qt, "trades_between", lambda a, b: [])
    monkeypatch.setattr(qt, "trades_received_after", lambda a, b, c: [])
    monkeypatch.setattr(qt, "trades_coverage", lambda: {"lo_s": pos["entry_ts"] - 60.0, "hi_s": now + 1.0})
    monkeypatch.setattr(bot, "_apply_family_tile_exit", lambda *a, **k: False)  # policy: no level reached
    # RK/HM K1: the real BFXCI mark series over the hold (never near the liquidation distance) -> liq proven clear
    marks = [{"s": float(x), "mark": ENTRY} for x in range(int(pos["entry_ts"]), int(now) + 1, 10)]
    monkeypatch.setattr(qt, "marks_between", lambda a, b: [m for m in marks if a < m["s"] + 1 <= b])
    monkeypatch.setattr(bot, "_real_mark_at", lambda t: ENTRY)
    assert bot._realism_family_exit(pos, BID, now, exit_source="POSITION_MANAGER", close_async=False) is False
    assert pos.get("levels_scan_to_s") is not None and pos.get("status") == "OPEN"


def _close(monkeypatch, reason, exit_pricer_wrap=None, proven=True):
    now = time.time()
    if exit_pricer_wrap is not None:
        import tile_fill as tf
        monkeypatch.setattr(tf, "exit_fill", exit_pricer_wrap(tf.exit_fill))
    pos = _pos(now, monkeypatch)
    _hold_scan(monkeypatch, pos, now)
    pos["exit_trigger_ts"] = now - 20.0
    built = {}
    real_stamp = r17.stamp_on_write

    def _capture(row):
        real_stamp(row)
        built["row"] = dict(row)

    def _stop(*a, **k):
        raise _RowBuilt()

    monkeypatch.setattr(r17, "stamp_on_write", _capture)
    monkeypatch.setattr(bot, "_commit_paper_lifecycle_transition", _stop)
    monkeypatch.setattr(bot, "open_positions", [pos])
    monkeypatch.setattr(bot, "validate_state", lambda: True)
    monkeypatch.setattr(bot, "accrue_position_funding", lambda *a, **k: None)  # no network funding fetch in CI
    # RK ruling 22:32: the trigger row's own WS_FRAME receive_ts (as _stamp_trigger_basis stamps it); a row
    # without it is EXIT_LAG_UNMEASURED and non-countable, so a clean measured trigger is stamped here.
    # RK option (a) 25 Oct: until HM sets the real exchange threshold every in-scope exit is EXIT_LAG_UNPROVEN; these
    # contract tests prove the REST of the row, so they run with a recorded threshold (see test_unproven_... below).
    if proven:
        import exit_lag_regression as elr
        monkeypatch.setattr(elr, "REAL_EXCHANGE_THRESHOLD_S", 1.0)
        monkeypatch.setattr(elr, "REAL_EXCHANGE_THRESHOLD_SOURCE_IDS", tuple(f"x{i}" for i in range(10)))
    _rx = time.time()
    pos.update(exit_trigger_row_s=int(_rx), exit_trigger_receive_ts=_rx, exit_trigger_receive_ts_source="WS_FRAME",
               exit_trigger_eval_ts=_rx + 0.01)
    with pytest.raises(_RowBuilt):
        bot.close_position(pos, reason)
    return built["row"]


@pytest.mark.parametrize("reason", ["TAKE_PROFIT", "BREAKEVEN_LOCK", "PATH_END_60M"])
def test_close_position_row_passes_r17_on_a_clean_book(monkeypatch, tmp_path, reason):
    monkeypatch.chdir(tmp_path)
    row = _close(monkeypatch, reason)
    assert set(r17_fields.REQUIRED_FIELDS) <= set(row), set(r17_fields.REQUIRED_FIELDS) - set(row)
    # BOT_SENT MODEL_TIME latest-row exit leg (TP/BE/time are bot-sent; only the exchange stop is resident)
    assert row["exit_order_class"] == "BOT_SENT" and row["exit_pricing_basis"] == "MODEL_TIME"
    # #845 @470e9cd3 HM R1: exact strings, no case folding
    assert row["exit_evidence_status"] == "COMPLETE" and row["evidence_status"] == "COMPLETE"
    assert row["trigger_price_source"] == "book" and row["stop_coverage"] == "STOP_COVERAGE_OK"
    assert row["levels_crossed_unfired"] == [] and row["exit_priced_delay_source"].startswith(
        "POOLED_LIVE_ACK_CROSS_LANE:EXIT_RO_MKT_")  # HM 02:55 N1 / RK (2): pooled by order type
    # HM (a) / R2: finite ages >= 0 from the exit pricer; exit_book_age_ms is the pricer's own book_age_ms
    assert isinstance(row["exit_price_age_s"], (int, float)) and math.isfinite(row["exit_price_age_s"])
    assert row["exit_price_age_s"] >= 0
    assert isinstance(row["exit_book_age_ms"], int) and row["exit_book_age_ms"] >= 0
    assert abs(row["exit_book_age_ms"] - row["exit_price_age_s"] * 1000) <= 1
    for k in ("entry_walk_bp", "exit_walk_bp", "entry_book_age_ms"):
        assert isinstance(row[k], (int, float)) and math.isfinite(row[k]) and row[k] >= 0, (k, row[k])
    assert r17.proof_failures(row) == [], r17.proof_failures(row)
    assert row["liq_unverified"] is False and row["liq_trigger_ts"] is None and row["liq_mark_rows_n"] > 0
    assert row["non_countable"] is False and row["non_countable_reason"] is None  # HM K2: the row COUNTS


@pytest.mark.parametrize("reason", ["TAKE_PROFIT", "BREAKEVEN_LOCK", "PATH_END_60M"])
def test_bot_sent_model_time_exit_counts_on_exit_book_age_ms_alone(monkeypatch, tmp_path, reason):
    """#845 @470e9cd3: a latest-row BOT_SENT MODEL_TIME exit whose pricer returns price_age_s=None but book_age_ms
    passes on exit_book_age_ms (the accepted alternative) - stamped from the exit pricer's book_age_ms."""
    monkeypatch.chdir(tmp_path)

    def wrap(real):
        def exit_fill(*a, **k):
            res = dict(real(*a, **k))
            res["price_age_s"] = None
            return res
        return exit_fill

    row = _close(monkeypatch, reason, exit_pricer_wrap=wrap)
    assert row["exit_order_class"] == "BOT_SENT" and row["exit_pricing_basis"] == "MODEL_TIME"
    assert row["exit_price_age_s"] is None
    assert isinstance(row["exit_book_age_ms"], int) and 0 <= row["exit_book_age_ms"] <= 2000
    assert r17.proof_failures(row) == [], r17.proof_failures(row)
    for bad in (-1, float("nan"), float("inf"), None):
        assert "EXIT_PRICE_AGE_MISSING" in r17.proof_failures(dict(row, exit_book_age_ms=bad)), bad
    assert "TRIGGER_NOT_BOOK" in r17.proof_failures(dict(row, trigger_price_source="BOOK"))
    assert "EXIT_EVIDENCE_NOT_COMPLETE" in r17.proof_failures(dict(row, exit_evidence_status="complete"))
    assert any(f.startswith("STOP_COVERAGE") for f in r17.proof_failures(dict(row, stop_coverage="stop_coverage_ok")))


def test_close_position_without_hold_scan_is_level_cross_unknown(monkeypatch, tmp_path):
    """HM (c): no hold scan -> levels_crossed_unfired None (UNKNOWN), never a default []."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(bot, "_realism_family_exit", lambda *a, **k: False)
    now = time.time()
    pos = _pos(now, monkeypatch)
    pos.update(trigger_price_source="book", stop_tail_lo_s=now - 400, stop_tail_hi_s=now, exit_trigger_ts=now - 20.0)
    built = {}
    real_stamp = r17.stamp_on_write
    monkeypatch.setattr(r17, "stamp_on_write", lambda row: (real_stamp(row), built.update(row=dict(row))))
    monkeypatch.setattr(bot, "_commit_paper_lifecycle_transition",
                        lambda *a, **k: (_ for _ in ()).throw(_RowBuilt()))
    monkeypatch.setattr(bot, "open_positions", [pos])
    monkeypatch.setattr(bot, "validate_state", lambda: True)
    monkeypatch.setattr(bot, "accrue_position_funding", lambda *a, **k: None)  # no network funding fetch in CI
    with pytest.raises(_RowBuilt):
        bot.close_position(pos, "TAKE_PROFIT")
    row = built["row"]
    assert row["levels_crossed_unfired"] is None
    assert "LEVEL_CROSS_UNKNOWN" in r17.proof_failures(row)


def test_resident_exchange_stop_row_passes_r17(monkeypatch, tmp_path):
    """HM K2: the exchange-resident stop (EXCHANGE_RESIDENT_NO_DELAY, first book row at/after the trigger) is run
    through the REAL close_position and passes R17 without an exit delay pool."""
    import order_model as om
    monkeypatch.chdir(tmp_path)
    row = _close(monkeypatch, om.exchange_stop_reason(35))
    assert row["exit_order_class"] == om.EXCHANGE_RESIDENT
    assert row["exit_pricing_basis"] == r17_fields.RESIDENT_EXIT_BASIS
    assert r17.proof_failures(row) == [], r17.proof_failures(row)
    assert row["non_countable"] is False and row["non_countable_reason"] is None


@pytest.mark.parametrize("reason", ["ADMIN_FORCE_FLAT", "DEPLOY_FORCE_FLAT", "ADMIN_MANUAL_CLOSE"])
def test_forced_exit_row_is_non_countable(monkeypatch, tmp_path, reason):
    """HM K2 / R8-live: a forced close through the REAL close_position never counts (FORCE_FLATTENED)."""
    monkeypatch.chdir(tmp_path)
    row = _close(monkeypatch, reason)
    assert row["non_countable"] is True
    assert "FORCE_FLATTENED" in (row.get("non_countable_reasons") or [])
    assert r17.non_countable_reason(row)


@pytest.mark.parametrize("liq", [{"liq_unverified": True, "liq_reason": "LIQ_BEFORE_STOP"},
                                 {"liq_unverified": None}, {"liq_mark_rows_n": 0},
                                 {"liq_trigger_ts": 123.0}])
def test_liq_not_proven_clear_is_non_countable(monkeypatch, tmp_path, liq):
    """RK/HM K1: a row whose hold path is not PROVEN clear of the liquidation distance never counts."""
    monkeypatch.chdir(tmp_path)
    row = _close(monkeypatch, "TAKE_PROFIT")
    bad = dict(row, **liq)
    assert any(f.startswith("LIQ_") for f in r17.proof_failures(bad)), r17.proof_failures(bad)
    assert r17.non_countable_reason(dict(bad, non_countable=False)).startswith("R17_UNPROVEN")


def test_unproven_exit_lag_makes_the_clean_row_non_countable(monkeypatch, tmp_path):
    """RK option (a) 25 Oct: with no real exchange threshold recorded, a clean row is non-countable EXIT_LAG_UNPROVEN only."""
    monkeypatch.chdir(tmp_path)
    row = _close(monkeypatch, "TAKE_PROFIT", proven=False)
    assert r17.proof_failures(row) == ["EXIT_LAG_UNPROVEN"]
    assert row["non_countable"] is True and "EXIT_LAG_UNPROVEN" in str(row["non_countable_reason"])
