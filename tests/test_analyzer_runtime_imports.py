from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ANALYZER = (
    REPO
    / "services"
    / "btc-conservative-agent"
    / "analyzer_research_engine_v62.py"
)
BOOK10 = (
    REPO
    / "services"
    / "btc-conservative-agent"
    / "analyzer_book10_regime.py"
)


def test_trade_accumulator_uses_packaged_module_path() -> None:
    source = ANALYZER.read_text(encoding="utf-8")

    assert "from research_trade_accumulator import" not in source
    # Post-#962 AMEND2: three packaged imports remain (_adx_bucket, load_accumulated_trades_df,
    # sync_accumulator_from_analyzer_run). Regime tags no longer import compute_regime_tags.
    assert source.count("from research.research_trade_accumulator import") == 3


def test_amend2_regime_key_is_unknown_or_dir_vol_and_missing_book_raises() -> None:
    """AMEND2 contract: regime_key is UNKNOWN or ``dir|vol``; missing book raises ValueError."""
    import sys

    sys.path.insert(0, str(REPO / "services" / "btc-conservative-agent"))
    import analyzer_book10_regime as abr

    source = BOOK10.read_text(encoding="utf-8")
    assert 'UNKNOWN = "UNKNOWN"' in source
    assert '"regime_key": f"{direction}|{vol_bucket}"' in source
    assert '"regime_key": UNKNOWN' in source

    with pytest.raises(ValueError, match="book_rows"):
        abr.tags_from_trade_row(
            {"ts": abr.EPOCH_FLOOR_TS + 10},
            book_rows=None,
            trade_rows=(),
        )
    with pytest.raises(ValueError, match="book_rows"):
        abr.classify_at(abr.EPOCH_FLOOR_TS + 10, None, trade_rows=())

    pre = abr.classify_at(abr.EPOCH_FLOOR_TS - 5, [], trade_rows=())
    assert pre["regime_key"] == abr.UNKNOWN

    # ≥73 consecutive 10s mids so n_returns ≥ MIN_RETURNS (72) and reason=OK (not THIN/UNKNOWN).
    t = abr.EPOCH_FLOOR_TS + 10_000
    mid = 100_000.0
    book = []
    for i in range(80):
        ts = t - (79 - i) * abr.STEP_SEC
        mid = mid * (1.0 + 0.0002)
        book.append({"ts": float(ts), "bid": mid - 1.0, "ask": mid + 1.0})
    labeled = abr.classify_at(t, book, trade_rows=())
    key = labeled["regime_key"]
    assert labeled.get("reason") == "OK"
    assert key != abr.UNKNOWN
    direction, vol = key.split("|", 1)
    assert direction in ("bull", "bear", "sideways")
    assert vol in ("vol_high", "vol_med", "vol_low")


def test_launcher_refuses_dirty_analyzer_executable_provenance() -> None:
    launcher = (REPO / "scripts" / "start-home-analyzer.ps1").read_text(encoding="utf-8")

    assert "git -C $repoRoot status --porcelain=v1 --untracked-files=all" in launcher
    assert "services/btc-conservative-agent/research_v3_store.py" in launcher
    assert "services/btc-conservative-agent/research" in launcher
    assert "REFUSED: analyzer executable provenance is dirty" in launcher
    assert launcher.index("$dirtyAnalyzerSources") < launcher.index("$env:SOURCE_GIT_REV")
