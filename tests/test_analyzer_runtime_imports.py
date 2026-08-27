from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
ANALYZER = (
    REPO
    / "services"
    / "btc-conservative-agent"
    / "analyzer_research_engine_v62.py"
)


def test_trade_accumulator_uses_packaged_module_path() -> None:
    source = ANALYZER.read_text(encoding="utf-8")

    assert "from research_trade_accumulator import" not in source
    assert source.count("from research.research_trade_accumulator import") == 4


def test_pathway_trade_count_never_coerces_dataframe_to_bool() -> None:
    source = ANALYZER.read_text(encoding="utf-8")

    assert "len(trades or [])" not in source
    assert "if trades is not None and not trades.empty:" in source
    assert 'if "trade_id" in trades.columns:' in source
    assert "stc = len(trades)" in source
