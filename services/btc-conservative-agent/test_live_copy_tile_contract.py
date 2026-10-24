"""Live-copy tile contract (Danish 12 Oct 2026 09:19 / 09:20).

Both directions, CI-enforced:
* every tile on the dashboard (ACTIVE_TILE_REGISTRY) is live-copyable through
  the ONE signed copy path: it emits signed copy intents (order pipeline or a
  registered self-sim bridge), has a website approval prefix, and has a working
  Live toggle (operator eligibility + live-orders switch accept it);
* every tile NOT on the dashboard (retired / old lanes) is absent from every
  live-copy path: website prefix list, generated website registry, Fly
  eligibility, the live-orders switch and the relay allowlists. Its data stays.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from combo_pathway_config import (ACTIVE_TILE_ORDER, ACTIVE_TILE_REGISTRY, COMBO_LANE_SPECS,
                                  RETIRED_TILE_LANES)
import live_copy_control as lc
from live_tile_gate import PAPER_ONLY_TILE_KEYS

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
TS = ROOT / "apps/api/src/trading-agents/live-copy-approval.ts"
GEN = ROOT / "packages/utils/src/generated/tile-registry.generated.ts"
BOT = HERE / "bot.py"
# The only self-sim bridge (disloc_engine -> _disloc_live_copy_bridge) was removed with
# Tiles 6/7 (23 Oct 2026). A future self-simulated tile must register its bridge here.
SELF_SIM_BRIDGES: dict = {}


def _ts_prefixes() -> dict:
    if not TS.exists():
        pytest.skip("website source not present (image build)")
    text = TS.read_text(encoding="utf-8")
    block = text[text.index("LIVE_COPY_TILE_PREFIXES"):]
    block = block[:block.index("});")]
    return dict(re.findall(r"(FAMILY_[A-Z0-9_]+):\s*'([a-z0-9]+)'", block))


def _retired_like() -> set:
    """Retired lanes plus any lane defined in code that is not on the dashboard."""
    return set(RETIRED_TILE_LANES) | (set(COMBO_LANE_SPECS) - set(ACTIVE_TILE_ORDER))


def test_every_dashboard_tile_has_website_prefix():
    ts = _ts_prefixes()
    for lane in ACTIVE_TILE_ORDER:
        if ACTIVE_TILE_REGISTRY[lane]["id_prefix"] in PAPER_ONLY_TILE_KEYS:
            assert lane not in ts, f"{lane}: live-refused paper tile must not be live-copyable on the website"
            continue
        assert ts.get(lane) == ACTIVE_TILE_REGISTRY[lane]["id_prefix"], lane


def test_every_dashboard_tile_emits_signed_intents():
    bot = BOT.read_text(encoding="utf-8")
    assert "_publish(event, trade_id, local_extra)" in bot  # pipeline tiles -> _live_copy_publish
    for lane in ACTIVE_TILE_ORDER:
        spec = ACTIVE_TILE_REGISTRY[lane]
        if spec["id_prefix"] in PAPER_ONLY_TILE_KEYS:  # gf3: live refused, so no signed intents by design
            assert not spec.get("live_copy_bridge") and spec["live_copy_eligible"] is False, lane
            continue
        if spec.get("self_simulated_paper"):
            bridge = spec.get("live_copy_bridge")
            assert bridge in SELF_SIM_BRIDGES, f"{lane}: self-simulated tile without a live-copy bridge"
            fn = SELF_SIM_BRIDGES[bridge]
            assert f"def {fn}(" in bot and "_live_copy_publish(event, tid, extra)" in bot, lane


def test_every_dashboard_tile_has_working_live_toggle(monkeypatch):
    elig = lc.LiveEligibility(path="/nonexistent-dir/x.json")
    monkeypatch.setenv("LIVE_COPY_PARTIAL_REDUCTIONS_MIRRORED", "true")
    for lane in ACTIVE_TILE_ORDER:
        spec = ACTIVE_TILE_REGISTRY[lane]
        assert not spec.get("planned"), lane
        assert spec.get("toggle_key"), lane
        assert lane in elig._active, lane
        assert lc.exchange_stop_bp_for_spec(spec) is not None, f"{lane}: no catastrophe stop -> toggle can never arm"
        ok, why = lc.effective_eligibility(spec, operator_eligible=True)
        assert ok, (lane, why)  # no exceptions (Danish 12 Oct; boss 10:06)


def test_partial_exit_mirroring_enabled_in_fly_and_railway_config():
    toml = (HERE / "fly.toml").read_text(encoding="utf-8")
    assert 'LIVE_COPY_PARTIAL_REDUCTIONS_MIRRORED = "true"' in toml
    import bitfinex_live_switch as bls
    for mod in (lc, bls):
        assert mod.PARTIAL_REDUCTION_CAPABILITY in mod.operator_waivable_capabilities(
            {"LIVE_COPY_PARTIAL_REDUCTIONS_MIRRORED": "true"})
        assert mod.PARTIAL_REDUCTION_CAPABILITY not in mod.operator_waivable_capabilities({})


def test_no_retired_tile_on_any_live_copy_path():
    ts = _ts_prefixes()
    gen = GEN.read_text(encoding="utf-8") if GEN.exists() else ""
    active_gen = re.search(r"ACTIVE_TILE_LANES[^=]*=\s*\[([^\]]*)\]", gen)
    elig = lc.LiveEligibility(path="/nonexistent-dir/x.json")
    for lane in _retired_like():
        assert lane not in ts, f"{lane} still in website LIVE_COPY_TILE_PREFIXES"
        if active_gen:
            assert f'"{lane}"' not in active_gen.group(1), f"{lane} still in generated ACTIVE_TILE_LANES"
        assert not elig.is_eligible(lane)
        assert elig.set(lane, True)[0] is False
        assert not COMBO_LANE_SPECS.get(lane, {}).get("platform_relay_eligible"), lane
    route = BOT.read_text(encoding="utf-8").split("def api_bitfinex_tile_live_orders", 1)[1][:2500]
    assert "TILE_RETIRED" in route and "RETIRED_TILE_LANES" in route


def test_disloc_tiles_retired_and_bridge_removed():
    """Tiles 6/7 removal (23 Oct 2026): DISLOC lanes are retired, off every live-copy path,
    and the self-sim bridge + its module are gone."""
    for lane in ("FAMILY_DISLOC_BFX_DISLOCATION_1M", "FAMILY_DISLOC_FOI_LONG_1M"):
        assert lane in RETIRED_TILE_LANES and lane not in ACTIVE_TILE_ORDER, lane
        assert lane not in ACTIVE_TILE_REGISTRY, lane
    assert not (HERE / "disloc_live_copy.py").exists()
    assert not (HERE / "disloc_engine.py").exists()
    assert "def _disloc_live_copy_bridge(" not in BOT.read_text(encoding="utf-8")
