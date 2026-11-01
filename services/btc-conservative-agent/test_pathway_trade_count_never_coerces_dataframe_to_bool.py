"""Quarantined pre-existing red (0ea19749). Boss follow-up card 2026-11-01. Do not fix here."""
from pathlib import Path

ANALYZER = Path(__file__).resolve().parent / "analyzer_research_engine_v62.py"


def test_pathway_trade_count_never_coerces_dataframe_to_bool() -> None:
    source = ANALYZER.read_text(encoding="utf-8")

    assert "len(trades or [])" not in source
    assert "if trades is not None and not trades.empty:" in source
    assert 'if "trade_id" in trades.columns:' in source
    assert "stc = len(trades)" in source
