from family_policy_common import PolicySpec, account_risk_quantity, entry_fields, exit_action


def spec(**overrides):
    values = dict(
        policy_id="OFFSET_0.02_CHASE_w234_s25_i180|ATR_TP_2.5_ATR_SL_1.5",
        lane="FAMILY_ATR_TARGET",
        label="Fixed ATR Target",
        family="ATR_TARGET",
        entry_offset_pct=0.02,
        chase_windows=(2, 3, 4),
        chase_interval_sec=180,
        chase_step=0.25,
        initial_stop_atr_k=1.5,
        atr_target_k=2.5,
    )
    values.update(overrides)
    return PolicySpec(**values)


def test_entry_offset_is_percent_not_fraction_and_remains_paper_only():
    row = entry_fields(spec(), "LONG", 100_000)
    assert row["planned_limit_price"] == 99_980
    assert row["paper_only"] is True
    assert row["relay_eligible"] is False


def test_hard_stop_precedes_wider_atr_stop():
    action = exit_action(
        spec(initial_stop_atr_k=2.0), entry=100, direction="LONG",
        price=99.6, atr_abs=1.0, leverage=100,
    )
    assert action.reason == "PHYSICAL_HARD_STOP_30PCT"
    assert action.remaining_fraction == 0


def test_atr_target_and_stop_are_direction_symmetric():
    long_tp = exit_action(spec(), entry=100, direction="LONG", price=102.5, atr_abs=1)
    short_tp = exit_action(spec(), entry=100, direction="SHORT", price=97.5, atr_abs=1)
    assert long_tp.reason == short_tp.reason == "ATR_TP"
    long_sl = exit_action(spec(hard_stop_margin_pct=10_000), entry=100, direction="LONG", price=98.5, atr_abs=1)
    short_sl = exit_action(spec(hard_stop_margin_pct=10_000), entry=100, direction="SHORT", price=101.5, atr_abs=1)
    assert long_sl.reason == short_sl.reason == "INITIAL_ATR_STOP"


def test_hybrid_partial_is_idempotent_and_leaves_runner():
    policy = spec(
        family="HYBRID_RUNNER", atr_target_k=None, initial_stop_atr_k=1.5,
        trail_activation_atr_k=1.0, trail_atr_k=1.0,
        partial_targets=((1.0, 0.33),),
    )
    first = exit_action(policy, entry=100, direction="LONG", price=101, atr_abs=1)
    assert first.partial_key == "partial_0_1atr"
    assert round(first.remaining_fraction, 2) == 0.67
    again = exit_action(
        policy, entry=100, direction="LONG", price=101, atr_abs=1,
        remaining_fraction=first.remaining_fraction,
        completed_partials=(first.partial_key,), peak_price=first.peak_price,
    )
    assert again is None


def test_mfe_giveback_and_time_cap_are_explicit():
    policy = spec(
        family="MFE_GIVEBACK", atr_target_k=None, initial_stop_atr_k=None,
        mfe_giveback_fraction=0.2,
    )
    action = exit_action(
        policy, entry=100, direction="LONG", price=103.9, atr_abs=1,
        peak_price=105,
    )
    assert action.reason == "PROFIT_PROTECTION_STOP"
    timeout = exit_action(policy, entry=100, direction="LONG", price=100, atr_abs=1, age_sec=7200)
    assert timeout.reason == "PATH_END_120M"


def test_account_risk_sizing_never_exceeds_margin_cap():
    result = account_risk_quantity(
        spec(), equity_usd=1_000, entry_price=100_000, atr_abs=1_000, leverage=100,
    )
    assert result["quantity"] * 100_000 / 100 <= 0.25


def test_no_atr_stop_family_sizes_from_physical_hard_stop():
    result = account_risk_quantity(
        spec(initial_stop_atr_k=None),
        equity_usd=500,
        entry_price=80_000,
        atr_abs=500,
        leverage=100,
    )
    assert result["quantity"] > 0
    assert result["quantity"] * 80_000 / 100 <= 0.25
