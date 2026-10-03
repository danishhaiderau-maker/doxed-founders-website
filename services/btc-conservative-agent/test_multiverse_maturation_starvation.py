"""CONTROL_V1 order-multiverse maturation must not starve under order volume.

Regression for the 2026-10-02 collapse after 29742de53: taker tiles close
within ~60 s, so their rows looked terminal-ready immediately, but the
collector cannot finalize before signal+entry window / fill+hold.  The
fewest-attempts-first drain charged an attempt on every one of those passes,
so rows that waited longest sorted behind every newer row and were never
written (no CONTROL_V1 entry-grid intent, analyzer entry specs 304 -> 4).
"""

import ast
import os
import sys
import tempfile
import types
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("FORCE_PAPER_MODE", "1")
os.environ.setdefault("RESEARCH_DATA_COLLECTION", "1")
os.environ.setdefault("SKIP_EXCHANGE_MARKET_LOAD", "1")

import bot
from chase_offset_touch_grid import CHASE_POLICIES, OFFSET_PCT_GRID
from collector_v22_schema import MAX_ENTRY_WINDOW_SEC, MAX_HOLD_PERIOD_SEC

T0 = 1_790_935_000.0
SYNC_COST_SEC = 0.075


class _Clock:
    def __init__(self, now):
        self.now = float(now)
        self.mono = 0.0

    def time(self):
        return self.now

    def monotonic(self):
        return self.mono

    def sleep(self, seconds):
        self.mono += float(seconds or 0)


class _SavedCollectorState:
    """Snapshot/restore every module global the maturation drain mutates."""

    NAMES = (
        "_order_multiverse_last_poll",
        "_collector_v22_last_merge",
        "_order_multiverse_maturation_cursor",
        "_order_multiverse_ready_sweep_batch",
        "_order_multiverse_ready_sweep_started_ts",
    )

    def __enter__(self):
        self.scalars = {name: getattr(bot, name) for name in self.NAMES}
        self.pending = dict(bot._order_multiverse_pending_src)
        self.state = dict(bot._order_multiverse_state)
        self.written = set(bot._order_multiverse_written)
        self.attempts = dict(bot._order_multiverse_maturation_attempts)
        self.worker = dict(bot._collector_maturation_worker_status)
        self.reconcile = dict(bot._collector_v3_reconcile_status)
        self.status = dict(bot.state.get("collector_maturation") or {})
        bot._order_multiverse_pending_src.clear()
        bot._order_multiverse_state.clear()
        bot._order_multiverse_written.clear()
        bot._order_multiverse_maturation_attempts.clear()
        bot._order_multiverse_ready_sweep_batch = 0
        bot._order_multiverse_ready_sweep_started_ts = 0.0
        bot._order_multiverse_maturation_cursor = 0
        bot._collector_v3_reconcile_status["alive"] = True
        return self

    def __exit__(self, *exc):
        for name, value in self.scalars.items():
            setattr(bot, name, value)
        for target, saved in (
            (bot._order_multiverse_pending_src, self.pending),
            (bot._order_multiverse_state, self.state),
            (bot._order_multiverse_maturation_attempts, self.attempts),
            (bot._collector_maturation_worker_status, self.worker),
            (bot._collector_v3_reconcile_status, self.reconcile),
        ):
            target.clear()
            target.update(saved)
        bot._order_multiverse_written.clear()
        bot._order_multiverse_written.update(self.written)
        with bot.state_lock:
            bot.state["collector_maturation"] = self.status
        return False


def _taker_row(trade_id, signal_ts, finalize_at):
    """A filled+closed 60 s taker order, the XVP/XVL shape on 29742de53."""
    return {
        "trade_id": trade_id,
        "created_ts_ts": signal_ts,
        "expires_ts": signal_ts + 60.0,
        "live_fill_ts": signal_ts + 1.0,
        "status": "CLOSED",
        "ticket_closed": True,
        "_finalize_at": finalize_at,
    }


