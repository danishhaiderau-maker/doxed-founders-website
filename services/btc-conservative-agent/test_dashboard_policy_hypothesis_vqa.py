from pathlib import Path


ROOT = Path(__file__).resolve().parent
BOT_SOURCE = (ROOT / "bot.py").read_text(encoding="utf-8")
ENGINE_SOURCE = (
    ROOT.parent / "btc-signal-engine" / "engine.py"
).read_text(encoding="utf-8")


def test_dashboard_distinguishes_benchmark_anchor_from_family_offsets():
    for source in (BOT_SOURCE, ENGINE_SOURCE):
        assert "Benchmark / legacy direct anchor (deterministic 0.1%)" in source
        assert "Fixed uses 0.27%" in source
        assert "the other four 0.30%" in source
        assert "Initial policy limit" in source
        assert "Direction-only entries use the deterministic 0.1% offset anchor" not in source


def test_dashboard_renders_truthful_hypothesis_receipt():
    for source in (BOT_SOURCE, ENGINE_SOURCE):
        assert 'const result = spec.hypothesis_result || {}' in source
        assert "Analyzer hypothesis receipt:" in source
        assert "Diagnostic promotion evidence only; this tile is the prospective paper execution test." in source
        assert '"hypothesis_result": dict(lane_spec.get("presentation", {}).get("hypothesis_result") or {})' in source


def test_every_downstream_activity_table_has_a_mobile_scroll_region():
    labels = (
        "Virtual chase candidates table",
        "Active signals table",
        "Positions table",
        "Pending orders table",
        "Expired orders table",
        "Trades table",
        "AI history table",
    )
    for source in (BOT_SOURCE, ENGINE_SOURCE):
        assert ".activity-table-scroll {" in source
        assert "overflow-x:auto" in source
        assert "touch-action:pan-x" in source
        assert source.count('class="activity-table-scroll"') == len(labels)
        for label in labels:
            assert f'aria-label="{label}"' in source


def test_main_and_signal_dashboard_sources_remain_identical():
    assert BOT_SOURCE == ENGINE_SOURCE
