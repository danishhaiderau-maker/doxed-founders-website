import paper_policy_family_atr_target as policy

def test_fixed_target_binding_is_exact_and_paper_only():
    row = policy.entry_fields("SHORT", 100_000)
    assert row["planned_limit_price"] == 100_270
    assert row["paper_only"] and not row["relay_eligible"]
    assert policy.SPEC.atr_target_k == 2.5
    assert policy.SPEC.initial_stop_atr_k is None
    assert policy.POLICY_ID == "OFFSET_0.27_CHASE_w234_s50_i180|ATR_TP_2.5_SCENARIO_C"
    config = policy.exit_config("test")
    assert config["trail_ladder"] == [[8, 5], [12, 10], [19, 17], [40, 28], [60, 45], [80, 60], [100, 75], [150, 120]]
    assert config["ladder_first_trigger_pct"] == 8
    assert config["ladder_first_lock_pct"] == 5
    assert config["thesis_cut_margin_pct"] == -12.0
    assert config["thesis_window_sec"] == 300
