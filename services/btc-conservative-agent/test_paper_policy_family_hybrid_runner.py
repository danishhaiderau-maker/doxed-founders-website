import paper_policy_family_hybrid_runner as policy

def test_hybrid_binding_requires_partial_reduction_and_is_paper_only():
    row = policy.entry_fields("LONG", 100_000)
    config = policy.exit_config("test")
    assert row["paper_only"] and not row["relay_eligible"]
    assert config["partial_reduction_required"] is True
    assert policy.SPEC.partial_targets == ((1.0, 0.33),)
