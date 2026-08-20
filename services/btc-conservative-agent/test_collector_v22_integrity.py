import json
import os
import tempfile
import unittest
from unittest import mock

import bot
import collector_v22
from collector_v22 import build_research_event, event_already_written, event_replay_eligibility, write_research_event_once
from collector_v22_provisional import load_provisional_events, remove_provisional_event, upsert_provisional_event
from collector_v22_schema import EVENT_INDEX_FILE, RESEARCH_EVENTS_FILE
from replay_eligibility import validate_replay_eligibility


SIGNAL_TS = 1_700_000_000.0


def _bar(minute):
    ts = SIGNAL_TS + minute * 60
    return [int(ts * 1000), 100000.0, 100000.0, 99990.0, 100000.0, 1.0]


def _mature_event(event_id="integrity-event"):
    return build_research_event(
        trade_id=event_id,
        epoch_id="epoch-integrity",
        signal_ts=SIGNAL_TS,
        signal_price=100000.0,
        candles_1m=[_bar(i) for i in range(-60, 61)],
        submitted=False,
        rejected=True,
        ticket_closed=True,
    )


class CollectorIntegrityTests(unittest.TestCase):
    def test_crash_after_jsonl_append_before_index_does_not_duplicate(self):
        with tempfile.TemporaryDirectory() as root:
            event = _mature_event("crash-window")
            self.assertFalse(event_already_written("crash-window", data_dir=root))
            real_save = collector_v22._save_event_index
            with mock.patch.object(collector_v22, "_save_event_index", side_effect=OSError("crash after append")):
                with self.assertRaisesRegex(OSError, "crash after append"):
                    write_research_event_once(event, data_dir=root)
            ok, reason = write_research_event_once(event, data_dir=root)
            self.assertFalse(ok)
            self.assertEqual(reason, "duplicate event_id")
            with open(os.path.join(root, RESEARCH_EVENTS_FILE), encoding="utf-8") as handle:
                rows = [json.loads(line) for line in handle if line.strip().startswith("{")]
            self.assertEqual([row["event_id"] for row in rows], ["crash-window"])
            self.assertTrue(real_save)

    def test_corrupt_or_missing_index_rebuilds_from_durable_rows(self):
        with tempfile.TemporaryDirectory() as root:
            event = _mature_event("repair-index")
            self.assertTrue(write_research_event_once(event, data_dir=root)[0])
            index_path = os.path.join(root, EVENT_INDEX_FILE)
            with open(index_path, "w", encoding="utf-8") as handle:
                handle.write('{"events":')
            self.assertTrue(event_already_written("repair-index", data_dir=root))
            with open(index_path, encoding="utf-8") as handle:
                repaired = json.load(handle)
            self.assertIn("repair-index", repaired["events"])
            os.remove(index_path)
            self.assertTrue(event_already_written("repair-index", data_dir=root))

    def test_restart_provisional_matures_once_and_is_cleaned_after_commit(self):
        with tempfile.TemporaryDirectory() as root:
            source = {"trade_id": "restart-mature", "created_ts_ts": SIGNAL_TS, "status": "REJECTED"}
            upsert_provisional_event("restart-mature", source, epoch_id="epoch-integrity", data_dir=root)
            restored = load_provisional_events(epoch_id="epoch-integrity", data_dir=root)
            self.assertEqual(restored["restart-mature"], source)
            event = _mature_event("restart-mature")
            self.assertTrue(write_research_event_once(event, data_dir=root)[0])
            self.assertTrue(remove_provisional_event("restart-mature", data_dir=root))
            self.assertEqual(load_provisional_events(epoch_id="epoch-integrity", data_dir=root), {})
            self.assertEqual(write_research_event_once(event, data_dir=root), (False, "duplicate event_id"))

    def test_writer_and_replay_reject_same_pre_signal_gap(self):
        event = _mature_event("pre-gap")
        path = event["canonical_tape"]["path_1m"]
        event["canonical_tape"]["path_1m"] = [row for row in path if row[0] != int((SIGNAL_TS - 30 * 60) * 1000)]
        writer = event_replay_eligibility(event)
        replay = validate_replay_eligibility(event)
        self.assertFalse(writer["eligible"])
        self.assertFalse(replay["eligible"])
        self.assertEqual(writer["status"], replay["status"])
        self.assertEqual(writer["reasons"], replay["reasons"])

    def test_shadow_collecting_uses_executed_direction_without_second_invert(self):
        captured = {}
        old_price = bot.state.get("price")
        bot.state["price"] = 100000.0
        try:
            with mock.patch.object(bot, "start_replay_buffer", side_effect=lambda *a, **kw: captured.update(kw)), \
                 mock.patch.object(bot, "append_replay_tick"), \
                 mock.patch.object(bot, "log_lane_opportunity_event"):
                bot._spawn_shadow_collecting_lane(
                    {"trade_id": "source"}, {"direction": "LONG", "win_prob": 60}, 4.0, {},
                    "TEST_LANE", "AI_SCAN", raw_direction="LONG", executed_direction="SHORT",
                )
        finally:
            bot.state["price"] = old_price
        self.assertEqual(captured["raw_direction"], "LONG")
        self.assertEqual(captured["executed_direction"], "SHORT")
        self.assertEqual(captured["direction"], "SHORT")
        self.assertTrue(captured["invert_on"])

    def test_control_invert_remains_off(self):
        self.assertFalse(bot.state["invert_signal"])
        self.assertNotIn("INVERT_SIGNAL_DEFAULT", vars(bot))
        self.assertNotIn("_ensure_invert_live_default", vars(bot))


if __name__ == "__main__":
    unittest.main()
