"""Focused analyzer contract for direction-only versus probability confidence."""

import contextlib
import io
import os
import sys
import tempfile

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import analyzer_research_engine_v62 as analyzer


def _calibration_row(trade_id, confidence=None, mode="DIRECTION_ONLY", pnl=1.0):
    return {
        "trade_id": trade_id,
        "ai_confidence": confidence,
        "ai_confidence_bucket": analyzer._ai_calib_report_bucket(
            confidence,
            mode == "PROBABILITY",
        ),
        "confidence_requested": mode == "PROBABILITY",
        "confidence_mode": mode,
        "edge_score": 3.2,
        "edge_score_bucket": "3-4",
        "net_pnl_usd": pnl,
        "win": pnl > 0,
        "executed": True,
        "structure_bucket": "BULLISH",
        "participation_bucket": "NORMAL",
        "context_bucket": "MID_RANGE",
        "regime_bucket": "BULL",
    }


def main() -> int:
    checks = {
        "zero probability is direction-only": analyzer._ai_prob_bucket_val(0) == "DIRECTION_ONLY",
        "missing probability is direction-only": analyzer._ai_calib_report_bucket(None) == "DIRECTION_ONLY",
        "explicit false overrides nonzero probability": (
            analyzer._confidence_band_label(72, False) == "DIRECTION_ONLY"
        ),
        "explicit true preserves genuine probability": (
            analyzer._confidence_band_label(72, True) == "65+"
        ),
        "legacy nonzero probability remains compatible": (
            analyzer._ai_calib_report_bucket(58) == "55-60"
        ),
    }

    enriched = analyzer._enrich_trades_with_buckets(pd.DataFrame([
        {"trade_id": "direction-only-zero", "ai_win_prob": 0, "confidence_requested": False},
        {"trade_id": "direction-only-flag", "ai_win_prob": 75, "confidence_requested": False},
        {"trade_id": "probability", "ai_win_prob": 75, "confidence_requested": True},
    ]))
    buckets = dict(zip(enriched["trade_id"], enriched["ai_probability_bucket"]))
    checks.update({
        "enrichment excludes zero direction-only row": buckets["direction-only-zero"] == "DIRECTION_ONLY",
        "enrichment respects explicit false": buckets["direction-only-flag"] == "DIRECTION_ONLY",
        "enrichment retains explicit historical confidence": buckets["probability"] == "65+",
    })

    with tempfile.TemporaryDirectory() as tmp:
        original_band = analyzer.CONFIDENCE_BAND_REPORT_FILE
        original_expectancy = analyzer.AI_CONFIDENCE_EXPECTANCY_FILE
        original_calibration = analyzer.AI_CALIBRATION_REPORT_FILE
        original_builder = analyzer._build_ai_calibration_cohort
        try:
            analyzer.CONFIDENCE_BAND_REPORT_FILE = os.path.join(tmp, "bands.json")
            analyzer.AI_CONFIDENCE_EXPECTANCY_FILE = os.path.join(tmp, "expectancy.json")
            analyzer.AI_CALIBRATION_REPORT_FILE = os.path.join(tmp, "calibration.json")

            trades = pd.DataFrame([
                {"trade_id": "d0", "ai_win_prob": 0, "confidence_requested": False, "net_pnl_usd": 1.0},
                {"trade_id": "d1", "ai_win_prob": 75, "confidence_requested": False, "net_pnl_usd": -1.0},
                {"trade_id": "p1", "ai_win_prob": 70, "confidence_requested": True, "net_pnl_usd": 1.0},
            ])
            decisions = trades.drop(columns=["net_pnl_usd"])
            with contextlib.redirect_stdout(io.StringIO()):
                band_report = analyzer.confidence_band_report(
                    trades=trades,
                    decisions=decisions,
                    session={},
                )
            probability_fills = sum(row["trades"] for row in band_report["filled_trades_by_band"])
            checks.update({
                "confidence report excludes direction-only fills": probability_fills == 1,
                "confidence report counts excluded fills": (
                    band_report["direction_only"]["filled_trades"] == 2
                ),
                "confidence report counts excluded decisions": (
                    band_report["direction_only"]["ai_decisions"] == 2
                ),
            })

            direction_only = pd.DataFrame([
                _calibration_row("d0"),
                _calibration_row("d1", pnl=-1.0),
            ])
            analyzer._build_ai_calibration_cohort = lambda trades=None, session=None: direction_only.copy()
            with contextlib.redirect_stdout(io.StringIO()):
                direction_report = analyzer.ai_calibration_report(session={})
            checks.update({
                "direction-only calibration has explicit status": (
                    direction_report["calibration_status"] == "DIRECTION_ONLY"
                ),
                "direction-only calibration has no probability sample": (
                    direction_report["sample_size"]["with_ai_confidence"] == 0
                ),
                "direction-only calibration preserves excluded count": (
                    direction_report["sample_size"]["direction_only_rows"] == 2
                ),
                "direction-only calibration has no expected WR claim": (
                    direction_report["expected_vs_actual"]["overall_expected_wr_pct"] is None
                ),
                "direction-only calibration has no underconfidence claim": (
                    direction_report["underconfidence_note"] is None
                    and analyzer._ai_calibration_verdict(direction_report) is None
                ),
                "direction-only calibration has no best confidence": (
                    analyzer._best_worst_confidence_bands(direction_report, band_report)
                    == (None, None)
                ),
            })

            probability = pd.DataFrame([
                _calibration_row("p1", 60, "PROBABILITY"),
                _calibration_row("p2", 60, "PROBABILITY"),
                _calibration_row("p3", 60, "PROBABILITY"),
            ])
            analyzer._build_ai_calibration_cohort = lambda trades=None, session=None: probability.copy()
            with contextlib.redirect_stdout(io.StringIO()):
                probability_report = analyzer.ai_calibration_report(session={})
            checks.update({
                "genuine probability calibration remains available": (
                    probability_report["calibration_status"] == "AVAILABLE"
                ),
                "genuine probability rows remain counted": (
                    probability_report["sample_size"]["with_ai_confidence"] == 3
                ),
                "genuine probability verdict remains supported": (
                    "under-confident" in analyzer._ai_calibration_verdict(probability_report)
                ),
            })
        finally:
            analyzer.CONFIDENCE_BAND_REPORT_FILE = original_band
            analyzer.AI_CONFIDENCE_EXPECTANCY_FILE = original_expectancy
            analyzer.AI_CALIBRATION_REPORT_FILE = original_calibration
            analyzer._build_ai_calibration_cohort = original_builder

    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        for name in failed:
            print(f"[FAIL] {name}")
        return 1
    print(f"[PASS] {len(checks)} direction-only confidence checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
