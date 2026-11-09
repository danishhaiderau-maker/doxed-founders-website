"""R7 costs and Roll fit leakage. Synthetic prices only."""
from datetime import date, timedelta

import pytest

from research import ext2_costs as costs


def _days(start: date, end: date):
    return costs.dates_inclusive(start, end)


def test_fee_is_zero_and_funding_is_set_aside_not_a_measured_zero():
    config = costs.cost_config()
    assert config["fee_bp"] == 0.0
    assert config["fee_enters_pnl"] is False
    assert config["funding"] == "set_aside"
    assert isinstance(config["funding"], str)
    assert config["funding"] != 0 and config["funding"] != 0.0
    assert config["funding_modelled"] is False
    assert config["funding_measured_zero"] is False
    assert config["funding_gates"] is False
    assert config["live"] is False
    assert costs.LIQUIDATION_ADVERSE_BP == pytest.approx(50.0)


def test_friction_floors_at_one_bp_and_can_only_rise_with_roll():
    assert costs.friction_bp_per_side(0.0) == 1.0
    assert costs.friction_bp_per_side(0.4) == 1.0
    assert costs.friction_bp_per_side(2.0) == pytest.approx(2.5)
    assert costs.round_trip_bp(0.0) == 2.0
    assert costs.stress_friction_bp_per_side(0.0) == pytest.approx(1.5)
    assert costs.net_bp(10.0, 0.0) == pytest.approx(8.0)
    assert costs.cost_config()["stress_tick_adverse_per_fill"] == 1


def test_same_day_and_test_day_roll_fit_is_rejected_before_those_prices_are_read():
    test_day = date(2024, 12, 17)
    train_day = date(2024, 12, 14)
    earlier = date(2024, 11, 1)
    other_test = date(2024, 12, 20)
    train = _days(date(2024, 11, 29), date(2024, 12, 14))
    test_days = _days(date(2024, 12, 17), date(2024, 12, 27))

    class Tracking(dict):
        def __init__(self):
            super().__init__()
            self.reads = []

        def __getitem__(self, key):
            self.reads.append(key)
            return dict.__getitem__(self, key)

    prices = Tracking()
    prices[train_day] = [100.0, 101.0, 100.0, 101.0, 100.0]
    prices[earlier] = [100.0, 100.5, 100.0, 100.5, 100.0]
    prices[test_day] = [10.0, 500.0, 10.0, 500.0, 10.0]
    prices[other_test] = [10.0, 800.0, 10.0]

    with pytest.raises(costs.Ext2Stop, match="STOP_ROLL_LEAK"):
        costs.fit_roll_half_spread_bp(
            prices, fit_days=[test_day], test_day=test_day, test_days=test_days, fold_train_days=train,
        )
    assert test_day not in prices.reads

    with pytest.raises(costs.Ext2Stop, match="STOP_ROLL_LEAK"):
        costs.fit_roll_half_spread_bp(
            prices, fit_days=[other_test], test_day=test_day, test_days=test_days, fold_train_days=train,
        )
    assert other_test not in prices.reads

    fitted = costs.fit_roll_half_spread_bp(
        prices, fit_days=[train_day, earlier], test_day=test_day, test_days=test_days, fold_train_days=train,
    )
    prices[test_day] = [1.0, 9_000.0, 1.0, 9_000.0]
    again = costs.fit_roll_half_spread_bp(
        prices, fit_days=[train_day, earlier], test_day=test_day, test_days=test_days, fold_train_days=train,
    )
    assert fitted == again
    assert test_day not in prices.reads
    assert set(prices.reads) <= {train_day, earlier}


def test_roll_days_move_one_day_later_and_the_new_test_day_is_banned():
    train = [date(2024, 12, 1) + timedelta(days=offset) for offset in range(5)]
    test_day = date(2024, 12, 10)
    with pytest.raises(costs.Ext2Stop, match="STOP_ROLL_LEAK"):
        costs.assert_roll_fit_days([test_day], test_day=test_day, test_days=[test_day], fold_train_days=train)
    costs.assert_roll_fit_days(train, test_day=test_day, test_days=[test_day], fold_train_days=train)
