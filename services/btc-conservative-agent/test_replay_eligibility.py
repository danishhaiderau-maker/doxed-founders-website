"""Golden replay-eligibility fixtures for complete and defective 1m paths."""
import unittest
from unittest.mock import patch

from collector_v22_schema import COLLECTOR_VERSION, OBS_FUNNEL_COMPLETE
from replay_eligibility import LEGACY_PREMATURE, validate_replay_eligibility
from replay_event_report import replay_event_report


ORIGIN = 1_700_000_000.0


def _bar(minute, timestamp=None):
    ts = ORIGIN + minute * 60.0 if timestamp is None else timestamp
    return [int(ts * 1000), 100.0, 101.0, 99.0, 100.0, 1.0]


def _event(last_minute, *, fill_minute=None, path=None, terminal=True):
    child = {
        "entry_policy_id": "golden",
        "fill_ts": None if fill_minute is None else ORIGIN + fill_minute * 60.0,
        "fill_price": None if fill_minute is None else 100.0,
    }
    return {
        "collector_version": COLLECTOR_VERSION,
        "event_id": "golden-event",
        "observation_status": OBS_FUNNEL_COMPLETE if terminal else "PENDING",
        "envelope": {"signal_ts": ORIGIN, "direction": "LONG"},
        "canonical_tape": {
            "path_1m": path if path is not None else [_bar(i) for i in range(last_minute + 1)],
        },
        "entry_children": [child],
    }


class ReplayEligibilityGoldenTests(unittest.TestCase):
    def test_no_fill_requires_complete_60m_entry_window(self):
        self.assertTrue(validate_replay_eligibility(_event(59))["eligible"])
        result = validate_replay_eligibility(_event(58))
        self.assertFalse(result["eligible"])
        self.assertIn("ENTRY_WINDOW_INCOMPLETE", result["reasons"])

    def test_hypothetical_fill_at_minute_5_requires_full_120m_hold(self):
        self.assertTrue(validate_replay_eligibility(_event(124, fill_minute=5))["eligible"])
        result = validate_replay_eligibility(_event(123, fill_minute=5))
        self.assertIn(f"HOLD_WINDOW_INCOMPLETE:{ORIGIN + 300:g}", result["reasons"])

    def test_hypothetical_fill_at_minute_59_requires_full_120m_hold(self):
        self.assertTrue(validate_replay_eligibility(_event(178, fill_minute=59))["eligible"])
        self.assertFalse(validate_replay_eligibility(_event(177, fill_minute=59))["eligible"])

    def test_gap_duplicate_and_out_of_order_are_each_rejected(self):
        gap = [_bar(i) for i in range(60) if i != 30]
        duplicate = [_bar(i) for i in range(60)]
        duplicate.insert(31, _bar(30))
        out_of_order = [_bar(i) for i in range(60)]
        out_of_order[30], out_of_order[31] = out_of_order[31], out_of_order[30]
        self.assertTrue(any(r.startswith("CANDLE_GAP") for r in validate_replay_eligibility(_event(0, path=gap))["reasons"]))
        self.assertTrue(any(r.startswith("DUPLICATE_CANDLE") for r in validate_replay_eligibility(_event(0, path=duplicate))["reasons"]))
        self.assertTrue(any(r.startswith("OUT_OF_ORDER_CANDLE") for r in validate_replay_eligibility(_event(0, path=out_of_order))["reasons"]))

    def test_terminal_v22_partial_path_is_legacy_and_never_scored(self):
        event = _event(10)
        eligibility = validate_replay_eligibility(event)
        self.assertEqual(eligibility["classification"], LEGACY_PREMATURE)
        with patch("replay_event_report.stage1_replay") as replay:
            report = replay_event_report(event)
            replay.assert_not_called()
        self.assertEqual(report["replay_status"], "REPLAY_INELIGIBLE")
        self.assertIsNone(report["control_outcome"])
        self.assertEqual(report["hypothetical_entries"], [])

    def test_fill_outside_supported_entry_window_is_rejected(self):
        result = validate_replay_eligibility(_event(181, fill_minute=61))
        self.assertIn("FILL_OUTSIDE_SUPPORTED_ENTRY_WINDOW", result["reasons"])


if __name__ == "__main__":
    unittest.main()
