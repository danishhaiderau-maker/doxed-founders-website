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
import time
from pathlib import Path

import pytest

from combo_pathway_config import (ACTIVE_TILE_ORDER, ACTIVE_TILE_REGISTRY, COMBO_LANE_SPECS,
                                  RETIRED_TILE_LANES)
import live_copy_control as lc

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
TS = ROOT / "apps/api/src/trading-agents/live-copy-approval.ts"
GEN = ROOT / "packages/utils/src/generated/tile-registry.generated.ts"
BOT = HERE / "bot.py"
SELF_SIM_BRIDGES = {"disloc_engine": "_disloc_live_copy_bridge"}


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
        assert ts.get(lane) == ACTIVE_TILE_REGISTRY[lane]["id_prefix"], lane


def test_every_dashboard_tile_emits_signed_intents():
    bot = BOT.read_text(encoding="utf-8")
    assert "_publish(event, trade_id, local_extra)" in bot  # pipeline tiles -> _live_copy_publish
    for lane in ACTIVE_TILE_ORDER:
        spec = ACTIVE_TILE_REGISTRY[lane]
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


def test_disloc_bridge_lifecycle_and_signal_age():
    import disloc_live_copy as d
    now = time.time()
    base = {"trade_id": "dsl-1-1", "research_lane": "FAMILY_DISLOC_BFX_DISLOCATION_1M", "side": "LONG",
            "side_tag": "LONG_CONFIRMED_BY_DEEP_SEARCH", "signal_ts": now - 0.4, "signal_bid": 100000.0,
            "signal_ask": 100010.0}
    [(ev, tid, x)] = d.disloc_live_copy_events({**base, "event": "DISLOC_ORDER",
                                                "order_type": "MARKET_TAKER_AT_REAL_TOUCH_AFTER_DELAY"}, now=now)
    assert ev == "ORDER_PLACED" and tid == "dsl-1-1" and x["executable"] is True
    assert x["limit_price"] >= 100010.0 * 1.0005 and x["entry_order_semantics"] == "TAKER_IOC"
    stale = d.disloc_live_copy_events({**base, "event": "DISLOC_ORDER", "signal_ts": now - 2.5,
                                       "order_type": "MARKET_TAKER_AT_REAL_TOUCH_AFTER_DELAY"}, now=now)
    assert stale == []
    assert d.disloc_live_copy_events({**base, "event": "DISLOC_FILLED", "fill_price": 100010.0})[0][0] == "POSITION_OPENED"
    [(ev, _, x)] = d.disloc_live_copy_events({**base, "event": "DISLOC_CLOSED", "exit_price": 100020.0,
                                              "exit_reason": "TIME_60S"})
    assert ev == "POSITION_CLOSED" and x["exit_price"] == 100020.0
    assert d.disloc_live_copy_events({**base, "side": "SHORT", "side_tag": "SHORT_SHADOW_ONLY_NOT_TRADED",
                                      "event": "DISLOC_FILLED", "fill_price": 1.0}) == []


def test_disloc_approval_carries_signal_age_bound_and_stop():
    for lane in ("FAMILY_DISLOC_BFX_DISLOCATION_1M", "FAMILY_DISLOC_FOI_LONG_1M"):
        spec = ACTIVE_TILE_REGISTRY[lane]
        a = lc.build_approval(event="ORDER_PLACED", trade_id=spec["id_prefix"] + "-1-1", lane=lane, spec=spec,
                              output={"enabled": True}, tile_row={}, entry_allowed=True, trade_approved_at_ts=None,
                              created_at_ts=1.0, signal_at_ts=1.0)
        assert a["max_signal_age_sec"] == 2.0
        assert 5 < a["exchange_stop_bp"] <= lc.exchange_stop_cap_bp()
