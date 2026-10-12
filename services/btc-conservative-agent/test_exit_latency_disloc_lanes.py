"""Exit-latency must cover Tile 6 · DISLOC / Tile 7 · DISLOC-FOI and drop retired lanes."""
import exit_latency_slo as els
import monitor_checks as mc
from combo_pathway_config import ACTIVE_TILE_ORDER, RETIRED_TILE_LANES


DISLOC = "FAMILY_DISLOC_BFX_DISLOCATION_1M"
FOI = "FAMILY_DISLOC_FOI_LONG_1M"


def test_paper_rows_from_disloc_ledger_shape():
    rows = els.paper_rows_from_disloc_ledger([
        {"event": "DISLOC_CLOSED", "trade_id": "dsl-1", "exit_ts": 200.0, "exit_reason": "TIME_60S",
         "signal_ts": 100.0, "fill_ts": 101.5, "research_lane": DISLOC},
        {"event": "DISLOC_FILLED", "trade_id": "dsl-open", "fill_ts": 150.0},
        {"event": "DISLOC_CLOSED", "trade_id": "dsl-old", "exit_ts": 50.0, "exit_reason": "TIME_60S"},
    ], lane=DISLOC, since=100.0)
    assert len(rows) == 1 and rows[0]["trade_id"] == "dsl-1"
    ev = rows[0]["event_timestamps"]
    assert ev["exit_trigger_ts"] == 200.0 and ev["exit_fill_ts"] == 200.0
    assert ev["signal_to_fill_sec"] == 1.5
    slo = els.evaluate(rows, since=100.0)
    assert slo["n"] == 1 and slo["status"] == "GREEN"


def test_filter_drops_retired_keeps_disloc():
    per = {
        "FAMILY_DANISH_REGIME_ROUTER": {"exit": {"max_sec": 1.0}},
        "FAMILY_FADE_POOL": {"exit": {"max_sec": 1.0}},
        "FAMILY_GS07_FAST_PREMIUM_FADE": {"exit": {"max_sec": 1.0}},
        "FAMILY_COMMITTED_FADE_TAKER_90": {"exit": {"max_sec": 1.0}},
        DISLOC: {"exit": {"max_sec": 0.0}},
        FOI: {"exit": {"max_sec": 0.0}},
        "FAMILY_GS01_XV_PREMIUM_ATR_TP": {"exit": {"max_sec": 0.5}},
    }
    out = els.filter_active_per_tile(per, active=ACTIVE_TILE_ORDER, retired=RETIRED_TILE_LANES)
    assert DISLOC in out and FOI in out and "FAMILY_GS01_XV_PREMIUM_ATR_TP" in out
    assert "FAMILY_DANISH_REGIME_ROUTER" not in out
    assert "FAMILY_FADE_POOL" not in out
    assert "FAMILY_GS07_FAST_PREMIUM_FADE" not in out
    assert "FAMILY_COMMITTED_FADE_TAKER_90" not in out


def test_live_on_missing_from_exit_latency_is_red():
    out = mc.latency_guard({}, { "FAMILY_GS01_XV_PREMIUM_ATR_TP": {"exit": {"max_sec": 0.5}} },
                           live_on_lanes=[DISLOC])
    assert out["status"] == "RED"
    assert any("live-ON but missing" in r for r in out["reasons"])


def test_live_exit_slo_amber_and_red():
    amber = mc.latency_guard({}, {DISLOC: {"exit": {"max_sec": 0.0}, "live_exit": {"max_sec": 4.5}}},
                             live_on_lanes=[DISLOC])
    assert amber["status"] == "AMBER"
    red = mc.latency_guard(
        {},
        {DISLOC: {"exit": {"max_sec": 0.0},
                  "live_exit": {"max_sec": 12.0,
                                "breaches_over_8s": [{"top_hop": "paper_exit->tick"}]}}},
        live_on_lanes=[DISLOC],
    )
    assert red["status"] == "RED"
    assert any("top hop paper_exit->tick" in r for r in red["reasons"])


def test_bot_wires_disloc_fold_and_live_on_guard():
    src = open("bot.py", encoding="utf-8").read()
    assert "def _exit_latency_closed_trade_rows" in src
    assert "paper_rows_from_disloc_ledger" in src
    assert "live_on_lanes=inp.get(\"live_on_lanes\")" in src
    assert "DISLOC_EXIT_LANES" in src or "_exit_latency_per_tile" in src


def test_active_roster_includes_disloc_tiles():
    assert DISLOC in ACTIVE_TILE_ORDER and FOI in ACTIVE_TILE_ORDER
    for retired in ("FAMILY_DANISH_REGIME_ROUTER", "FAMILY_FADE_POOL",
                    "FAMILY_GS07_FAST_PREMIUM_FADE", "FAMILY_COMMITTED_FADE_TAKER_90"):
        assert retired in RETIRED_TILE_LANES
        assert retired not in ACTIVE_TILE_ORDER
