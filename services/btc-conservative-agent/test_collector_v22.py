"""collector_v2.2 verification gates — synthetic fixtures."""
import json
import os
import shutil
import tempfile
import unittest

from chase_offset_touch_grid import orig_limit_price, simulate_touch_fill
from collector_storage import storage_blocks_new_events, project_capacity
from collector_v22 import (
    BYTES_PER_EVENT_TYPICAL,
    build_research_event,
    event_already_written,
    terminal_observation,
    write_research_event_once,
    event_replay_eligibility,
    make_event_episode,
)
from collector_v22_schema import (
    COLLECTOR_VERSION,
    OBS_FUNNEL_COMPLETE,
    OBS_INSUFFICIENT_PATH,
    OBS_WAITING_120M,
    OBS_WAITING_ENTRY_WINDOW,
    PRIMARY_ACCEPTED_FILLED,
    PRIMARY_ACCEPTED_UNFILLED,
    PRIMARY_REJECTED,
    STORAGE_PRESSURE_THRESHOLD,
)
from opportunity_capture_v22 import analyze_v22_events
from replay_event_report import replay_event_report


def _1m(i, high, low, close, origin=1_700_000_000):
    return [int((origin + i * 60) * 1000), close, high, low, close, 1.0]


class CollectorV22Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_write_once_idempotent(self):
        candles = [_1m(i, 100010, 99990, 100000) for i in range(200)]
        event = build_research_event(
            trade_id="cont-idem-1",
            epoch_id="epoch-v22-test",
            signal_ts=1_700_000_000.0,
            signal_price=100000.0,
            candles_1m=candles,
            submitted=False,
            rejected=True,
            ticket_closed=True,
        )
        ok1, _ = write_research_event_once(event, data_dir=self.tmp)
        ok2, reason2 = write_research_event_once(event, data_dir=self.tmp)
        self.assertTrue(ok1)
        self.assertFalse(ok2)
        self.assertEqual(reason2, "duplicate event_id")
        self.assertTrue(event_already_written("cont-idem-1", data_dir=self.tmp))

    def test_chase_schedule_is_the_actual_deterministic_limit_history(self):
        signal_ts = 1_700_000_000.0
        candles = [_1m(i, 99510, 99490, 99500) for i in range(31)]
        no_chase = simulate_touch_fill(
            candles,
            signal_ts=signal_ts,
            signal_price=100000.0,
            direction="SHORT",
            offset_pct=0.30,
            chase={"id": "no_chase", "no_chase": True, "windows": set()},
        )
        fast_chase = simulate_touch_fill(
            candles,
            signal_ts=signal_ts,
            signal_price=100000.0,
            direction="SHORT",
            offset_pct=0.30,
            chase={"id": "fast", "windows": {0, 1, 2, 3, 4, 5}, "step_pct": 0.50, "interval_sec": 60},
        )
        self.assertEqual(len(no_chase["chase_schedule"]), 1)
        self.assertGreater(len(fast_chase["chase_schedule"]), 1)
        self.assertNotEqual(no_chase["chase_schedule"], fast_chase["chase_schedule"])
        for index, interval in enumerate(fast_chase["chase_schedule"]):
            self.assertEqual(interval["chase_step_index"], index)
            self.assertIsNotNone(interval["active_until_ts"])
            self.assertGreaterEqual(interval["active_until_ts"], interval["active_from_ts"])
            if index:
                self.assertEqual(
                    interval["active_from_ts"],
                    fast_chase["chase_schedule"][index - 1]["active_until_ts"],
                )

    def test_episode_grouping_preserves_causal_relationships(self):
        common = dict(signal_ts=1_700_000_000.0, direction="SHORT", symbol="BTCUSD")
        scan = make_event_episode(**common, shared_ai_call_id="scan-causal-1")
        lane = make_event_episode(
            signal_ts=common["signal_ts"] + 25,
            direction="SHORT",
            symbol="BTCUSD",
            shared_ai_call_id="scan-causal-1",
        )
        different_call = make_event_episode(**common, shared_ai_call_id="scan-causal-2")
        self.assertEqual(scan["event_episode_id"], lane["event_episode_id"])
        self.assertNotEqual(scan["event_episode_id"], different_call["event_episode_id"])
        self.assertTrue(scan["raw_signal_preserved"])

    def test_episode_fallback_groups_correlated_but_not_separated_signals(self):
        first = make_event_episode(signal_ts=1_700_000_010.0, direction="LONG", symbol="BTCUSD")
        correlated = make_event_episode(signal_ts=1_700_000_080.0, direction="LONG", symbol="BTCUSD")
        separated = make_event_episode(signal_ts=1_700_000_610.0, direction="LONG", symbol="BTCUSD")
        opposite = make_event_episode(signal_ts=1_700_000_080.0, direction="SHORT", symbol="BTCUSD")
        self.assertEqual(first["event_episode_id"], correlated["event_episode_id"])
        self.assertNotEqual(first["event_episode_id"], separated["event_episode_id"])
        self.assertNotEqual(first["event_episode_id"], opposite["event_episode_id"])

    def test_synthetic_actual_fill(self):
        signal_price = 100000.0
        signal_ts = 1_700_000_000.0
        fill_px = orig_limit_price(signal_price, "SHORT", 0.10)
        candles = [
            _1m(i, fill_px if i == 2 else signal_price, signal_price - 20, signal_price)
            for i in range(200)
        ]
        event = build_research_event(
            trade_id="cont-fill-1",
            epoch_id="epoch-v22-test",
            signal_ts=signal_ts,
            signal_price=signal_price,
            candles_1m=candles,
            live_fill_ts=signal_ts + 120.0,
            live_fill_price=fill_px,
            ticket_closed=True,
            path_complete=True,
        )
        self.assertEqual(event["primary_outcome"], PRIMARY_ACCEPTED_FILLED)
        self.assertEqual(event["collector_version"], COLLECTOR_VERSION)
        self.assertIn("pre_signal_context", event)
        self.assertIn("entry_children", event)
        self.assertFalse(event["canonical_tape"]["ticks_1s_optional"])

    def test_synthetic_unfilled(self):
        signal_price = 100000.0
        signal_ts = 1_700_000_000.0
        candles = [_1m(i, signal_price, signal_price - 10, signal_price) for i in range(80)]
        event = build_research_event(
            trade_id="cont-unfilled-1",
            epoch_id="epoch-v22-test",
            signal_ts=signal_ts,
            signal_price=signal_price,
            candles_1m=candles,
            ticket_closed=True,
        )
        self.assertEqual(event["primary_outcome"], PRIMARY_ACCEPTED_UNFILLED)
        self.assertEqual(event["observation_status"], OBS_FUNNEL_COMPLETE)
        self.assertTrue(event["immutable"])

    def test_entry_window_cannot_finalize_early(self):
        signal_ts = 1_700_000_000.0
        event = build_research_event(
            trade_id="cont-too-early",
            epoch_id="epoch-v22-test",
            signal_ts=signal_ts,
            signal_price=100000.0,
            candles_1m=[_1m(i, 100000, 99990, 100000) for i in range(10)],
            ticket_closed=True,
        )
        self.assertEqual(event["observation_status"], OBS_WAITING_ENTRY_WINDOW)
        self.assertFalse(terminal_observation(event["observation_status"]))

    def test_latest_hypothetical_fill_requires_full_hold(self):
        signal_ts = 1_700_000_000.0
        fill_px = orig_limit_price(100000.0, "SHORT", 0.10)
        candles = [
            _1m(i, fill_px + 0.01 if i == 29 else 100000.0, 99990, 100000.0)
            for i in range(148)
        ]
        incomplete = build_research_event(
            trade_id="cont-minute-59-short",
            epoch_id="epoch-v22-test",
            signal_ts=signal_ts,
            signal_price=100000.0,
            candles_1m=candles,
            ticket_closed=True,
        )
        self.assertEqual(incomplete["observation_status"], OBS_WAITING_120M)
        complete = build_research_event(
            trade_id="cont-minute-59-full",
            epoch_id="epoch-v22-test",
            signal_ts=signal_ts,
            signal_price=100000.0,
            candles_1m=candles + [_1m(148, 100000, 99990, 100000.0)],
            ticket_closed=True,
        )
        self.assertEqual(complete["observation_status"], OBS_FUNNEL_COMPLETE)
        self.assertTrue(complete["immutable"])

    def test_gap_blocks_finalization_and_legacy_terminal_is_ineligible(self):
        signal_ts = 1_700_000_000.0
        candles = [_1m(i, 100000, 99990, 100000) for i in range(180) if i != 30]
        event = build_research_event(
            trade_id="cont-gap",
            epoch_id="epoch-v22-test",
            signal_ts=signal_ts,
            signal_price=100000.0,
            candles_1m=candles,
            submitted=False,
            rejected=True,
            ticket_closed=True,
        )
        self.assertEqual(event["observation_status"], OBS_INSUFFICIENT_PATH)
        self.assertTrue(event["negative_evidence"])
        self.assertFalse(event["ranking_eligible"])
        event["observation_status"] = OBS_FUNNEL_COMPLETE
        eligibility = event_replay_eligibility(event)
        self.assertEqual(eligibility["replay_status"], "REPLAY_INELIGIBLE")
        self.assertEqual(eligibility["integrity_code"], "LEGACY_V22_PREMATURE_FINALIZATION")

    def test_synthetic_rejected(self):
        event = build_research_event(
            trade_id="cont-reject-1",
            epoch_id="epoch-v22-test",
            signal_ts=1_700_000_000.0,
            signal_price=100000.0,
            candles_1m=[_1m(i, 100010, 99990, 100000) for i in range(50)],
            submitted=False,
            rejected=True,
            exact_reason="SPREAD_BUCKET_BLOCKED",
            ticket_closed=True,
        )
        self.assertEqual(event["primary_outcome"], PRIMARY_REJECTED)
        self.assertEqual(event["envelope"]["policy_id"], "CONTROL_V1")

    def test_post_ttl_touch_not_fill(self):
        signal_price = 100000.0
        signal_ts = 1_700_000_000.0
        fill_px = orig_limit_price(signal_price, "SHORT", 0.10)
        candles = [
            _1m(i, fill_px if i == 37 else signal_price, signal_price - 10, signal_price)
            for i in range(50)
        ]
        event = build_research_event(
            trade_id="cont-post-ttl",
            epoch_id="epoch-v22-test",
            signal_ts=signal_ts,
            signal_price=signal_price,
            candles_1m=candles,
            ticket_closed=True,
            ttl_sec=1800.0,
        )
        control_children = [
            c for c in event["entry_children"]
            if abs(float(c.get("offset_pct", 0)) - 0.10) < 1e-9 and c.get("chase_id") == "no_chase"
        ]
        self.assertTrue(control_children)
        self.assertIsNone(control_children[0].get("fill_ts"))
        self.assertTrue(control_children[0].get("post_ttl_observation") or control_children[0].get("post_ttl_touch_ts"))

    def test_analyzer_three_primaries(self):
        for tid, outcome in (
            ("cont-a", PRIMARY_ACCEPTED_FILLED),
            ("cont-b", PRIMARY_ACCEPTED_UNFILLED),
            ("cont-c", PRIMARY_REJECTED),
        ):
            ev = build_research_event(
                trade_id=tid,
                epoch_id="epoch-v22-test",
                signal_ts=1_700_000_000.0,
                signal_price=100000.0,
                candles_1m=[_1m(i, 100010, 99990, 100000) for i in range(180)],
                submitted=(outcome != PRIMARY_REJECTED),
                rejected=(outcome == PRIMARY_REJECTED),
                ticket_closed=True,
            )
            ev["primary_outcome"] = outcome
            write_research_event_once(ev, data_dir=self.tmp)
        report = analyze_v22_events(data_dir=self.tmp)
        self.assertEqual(report["primaries"][PRIMARY_ACCEPTED_FILLED]["n"], 1)
        self.assertEqual(report["primaries"][PRIMARY_ACCEPTED_UNFILLED]["n"], 1)
        self.assertEqual(report["primaries"][PRIMARY_REJECTED]["n"], 1)
        self.assertIn("capacity_projection", report)
        self.assertIn("pct_70", report["capacity_projection"]["thresholds"])

    def test_capacity_projection_bytes(self):
        cap = project_capacity(data_dir=self.tmp, bytes_per_event_typical=BYTES_PER_EVENT_TYPICAL, events_per_day=15)
        self.assertEqual(cap["bytes_per_event_typical"], BYTES_PER_EVENT_TYPICAL)
        self.assertIn("pct_100", cap["thresholds"])

    def test_replay_report_rejected(self):
        event = build_research_event(
            trade_id="cont-replay-reject",
            epoch_id="epoch-v22-test",
            signal_ts=1_700_000_000.0,
            signal_price=100000.0,
            candles_1m=[_1m(i, 100010, 99990, 100000) for i in range(40)],
            submitted=False,
            rejected=True,
            ticket_closed=True,
        )
        write_research_event_once(event, data_dir=self.tmp)
        report = replay_event_report(event, data_dir=self.tmp)
        self.assertEqual(report["event_id"], "cont-replay-reject")
        self.assertEqual(report["replay_status"], "REPLAY_INELIGIBLE")
        self.assertIn("ENTRY_WINDOW_INCOMPLETE", report["eligibility"]["reasons"])

    def test_terminal_observation_enums(self):
        self.assertTrue(terminal_observation(OBS_FUNNEL_COMPLETE))
        self.assertFalse(terminal_observation(OBS_WAITING_120M))


if __name__ == "__main__":
    unittest.main()
