"""LIVE-FIX-1484 (Danish 12 Oct 18:22): deploys restore only the pre-deploy
live-orders set (never a hardcoded GS-01 list); tile Live toggles are one click."""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
WF = (ROOT / ".github/workflows/fly-bot-deploy.yml").read_text()
BOT = (ROOT / "services/btc-conservative-agent/bot.py").read_text()


def test_snapshot_precedes_maintenance_and_flatten():
    snap = WF.index("Snapshot current Bitfinex live-orders tile set")
    job = WF.index("\n  test-and-deploy:")
    assert job < snap
    assert snap < WF.index("Enter durable authenticated paper maintenance boundary", job)
    assert snap < WF.index("Force flat (paper + live) and verify on the exchange immediately before deploy", job)


def test_restore_uses_snapshot_and_falls_back_to_disloc_only():
    start = WF.index("Restore the pre-deploy Bitfinex live-orders tile set")
    block = WF[start:WF.index("      - name:", start + 10)]
    assert "steps.live_set.outputs.live_lanes" in block
    assert 'LIVE = ("FAMILY_DISLOC_BFX_DISLOCATION_1M",)' in block
    assert "FAMILY_GS01_XV_PREMIUM_ATR_TP" not in block
    assert "FAMILY_DISLOC_FOI_LONG_1M" not in block
    assert "nothing to restore" in block


def test_tile_live_toggle_is_one_click():
    start = BOT.index("async function toggleBitfinexTileLive(")
    fn = BOT[start:BOT.index("function renderAiBandGateStatus", start)]
    assert "confirm(" not in fn
    assert "alert(" not in fn
    assert "b.disabled = true" in fn
    assert re.search(r"toggleBitfinexTileLive\(\\\\'' \+ cardEsc\(spec\.lane\) \+ '\\\\', ' \+ \(tileLiveRequested \? 'false' : 'true'\) \+ ', this\)", BOT)
