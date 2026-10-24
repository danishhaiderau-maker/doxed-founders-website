"""RK 25 Oct 06:29 (2a): only GS-01 (gs1) and FADE3 (gf3, under its own LIVE_HM_GATE_FADE3, unset) may ever be
operator-eligible; every other tile is refused in code whatever the admin action."""
import live_copy_control as lcc
import live_tile_gate as ltg
from combo_pathway_config import ACTIVE_TILE_REGISTRY, ACTIVE_TILE_ORDER


def test_allowed_set_is_exactly_gs1_and_gf3():
    assert ltg.OPERATOR_ELIGIBLE_TILE_KEYS == frozenset({"gs1", "gf3"})


def test_every_other_active_tile_refused_on_set_and_on_read(tmp_path, monkeypatch):
    monkeypatch.delenv("LIVE_HM_GATE_FADE3", raising=False)
    el = lcc.LiveEligibility(path=tmp_path / "elig.json")
    seen_other = False
    for lane in ACTIVE_TILE_ORDER:
        key = str((ACTIVE_TILE_REGISTRY.get(lane) or {}).get("id_prefix") or "").lower()
        ok, row = el.set(lane, True, by="admin", reason="test")
        if key == "gs1":
            assert ok and el.is_eligible(lane), lane
        else:
            seen_other = True
            assert not ok and row["live_eligible"] is False and row["error"] in (
                "TILE_NEVER_OPERATOR_ELIGIBLE", "FADE3_HM_GATE_NOT_PASSED"), (lane, row)
            assert not el.is_eligible(lane), lane
        assert el.set(lane, False)[0]   # turning OFF is always allowed
    assert seen_other


def test_persisted_eligibility_for_other_tile_is_ignored(tmp_path):
    import json
    lanes = [l for l in ACTIVE_TILE_ORDER if str(ACTIVE_TILE_REGISTRY[l].get("id_prefix")).lower() not in ltg.OPERATOR_ELIGIBLE_TILE_KEYS]
    p = tmp_path / "elig.json"
    p.write_text(json.dumps({"schema": lcc.ELIGIBILITY_SCHEMA, "rows": {l: {"lane": l, "live_eligible": True} for l in lanes}}))
    el = lcc.LiveEligibility(path=p)
    assert lanes and not any(el.is_eligible(l) for l in lanes)
    assert not set(el.status()["eligible_lanes"]) & set(lanes)


def test_gf3_needs_its_own_hm_gate():
    reg = {"FADE3": {"id_prefix": "gf3"}, "GS01": {"id_prefix": "gs1"}, "GS06": {"id_prefix": "gs6"}}
    assert ltg.operator_eligibility_refusal("FADE3", reg, env={}) == "FADE3_HM_GATE_NOT_PASSED"
    assert ltg.operator_eligibility_refusal("FADE3", reg, env={"LIVE_HM_GATE_FADE3": "PASS:hm-ref"}) is None
    assert ltg.operator_eligibility_refusal("GS01", reg, env={}) is None
    assert ltg.operator_eligibility_refusal("GS06", reg, env={"LIVE_HM_GATE_FADE3": "PASS:x"}) == "TILE_NEVER_OPERATOR_ELIGIBLE"
    assert ltg.operator_eligibility_refusal("NOPE", reg) == "TILE_NEVER_OPERATOR_ELIGIBLE"
