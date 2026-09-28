import json
import os
import tempfile
import threading
import unittest
from unittest import mock

import bot
import collector_v22
from collector_v22 import build_research_event, event_already_written, event_replay_eligibility, write_research_event_once
from collector_v22_provisional import load_provisional_events, remove_provisional_event, upsert_provisional_event
from collector_v22_schema import EVENT_INDEX_FILE, RESEARCH_EVENTS_FILE
from collector_v22_schema import OBS_INSUFFICIENT_PATH, OBS_WAITING_120M
from opportunity_capture_v22 import analyze_v22_events
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
            real_insert = collector_v22._insert_index_line
            with mock.patch.object(collector_v22, "_insert_index_line", side_effect=OSError("crash after append")):
                with self.assertRaisesRegex(OSError, "crash after append"):
                    write_research_event_once(event, data_dir=root)
            ok, reason = write_research_event_once(event, data_dir=root)
            self.assertFalse(ok)
            self.assertEqual(reason, "duplicate event_id")
            with open(os.path.join(root, RESEARCH_EVENTS_FILE), encoding="utf-8") as handle:
                rows = [json.loads(line) for line in handle if line.strip().startswith("{")]
            self.assertEqual([row["event_id"] for row in rows], ["crash-window"])
            self.assertTrue(real_insert)

    def test_corrupt_or_missing_index_rebuilds_from_durable_rows(self):
        with tempfile.TemporaryDirectory() as root:
            event = _mature_event("repair-index")
            self.assertTrue(write_research_event_once(event, data_dir=root)[0])
            index_path = os.path.join(root, EVENT_INDEX_FILE)
            with open(index_path, "w", encoding="utf-8") as handle:
                handle.write('{"events":')
            self.assertTrue(event_already_written("repair-index", data_dir=root))
            # The legacy JSON is immutable migration evidence, not the live index.
            with self.assertRaises(json.JSONDecodeError):
                json.loads(open(index_path, encoding="utf-8").read())
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

    def test_retired_shadow_order_fanout_is_not_callable(self):
        # Shadow evidence remains readable by the analyzer, but the retired
        # runtime lane must not be callable from the money/paper execution
        # process.  The active execution-graph contract separately proves that
        # one AI decision feeds only CONTINUOUS and OFFSET_029_ATR_TP_25.
        self.assertFalse(hasattr(bot, "_spawn_shadow_collecting_lane"))
        self.assertFalse(hasattr(bot, "spawn_shadow_collecting_lanes_from_ai_scan"))

    def test_control_invert_remains_off(self):
        self.assertFalse(bot.state["invert_signal"])
        self.assertNotIn("INVERT_SIGNAL_DEFAULT", vars(bot))
        self.assertNotIn("_ensure_invert_live_default", vars(bot))

    def test_startup_and_poll_self_heal_missing_same_epoch_provisionals(self):
        saved_pending = dict(bot._order_multiverse_pending_src)
        saved_state = dict(bot._order_multiverse_state)
        saved_written = set(bot._order_multiverse_written)
        saved_poll = bot._order_multiverse_last_poll
        saved_merge = bot._collector_v22_last_merge
        try:
            bot._order_multiverse_pending_src.clear()
            bot._order_multiverse_state.clear()
            bot._order_multiverse_written.clear()
            source = {
                "trade_id": "journal-overdue",
                "created_ts_ts": SIGNAL_TS,
                "expires_ts": SIGNAL_TS + 1800,
                "status": "REJECTED",
                "collector_rejected": True,
            }
            with mock.patch.object(bot, "_collector_v22_epoch_id", return_value="epoch-integrity"), \
                 mock.patch.object(bot, "load_provisional_events", return_value={"journal-overdue": source}), \
                 mock.patch.object(bot, "event_already_written", return_value=False):
                self.assertEqual(bot._restore_collector_v22_provisionals(), 1)
                self.assertIsNot(bot._order_multiverse_pending_src["journal-overdue"], source)
                # Active memory wins over an older journal snapshot.
                bot._order_multiverse_pending_src["journal-overdue"]["status"] = "ACTIVE_MEMORY"
                self.assertEqual(bot._merge_collector_v22_provisionals(reason="TEST"), 0)
                self.assertEqual(bot._order_multiverse_pending_src["journal-overdue"]["status"], "ACTIVE_MEMORY")

                # Simulate the low-priority periodic repair after runtime-map
                # loss, then send the restored source through maturation.
                bot._order_multiverse_pending_src.clear()
                bot._order_multiverse_last_poll = 0.0
                bot._collector_v22_last_merge = 0.0
                self.assertEqual(bot._merge_collector_v22_provisionals(reason="TEST_RUNTIME_MAP_LOSS"), 1)
                processed = []
                with mock.patch.object(bot, "persist_rejected_opportunity", side_effect=lambda src, **kw: processed.append(dict(src))):
                    bot._maybe_complete_pending_order_multiverse()
                self.assertEqual(processed[0]["trade_id"], "journal-overdue")
        finally:
            bot._order_multiverse_pending_src.clear()
            bot._order_multiverse_pending_src.update(saved_pending)
            bot._order_multiverse_state.clear()
            bot._order_multiverse_state.update(saved_state)
            bot._order_multiverse_written.clear()
            bot._order_multiverse_written.update(saved_written)
            bot._order_multiverse_last_poll = saved_poll
            bot._collector_v22_last_merge = saved_merge

    def test_maturation_poll_rotates_a_bounded_batch_without_rescanning_journal(self):
        saved_pending = dict(bot._order_multiverse_pending_src)
        saved_poll = bot._order_multiverse_last_poll
        saved_merge = bot._collector_v22_last_merge
        saved_cursor = bot._order_multiverse_maturation_cursor
        try:
            bot._order_multiverse_pending_src.clear()
            for index in range(5):
                bot._order_multiverse_pending_src[f"pending-{index}"] = {
                    "trade_id": f"pending-{index}",
                    "collector_rejected": True,
                }
            bot._order_multiverse_last_poll = 0.0
            bot._collector_v22_last_merge = bot.time.time()
            bot._order_multiverse_maturation_cursor = 0
            processed = []
            with mock.patch.object(bot, "_schedule_collector_v22_provisional_merge") as schedule, \
                 mock.patch.object(
                     bot,
                     "persist_rejected_opportunity",
                     side_effect=lambda src, **kw: processed.append(src["trade_id"]),
                 ):
                bot._maybe_complete_pending_order_multiverse()
            schedule.assert_not_called()
            self.assertEqual(len(processed), bot.COLLECTOR_MATURATION_BATCH_SIZE)
            self.assertEqual(processed, ["pending-0", "pending-1"])

            bot._order_multiverse_last_poll = 0.0
            with mock.patch.object(bot, "_schedule_collector_v22_provisional_merge") as schedule, \
                 mock.patch.object(
                     bot,
                     "persist_rejected_opportunity",
                     side_effect=lambda src, **kw: processed.append(src["trade_id"]),
                 ):
                bot._maybe_complete_pending_order_multiverse()
            schedule.assert_not_called()
            self.assertEqual(processed[-2:], ["pending-2", "pending-3"])

            bot._order_multiverse_last_poll = 0.0
            with mock.patch.object(bot, "_schedule_collector_v22_provisional_merge") as schedule, \
                 mock.patch.object(
                     bot,
                     "persist_rejected_opportunity",
                     side_effect=lambda src, **kw: processed.append(src["trade_id"]),
                 ):
                bot._maybe_complete_pending_order_multiverse()
            schedule.assert_not_called()
            self.assertEqual(processed[-2:], ["pending-4", "pending-0"])
        finally:
            bot._order_multiverse_pending_src.clear()
            bot._order_multiverse_pending_src.update(saved_pending)
            bot._order_multiverse_last_poll = saved_poll
            bot._collector_v22_last_merge = saved_merge
            bot._order_multiverse_maturation_cursor = saved_cursor

    def test_terminal_ready_backlog_is_prioritized_and_adaptive_with_telemetry(self):
        saved_pending = dict(bot._order_multiverse_pending_src)
        saved_poll = bot._order_multiverse_last_poll
        saved_merge = bot._collector_v22_last_merge
        saved_cursor = bot._order_multiverse_maturation_cursor
        saved_sweep_batch = bot._order_multiverse_ready_sweep_batch
        saved_sweep_started = bot._order_multiverse_ready_sweep_started_ts
        saved_status = dict(bot.state.get("collector_maturation") or {})
        now = bot.time.time()
        try:
            bot._order_multiverse_pending_src.clear()
            for index in range(121):
                bot._order_multiverse_pending_src[f"ready-{index:03d}"] = {
                    "trade_id": f"ready-{index:03d}",
                    "collector_rejected": True,
                    "created_ts_ts": now - 7200 - index,
                    "expires_ts": now - bot.POST_TTL_LOOKAHEAD_SEC - 1 - index,
                }
            bot._order_multiverse_pending_src["waiting"] = {
                "trade_id": "waiting",
                "collector_rejected": True,
                "created_ts_ts": now,
                "expires_ts": now + 1800,
            }
            bot._order_multiverse_last_poll = 0.0
            bot._collector_v22_last_merge = now
            bot._order_multiverse_maturation_cursor = 0
            bot._order_multiverse_ready_sweep_batch = 0
            bot._order_multiverse_ready_sweep_started_ts = 0.0
            processed = []
            with mock.patch.object(bot, "persist_rejected_opportunity", side_effect=lambda src, **kw: processed.append(src["trade_id"])):
                bot._maybe_complete_pending_order_multiverse()
            # ceil(121 / 60-minute target) = 3 and mature rows always outrank waiting rows.
            self.assertEqual(len(processed), 3)
            self.assertEqual(processed, ["ready-120", "ready-119", "ready-118"])
            status = bot.state["collector_maturation"]
            self.assertEqual(status["pending"], 122)
            self.assertEqual(status["terminal_ready"], 121)
            self.assertEqual(status["effective_batch_size"], 3)
            self.assertEqual(status["estimated_ready_sweep_minutes"], 41)
            self.assertEqual(status["target_status"], "ON_TARGET")

            # The sweep batch remains fixed as the backlog shrinks, so its ETA
            # is achievable rather than being recomputed optimistically.
            for trade_id in processed:
                bot._order_multiverse_pending_src.pop(trade_id)
            processed.clear()
            bot._order_multiverse_last_poll = 0.0
            with mock.patch.object(bot, "persist_rejected_opportunity", side_effect=lambda src, **kw: processed.append(src["trade_id"])):
                bot._maybe_complete_pending_order_multiverse()
            self.assertEqual(len(processed), 3)
            self.assertEqual(bot.state["collector_maturation"]["effective_batch_size"], 3)
            self.assertEqual(bot.state["collector_maturation"]["estimated_ready_sweep_minutes"], 40)
        finally:
            bot._order_multiverse_pending_src.clear()
            bot._order_multiverse_pending_src.update(saved_pending)
            bot._order_multiverse_last_poll = saved_poll
            bot._collector_v22_last_merge = saved_merge
            bot._order_multiverse_maturation_cursor = saved_cursor
            bot._order_multiverse_ready_sweep_batch = saved_sweep_batch
            bot._order_multiverse_ready_sweep_started_ts = saved_sweep_started
            with bot.state_lock:
                bot.state["collector_maturation"] = saved_status

    def test_periodic_background_remerge_is_cooldown_limited(self):
        saved_pending = dict(bot._order_multiverse_pending_src)
        saved_poll = bot._order_multiverse_last_poll
        saved_merge = bot._collector_v22_last_merge
        try:
            bot._order_multiverse_pending_src.clear()
            bot._order_multiverse_last_poll = 0.0
            bot._collector_v22_last_merge = bot.time.time()
            with mock.patch.object(bot, "_schedule_collector_v22_provisional_merge") as schedule:
                bot._maybe_complete_pending_order_multiverse()
            schedule.assert_not_called()

            bot._order_multiverse_last_poll = 0.0
            bot._collector_v22_last_merge = 0.0
            with mock.patch.object(bot, "_schedule_collector_v22_provisional_merge", return_value=True) as schedule:
                bot._maybe_complete_pending_order_multiverse()
            schedule.assert_called_once()
            self.assertEqual(schedule.call_args.kwargs["reason"], "MATURATION_POLL")
        finally:
            bot._order_multiverse_pending_src.clear()
            bot._order_multiverse_pending_src.update(saved_pending)
            bot._order_multiverse_last_poll = saved_poll
            bot._collector_v22_last_merge = saved_merge

    def test_periodic_merge_recovers_missing_row_while_resident_row_remains(self):
        saved_pending = dict(bot._order_multiverse_pending_src)
        saved_state = dict(bot._order_multiverse_state)
        try:
            resident = {"trade_id": "resident", "status": "REJECTED"}
            missing = {"trade_id": "durable-only", "status": "REJECTED"}
            bot._order_multiverse_pending_src.clear()
            bot._order_multiverse_pending_src["resident"] = resident
            with mock.patch.object(bot, "_collector_v22_epoch_id", return_value="epoch-integrity"), \
                 mock.patch.object(bot, "load_provisional_events", return_value={"resident": resident, "durable-only": missing}), \
                 mock.patch.object(bot, "event_already_written", return_value=False):
                self.assertEqual(bot._merge_collector_v22_provisionals(reason="PERIODIC_TEST"), 1)
            self.assertIs(bot._order_multiverse_pending_src["resident"], resident)
            self.assertEqual(bot._order_multiverse_pending_src["durable-only"]["trade_id"], "durable-only")
        finally:
            bot._order_multiverse_pending_src.clear()
            bot._order_multiverse_pending_src.update(saved_pending)
            bot._order_multiverse_state.clear()
            bot._order_multiverse_state.update(saved_state)

    def test_stale_merge_cannot_remove_provisional_after_epoch_changes(self):
        source = {"trade_id": "same-id", "status": "REJECTED"}
        # Capture old epoch, pass the pre-check, then simulate reset before the
        # already-written cleanup boundary.
        epochs = iter(("epoch-old", "epoch-old", "epoch-new"))
        with mock.patch.object(bot, "_collector_v22_epoch_id", side_effect=lambda: next(epochs)), \
             mock.patch.object(bot, "load_provisional_events", return_value={"same-id": source}), \
             mock.patch.object(bot, "event_already_written", return_value=True), \
             mock.patch.object(bot, "remove_provisional_event") as remove:
            self.assertEqual(bot._merge_collector_v22_provisionals(reason="EPOCH_RACE_TEST"), 0)
        remove.assert_not_called()

    def test_maturation_reports_when_target_is_unachievable_at_hard_cap(self):
        saved_pending = dict(bot._order_multiverse_pending_src)
        saved_poll = bot._order_multiverse_last_poll
        saved_merge = bot._collector_v22_last_merge
        saved_batch = bot._order_multiverse_ready_sweep_batch
        saved_started = bot._order_multiverse_ready_sweep_started_ts
        now = bot.time.time()
        try:
            bot._order_multiverse_pending_src.clear()
            total = (
                bot.COLLECTOR_MATURATION_MAX_BATCH_SIZE
                * bot.COLLECTOR_MATURATION_TARGET_SWEEP_MIN
                + 1
            )
            for index in range(total):
                bot._order_multiverse_pending_src[f"capped-{index:04d}"] = {
                    "trade_id": f"capped-{index:04d}",
                    "collector_rejected": True,
                    "created_ts_ts": now - 7200,
                    "expires_ts": now - bot.POST_TTL_LOOKAHEAD_SEC - 1,
                }
            bot._order_multiverse_last_poll = 0.0
            bot._collector_v22_last_merge = now
            bot._order_multiverse_ready_sweep_batch = 0
            bot._order_multiverse_ready_sweep_started_ts = 0.0
            with mock.patch.object(bot, "persist_rejected_opportunity") as persist:
                bot._maybe_complete_pending_order_multiverse()
            self.assertEqual(persist.call_count, bot.COLLECTOR_MATURATION_MAX_BATCH_SIZE)
            status = bot.state["collector_maturation"]
            self.assertEqual(status["target_status"], "TARGET_UNACHIEVABLE_MAX_BATCH")
            self.assertGreater(status["estimated_ready_sweep_minutes"], status["target_sweep_minutes"])
        finally:
            bot._order_multiverse_pending_src.clear()
            bot._order_multiverse_pending_src.update(saved_pending)
            bot._order_multiverse_last_poll = saved_poll
            bot._collector_v22_last_merge = saved_merge
            bot._order_multiverse_ready_sweep_batch = saved_batch
            bot._order_multiverse_ready_sweep_started_ts = saved_started

    def test_slow_shadow_append_does_not_hold_compressed_ownership_lock(self):
        saved_book = dict(bot._compressed_shadow_chase_book)
        saved_seen = set(bot._compressed_shadow_seen_call_ids)
        saved_attempted = bot._compressed_shadow_recovery_attempted
        append_started = threading.Event()
        release_append = threading.Event()
        finished = threading.Event()

        def slow_append(*args, **kwargs):
            append_started.set()
            release_append.wait(2.0)
            return True

        bot._compressed_shadow_chase_book.clear()
        bot._compressed_shadow_chase_book["shadow"] = {"terminal_emitted": False}
        bot._compressed_shadow_recovery_attempted = True

        def poll_shadow():
            bot._poll_chase_offset_touch_grid(100000.0, 99999.0, 100001.0)
            finished.set()

        try:
            with mock.patch.object(bot, "_shadow_stage_direction_revalidation", return_value=("VALID", "TEST")), \
                 mock.patch.object(bot, "poll_compressed_shadow_chase", return_value=[{"event": "TEST"}]), \
                 mock.patch.object(bot, "_safe_append_jsonl", side_effect=slow_append), \
                 mock.patch.object(bot, "_maybe_complete_pending_order_multiverse"):
                worker = threading.Thread(target=poll_shadow)
                worker.start()
                self.assertTrue(append_started.wait(1.0))
                acquired = bot._compressed_shadow_lock.acquire(timeout=1.0)
                self.assertTrue(acquired)
                if acquired:
                    bot._compressed_shadow_lock.release()
                release_append.set()
                self.assertTrue(finished.wait(1.0))
                worker.join(2.0)
        finally:
            release_append.set()
            bot._compressed_shadow_chase_book.clear()
            bot._compressed_shadow_chase_book.update(saved_book)
            bot._compressed_shadow_seen_call_ids.clear()
            bot._compressed_shadow_seen_call_ids.update(saved_seen)
            bot._compressed_shadow_recovery_attempted = saved_attempted

    def test_compressed_shadow_recovery_does_not_wait_for_collector_epoch_lock(self):
        saved_attempted = bot._compressed_shadow_recovery_attempted
        lock_held = threading.Event()
        release_lock = threading.Event()
        recovered = threading.Event()

        def hold_collector():
            with bot._collector_epoch_lock:
                lock_held.set()
                release_lock.wait(2.0)

        def recover_shadow():
            bot._recover_compressed_shadow_chases_once()
            recovered.set()

        bot._compressed_shadow_recovery_attempted = False
        holder = threading.Thread(target=hold_collector)
        worker = threading.Thread(target=recover_shadow)
        try:
            holder.start()
            self.assertTrue(lock_held.wait(1.0))
            with mock.patch("builtins.open", side_effect=FileNotFoundError), \
                 mock.patch.object(bot, "recover_compressed_shadow_states", return_value={}):
                worker.start()
                self.assertTrue(recovered.wait(1.0))
        finally:
            release_lock.set()
            holder.join(2.0)
            worker.join(2.0)
            bot._compressed_shadow_recovery_attempted = saved_attempted

    def test_overdue_gapped_path_terminalizes_as_unranked_negative_evidence(self):
        candles = [_bar(i) for i in range(-60, 181) if i != -30]
        event = build_research_event(
            trade_id="overdue-gap", epoch_id="epoch-integrity", signal_ts=SIGNAL_TS,
            signal_price=100000.0, candles_1m=candles, submitted=False,
            rejected=True, ticket_closed=True, evaluation_ts=SIGNAL_TS + 181 * 60,
        )
        self.assertEqual(event["observation_status"], OBS_INSUFFICIENT_PATH)
        self.assertTrue(event["immutable"])
        self.assertTrue(event["negative_evidence"])
        self.assertFalse(event["ranking_eligible"])
        self.assertFalse(event["replay_eligibility"]["eligible"])
        self.assertEqual(event["replay_outcomes"], [])

    def test_not_yet_due_incomplete_hold_stays_waiting(self):
        candles = []
        for i in range(-60, 101):
            row = _bar(i)
            if i == 30:
                row[2] = 100200.0  # SHORT alternative entry fills at minute 30.
            candles.append(row)
        event = build_research_event(
            trade_id="not-due", epoch_id="epoch-integrity", signal_ts=SIGNAL_TS,
            signal_price=100000.0, candles_1m=candles, ticket_closed=True,
            evaluation_ts=SIGNAL_TS + 100 * 60,
        )
        self.assertEqual(event["observation_status"], OBS_WAITING_120M)
        self.assertFalse(event["immutable"])

    def test_terminal_negative_write_once_survives_crash_and_analyzer_excludes_it(self):
        with tempfile.TemporaryDirectory() as root:
            event = build_research_event(
                trade_id="negative-crash", epoch_id="epoch-integrity", signal_ts=SIGNAL_TS,
                signal_price=100000.0,
                candles_1m=[_bar(i) for i in range(-60, 181) if i != 20],
                submitted=False, rejected=True, ticket_closed=True,
                evaluation_ts=SIGNAL_TS + 181 * 60,
            )
            self.assertFalse(event_already_written("negative-crash", data_dir=root))
            with mock.patch.object(collector_v22, "_insert_index_line", side_effect=OSError("crash after negative append")):
                with self.assertRaisesRegex(OSError, "crash after negative append"):
                    write_research_event_once(event, data_dir=root)
            self.assertEqual(write_research_event_once(event, data_dir=root), (False, "duplicate event_id"))
            report = analyze_v22_events(data_dir=root)
            integrity = report["replay_integrity"]
            self.assertEqual(integrity["eligible_events"], 0)
            self.assertEqual(integrity["ineligible_events"], 1)
            self.assertEqual(integrity["observation_statuses"][OBS_INSUFFICIENT_PATH], 1)
            self.assertGreater(sum(integrity["blockers"].values()), 0)


if __name__ == "__main__":
    unittest.main()
