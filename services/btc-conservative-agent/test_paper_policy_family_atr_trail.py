import paper_policy_family_atr_trail as policy

def test_atr_trail_binding_is_exact_and_paper_only():
    row = policy.entry_fields("LONG", 100_000)
    assert row["planned_limit_price"] == 99_700
    assert row["paper_only"] and not row["relay_eligible"]
    assert policy.SPEC.chase_windows == (2, 3, 4)
    assert policy.SPEC.initial_stop_atr_k == 1.5
    assert policy.SPEC.trail_activation_atr_k == 0.75
    assert policy.SPEC.trail_atr_k == 1.0
