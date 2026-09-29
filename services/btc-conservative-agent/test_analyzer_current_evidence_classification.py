"""Focused contracts for current V3.1 execution-evidence classification."""

import json
import sys
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

import analyzer_research_engine_v62 as analyzer  # noqa: E402


def _trade(**overrides):
    row = {
        "trade_id": "trade-1",
        "bot_version": "v31-five-family-atomic-paper",
        "net_pnl_usd": 0.01,
        "book_slippage_usd_entry": 0.0,
        "book_slippage_usd_exit": 0.0,
        "book_slippage_usd_total": 0.0,
        "execution_entry_type": "MAKER",
        "execution_exit_type": "TAKER",
    }
    row.update(overrides)
    return row


def test_current_v31_version_is_depth_realism_not_legacy():
    assert analyzer._bot_version_era("v31-five-family-atomic-paper") == "DEPTH_REALISM"
    assert analyzer._bot_version_era("v3.1-patient-chase") == "DEPTH_REALISM"
    assert analyzer._bot_version_era("unknown-old-writer") == "LEGACY_LAST_PRICE"


def test_explicit_zero_book_slippage_is_observed_zero(monkeypatch, tmp_path):
    monkeypatch.setattr(analyzer, "FILL_QUALITY_REPORT_FILE", str(tmp_path / "fill.json"))
    trades = pd.DataFrame([_trade(), _trade(trade_id="trade-2", net_pnl_usd=-0.01)])

    report = analyzer.realism_sim_audit(trades, pd.DataFrame())

    assert report["era_breakdown"][0]["sim_era"] == "DEPTH_REALISM"
    assert report["book_slippage"]["evidence_status"] == "OBSERVED_ZERO"
    assert report["book_slippage"]["observed_trades"] == 2
    assert report["book_slippage"]["missing_trades"] == 0


def test_missing_book_slippage_is_not_silently_coerced_to_zero(monkeypatch, tmp_path):
    monkeypatch.setattr(analyzer, "FILL_QUALITY_REPORT_FILE", str(tmp_path / "fill.json"))
    trades = pd.DataFrame([
        _trade(
            book_slippage_usd_entry=None,
            book_slippage_usd_exit=None,
            book_slippage_usd_total=None,
        )
    ])

    report = analyzer.realism_sim_audit(trades, pd.DataFrame())

    assert report["book_slippage"] == {
        "evidence_status": "MISSING",
        "observed_trades": 0,
        "missing_trades": 1,
    }


def test_constant_legacy_momentum_does_not_hide_varying_velocity(capsys):
    trades = pd.DataFrame({
        "momentum": [1.0, 1.0, 1.0],
        "features_velocity": [-0.00003, 0.00001, 0.00004],
        "net_pnl_usd": [-0.01, 0.01, 0.02],
    })

    analyzer.validate_feature_variance(trades)
    analyzer.momentum_edge(trades)
    output = capsys.readouterr().out

    assert "momentum is a constant legacy/coarse label" in output
    assert "Current continuous evidence is available as features_velocity" in output
    assert "momentum has zero variance" not in output


def test_pre_fix_terminal_double_count_is_flagged_with_separate_gross_estimate(monkeypatch, tmp_path):
    monkeypatch.setattr(analyzer, "FILL_QUALITY_REPORT_FILE", str(tmp_path / "fill.json"))
    row = _trade(
        trade_id="family-bad-1",
        research_lane="FAMILY_ATR_TARGET_2_5",
        final_direction="LONG",
        execution_entry_price=100.0,
        execution_exit_price=101.0,
        execution_qty=0.2,
        gross_pnl_usd=0.4,
        net_pnl_usd=0.39,
        partial_exit_receipts=json.dumps([{
            "remaining_fraction": 0.0,
            "cumulative_realized_net_usd": 0.2,
            "realized_gross_usd": None,
        }]),
    )

    report = analyzer.realism_sim_audit(pd.DataFrame([row]), pd.DataFrame())
    audit = report["family_terminal_double_count"]

    assert audit["evidence_status"] == "CONTAMINATED_RAW_EXCLUDED"
    assert audit["contaminated_rows"] == 1
    assert audit["rows"][0]["raw_net_pnl_usd"] == 0.39
    assert audit["rows"][0]["corrected_quantity_price_gross_usd"] == 0.2


def test_clean_terminal_receipt_keeps_prior_partial_cumulative_unchanged():
    detail = analyzer._family_terminal_double_count_detail(pd.Series({
        "partial_exit_receipts": [
            {"remaining_fraction": 0.5, "cumulative_realized_net_usd": 0.1,
             "realized_gross_usd": 0.1},
            {"remaining_fraction": 0.0, "cumulative_realized_net_usd": 0.1,
             "realized_gross_usd": None},
        ],
    }))

    assert detail == {"contaminated": False}