class MaturationStarvationTests(unittest.TestCase):
    def _simulate(self, *, hours, orders_per_hour):
        """Drive the real drain with a fake collector at live 29742de53 rates."""
        clock = _Clock(T0)
        emitted = Counter()
        emitted_at = {}
        decisions = {}

        def fake_sync(src, *, path_complete=False):
            clock.mono += SYNC_COST_SEC
            tid = src["trade_id"]
            if clock.now >= src["_finalize_at"]:
                bot._order_multiverse_pending_src.pop(tid, None)
                bot._order_multiverse_written.add(tid)
                emitted[tid] += 1
                emitted_at[tid] = clock.now
                return {"observation_status": "COMPLETE"}
            return {"observation_status": "WAITING_120M"}

        fake_time = types.SimpleNamespace(
            time=clock.time, monotonic=clock.monotonic, sleep=clock.sleep,
        )
        spacing = 3600.0 / orders_per_hour
        next_order = T0
        index = 0
        end = T0 + hours * 3600.0
        with mock.patch.object(bot, "time", fake_time), \
             mock.patch.object(bot, "_sync_order_multiverse", side_effect=fake_sync), \
             mock.patch.object(bot, "_schedule_collector_v22_provisional_merge"):
            bot._collector_v22_last_merge = T0 + hours * 3600.0
            while clock.now < end:
                while next_order <= clock.now:
                    tid = f"xvp-{index:05d}"
                    # Terminal no earlier than fill+hold, sometimes up to the
                    # entry-window + hold upper bound (latest child fill).
                    finalize_at = next_order + 1.0 + MAX_HOLD_PERIOD_SEC + (index % 4) * 900.0
                    bot._order_multiverse_pending_src[tid] = _taker_row(tid, next_order, finalize_at)
                    decisions[tid] = finalize_at
                    index += 1
                    next_order += spacing
                start_mono = clock.mono
                bot._maybe_complete_pending_order_multiverse(from_worker=True)
                clock.now += (clock.mono - start_mono) + bot.COLLECTOR_MATURATION_WORKER_BACKLOG_INTERVAL_SEC
        return clock, decisions, emitted, emitted_at

    def test_high_volume_taker_rows_all_finalize_once_without_starvation(self):
        with _SavedCollectorState():
            clock, decisions, emitted, emitted_at = self._simulate(hours=9, orders_per_hour=90)
            due = {tid: at for tid, at in decisions.items() if at <= clock.now - 600.0}
            self.assertGreater(len(due), 400)
            missing = sorted(tid for tid in due if emitted[tid] == 0)
            self.assertEqual(missing, [], f"{len(missing)} decisions never emitted CONTROL_V1 grid")
            # Exactly one CONTROL_V1 entry-grid event per decision.
            self.assertEqual({tid for tid, n in emitted.items() if n != 1}, set())
            worst_lag = max(emitted_at[tid] - due[tid] for tid in due)
            self.assertLess(worst_lag, 600.0)

    def test_rows_inside_their_path_window_are_not_ready_and_not_charged(self):
        with _SavedCollectorState():
            now = T0 + 10_000.0
            fresh = _taker_row("fresh", now - 120.0, now + 7200.0)
            mature = _taker_row("mature", now - MAX_ENTRY_WINDOW_SEC - MAX_HOLD_PERIOD_SEC - 60.0, now - 1.0)
            bot._order_multiverse_pending_src.update({"fresh": fresh, "mature": mature})
            seen = []

            def fake_sync(src, *, path_complete=False):
                seen.append(src["trade_id"])
                return {"observation_status": "WAITING_120M"}

            with mock.patch.object(bot.time, "time", return_value=now), \
                 mock.patch.object(bot, "_sync_order_multiverse", side_effect=fake_sync), \
                 mock.patch.object(bot, "_schedule_collector_v22_provisional_merge"):
                bot._collector_v22_last_merge = now
                bot._maybe_complete_pending_order_multiverse(from_worker=True)
            status = bot.state["collector_maturation"]
            self.assertEqual(status["terminal_ready"], 1)
            self.assertEqual(status["overdue"], 1)
            self.assertEqual(seen[0], "mature")
            self.assertIn("fresh", seen)
            # A WAITING observation is not a failed attempt for either row.
            self.assertEqual(bot._order_multiverse_maturation_attempts.get("fresh", 0), 0)
            self.assertEqual(bot._order_multiverse_maturation_attempts.get("mature", 0), 0)

    def test_failed_finalize_after_deadline_still_demotes_the_row(self):
        with _SavedCollectorState():
            now = T0 + 20_000.0
            old = now - MAX_ENTRY_WINDOW_SEC - MAX_HOLD_PERIOD_SEC - 600.0
            for tid in ("broken", "healthy"):
                bot._order_multiverse_pending_src[tid] = _taker_row(tid, old, old)

            with mock.patch.object(bot.time, "time", return_value=now), \
                 mock.patch.object(bot, "_sync_order_multiverse", return_value=None), \
                 mock.patch.object(bot, "_schedule_collector_v22_provisional_merge"):
                bot._collector_v22_last_merge = now
                bot._maybe_complete_pending_order_multiverse(from_worker=True)
            self.assertEqual(bot._order_multiverse_maturation_attempts["broken"], 1)
            self.assertEqual(bot._order_multiverse_maturation_attempts["healthy"], 1)

    def test_overdue_backlog_raises_research_collection_alarm(self):
        with _SavedCollectorState():
            now = T0 + 50_000.0
            bot._order_multiverse_pending_src["stuck"] = _taker_row("stuck", T0, T0)
            bot._collector_maturation_worker_status.update({
                "alive": True,
                "last_pass_ts": now,
                "last_overdue": 1,
                "last_oldest_overdue_ts": now - bot.COLLECTOR_FINALIZABLE_BACKLOG_ALARM_SEC - 5.0,
            })
            health = bot.research_collection_health(now=now)
            self.assertIn("COLLECTOR_MATURATION_FINALIZABLE_BACKLOG", health["alarms"])
            self.assertEqual(health["status"], "ALARM")
            self.assertEqual(health["multiverse"]["overdue"], 1)

            bot._collector_maturation_worker_status["last_oldest_overdue_ts"] = now - 60.0
            health = bot.research_collection_health(now=now)
            self.assertNotIn("COLLECTOR_MATURATION_FINALIZABLE_BACKLOG", health["alarms"])


