from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from research_opportunity_v2 import append_event, child_event, opportunity_event


def _features() -> dict:
    return {
        "price": 64000, "adx": 24.1, "adx_slope_3": 1.0, "plus_di": 15.0, "minus_di": 29.0,
        "di_separation": 14.0, "atr": 90.0, "volatility_percentile": 55.0,
        "volume_ratio": 1.2, "volume_percentile": 70.0, "ret_1m": -0.001,
        "ret_5m": -0.004, "ema_slope": -0.002, "structure": -3.0,
        "momentum": -0.2, "delta": -4.0, "imbalance": -0.1,
        "velocity": -0.01, "directional_spread": 6, "long_score": 20,
        "short_score": 80, "funding_rate_pct_8h": 0.01, "session_utc": "US",
        "is_weekend": False, "hour_utc": 8, "regime": "TREND",
        "book_spread_bps": 0.4,
    }


def run() -> None:
    original = Path.cwd()
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        try:
            import analyzer_research_engine_v62 as analyzer

            append_event(tmp, opportunity_event(
                opportunity_id="scan-analyzer-v2",
                ts="2026-07-26T08:00:00Z",
                direction="SHORT",
                entry_features=_features(),
                mode="PAPER",
                bot_version=analyzer.EXPECTED_BOT_VERSION,
                analyzer_sync_id=analyzer.ANALYZER_SYNC_ID,
                policy_version="test",
            ))
            append_event(tmp, child_event(
                event="OUTCOME",
                opportunity_id="scan-analyzer-v2",
                ts="2026-07-26T08:10:00Z",
                lane="CONTINUOUS",
                mode="PAPER",
                trade_id="cont-analyzer-v2",
                payload={"filled": True, "net_pnl_usd": 2.0, "max_mfe_pct": 18.0},
            ))
            report = analyzer.type_b_research_v2_report()
            assert report["independent_opportunities"] == 1
            assert report["type_b_outcomes"] == 1
            assert report["execution_policy"] == "ADVISORY_ONLY_NEVER_AUTO_APPLY"
            saved = json.loads(Path("type_b_research_v2_report.json").read_text(encoding="utf-8"))
            assert saved["recent_opportunities"][0]["opportunity_id"] == "scan-analyzer-v2"
        finally:
            os.chdir(original)
    print("PASS: analyzer materializes and publishes Type-B Research V2")


if __name__ == "__main__":
    run()
