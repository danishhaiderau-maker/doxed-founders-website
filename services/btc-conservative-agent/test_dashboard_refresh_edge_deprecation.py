"""Dashboard refresh must remain independent of retired Edge controls."""

from pathlib import Path


BOT_PATH = Path(__file__).resolve().parent / "bot.py"


def test_dashboard_refresh_has_no_retired_edge_formatter_reference() -> None:
    source = BOT_PATH.read_text(encoding="utf-8")
    assert "normalizeEdgeOptionValue" not in source
    assert "safeText('edgeThresholdDisplay', 'analytics only')" in source


def test_dashboard_keeps_truthful_analytics_only_edge_copy() -> None:
    source = BOT_PATH.read_text(encoding="utf-8")
    assert 'id="edgeThresholdDisplay">analytics only<' in source
    assert "EDGE STATUS: DEPRECATED" in source
