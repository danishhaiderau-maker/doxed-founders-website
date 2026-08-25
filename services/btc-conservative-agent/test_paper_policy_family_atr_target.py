import paper_policy_family_atr_target as policy

def test_fixed_target_binding_is_exact_and_paper_only():
    row = policy.entry_fields("SHORT", 100_000)
    assert row["planned_limit_price"] == 100_020
    assert row["paper_only"] and not row["relay_eligible"]
    assert policy.SPEC.atr_target_k == 2.5
    assert policy.SPEC.initial_stop_atr_k == 1.5
