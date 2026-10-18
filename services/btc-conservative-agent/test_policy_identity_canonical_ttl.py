"""B1/B2 policy identity: decision (TTL 1200) and order/execution (cell TTL 660) must hash to ONE signature."""
import research_v3_bridge as bridge

EPOCH = "ce-test"
LANES = ("FAMILY_GSB1_CVD_DIV_REGIME", "FAMILY_GSB2_REGIME_SWITCHER")


def _sig(lane, ttl):
    src = {"research_lane": lane, "policy_id": "P", "entry_ttl_sec": ttl, "entry_limit_policy": "TOUCH"}
    return bridge._paper_policy_identity(EPOCH, src)


def test_decision_and_execution_ttl_give_one_signature_for_regime_lanes():
    for lane in LANES:
        a, b = _sig(lane, 1200.0), _sig(lane, 660.0)
        assert a["policy_signature"] == b["policy_signature"]
        assert a["paper_policy_spec"]["declared_entry_ttl_sec"] == 1200.0


def test_source_order_does_not_matter():
    lane = LANES[0]
    s1 = {"research_lane": lane, "policy_id": "P", "entry_ttl_sec": 660.0}
    s2 = {"research_lane": lane, "policy_id": "P", "entry_ttl_sec": 1200.0}
    assert (bridge._paper_policy_identity(EPOCH, s1, s2)["policy_signature"]
            == bridge._paper_policy_identity(EPOCH, s2, s1)["policy_signature"])


def test_lane_outside_registry_still_uses_sources():
    a = bridge._paper_policy_identity(EPOCH, {"research_lane": "NOT_A_TILE", "policy_id": "P", "entry_ttl_sec": 900.0})
    assert a["paper_policy_spec"]["declared_entry_ttl_sec"] == 900.0


def test_registry_ttl_helper_never_raises():
    assert bridge._registry_declared_entry_ttl(None) is None
    assert bridge._registry_declared_entry_ttl("FAMILY_GSB1_CVD_DIV_REGIME") == 1200.0
