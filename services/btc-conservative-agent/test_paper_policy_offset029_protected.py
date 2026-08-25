import paper_policy_offset029_protected as policy


def test_static_policy_identity_and_fail_closed_relay():
    fields = policy.entry_fields("LONG", 100.0)
    assert fields["policy_id"] == policy.POLICY_ID
    assert fields["paper_only"] is True
    assert fields["relay_eligible"] is False


def test_static_stop_partials_break_even_and_final_target():
    stopped = policy.exit_action(entry=100, direction="LONG", price=98.9, atr_abs=1)
    assert stopped.reason == "INITIAL_STOP_1_ATR"
    assert stopped.remaining_fraction == 0
    first = policy.exit_action(entry=100, direction="LONG", price=101, atr_abs=1)
    assert first.reason == "PARTIAL_TP_1_ATR"
    assert first.remaining_fraction == 0.75
    second = policy.exit_action(
        entry=100, direction="LONG", price=101.5, atr_abs=1,
        remaining_fraction=.75, first_partial_done=True,
    )
    assert second.reason == "PARTIAL_TP_1_5_ATR"
    final = policy.exit_action(
        entry=100, direction="LONG", price=102.5, atr_abs=1,
        remaining_fraction=.5, first_partial_done=True, second_partial_done=True,
        break_even_armed=True, peak_price=102.5,
    )
    assert final.reason == "FINAL_TP_2_5_ATR"
    assert final.remaining_fraction == 0


def test_static_time_cap_and_account_risk_cap():
    action = policy.exit_action(entry=100, direction="SHORT", price=100, atr_abs=1, age_sec=7200)
    assert action.reason == "PATH_END_120M"
    sizing = policy.account_risk_quantity(equity_usd=1000, entry_price=100, atr_abs=1, leverage=100)
    assert sizing["quantity"] <= policy.MARGIN_CAP_USD * 100 / 100

