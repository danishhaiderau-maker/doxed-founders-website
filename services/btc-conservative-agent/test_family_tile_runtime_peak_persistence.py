import time
from types import SimpleNamespace

import bot
import family_policy_common as common
from combo_pathway_config import ACTIVE_TILE_ORDER

LANE = ACTIVE_TILE_ORDER[0]
ATR_TRAIL_SPEC = common.PolicySpec(
    policy_id="GENERIC_ATR_TRAIL_PRIMITIVE", lane=LANE, label="generic", family="ATR_TRAIL",
    entry_offset_pct=0.3, chase_windows=(2,), chase_interval_sec=180, chase_step=0.5,
    initial_stop_atr_k=1.5, trail_activation_atr_k=0.75, trail_atr_k=1.0,
)


def _position() -> dict:
    return {
        "trade_id": f"test-{LANE}",
        "research_lane": LANE,
        "entry": 100.0,
        "dir": "LONG",
        "atr14_3m": 1.0,
        "atr14_pct_3m": 1.0,
        "entry_ts": time.time() - 10,
        "leverage": 100.0,
        "qty": 1.0,
        "policy_remaining_fraction": 1.0,
    }


def _use_generic_atr_trail(monkeypatch):
    policy = SimpleNamespace(
        POLICY_ID=ATR_TRAIL_SPEC.policy_id,
        exit_action=lambda **kwargs: common.exit_action(ATR_TRAIL_SPEC, **kwargs),
    )
    monkeypatch.setattr(bot, "_patient_chase_policy", lambda lane: policy)


def test_registry_tile_persists_its_peak_between_ticks(monkeypatch):
    pos = _position()
    monkeypatch.setattr(bot, "close_position", lambda row, reason: None)
    assert bot._apply_family_tile_exit(pos, 100.2, time.time()) is False
    assert pos["policy_peak_price"] == 100.2
    assert bot._apply_family_tile_exit(pos, 100.1, time.time()) is False
    assert pos["policy_peak_price"] == 100.2


def test_generic_atr_trail_uses_persisted_peak(monkeypatch):
    _use_generic_atr_trail(monkeypatch)
    pos = _position()
    closed = []
    monkeypatch.setattr(bot, "close_position", lambda row, reason: closed.append(reason))
    assert bot._apply_family_tile_exit(pos, 101.0, time.time()) is False
    assert pos["policy_peak_price"] == 101.0
    assert bot._apply_family_tile_exit(pos, 100.5, time.time()) is False
    assert pos["policy_peak_price"] == 101.0
    assert bot._apply_family_tile_exit(pos, 99.9, time.time()) is True
    assert closed == ["PROFIT_PROTECTION_STOP"]


def test_generic_atr_trail_ratchets_from_a_prior_tick(monkeypatch):
    _use_generic_atr_trail(monkeypatch)
    pos = _position()
    closed = []
    monkeypatch.setattr(bot, "close_position", lambda row, reason: closed.append(reason))
    assert bot._apply_family_tile_exit(pos, 102.0, time.time()) is False
    assert pos["policy_peak_price"] == 102.0
    assert bot._apply_family_tile_exit(pos, 100.9, time.time()) is True
    assert closed == ["PROFIT_PROTECTION_STOP"]
