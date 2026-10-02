import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import research_v3_candidates
from research_v3_candidates import (
    DEFAULT_PROTECTION_REPLAY_MAX_EVENTS,
    load_candidate_inputs,
    protection_replay_max_events,
    protection_replay_window_summary,
    select_recent_events,
)
from research_v3_store import V3EvidenceStore


def _seed_events(tmp, signal_times):
    store = V3EvidenceStore(tmp, epoch_id="epoch-w")
    for index, signal_ts in enumerate(signal_times):
        episode, event = f"episode-{index}", f"event-{index:02d}"
        store.append("opportunity", {
            "record_id": f"opportunity:{episode}", "episode_id": episode, "signal_ts": signal_ts,
        })
        store.append("order_intent", {
            "record_id": f"order-intent:{event}", "episode_id": episode, "event_id": event,
            "executed_direction": "LONG", "policy_id": "OFFSET_0.29_CHASE_patient",
        })
        segment = store.put_market_segment(
            source="TEST_1S", symbol="BTCUSD", timeframe="1s",
            start_ts=signal_ts, end_ts=signal_ts + 1,
            rows=[{"ts": signal_ts, "price": 100}, {"ts": signal_ts + 1, "price": 100.1}],
        )
        store.append("lifecycle", {
            "record_id": f"lifecycle:{event}:terminal", "episode_id": episode, "event_id": event,
            "terminal": True, "market_segment_refs": [segment],
        })
    return store


class ProtectionReplayWindowTest(unittest.TestCase):
    def test_keeps_most_recent_events_in_event_id_order(self):
        ts = {"a": 30, "b": 10, "c": 40, "d": 20}
        selected = select_recent_events(ts, ts.get, max_events=2)
        self.assertEqual(selected["event_ids"], ["a", "c"])
        receipt = selected["receipt"]
        self.assertEqual(receipt["schema"], "protection_replay_event_window_v1")
        self.assertEqual((receipt["events_eligible"], receipt["events_selected"]), (4, 2))
        self.assertTrue(receipt["truncated"])
        self.assertEqual((receipt["first_signal_ts"], receipt["last_signal_ts"]), (30.0, 40.0))

    def test_unbounded_or_small_cohort_is_untouched(self):
        ts = {"b": 2, "a": 1}
        for bound in (None, 2, 5):
            selected = select_recent_events(ts, ts.get, max_events=bound)
            self.assertEqual(selected["event_ids"], ["a", "b"])
            self.assertFalse(selected["receipt"]["truncated"])

    def test_events_without_signal_time_are_dropped_first(self):
        ts = {"a": None, "b": 5, "c": 1}
        self.assertEqual(select_recent_events(ts, ts.get, max_events=2)["event_ids"], ["b", "c"])

    def test_env_bound(self):
        cases = {
            None: DEFAULT_PROTECTION_REPLAY_MAX_EVENTS,
            "": DEFAULT_PROTECTION_REPLAY_MAX_EVENTS,
            "junk": DEFAULT_PROTECTION_REPLAY_MAX_EVENTS,
            "50": 50,
            "0": None,
        }
        for raw, expected in cases.items():
            with patch.dict(os.environ):
                os.environ.pop("ANALYZER_PROTECTION_REPLAY_MAX_EVENTS", None)
                if raw is not None:
                    os.environ["ANALYZER_PROTECTION_REPLAY_MAX_EVENTS"] = raw
                self.assertEqual(protection_replay_max_events(), expected, raw)

    def test_loader_skips_market_segments_of_events_outside_the_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            _seed_events(tmp, [100, 300, 200, 400])
            loaded_refs = []
            real_load = research_v3_candidates._load_segment

            def spy(root, ref):
                loaded_refs.append(ref)
                return real_load(root, ref)

            window = {}
            with patch.object(research_v3_candidates, "_load_segment", spy):
                rows = load_candidate_inputs(tmp, epoch_id="epoch-w", max_events=2, window=window)
            self.assertEqual([row["event_id"] for row in rows], ["event-01", "event-03"])
            self.assertEqual(window["events_eligible"], 4)
            self.assertEqual(window["events_selected"], 2)
            self.assertTrue(window["truncated"])
            self.assertEqual(sorted(ref["start_ts"] for ref in loaded_refs), [300, 400])

            full = load_candidate_inputs(tmp, epoch_id="epoch-w")
            self.assertEqual(len(full), 4)

    def test_summary_alert_levels(self):
        def receipt(eligible, selected):
            ts = {f"e{i}": i for i in range(eligible)}
            return select_recent_events(ts, ts.get, max_events=selected)["receipt"]

        self.assertEqual(protection_replay_window_summary(receipt(4, 4))["alert_level"], "GREEN")
        amber = protection_replay_window_summary(receipt(4, 3))
        self.assertEqual((amber["alert_level"], amber["events_dropped"]), ("AMBER", 1))
        red = protection_replay_window_summary(receipt(1764, 150))
        self.assertEqual(red["alert_level"], "RED")
        self.assertEqual(red["reason"], "PROTECTION_REPLAY_COVERAGE_BELOW_HALF")
        self.assertAlmostEqual(red["coverage_ratio"], 150 / 1764, places=6)
        self.assertEqual(protection_replay_window_summary(receipt(0, 5))["alert_level"], "GREEN")
        missing = protection_replay_window_summary(None)
        self.assertEqual((missing["status"], missing["alert_level"]), ("NOT_RUN", "AMBER"))

    def test_report_persists_window_receipt_and_top_level_summary(self):
        from research.research_v3_report import (
            PROTECTION_REPLAY_WINDOW_FILE,
            build_safe_policy_genome_v3_report,
        )

        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as reports:
            _seed_events(data, [100, 300, 200, 400])
            with patch.dict(os.environ, {"ANALYZER_PROTECTION_REPLAY_MAX_EVENTS": "1"}):
                report = build_safe_policy_genome_v3_report(data, reports)
            window = report["candidate_screen"]["input_window"]
            self.assertEqual(window["schema"], "protection_replay_event_window_v1")
            self.assertEqual((window["events_eligible"], window["events_selected"]), (4, 1))
            self.assertEqual(window["alert_level"], "RED")
            self.assertEqual(window["events_replayed"], 1)
            summary = report["protection_replay_window"]
            self.assertEqual(summary["events_replayed"], 1)
            self.assertEqual(summary["alert_level"], "RED")
            self.assertTrue(summary["truncated"])
            persisted = json.loads(
                (Path(reports) / PROTECTION_REPLAY_WINDOW_FILE).read_text(encoding="utf-8")
            )
            self.assertEqual(persisted["events_selected"], 1)
            self.assertEqual(persisted["report_key"], "protection_replay_window")
            on_disk = json.loads(
                (Path(reports) / "safe_policy_genome_v3_report.json").read_text(encoding="utf-8")
            )
            self.assertEqual(on_disk["candidate_screen"]["input_window"]["events_eligible"], 4)


if __name__ == "__main__":
    unittest.main()
