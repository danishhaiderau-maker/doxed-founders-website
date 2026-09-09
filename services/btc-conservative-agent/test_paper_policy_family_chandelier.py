import paper_policy_family_chandelier as policy
from combo_pathway_config import SCORE_LED_PAPER_RESEARCH_ENABLED

def test_chandelier_binding_is_exact_and_paper_only():
    row = policy.entry_fields("LONG", 100_000)
    assert row["planned_limit_price"] == 99_700
    assert row["paper_only"] and not row["relay_eligible"]
    expected = "OFFSET_0.30_CHASE_w234_s50_i180|CHANDELIER_1.5"
    if SCORE_LED_PAPER_RESEARCH_ENABLED:
        expected = "SCORE_LED_PAPER_V1::" + expected
    assert policy.POLICY_ID == expected
    assert policy.SPEC.policy_id == expected
    assert policy.SPEC.chandelier_atr_k == 1.5
    assert policy.SPEC.initial_stop_atr_k == 2.0
    assert policy.SPEC.trail_activation_atr_k == 1.0
