"""RK 25 Oct 06:29 (2a) / R22 2026-10-30: only GS-01 (gs1) may ever be operator-eligible;
FADE3 G6 gate deleted; every other tile is refused in code whatever the admin action."""
import live_copy_control as lcc
import live_tile_gate as ltg
from combo_pathway_config import ACTIVE_TILE_REGISTRY, ACTIVE_TILE_ORDER


def test_allowed_set_is_exactly_gs1():
    assert ltg.OPERATOR_ELIGIBLE_TILE_KEYS == frozenset({"gs1"})
    assert not hasattr(ltg, "FADE3_HM_GATE_ENV")
    assert not hasattr(ltg, "G6_GATED_TILE")


def test_every_other_active_tile_refused_on_set_and_on_read(tmp_path, monkeypatch):
    el = lcc.LiveEligibility(path=tmp_path / "elig.json")
    seen_other = False
    for lane in ACTIVE_TILE_ORDER:
        key = str((ACTIVE_TILE_REGISTRY.get(lane) or {}).get("id_prefix") or "").lower()
        ok, row = el.set(lane, True, by="admin", reason="test")
        if key == "gs1":
            assert ok and el.is_eligible(lane), lane
        else:
            seen_other = True
            assert not ok and row["live_eligible"] is False and row["error"] == "TILE_NEVER_OPERATOR_ELIGIBLE", (lane, row)
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


def test_retired_fade3_and_b_prefixes_never_operator_eligible():
    reg = {"FADE3": {"id_prefix": "gf3"}, "GS01": {"id_prefix": "gs1"}, "B1": {"id_prefix": "gb1"}}
    assert ltg.operator_eligibility_refusal("FADE3", reg) == "TILE_NEVER_OPERATOR_ELIGIBLE"
    assert ltg.operator_eligibility_refusal("B1", reg) == "TILE_NEVER_OPERATOR_ELIGIBLE"
    assert ltg.operator_eligibility_refusal("GS01", reg) is None


# ---- RK 25 Oct 06:35 (3): arming is an allowlist (gs1 only); no per-name denylist
import pathlib
import pytest

_NAMES = ["DISLOC", "DISLOC-FOI", "H-A", "ROUTER", "FADE_POOL", "GS07", "zz-arbitrary string", "", "GS01 ", "gs1"]


@pytest.mark.parametrize("name", _NAMES)
def test_unknown_and_retired_names_refused(name):
    assert ltg.operator_eligibility_refusal(name) is not None, name


def test_switch_evaluate_refuses_non_allowlisted_lanes(tmp_path):
    import bitfinex_live_switch as bls
    sw = bls.BitfinexLiveSwitch(sidecar=tmp_path / "sw.json")
    for name in _NAMES + [l for l in ACTIVE_TILE_ORDER if str(ACTIVE_TILE_REGISTRY[l].get("id_prefix")).lower() != "gs1"]:
        d = sw.evaluate(name, operator_eligible=True)["denials"]
        assert any(x.startswith(bls.DENY_LANE_NOT_ARM_ALLOWLISTED) for x in d), (name, d)


def test_route_uses_allowlist_not_retired_denylist():
    src = (pathlib.Path(__file__).resolve().parent / "bot.py").read_text(encoding="utf-8", errors="ignore")
    start = src.index("def api_bitfinex_tile_live_orders")
    body = src[start:start + 3000]
    assert "operator_eligibility_refusal(lane)" in body and "RETIRED_TILE_LANES" not in body


def test_hm_gate_helper_still_requires_pass_colon_nonempty_ref():
    """Utility kept for stop-amendment style PASS:<ref> checks; FADE3 G6 env path deleted."""
    for bad in ("PASS", "PASSED", "pass", "PASS:", "PASS:   ", "PASSX:ref", "FAIL:ref", "", None):
        assert not ltg.hm_gate_passed(bad), bad
    for good in ("PASS:hm-r3", " pass : ref-1 "):
        assert ltg.hm_gate_passed(good)
