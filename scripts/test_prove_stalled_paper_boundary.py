#!/usr/bin/env python3
"""Contract tests for the Path C paper pending-stall recover gate."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from prove_stalled_paper_boundary import (
    STALL_WINDOW,
    FlatCheckObservation,
    parse_showcase_counts,
    ready_eligibility_failures,
    stall_window_failures,
)

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


def live_ready(**overrides):
    """Shape verified by read-only GET of live /ready (image 37f3a57a)."""
    payload = {
        "ok": True,
        "live_entry_arm_block_reason": "FORCE_PAPER_MODE",
        "live_entry_armable": False,
        "trading_block_reason": "FORCE_PAPER_MODE",
        "trading_ready": False,
        "source_git_rev": "37f3a57a36db",
        "strategy_progress": {
            "live_armed": False,
            "open_positions": 0,
            "pending_orders": 13,
            "reasons": [],
            "trade_lock_available": False,
        },
        "strategy_progress_incident": {
            "active": False,
            "new_entries_suppressed": False,
            "reasons": ["TRADE_LOCK_UNAVAILABLE"],
        },
    }
    payload.update(overrides)
    return payload


def obs(run_id, *, hours_ago, conclusion="failure", positions=0, pending=13):
    return FlatCheckObservation(
        run_id=run_id,
        conclusion=conclusion,
        completed_at=NOW - timedelta(hours=hours_ago),
        showcase_positions=positions,
        showcase_pending_orders=pending,
        run_url=f"https://github.com/example/actions/runs/{run_id}",
    )


class ReadyEligibilityTest(unittest.TestCase):
    def test_live_ready_shape_is_eligible_without_incident_latch(self):
        self.assertEqual(ready_eligibility_failures(live_ready()), [])

    def test_explicit_force_paper_mode_is_eligible(self):
        payload = live_ready(
            force_paper_mode=True,
            live_entry_arm_block_reason=None,
            trading_block_reason=None,
        )
        self.assertEqual(ready_eligibility_failures(payload), [])

    def test_liveness_payload_without_progress_is_refused(self):
        reasons = ready_eligibility_failures(
            {
                "probe_contract": "PROCESS_LIVENESS_ONLY",
                "force_paper_mode": True,
                "live_armed": False,
                "process_alive": True,
            }
        )
        self.assertIn("ready.strategy_progress is missing", reasons)

    def test_pending_alone_is_refused(self):
        payload = live_ready()
        payload["strategy_progress"] = {
            "live_armed": True,
            "open_positions": 1,
            "pending_orders": 13,
        }
        payload["live_entry_arm_block_reason"] = "PRIVATE_API_KEYS_MISSING"
        payload["trading_block_reason"] = "PRIVATE_API_KEYS_MISSING"
        reasons = ready_eligibility_failures(payload)
        self.assertTrue(any("live_armed" in reason for reason in reasons))
        self.assertTrue(any("open_positions" in reason for reason in reasons))
        self.assertTrue(any("force-paper" in reason for reason in reasons))

    def test_open_positions_block_pending_stall_class(self):
        payload = live_ready()
        payload["strategy_progress"]["open_positions"] = 1
        reasons = ready_eligibility_failures(payload)
        self.assertEqual(reasons, ["strategy_progress.open_positions must be 0"])

    def test_zero_pending_is_refused(self):
        payload = live_ready()
        payload["strategy_progress"]["pending_orders"] = 0
        reasons = ready_eligibility_failures(payload)
        self.assertEqual(reasons, ["strategy_progress.pending_orders must be > 0"])

    def test_top_level_live_armed_true_is_refused(self):
        reasons = ready_eligibility_failures(live_ready(live_armed=True))
        self.assertIn("top-level live_armed must be false", reasons)

    def test_force_paper_mode_false_is_refused(self):
        reasons = ready_eligibility_failures(live_ready(force_paper_mode=False))
        self.assertIn("force_paper_mode must be true", reasons)

    def test_missing_force_paper_equivalent_is_refused(self):
        payload = live_ready(
            live_entry_arm_block_reason="WS_NOT_READY",
            trading_block_reason="WS_NOT_READY",
        )
        reasons = ready_eligibility_failures(payload)
        self.assertTrue(any("force-paper" in reason for reason in reasons))

    def test_armable_live_entry_is_refused(self):
        reasons = ready_eligibility_failures(live_ready(trading_ready=True))
        self.assertIn("live entry must stay disarmed", reasons)


class StallWindowTest(unittest.TestCase):
    def test_single_qualifying_failure_at_least_two_hours_old_passes(self):
        reasons, evidence = stall_window_failures([obs(1, hours_ago=3)], NOW)
        self.assertEqual(reasons, [])
        self.assertEqual(evidence["newest_run_id"], 1)
        self.assertGreaterEqual(evidence["window_sec"], int(STALL_WINDOW.total_seconds()))

    def test_recent_failure_under_two_hours_is_refused(self):
        reasons, _evidence = stall_window_failures([obs(1, hours_ago=1.4)], NOW)
        self.assertTrue(any("stall window is not proven" in reason for reason in reasons))

    def test_stale_failure_is_refused(self):
        reasons, _evidence = stall_window_failures([obs(1, hours_ago=7)], NOW)
        self.assertTrue(any("stale" in reason for reason in reasons))

    def test_newer_success_clears_the_streak(self):
        reasons, _evidence = stall_window_failures(
            [
                obs(2, hours_ago=1, conclusion="success", positions=None, pending=None),
                obs(1, hours_ago=5),
            ],
            NOW,
        )
        self.assertTrue(any("latest flat-check succeeded" in reason for reason in reasons))

    def test_pending_zero_flat_check_does_not_qualify(self):
        reasons, _evidence = stall_window_failures(
            [obs(1, hours_ago=3, pending=0)],
            NOW,
        )
        self.assertTrue(any("not a paper pending stall" in reason for reason in reasons))

    def test_older_non_pending_failure_does_not_shorten_or_clear(self):
        reasons, evidence = stall_window_failures(
            [
                obs(2, hours_ago=3, pending=13),
                obs(1, hours_ago=20, pending=0, positions=0),
            ],
            NOW,
        )
        self.assertEqual(reasons, [])
        self.assertEqual(evidence["oldest_run_id"], 2)

    def test_two_samples_without_net_drain_pass(self):
        reasons, evidence = stall_window_failures(
            [
                obs(2, hours_ago=1, pending=13),
                obs(1, hours_ago=4, pending=12),
            ],
            NOW,
        )
        self.assertEqual(reasons, [])
        self.assertEqual(evidence["showcase_pending_oldest"], 12)
        self.assertEqual(evidence["showcase_pending_newest"], 13)

    def test_net_drain_on_showcase_pending_is_refused(self):
        reasons, _evidence = stall_window_failures(
            [
                obs(2, hours_ago=1, pending=9),
                obs(1, hours_ago=4, pending=13),
            ],
            NOW,
        )
        self.assertTrue(any("net drain" in reason for reason in reasons))

    def test_skipped_recover_runs_are_not_required_in_the_list(self):
        reasons, _evidence = stall_window_failures([obs(9, hours_ago=2)], NOW)
        self.assertEqual(reasons, [])


class LogParseTest(unittest.TestCase):
    def test_actions_log_showcase_counts(self):
        log = "\n".join(
            [
                "test-and-deploy\tUNKNOWN STEP\t2026-09-26T08:26:07.0311518Z node scripts/check-relay-flat.mjs",
                "test-and-deploy\tUNKNOWN STEP\t2026-09-26T08:26:07.0400000Z shell: /usr/bin/bash -e {0}",
                "test-and-deploy\tUNKNOWN STEP\t2026-09-26T08:26:09.5130000Z {",
                'test-and-deploy\tUNKNOWN STEP\t2026-09-26T08:26:09.5131000Z   "showcase": {',
                'test-and-deploy\tUNKNOWN STEP\t2026-09-26T08:26:09.5132000Z     "positions": 0,',
                'test-and-deploy\tUNKNOWN STEP\t2026-09-26T08:26:09.5133000Z     "pendingOrders": 13',
                "test-and-deploy\tUNKNOWN STEP\t2026-09-26T08:26:09.5134000Z   }",
                "test-and-deploy\tUNKNOWN STEP\t2026-09-26T08:26:09.5135000Z }",
                "test-and-deploy\tUNKNOWN STEP\t2026-09-26T08:26:09.5295310Z ##[error]Process completed with exit code 2.",
            ]
        )
        self.assertEqual(parse_showcase_counts(log), (0, 13))

    def test_unparsed_log_is_not_evidence(self):
        self.assertIsNone(parse_showcase_counts("flat check crashed before output"))

    def test_null_showcase_counts_are_not_evidence(self):
        log = '"showcase": { "positions": null, "pendingOrders": 13 }'
        self.assertIsNone(parse_showcase_counts(log))


if __name__ == "__main__":
    unittest.main()
