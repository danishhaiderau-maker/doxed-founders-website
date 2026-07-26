from __future__ import annotations

import os
import logging
import shutil
import tempfile
import time
from pathlib import Path


def run() -> None:
    original_cwd = Path.cwd()
    tmp = tempfile.mkdtemp(prefix="typeb-v2-bot-")
    try:
        os.chdir(tmp)
        try:
            import bot
            from research_opportunity_v2 import load_events, materialize

            with bot.state_lock:
                bot.state["manual_admin_pause"] = False
                bot.state["execution_paused"] = False
                bot.state["live_armed"] = False
                bot.state["bitfinex_live_enabled"] = False
                bot.state["regime"] = "TREND"
                bot.state["feature_snapshot"] = {
                    "price": 64000,
                    "adx": 27.2,
                    "adx_slope_3": 1.8,
                    "plus_di": 18.0,
                    "minus_di": 31.0,
                    "di_separation": 13.0,
                    "volatility_atr": 100.0,
                    "volatility_percentile": 62.0,
                    "volume_ratio": 1.3,
                    "volume_percentile": 72.0,
                    "ret_1m": -0.001,
                    "ret_5m": -0.004,
                    "ema_slope": -0.002,
                    "delta": -4.0,
                    "imbalance": -0.2,
                    "velocity": -0.01,
                    "dist_to_support": 0.01,
                    "dist_to_resistance": 0.02,
                    "candle_range": 120.0,
                    "body_ratio": 0.7,
                    "wick_ratio": 0.3,
                    "indicator_normalization_version": "normalized_adx_dmi_atr_volume_v2",
                    "adx_source": "market_context.trend_strength.adx",
                    "dmi_source": "wilder_dmi_14",
                    "volume_percentile_source": "rolling_candle_volume_percentile",
                }
                bot.state["market_context"] = {
                    "trend_strength": {"adx": 27.2},
                    "market_structure": {"structure_score": -3},
                }
                bot.state["ema_status"] = {"ema9": 64000, "ema21": 64100}
                bot.state["funding"] = {"rate_pct_per_8h": 0.01}
                bot.state["order_book"] = {"best_bid": 63999, "best_ask": 64001}
                bot.state["ai_history"] = []

            ai = {
                "trade_id": "scan-v2-int",
                "shared_ai_call_id": "scan-v2-int",
                "shared_ai_call_ts": "2026-07-26T08:00:00Z",
                "direction": "SHORT",
                "candidate_direction": "SHORT",
                "long_score": 20,
                "short_score": 80,
                "factors": {"long_score": 20, "short_score": 80},
                "decision": "APPROVE",
                "source": "FRESH",
                "research_lane": "AI_SCAN",
                "shadow_only": False,
            }
            frozen = bot._freeze_type_b_research_v2_entry_context(
                dict(bot.state["feature_snapshot"]),
                {"price": 64000, "funding": {"rate_pct_per_8h": 0.01}},
            )
            ai["_type_b_research_v2_entry_context"] = frozen
            ai["_type_b_research_v2_request_ts"] = ai["shared_ai_call_ts"]
            bot._append_ai_history_row(ai)

            bot.start_replay_buffer(
                "lab-cont-v2-int",
                64000,
                lane="shadow_collect_CONTINUOUS",
                direction="SHORT",
                leverage=10,
                margin_usdt=20,
                virtual_entry=64000,
                virtual_fill_t=0,
                early_fail_enabled=False,
                exit_config=bot.get_exit_config_for_lane("CONTINUOUS"),
                research_lane="CONTINUOUS",
                source_trade_id="scan-v2-int",
                shared_ai_call_id="scan-v2-int",
                collection_mode="LAB",
                entry_features=dict(bot.state["feature_snapshot"]),
                ai_snapshot=ai,
            )
            with bot.replay_lock:
                replay = bot.replay_buffers["lab-cont-v2-int"]
                replay["start_ts"] = time.time() - 20
                replay["ticks"] = [
                    {"seq": 1, "t": 0.0, "price": 64000, "unreal_pct": 0.0},
                    {"seq": 2, "t": 10.0, "price": 63800, "unreal_pct": 31.25},
                ]
            bot.finalize_shadow_lane_collecting("lab-cont-v2-int", replay)

            expiry_ai = dict(ai)
            expiry_ai.update({
                "trade_id": "scan-v2-expiry",
                "shared_ai_call_id": "scan-v2-expiry",
                "shared_ai_call_ts": "2026-07-26T08:01:00Z",
                "_type_b_research_v2_request_ts": "2026-07-26T08:01:00Z",
            })
            expiry_ai["_type_b_research_v2_entry_context"] = frozen
            bot._append_ai_history_row(expiry_ai)
            bot._record_expired_order({
                "trade_id": "cont-v2-expiry",
                "shared_ai_call_id": "scan-v2-expiry",
                "research_lane": "CONTINUOUS",
                "research_collection_mode": "PAPER",
                "signal_dir": "SHORT",
                "limit_price": 64020,
                "created_ts": time.time() - 60,
            }, "SIGNAL_TTL_EXPIRED")

            independent_ai = dict(ai)
            independent_ai.update({
                "trade_id": "srmv2-independent",
                "shared_ai_call_id": "srmv2-independent",
                "research_lane": "SR_MICRO_TILE_V2_STATIC",
                "shared_ai_call_ts": "2026-07-26T08:02:00Z",
                "_type_b_research_v2_request_ts": "2026-07-26T08:02:00Z",
            })
            bot._append_ai_history_row(independent_ai)

            opportunities = materialize(load_events(Path(tmp)))
            assert len(opportunities) == 2
            assert "srmv2-independent" not in {
                row["opportunity_id"] for row in opportunities
            }
            by_id = {row["opportunity_id"]: row for row in opportunities}
            closed = by_id["scan-v2-int"]
            assert closed["quality"]["valid_for_holdout"] is True
            assert closed["entry_features"]["directional_spread"] == 6
            assert closed["entry_features"]["book_spread_bps"] > 0
            assert closed["outcome_label"] == "TYPE_B"
            assert closed["preferred_outcome"]["mode"] == "LAB"
            assert closed["preferred_outcome"]["max_mfe_pct"] == 31.25
            expired = by_id["scan-v2-expiry"]
            assert expired["status"] == "CLOSED"
            assert expired["preferred_outcome"]["filled"] is False
            assert expired["preferred_outcome"]["exit_reason"] == "SIGNAL_TTL_EXPIRED"
            assert expired["outcome_label"] is None
        finally:
            os.chdir(original_cwd)
    finally:
        for logger_obj in [logging.getLogger()] + [
            value for value in logging.Logger.manager.loggerDict.values()
            if isinstance(value, logging.Logger)
        ]:
            for handler in list(logger_obj.handlers):
                try:
                    handler.flush()
                    handler.close()
                except Exception:
                    pass
                logger_obj.removeHandler(handler)
        shutil.rmtree(tmp, ignore_errors=True)
    print("PASS: bot preserves one Type-B V2 opportunity from AI call through close evidence")


if __name__ == "__main__":
    run()
