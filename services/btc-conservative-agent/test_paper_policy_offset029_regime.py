import paper_policy_offset029_regime as policy


def test_regime_policy_identity_and_fail_closed_relay():
    fields = policy.entry_fields("SHORT", 100.0)
    assert fields["policy_id"] == policy.POLICY_ID
    assert fields["paper_only"] is True
    assert fields["relay_eligible"] is False


def test_regime_transition_never_widens_risk():
    change = policy.transition(
        previous_regime="SIDEWAYS", observed_regime="STRONG_ALIGNED_TREND",
        current_stop_distance_atr=.75,
    )
    assert change["requested_stop_atr"] == 1.25
    assert change["applied_stop_atr"] == .75
    assert change["risk_widened"] is False


def test_regime_stop_partial_final_and_time_cap():
    stopped = policy.exit_action(
        entry=100, direction="LONG", price=99.2, atr_abs=1, age_sec=0,
        regime="SIDEWAYS", current_stop_distance_atr=.75,
    )
    assert stopped["reason"] == "REGIME_ATR_STOP"
    partial = policy.exit_action(
        entry=100, direction="LONG", price=101, atr_abs=1, age_sec=0,
        regime="ORDINARY_TREND", current_stop_distance_atr=1,
    )
    assert partial["reason"] == "REGIME_PARTIAL_TP_1_ATR"
    final = policy.exit_action(
        entry=100, direction="LONG", price=102.5, atr_abs=1, age_sec=0,
        regime="ORDINARY_TREND", current_stop_distance_atr=1,
        remaining_fraction=.5, completed_partials=(1.0, 1.5), peak_price=102.5,
    )
    assert final["reason"] == "REGIME_FINAL_TP_2_5_ATR"
    timed = policy.exit_action(
        entry=100, direction="SHORT", price=100, atr_abs=1, age_sec=7200,
        regime="ORDINARY_TREND", current_stop_distance_atr=1,
    )
    assert timed["reason"] == "PATH_END_120M"

