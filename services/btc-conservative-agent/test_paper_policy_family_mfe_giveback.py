import paper_policy_family_mfe_giveback as policy

def test_mfe_binding_exposes_initial_stop_gap_and_is_paper_only():
    row = policy.entry_fields("LONG", 100_000)
    assert row["paper_only"] and not row["relay_eligible"]
    assert policy.SPEC.initial_stop_atr_k is None
    assert policy.SPEC.hard_stop_margin_pct == 30.0
    assert policy.SPEC.mfe_giveback_fraction == 0.20
    assert policy.POLICY_ID == "OFFSET_0.30_CHASE_w234_s50_i180|ATR_TP_2.5_GIVEBACK_20PCT"
