import paper_policy_family_chandelier as policy

def test_chandelier_binding_is_exact_and_paper_only():
    row = policy.entry_fields("LONG", 100_000)
    assert row["planned_limit_price"] == 99_970
    assert row["paper_only"] and not row["relay_eligible"]
    assert policy.SPEC.chandelier_atr_k == 3.0
    assert policy.SPEC.initial_stop_atr_k == 2.0
