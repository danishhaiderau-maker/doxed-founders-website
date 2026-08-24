import paper_policy_offset029_regime as policy


def test_strong_profile_requires_direction_alignment_and_nonweakening_adx():
    assert policy.classify_regime(
        direction="LONG", market_regime="BULL", trend_state="BULL",
        base_state="BULL", adx=31,
    ) == "STRONG_ALIGNED_TREND"
    assert policy.classify_regime(
        direction="LONG", market_regime="BEAR", trend_state="BEAR",
        base_state="BEAR", adx=40,
    ) == "ORDINARY_TREND"
    assert policy.classify_regime(
        direction="LONG", market_regime="BULL", trend_state="BULL_WEAKENING",
        base_state="BULL", adx=40,
    ) == "ORDINARY_TREND"
    assert policy.classify_regime(
        direction="SHORT", market_regime="RANGE", trend_state="MIXED",
        base_state="BEAR", adx=40,
    ) == "SIDEWAYS"


def test_regime_transition_is_dynamic_but_never_widens_existing_risk():
    tightened = policy.transition(
        previous_regime="STRONG_ALIGNED_TREND", observed_regime="SIDEWAYS",
        current_stop_distance_atr=1.25,
    )
    assert tightened["changed"] is True
    assert tightened["applied_stop_atr"] == 0.75
    assert tightened["risk_widened"] is False

    refused_widen = policy.transition(
        previous_regime="SIDEWAYS", observed_regime="STRONG_ALIGNED_TREND",
        current_stop_distance_atr=0.75,
    )
    assert refused_widen["to"] == "STRONG_ALIGNED_TREND"
    assert refused_widen["applied_stop_atr"] == 0.75


def test_sideways_profile_stops_adverse_trade_and_books_registered_partial():
    stopped = policy.exit_action(
        entry=100, direction="LONG", price=99.25, atr_abs=1,
        age_sec=30, regime="SIDEWAYS",
    )
    assert stopped["reason"] == "REGIME_ATR_STOP"
    assert stopped["remaining_fraction"] == 0

    partial = policy.exit_action(
        entry=100, direction="LONG", price=100.75, atr_abs=1,
        age_sec=30, regime="SIDEWAYS",
    )
    assert partial["reason"] == "REGIME_PARTIAL_TP_0.75_ATR"
    assert partial["close_fraction"] == 0.25
    assert partial["remaining_fraction"] == 0.75


def test_short_direction_and_time_cap_are_symmetric_and_terminal():
    stopped = policy.exit_action(
        entry=100, direction="SHORT", price=101, atr_abs=1,
        age_sec=30, regime="ORDINARY_TREND",
    )
    assert stopped["reason"] == "REGIME_ATR_STOP"
    timed = policy.exit_action(
        entry=100, direction="SHORT", price=99.9, atr_abs=1,
        age_sec=7200, regime="ORDINARY_TREND",
    )
    assert timed["reason"] == "PATH_END_120M"
    assert timed["remaining_fraction"] == 0


def test_entry_is_paper_capable_but_partial_relay_is_fail_closed():
    row = policy.entry_fields("LONG", 100)
    assert row["ai_direct_limit"] == 99.71
    assert row["relay_configured"] is True
    assert row["relay_eligible"] is False
    assert row["relay_copy_readiness"] == "BLOCKED_PARTIAL_CLOSE_UNSUPPORTED"
    assert row["margin_cap_usd"] == 0.25


def test_default_position_size_never_exceeds_quarter_dollar_margin():
    sized = policy.account_risk_quantity(
        equity_usd=500, account_risk_pct=0.5, entry_price=100,
        atr_abs=1, leverage=100,
    )
    assert sized["margin_cap_usd"] == 0.25
    assert sized["quantity"] <= 0.25


def test_dashboard_describes_actual_quarter_dollar_protection_and_relay_block():
    dashboard = policy.dashboard_policy()
    assert "$0.25 margin cap" in dashboard["filter_chips"]
    detail = " | ".join(dashboard["strategy_detail"])
    assert "stop and account risk never widen" in detail
    assert "fail-closed until partial-close relay support is verified" in detail
    assert "$2 margin" not in detail
