import paper_policy_family_hybrid_runner as policy

def test_hybrid_binding_requires_partial_reduction_and_is_paper_only():
    row = policy.entry_fields("LONG", 100_000)
    config = policy.exit_config("test")
    assert row["paper_only"] and not row["relay_eligible"]
    assert config["partial_reduction_required"] is True
    assert policy.POLICY_ID == "OFFSET_0.30_CHASE_w234_s50_i180|HYBRID_secure_25_25_runner_TRAIL_1"
    assert policy.SPEC.partial_targets == ((1.0, 0.25), (1.5, 0.25))
