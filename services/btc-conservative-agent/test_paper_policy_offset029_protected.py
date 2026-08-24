import paper_policy_offset029_protected as policy


def _act(price, **state):
    return policy.exit_action(
        entry=100.0, direction="LONG", price=price, atr_abs=1.0,
        age_sec=state.pop("age_sec", 60), **state,
    )


def test_immediate_adverse_trade_uses_initial_atr_stop_before_time_cap():
    action = _act(99.0)
    assert action.reason == "INITIAL_STOP_1_ATR"
    assert action.close_fraction == 1.0
    assert action.trigger_price == 99.0


def test_first_and_second_partial_are_independent():
    first = _act(101.0)
    assert first.reason == "PARTIAL_TP_1_ATR"
    assert first.close_fraction == 0.25
    assert first.remaining_fraction == 0.75

    second = _act(
        101.5, remaining_fraction=0.75, first_partial_done=True,
        peak_price=101.0,
    )
    assert second.reason == "PARTIAL_TP_1_5_ATR"
    assert second.close_fraction == 0.25
    assert second.remaining_fraction == 0.5


def test_break_even_arms_only_after_1_25_atr_and_trails_runner():
    assert _act(100.1, peak_price=101.2) is None
    action = _act(
        100.4, remaining_fraction=0.5, first_partial_done=True,
        second_partial_done=True, peak_price=101.5,
    )
    assert action.reason == "TRAIL_STOP_1_ATR"
    assert action.break_even_armed is True
    assert action.stop_price == 100.5


def test_final_target_and_time_cap_close_only_remaining_quantity():
    target = _act(
        102.5, remaining_fraction=0.5, first_partial_done=True,
        second_partial_done=True, break_even_armed=True, peak_price=102.0,
    )
    assert target.reason == "FINAL_TP_2_5_ATR"
    assert target.close_fraction == 0.5

    timed = _act(100.2, age_sec=7200, remaining_fraction=0.5)
    assert timed.reason == "PATH_END_120M"
    assert timed.close_fraction == 0.5


def test_short_policy_is_direction_symmetric():
    stop = policy.exit_action(
        entry=100.0, direction="SHORT", price=101.0, atr_abs=1.0,
    )
    assert stop.reason == "INITIAL_STOP_1_ATR"
    first = policy.exit_action(
        entry=100.0, direction="SHORT", price=99.0, atr_abs=1.0,
    )
    assert first.reason == "PARTIAL_TP_1_ATR"


def test_entry_identity_is_distinct_but_entry_price_matches_tile_one():
    row = policy.entry_fields("LONG", 100.0)
    assert row["ai_direct_limit"] == 99.71
    assert row["policy_id"] == policy.POLICY_ID
    assert row["exit_profile_id"] == policy.EXIT_PROFILE_ID
    assert row["paper_only"] is False
    assert row["relay_eligible"] is False
    assert row["relay_configured"] is True
    assert row["relay_copy_readiness"] == "BLOCKED_PARTIAL_CLOSE_UNSUPPORTED"
