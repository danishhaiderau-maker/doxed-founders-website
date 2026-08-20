"""Trust gates added after the independent collector-v2.2 audit.

These tests deliberately exercise replay as an untrusted-evidence boundary.  A
stored status/exit label is not sufficient evidence: replay must independently
validate both path coverage and the economic meaning of terminal labels.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

import replay_event_report as replay
from chase_offset_touch_grid import simulate_touch_fill
from collector_v22 import make_event_episode


SIGNAL_TS = 1_700_000_000.0


def _candle(minute: int, *, high: float = 100_010.0, low: float = 99_990.0):
    ts_ms = int((SIGNAL_TS + minute * 60.0) * 1000)
    return [ts_ms, 100_000.0, high, low, 100_000.0, 1.0]


def _event(*, path_minutes: int = 10, fill_minute: int | None = None) -> dict:
    fill_ts = None if fill_minute is None else SIGNAL_TS + fill_minute * 60.0
    fill_price = None if fill_ts is None else 100_000.0
    return {
        "schema": "research_event_v2.2",
        "collector_version": "collector_v2.2",
        "event_id": "legacy-v22-premature",
        "epoch_id": "epoch-v22-test",
        "primary_outcome": "ACCEPTED_UNFILLED" if fill_ts is None else "ACCEPTED_FILLED",
        "observation_status": "FUNNEL_COMPLETE",
        "envelope": {"signal_ts": SIGNAL_TS, "direction": "SHORT"},
        "live_fill_ts": fill_ts,
        "live_fill_price": fill_price,
        "canonical_tape": {"path_1m": [_candle(i) for i in range(path_minutes)]},
        "entry_children": [],
    }


class TrustworthyV22ReplayContracts(unittest.TestCase):
    def test_legacy_premature_funnel_complete_is_replay_ineligible(self):
        """A completion label cannot override a visibly truncated market path."""
        report = replay.replay_event_report(_event(path_minutes=10))

        self.assertEqual(report["replay_status"], "REPLAY_INELIGIBLE")
        self.assertIsNone(report.get("control_outcome"))
        self.assertFalse(report.get("hypothetical_entries"))

    def test_negative_pnl_cannot_keep_profit_lock_semantics(self):
        """A negative result must be rejected or relabelled, never called profit lock."""
        event = _event(path_minutes=181, fill_minute=60)
        event["observation_status"] = "COMPLETE"
        synthetic_bad_score = {
            "chase_exit_scores": [{
                "exit": "PROFIT_LOCK_LADDER",
                "first_hit": "PROFIT_LOCK_LADDER",
                "pnl": -0.25,
                "mfe_pct": 5.0,
                "mae_pct": -3.0,
                "green": False,
            }],
            "same_bar_ambiguity": False,
        }

        with patch.object(replay, "stage1_replay", return_value=synthetic_bad_score), patch.object(
            replay, "path_recovery_stats", return_value={}
        ):
            report = replay.replay_event_report(event)

        outcome = report.get("control_outcome") or {}
        negative_profit_lock = (
            outcome.get("pnl") is not None
            and float(outcome["pnl"]) < 0.0
            and (
                outcome.get("exit_policy") == "PROFIT_LOCK_LADDER"
                or outcome.get("first_hit") == "PROFIT_LOCK_LADDER"
            )
        )
        self.assertFalse(
            negative_profit_lock,
            "negative PnL must be rejected or explicitly relabelled; it cannot retain PROFIT_LOCK_LADDER",
        )

    def test_every_event_has_stable_episode_identity(self):
        event = make_event_episode(
            signal_ts=SIGNAL_TS,
            direction="SHORT",
            symbol="BTCUSD",
            shared_ai_call_id="causal-scan-1",
        )
        self.assertTrue(str(event.get("event_episode_id") or "").strip())
        self.assertTrue(event.get("raw_signal_preserved"))

    def test_chase_schedule_is_auditable_interval_history(self):
        candles = [_candle(i, high=99_510.0, low=99_490.0) for i in range(31)]
        hit = simulate_touch_fill(
            candles,
            signal_ts=SIGNAL_TS,
            signal_price=100_000.0,
            direction="SHORT",
            offset_pct=0.30,
            chase={
                "id": "auditable-fast",
                "windows": {0, 1, 2, 3, 4, 5},
                "step_pct": 0.50,
                "interval_sec": 60,
            },
        )
        child = {"chase_schedule": hit["chase_schedule"]}
        required = {
            "chase_step_index", "active_from_ts", "active_until_ts", "reference_price",
            "limit_price", "offset_pct", "reason",
        }
        self.assertGreater(len(child["chase_schedule"]), 1)
        self.assertTrue(all(required <= set(interval) for interval in child["chase_schedule"]))


if __name__ == "__main__":
    unittest.main()
