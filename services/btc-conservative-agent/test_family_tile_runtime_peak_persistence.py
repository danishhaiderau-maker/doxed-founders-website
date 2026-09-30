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


def test_adaptive_atr_trail_uses_persisted_peak(monkeypatch):
    pos = _position("FAMILY_ADAPTIVE_REGIME")
    closed = []
    monkeypatch.setattr(bot, "close_position", lambda row, reason: closed.append(reason))
    assert bot._apply_family_tile_exit(pos, 101.0, time.time()) is False
    assert pos["policy_peak_price"] == 101.0
    assert bot._apply_family_tile_exit(pos, 100.5, time.time()) is False
    assert pos["policy_peak_price"] == 101.0
    assert bot._apply_family_tile_exit(pos, 99.9, time.time()) is True
    assert closed == ["PROFIT_PROTECTION_STOP"]


def test_adaptive_atr_trail_ratchets_from_a_prior_tick(monkeypatch):
    pos = _position("FAMILY_ADAPTIVE_REGIME")
    closed = []
    monkeypatch.setattr(bot, "close_position", lambda row, reason: closed.append(reason))
    assert bot._apply_family_tile_exit(pos, 102.0, time.time()) is False
    assert pos["policy_peak_price"] == 102.0
    assert bot._apply_family_tile_exit(pos, 100.9, time.time()) is True
    assert closed == ["PROFIT_PROTECTION_STOP"]
