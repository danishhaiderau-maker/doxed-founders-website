import time

import bot


def _position(lane: str) -> dict:
    return {
        "trade_id": f"test-{lane}",
        "research_lane": lane,
        "entry": 100.0,
        "dir": "LONG",
        "atr14_3m": 1.0,
        "atr14_pct_3m": 1.0,
        "entry_ts": time.time() - 60,
        "leverage": 100.0,
        "qty": 1.0,
        "policy_remaining_fraction": 1.0,
    }


def test_fixed_scenario_c_uses_persisted_peak(monkeypatch):
    pos = _position("FAMILY_ATR_TARGET_2_5")
    closed = []
    monkeypatch.setattr(bot, "close_position", lambda row, reason: closed.append(reason))
    assert bot._apply_family_tile_exit(pos, 100.12, time.time()) is False
    assert pos["policy_peak_price"] == 100.12
    assert bot._apply_family_tile_exit(pos, 100.09, time.time()) is True
    assert closed == ["PROFIT_LOCK_LADDER"]


def test_chandelier_ratchets_from_a_prior_tick(monkeypatch):
    pos = _position("FAMILY_CHANDELIER_3")
    closed = []
    monkeypatch.setattr(bot, "close_position", lambda row, reason: closed.append(reason))
    assert bot._apply_family_tile_exit(pos, 102.0, time.time()) is False
    assert pos["policy_peak_price"] == 102.0
    assert bot._apply_family_tile_exit(pos, 100.4, time.time()) is True
    assert closed == ["PROFIT_PROTECTION_STOP"]