def _bars(signal_ts, start_min, end_min, price=86000.0):
    rows = []
    for minute in range(start_min, end_min):
        ts = int(signal_ts // 60 * 60 + minute * 60)
        rows.append([ts * 1000, price, price + 5.0, price - 5.0, price, 1.0])
    return rows


class ControlV1EmissionCountTests(unittest.TestCase):
    """One decision -> exactly one CONTROL_V1 event carrying the full entry grid."""

    def test_one_full_grid_event_per_decision_only_after_path_window(self):
        signal_ts = 1_790_950_000.0
        source = _taker_row("xvp-emission", signal_ts, 0.0)
        source.pop("_finalize_at")
        source.update({"signal_price": 86000.0, "final_direction": "LONG", "qty": 0.00029})
        appended = []
        with _SavedCollectorState(), tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as root:
            clock = {"now": signal_ts + 600.0}

            def path(ts):
                end = int((clock["now"] - signal_ts) // 60)
                return _bars(signal_ts, -70, end), {"window_state": "COMPLETE", "late_backfill": False}

            with mock.patch.object(bot, "_data_sync_runtime_root", return_value=Path(root)), \
                 mock.patch.object(bot, "_collector_v22_epoch_id", return_value="epoch-mv-test"), \
                 mock.patch.object(bot, "_collector_source_in_current_epoch", return_value=True), \
                 mock.patch.object(bot, "storage_blocks_new_events", return_value=False), \
                 mock.patch.object(bot, "_execution_trade_is_terminal", return_value=False), \
                 mock.patch.object(bot, "_collector_path_candles_1m", side_effect=path), \
                 mock.patch.object(bot, "_append_order_multiverse_row",
                                   side_effect=lambda record, obs: appended.append(record)), \
                 mock.patch.object(bot, "_schedule_collector_v22_provisional_merge"), \
                 mock.patch.object(bot.time, "time", side_effect=lambda: clock["now"]):
                bot._order_multiverse_pending_src["xvp-emission"] = dict(source)
                bot._collector_v22_last_merge = clock["now"]
                bot._maybe_complete_pending_order_multiverse(from_worker=True)
                self.assertEqual(appended, [])
                self.assertEqual(bot._order_multiverse_maturation_attempts.get("xvp-emission", 0), 0)

                clock["now"] = signal_ts + MAX_ENTRY_WINDOW_SEC + MAX_HOLD_PERIOD_SEC + 300.0
                bot._collector_v22_last_merge = clock["now"]
                for _ in range(3):
                    bot._maybe_complete_pending_order_multiverse(from_worker=True)
        self.assertEqual(len(appended), 1)
        event = appended[0]
        self.assertEqual(event["envelope"]["policy_id"], "CONTROL_V1")
        self.assertEqual(len(event["entry_children"]), len(OFFSET_PCT_GRID) * len(CHASE_POLICIES))
        self.assertEqual(
            {child["entry_policy_id"] for child in event["entry_children"]},
            {f"OFFSET_{offset:.2f}_CHASE_{policy['id']}" for offset in OFFSET_PCT_GRID for policy in CHASE_POLICIES},
        )


def _bot_function_source(name):
    tree = ast.parse(Path(bot.__file__).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(name)


class ShadowNeverOrdersOrRelaysTests(unittest.TestCase):
    MATURATION_PATH = (
        "_maybe_complete_pending_order_multiverse",
        "_sync_order_multiverse",
        "_collector_earliest_finalize_ts",
        "_collector_overdue_finalize_ts",
    )
    FORBIDDEN = (
        "process_signal", "register_paper_order", "open_position", "close_position",
        "_push_showcase_relay_event", "_relay_mirror", "_deliver_relay_outbox_record",
        "_commit_marketable_relay_payload", "_commit_relay_limit_chase", "create_order",
        "place_order", "submit_order",
    )

    def test_maturation_path_references_no_order_or_relay_entrypoint(self):
        for name in self.MATURATION_PATH:
            called = {
                getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                for node in ast.walk(_bot_function_source(name))
                if isinstance(node, ast.Call)
            }
            self.assertEqual(called & set(self.FORBIDDEN), set(), name)

    def test_maturation_drain_never_creates_orders_or_relay_events(self):
        tripwires = {}
        with _SavedCollectorState():
            now = T0 + 30_000.0
            for i in range(40):
                tid = f"shadow-{i}"
                bot._order_multiverse_pending_src[tid] = _taker_row(tid, T0 + i, T0 + i)
            with bot.state_lock:
                orders_before = len(bot.state.get("orders") or [])
                positions_before = len(bot.state.get("positions") or [])
            patches = [
                mock.patch.object(bot, name, side_effect=AssertionError(f"shadow called {name}"))
                for name in self.FORBIDDEN if hasattr(bot, name)
            ]
            for patcher in patches:
                tripwires[patcher.attribute] = patcher.start()
            try:
                with mock.patch.object(bot.time, "time", return_value=now), \
                     mock.patch.object(bot, "_sync_order_multiverse",
                                       side_effect=lambda src, **kw: {"observation_status": "WAITING_120M"}), \
                     mock.patch.object(bot, "_schedule_collector_v22_provisional_merge"):
                    bot._collector_v22_last_merge = now
                    bot._maybe_complete_pending_order_multiverse(from_worker=True)
            finally:
                for patcher in patches:
                    patcher.stop()
            with bot.state_lock:
                self.assertEqual(len(bot.state.get("orders") or []), orders_before)
                self.assertEqual(len(bot.state.get("positions") or []), positions_before)
        self.assertTrue(tripwires)
        for name, tripwire in tripwires.items():
            tripwire.assert_not_called()


if __name__ == "__main__":
    unittest.main()
