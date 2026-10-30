"""Policy identity: decision and order/execution TTLs must hash to ONE signature (active tiles)."""
import research_v3_bridge as bridge

EPOCH = "ce-test"
# B1/B2 retired 2026-10-30 (R22); pin identity on remaining active/kept lanes.
LANES = ("FAMILY_GS01_XV_PREMIUM_ATR_TP", "FAMILY_RANDOM_CONTROL_TAKER_90")


def _sig(lane, ttl):
    src = {"research_lane": lane, "policy_id": "P", "entry_ttl_sec": ttl, "entry_limit_policy": "TOUCH"}
    return bridge._paper_policy_identity(EPOCH, src)


def test_decision_and_execution_ttl_give_one_signature_for_regime_lanes():
    for lane in LANES:
        registry_ttl = bridge._registry_declared_entry_ttl(lane)
        a, b = _sig(lane, float(registry_ttl)), _sig(lane, 660.0)
        assert a["policy_signature"] == b["policy_signature"]
        assert a["paper_policy_spec"]["declared_entry_ttl_sec"] == float(registry_ttl)


def test_source_order_does_not_matter():
    lane = LANES[0]
    s1 = {"research_lane": lane, "policy_id": "P", "entry_ttl_sec": 660.0}
    s2 = {"research_lane": lane, "policy_id": "P", "entry_ttl_sec": 3.0}
    assert (bridge._paper_policy_identity(EPOCH, s1, s2)["policy_signature"]
            == bridge._paper_policy_identity(EPOCH, s2, s1)["policy_signature"])


def test_lane_outside_registry_still_uses_sources():
    a = bridge._paper_policy_identity(EPOCH, {"research_lane": "NOT_A_TILE", "policy_id": "P", "entry_ttl_sec": 900.0})
    assert a["paper_policy_spec"]["declared_entry_ttl_sec"] == 900.0


def test_registry_ttl_helper_never_raises():
    assert bridge._registry_declared_entry_ttl(None) is None
    assert bridge._registry_declared_entry_ttl("FAMILY_GS01_XV_PREMIUM_ATR_TP") == 3.0
    assert bridge._registry_declared_entry_ttl("FAMILY_GSB1_CVD_DIV_REGIME") is None  # retired R22


def test_every_registry_lane_has_one_signature_whatever_ttl_a_source_carries():
    """Covers CTRL/GS-01: decision and order sources may carry different TTLs; identity cannot fork."""
    from combo_pathway_config import ACTIVE_TILE_REGISTRY
    for lane, spec in ACTIVE_TILE_REGISTRY.items():
        registry_ttl = float(spec["entry_ttl_sec"])
        sigs = {_sig(lane, ttl)["policy_signature"] for ttl in (None, 3.0, 5.0, 660.0, 1200.0, 1800.0)}
        assert len(sigs) == 1, lane
        assert _sig(lane, 1800.0)["paper_policy_spec"]["declared_entry_ttl_sec"] == registry_ttl


def test_research_resolution_deadline_follows_the_canonical_ttl_for_gs01_gs06_and_regime_lanes():
    """resolution_deadline_ts = signal_ts + declared_entry_ttl_sec + allowance, so it now reads the registry TTL."""
    from combo_pathway_config import ACTIVE_TILE_REGISTRY
    for lane, spec in ACTIVE_TILE_REGISTRY.items():
        ttl = float(spec["entry_ttl_sec"])
        paper = _sig(lane, 1800.0)["paper_policy_spec"]
        deadline = 100.0 + paper["declared_entry_ttl_sec"] + paper["entry_reconciliation_allowance_sec"]
        assert deadline == 100.0 + ttl + bridge.ENTRY_RECONCILIATION_ALLOWANCE_SEC, lane
