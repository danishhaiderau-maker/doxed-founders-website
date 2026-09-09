import paper_policy_family_hybrid_runner as policy
from combo_pathway_config import SCORE_LED_PAPER_RESEARCH_ENABLED

def test_hybrid_binding_requires_partial_reduction_and_is_paper_only():
    row = policy.entry_fields("LONG", 100_000)
    config = policy.exit_config("test")
    assert row["paper_only"] and not row["relay_eligible"]
    assert config["partial_reduction_required"] is True
    expected = "OFFSET_0.30_CHASE_w234_s50_i180|HYBRID_secure_25_25_runner_TRAIL_1"
    if SCORE_LED_PAPER_RESEARCH_ENABLED:
        expected = "SCORE_LED_PAPER_V1::" + expected
    assert policy.POLICY_ID == expected
    assert policy.SPEC.policy_id == expected
    assert policy.SPEC.partial_targets == ((1.0, 0.25), (1.5, 0.25))
