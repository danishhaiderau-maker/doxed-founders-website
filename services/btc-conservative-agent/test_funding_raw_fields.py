"""Funding evidence: raw vs capped rate, per-settlement source, unrounded funding on the row. No trading fees."""
import bot


def test_cap_is_reported_not_hidden():
    d = bot._funding_rate_detail(0.0008, 0.0)
    assert d["rate_raw"] == 0.0008 and d["rate_used"] == bot.FUNDING_RATE_CAP_PER_8H and d["rate_capped"] is True
    assert d["rate_source"] == "CURRENT_FUNDING"
    assert bot._effective_funding_rate_8h(0.0008, 0.0) == d["rate_used"]  # the rate itself is unchanged


def test_uncapped_and_accrued_fallback_and_zero():
    assert bot._funding_rate_detail(4.8e-05, 0.1)["rate_capped"] is False
    d = bot._funding_rate_detail(0.0, -0.0009)
    assert d["rate_source"] == "NEXT_FUNDING_ACCRUED" and d["rate_used"] == -bot.FUNDING_RATE_CAP_PER_8H and d["rate_capped"]
    assert bot._funding_rate_detail(0.0, 0.0)["rate_source"] == "ZERO"


def test_row_fields_are_unrounded_and_tiny_funding_is_visible():
    pos = {"funding_fees": 0.000312, "funding_settlements": [{"ts": 1, "rate": 5e-4, "source": "CURRENT_FUNDING"}]}
    f = bot._funding_raw_fields(pos, 80000.0, 0.0003)
    assert f["funding_usd_raw"] == 0.000312 and round(0.000312, 2) == 0.0
    assert abs(f["funding_bp_raw"] - 0.000312 / 24.0 * 1e4) < 1e-9
    assert f["funding_rate_source_detail"] == ["CURRENT_FUNDING"]
    assert bot._funding_raw_fields({}, 0.0, 0.0)["funding_bp_raw"] is None


def test_settlement_entry_carries_raw_used_capped_source(monkeypatch):
    monkeypatch.setattr(bot, "refresh_funding_state", lambda *a, **k: None)
    monkeypatch.setattr(bot, "FUNDING_SIMULATION_ENABLED", True)
    with bot.state_lock:
        bot.state["funding"] = {"rate": 0.0005, "rate_detail": {"rate_raw": 0.0009, "rate_capped": True, "rate_source": "CURRENT_FUNDING"}}
    pos = {"dir": "LONG", "entry": 80000.0, "qty": 0.0003, "entry_ts": 1792310390.0, "last_funding_accrual_ts": 1792310390.0}
    bot.accrue_position_funding(pos, now=1792310410.0, refresh=False)
    s = pos["funding_settlements"][0]
    assert s["ts"] == 1792310400 and s["rate_raw"] == 0.0009 and s["rate_used"] == 0.0005 and s["capped"] is True
    assert s["source"] == "CURRENT_FUNDING"
