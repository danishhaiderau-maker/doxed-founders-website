import re
from pathlib import Path

from research.source_market_evidence import append_market_observation

BOT = (Path(__file__).with_name("bot.py")).read_text(encoding="utf-8")


def _order(generation=0):
    return {
        "trade_id": "T-1", "symbol": "BTCUSDT", "side": "buy", "dir": "LONG",
        "price": 100.0, "limit_price": 100.0, "limit_generation": generation,
        "qty": 1.0, "status": "PENDING",
    }


def _observe(store, order, ts, *, reason="NO_EXECUTABLE_LIQUIDITY", price=101.0, force=False, interval=5.0):
    return append_market_observation(
        store, order, market_price=price, bid=price - 0.5, ask=price + 0.5,
        venue_snapshot={}, gate_evidence={"reason": reason}, observed_ts=ts,
        min_interval_sec=interval, force=force,
    )[1]


def test_unchanged_per_tick_reevaluations_are_throttled_but_extrema_advance():
    # 29 Sep: after the performance-1x resize the fill-gate loop ran at full
    # tick rate and wrote ~60-120 MB/h of identical observations.
    store, order = {}, _order()
    assert _observe(store, order, 1000.0)
    written = [ts for ts in (1000.2 + i * 0.2 for i in range(40)) if _observe(store, order, ts, price=99.0 + ts % 3)]
    assert written == [t for t in written if t >= 1005.0]
    assert len(written) <= 2
    record = store[next(iter(store))]
    assert record["market_min_price"] <= 99.1 and record["market_max_price"] >= 101.0
    assert record["observation_count"] == 1 + len(written)


def test_verdict_change_reprice_and_executable_fill_are_always_persisted():
    store, order = {}, _order()
    assert _observe(store, order, 1000.0)
    assert _observe(store, order, 1000.5, reason="EXECUTABLE_TOUCH")
    order["limit_chase_count"] = 1
    order["price"] = order["limit_price"] = 100.5
    assert _observe(store, order, 1000.7, reason="EXECUTABLE_TOUCH")
    assert _observe(store, order, 1000.8, reason="EXECUTABLE_TOUCH", force=True)
    assert not _observe(store, order, 1000.9, reason="EXECUTABLE_TOUCH")


def test_zero_interval_keeps_legacy_every_call_behaviour():
    store, order = {}, _order()
    assert all(_observe(store, order, 1000.0 + i * 0.1, interval=0.0) for i in range(5))


def test_bot_wires_env_interval_and_forces_executable_decisions():
    assert re.search(r'SOURCE_ORDER_EVIDENCE_MIN_INTERVAL_SEC = max\(\s*0\.0, float\(os\.getenv\("SOURCE_ORDER_EVIDENCE_MIN_INTERVAL_SEC", "5"\)\)', BOT)
    helper = BOT[BOT.index("def _pending_limit_ready_for_fill("):BOT.index("FILL_DIRECTION_REVALIDATE_AFTER_SEC = ")]
    assert "def persist_market_evidence(evidence: dict, executable: bool = False):" in helper
    assert 'min_interval_sec=globals().get("SOURCE_ORDER_EVIDENCE_MIN_INTERVAL_SEC", 0.0)' in helper
    assert "force=bool(executable)," in helper
    assert helper.count("persist_market_evidence(evidence, executable)") == 2
    assert "persist_market_evidence(evidence)\n" not in helper
