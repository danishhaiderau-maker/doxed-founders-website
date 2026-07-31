"""Regression checks for direction-only analyzer presentation."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from research import research_dashboard as dashboard


ROOT = Path(__file__).resolve().parent
DASHBOARD_SOURCE = (ROOT / "research" / "research_dashboard.py").read_text(
    encoding="utf-8"
)


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _with_roots(root: Path) -> tuple[Path, Path, Path]:
    previous = (dashboard.ROOT, dashboard.DATA_ROOT, dashboard.HISTORY_ROOT)
    dashboard.ROOT = root
    dashboard.DATA_ROOT = root
    dashboard.HISTORY_ROOT = root
    dashboard._API_RESPONSE_CACHE.clear()
    return previous


def _restore_roots(previous: tuple[Path, Path, Path]) -> None:
    dashboard.ROOT, dashboard.DATA_ROOT, dashboard.HISTORY_ROOT = previous
    dashboard._API_RESPONSE_CACHE.clear()


def test_direction_only_payload_suppresses_confidence_and_returns_gap_rows() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        previous = _with_roots(root)
        try:
            _write(
                root / "ai_calibration_report.json",
                {
                    "calibration_status": "DIRECTION_ONLY",
                    "confidence_buckets": [
                        {
                            "bucket": "65+",
                            "trades": 99,
                            "win_rate_pct": 99,
                            "sum_pnl_usd": 99,
                        }
                    ],
                },
            )
            _write(
                root / "confidence_band_report.json",
                {
                    "filled_trades_by_band": [
                        {
                            "bucket": "65+",
                            "trades": 99,
                            "win_rate_pct": 99,
                            "sum_pnl_usd": 99,
                        }
                    ]
                },
            )
            _write(
                root / "top_combinations_report.json",
                {
                    "top": [
                        {
                            "adx_bucket": "ADX 18-30",
                            "spread_bucket": "3",
                            "entry_mode": "CHASE 3",
                            "lane": "CONTINUOUS",
                            "trades": 10,
                            "pnl_usd": 5,
                            "wr_pct": 70,
                        }
                    ]
                },
            )

            payload = dashboard._ai_payload()
            assert payload["calibration_status"] == "DIRECTION_ONLY"
            assert payload["direction_only"] is True
            assert payload["calibration_buckets"] == []
            assert payload["confidence_bands"] == []
            assert payload["normalized_gap_buckets"] == [
                {
                    "spread_bucket": "3",
                    "trades": 10,
                    "wr_pct": 70.0,
                    "pnl_usd": 5.0,
                    "ev_usd": 0.5,
                }
            ]
            assert "raw gap 30 is bucket 3" in payload["normalized_gap_note"]
        finally:
            _restore_roots(previous)


def test_probability_payload_retains_historical_calibration_only_when_available() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        previous = _with_roots(root)
        try:
            _write(
                root / "ai_calibration_report.json",
                {
                    "calibration_status": "AVAILABLE",
                    "confidence_buckets": [{"bucket": "60-65", "trades": 2}],
                },
            )
            _write(
                root / "confidence_band_report.json",
                {"filled_trades_by_band": [{"bucket": "60-65", "trades": 2}]},
            )
            payload = dashboard._ai_payload()
            assert payload["direction_only"] is False
            assert payload["calibration_buckets"] == [
                {"bucket": "60-65", "trades": 2}
            ]
            assert payload["confidence_bands"] == [
                {"bucket": "60-65", "trades": 2}
            ]
        finally:
            _restore_roots(previous)


def test_dashboard_switches_views_from_calibration_status() -> None:
    assert 'id="ai-gap-view"' in DASHBOARD_SOURCE
    assert 'id="ai-confidence-view" style="display:none"' in DASHBOARD_SOURCE
    assert "const showConfidence = status === 'AVAILABLE';" in DASHBOARD_SOURCE
    assert "confidenceView.style.display = showConfidence ? '' : 'none'" in DASHBOARD_SOURCE
    assert "gapView.style.display = showConfidence ? 'none' : ''" in DASHBOARD_SOURCE
    assert "normalized_gap_buckets" in DASHBOARD_SOURCE
    assert "Normalized score gap = abs(LONG score - SHORT score) / 10." in DASHBOARD_SOURCE


def main() -> None:
    tests = (
        test_direction_only_payload_suppresses_confidence_and_returns_gap_rows,
        test_probability_payload_retains_historical_calibration_only_when_available,
        test_dashboard_switches_views_from_calibration_status,
    )
    for test in tests:
        test()
        print(f"[PASS] {test.__name__}")
    print(f"PASS: {len(tests)} direction-only analyzer UI checks")


if __name__ == "__main__":
    main()
