import paper_policy_family_atr_trail as policy

def test_atr_trail_binding_is_exact_and_paper_only():
    row = policy.entry_fields("LONG", 100_000)
    assert row["planned_limit_price"] == 99_960
    assert row["paper_only"] and not row["relay_eligible"]
    assert policy.SPEC.chase_windows == (0, 1, 2, 3, 4, 5)
    assert policy.SPEC.trail_activation_atr_k == 1.25
    assert policy.SPEC.trail_atr_k == 1.0
