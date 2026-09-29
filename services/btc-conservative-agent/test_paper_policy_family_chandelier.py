import paper_policy_family_chandelier as policy

def test_chandelier_binding_is_exact_and_paper_only():
    row = policy.entry_fields("LONG", 100_000)
    assert row["planned_limit_price"] == 99_700
    assert row["paper_only"] and not row["relay_eligible"]
    assert policy.POLICY_ID == "OFFSET_0.30_CHASE_w234_s50_i180|CHANDELIER_1.5"
    assert policy.SPEC.chandelier_atr_k == 1.5
    assert policy.SPEC.initial_stop_atr_k == 2.0
    assert policy.SPEC.trail_activation_atr_k == 1.0
