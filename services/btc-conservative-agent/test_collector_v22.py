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
)
from collector_v22_schema import (
    COLLECTOR_VERSION,
    OBS_FUNNEL_COMPLETE,
    OBS_WAITING_120M,
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
                candles_1m=[_1m(i, 100010, 99990, 100000) for i in range(30)],
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
        self.assertIn("capacity_projection", report)

    def test_terminal_observation_enums(self):
        self.assertTrue(terminal_observation(OBS_FUNNEL_COMPLETE))
        self.assertFalse(terminal_observation(OBS_WAITING_120M))


if __name__ == "__main__":
    unittest.main()
