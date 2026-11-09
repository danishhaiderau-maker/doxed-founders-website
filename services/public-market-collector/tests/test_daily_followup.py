"""PR988 follow-up: synthetic-only regression, launcher and daily runtime checks."""
from __future__ import annotations

import io
import json
import os
import signal
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_capture_residuals as fixtures
from public_market_collector import __main__ as entry
from public_market_collector.config import CaptureStopped, StartRefused
from public_market_collector.daily import Coverage, RuntimeLock, default_root, execute_daily, run_daily, utc_day
from public_market_collector.framing import read_journal
from public_market_collector.guards import scan_package
from public_market_collector.public_run import FREE_DISK_FLOOR_BYTES, Reconnect, open_session, pump
from public_market_collector.relaunch import LaunchJournal, decide, supervise


class FollowupTests(unittest.TestCase):
    _probe = fixtures.ResidualTests._probe
    _open = fixtures.ResidualTests._open
    _run = fixtures.ResidualTests._run

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="pmc-followup-")
        self.base = Path(self.tmp.name)
        self.free = 10**12
        self.addCleanup(self.tmp.cleanup)

    def test_f1_resume_unsubscribed_no_snapshot_trades_hb_cannot_keep_book_valid(self):
        session = self._open("resume")
        self.addCleanup(session.release)
        seen = []
        script = [fixtures._BOOK_SUB, fixtures._SNAP,
                  fixtures._js({"event": "info", "code": 20061}),
                  fixtures._js({"event": "unsubscribed", "chanId": 7, "status": "OK"}),
                  fixtures._js({"event": "error", "code": 10301, "msg": "already subscribed"}),
                  fixtures._js({"event": "subscribed", "channel": "trades", "chanId": 8})]
        script += [fixtures._js([8, "hb"])] * 50

        class Watch(fixtures._ListTransport):
            def recv_text(inner):
                if len(inner.messages) == 50:
                    seen.append((session.shadow.valid, session.resubscribe_deadline_ns))
                return super().recv_text()

        transport = Watch(script)
        with self.assertRaises(Reconnect) as caught:
            pump(session, transport, sleep=lambda _: None,
                 clock_ns=fixtures._Clock(step_ns=1_000_000_000), mono_ns=fixtures._Clock())
        self.assertEqual(caught.exception.cause, "resubscribe_timeout")
        self.assertEqual(seen[0][0], False)
        self.assertIsNotNone(seen[0][1])
        session.commit()
        raw = fixtures._raw_rows(session.root)
        gaps = [r for r in raw if r["channel"] == "gap"]
        self.assertEqual(gaps[0]["payload"]["cause"], "maintenance")
        self.assertEqual(gaps[0]["payload"]["maintenance_event"], "info_20061")
        norm = fixtures._norm_rows(session.root)
        self.assertTrue(any(r["interval_event"] == "close" and r["book_valid"] is False for r in norm))
        self.assertTrue(any(x.get("event") == "unsubscribe" and x.get("chanId") == 7 for x in transport.sent))
        self.assertTrue(any(x.get("event") == "subscribe" and x.get("channel") == "book" for x in transport.sent))

    def test_f1_lost_unsubscribe_reply_stale_snapshot_cannot_clear_deadline(self):
        session = self._open("stale")
        self.addCleanup(session.release)
        observed = []
        script = [fixtures._BOOK_SUB, fixtures._SNAP,
                  fixtures._js({"event": "info", "code": 20061}), fixtures._SNAP,
                  fixtures._js({"event": "subscribed", "channel": "book", "chanId": 9}),
                  fixtures._js([9, [6000, 1, 2]]),
                  fixtures._js([9, [[6000, 1, 1], [6100, 1, -1]]])]

        class Watch(fixtures._ListTransport):
            def recv_text(inner):
                if len(inner.messages) in {3, 1}:
                    observed.append((session.shadow.valid, session.resubscribe_deadline_ns))
                return super().recv_text()

        pump(session, Watch(script), sleep=lambda _: None, clock_ns=fixtures._Clock(),
             mono_ns=fixtures._Clock(), max_messages=len(script))
        self.assertEqual([x[0] for x in observed], [False, False])
        self.assertTrue(all(x[1] is not None for x in observed))
        self.assertTrue(session.shadow.valid)
        self.assertIsNone(session.resubscribe_deadline_ns)
        deltas = [r for r in fixtures._norm_rows(session.root) if r["channel"] == "book_delta"]
        self.assertFalse(deltas[0]["applied"])

    def test_f1_unsubscribe_book_invalidates_but_trade_unsubscribe_does_not(self):
        session = self._open("unsub")
        self.addCleanup(session.release)
        for text in [fixtures._BOOK_SUB, fixtures._SNAP,
                     fixtures._js({"event": "subscribed", "channel": "trades", "chanId": 8}),
                     fixtures._js({"event": "unsubscribed", "chanId": 8})]:
            session.handle(text, recv_utc_ns=1, recv_mono_ns=1)
        self.assertTrue(session.shadow.valid)
        session.handle(fixtures._js({"event": "unsubscribed", "chanId": 7}), recv_utc_ns=2, recv_mono_ns=2)
        self.assertFalse(session.shadow.valid)
        self.assertTrue(session.pending_resubscribe)
        session.commit()
        gaps = [r["payload"] for r in fixtures._raw_rows(session.root) if r["channel"] == "gap"]
        self.assertEqual(gaps[0]["maintenance_event"], "book_unsubscribed")

    def test_f1_idle_after_resume_hits_snapshot_deadline(self):
        session = self._open("idle-resume")
        self.addCleanup(session.release)
        script = [fixtures._BOOK_SUB, fixtures._SNAP, fixtures._js({"event": "info", "code": 20061})]
        with self.assertRaises(Reconnect) as caught:
            pump(session, fixtures._ListTransport(script, then="timeout"), sleep=lambda _: None,
                 clock_ns=fixtures._Clock(step_ns=4_000_000_000), mono_ns=fixtures._Clock())
        self.assertEqual(caught.exception.cause, "resubscribe_timeout")

    def test_hmx2_heartbeat_only_over_30_seconds_never_resets_backoff(self):
        socks = [fixtures._ScriptSock([fixtures._GREETING, fixtures._BOOK_SUB] +
                                     [fixtures._js([7, "hb"])] * 8) for _ in range(6)]
        sleeps = []
        session = self._run("hb-long", fixtures._connector(socks), sleeps,
                            max_connects=6, clock=fixtures._Clock(step_ns=5_000_000_000))
        self.assertEqual(session.data_count, 0)
        self.assertEqual(sleeps, [1, 2, 4, 8, 16, 30])

    def test_c3_decisions_terminal_75_78_and_graceful_0(self):
        for code in (0, 75, 78):
            self.assertFalse(decide(code).restart)
        self.assertTrue(decide(1).restart)
        self.assertGreaterEqual(decide(1).wait_s, 6)

    def test_c3_supervisor_waits_six_seconds_after_each_exit(self):
        clock = fixtures._Clock(start_ns=0, step_ns=0)
        starts = []
        codes = iter([1, 1, 75])
        def sleep(seconds):
            self.assertGreaterEqual(seconds, 6)
            clock.now += int(seconds * 1e9)
        def run():
            starts.append(clock.now)
            return next(codes)
        journal = LaunchJournal(self.base / "launch.journal")
        self.assertEqual(supervise(run, journal=journal, clock_ns=clock, sleep=sleep), 75)
        self.assertEqual(starts, [0, 6_000_000_000, 12_000_000_000])
        rows, torn = read_journal(journal.path)
        self.assertFalse(torn)
        self.assertEqual([r["kind"] for r in rows], ["start", "exit"] * 3)

    def test_c3_persisted_terminal_exit_blocks_separate_invocations(self):
        for code in (75, 78):
            root = self.base / str(code)
            root.mkdir()
            self.assertEqual(execute_daily(str(root), runner=lambda *_: code, disk_free_probe=self._probe), code)
            with redirect_stderr(io.StringIO()):
                rc = execute_daily(str(root), runner=lambda *_: self.fail("restarted"), disk_free_probe=self._probe, max_runs=1)
            self.assertEqual(rc, 78)

    def test_c3_prior_start_without_exit_and_torn_journal_refuse(self):
        for kind in ("uncertain", "torn"):
            root = self.base / kind
            root.mkdir()
            journal = LaunchJournal(root / "launch.journal")
            journal.append("start", 1)
            if kind == "torn":
                with journal.path.open("ab") as f:
                    f.write(b"torn")
            with self.assertRaises(StartRefused):
                journal.startup_gate()

    def test_c3_new_process_waits_full_floor_even_if_wall_clock_jumps(self):
        journal = LaunchJournal(self.base / "launch.journal")
        journal.append("start", 10**20)
        journal.append("exit", 10**20, exit_code=1)
        self.assertEqual(journal.startup_gate(), 6)

    def test_runtime_single_writer_lock_blocks_competing_launcher(self):
        lock = RuntimeLock(self.base / "runtime.lock")
        try:
            with self.assertRaises(StartRefused):
                RuntimeLock(self.base / "runtime.lock")
        finally:
            lock.close()
        RuntimeLock(self.base / "runtime.lock").close()

    def test_runtime_default_windows_root_and_explicit_nonwindows_root(self):
        self.assertEqual(default_root("win32"), r"C:\DoxxedCrypto\data\collector")
        self.assertIsNone(default_root("linux"))
        with redirect_stderr(io.StringIO()):
            self.assertEqual(entry.main(["daily"]), 78)
            self.assertEqual(entry.main(["daily", "--root"]), 78)
            self.assertEqual(entry.main([]), 78)

    def test_runtime_graceful_sigterm_sets_event_flushes_launcher_exit(self):
        before = signal.getsignal(signal.SIGTERM)
        def run(_path, event):
            signal.raise_signal(signal.SIGTERM)
            self.assertTrue(event.is_set())
            return 0
        self.assertEqual(execute_daily(str(self.base), runner=run, disk_free_probe=self._probe), 0)
        self.assertEqual(signal.getsignal(signal.SIGTERM), before)
        rows, _ = read_journal(self.base / "launch.journal")
        self.assertEqual(rows[-1]["exit_code"], 0)

    def test_runtime_low_disk_no_runner_or_socket(self):
        with redirect_stderr(io.StringIO()):
            code = execute_daily(str(self.base), runner=lambda *_: self.fail("started"),
                                 disk_free_probe=lambda _: FREE_DISK_FLOOR_BYTES)
        self.assertEqual(code, 75)
        self.assertFalse((self.base / "launch.journal").exists())

    def test_runtime_foreign_files_and_symlinks_refused_without_reading(self):
        secret = self.base / "not-collector.txt"
        secret.write_text("do not read")
        with redirect_stderr(io.StringIO()):
            self.assertEqual(execute_daily(str(self.base), runner=lambda *_: self.fail("started")), 78)
        secret.unlink()
        (self.base / "2026-11-10").symlink_to(self.base, target_is_directory=True)
        with redirect_stderr(io.StringIO()):
            self.assertEqual(execute_daily(str(self.base), runner=lambda *_: self.fail("started")), 78)

    def test_runtime_torn_status_journal_refuses(self):
        (self.base / "runtime.journal").write_bytes(b"not a journal")
        with redirect_stderr(io.StringIO()):
            self.assertEqual(execute_daily(str(self.base), runner=lambda *_: self.fail("started"),
                                           disk_free_probe=self._probe), 78)

    def test_runtime_coverage_distinguishes_hb_only_and_invalid_book(self):
        session = self._open("coverage")
        self.addCleanup(session.release)
        now = 1_700_000_000_000_000_000
        coverage = Coverage(utc_day(now), now)
        for parsed in [{"event": "subscribed", "channel": "trades", "chanId": 8},
                       {"event": "subscribed", "channel": "book", "chanId": 7},
                       [8, "hb"], [7, "hb"]]:
            session.handle(json.dumps(parsed), recv_utc_ns=now, recv_mono_ns=1)
            coverage.observe(session, now, parsed)
        record = coverage.record(session, now)
        self.assertEqual(len(record["observed_minutes"]["trades_feed"]), 1)
        self.assertEqual(record["observed_minutes"]["data"], [])
        self.assertEqual(record["observed_minutes"]["valid_book_feed"], [])
        self.assertFalse(record["sealed"])
        self.assertEqual(record["unobserved_minutes"]["data"], 1440)

    def test_runtime_two_utc_days_flush_and_emit_gap_coverage_receipts(self):
        from public_market_collector.daily import DAY_NS
        from public_market_collector.market import Book
        clock = fixtures._Clock(start_ns=20000 * DAY_NS + DAY_NS - 10_000_000_000, step_ns=0)
        first_day = utc_day(clock.now)
        stop = threading.Event()
        book = Book()
        book.replace_agg([[6000, 1, 1], [6100, 1, -1]])
        sockets = []

        class Transport(fixtures._ListTransport):
            def __init__(inner, *, rollover):
                script = [fixtures._BOOK_SUB,
                          fixtures._js({"event": "subscribed", "channel": "trades", "chanId": 8}),
                          fixtures._SNAP,
                          fixtures._js([8, "te", [1 if rollover else 2, clock.now // 1_000_000, 1, 6000]]),
                          fixtures._js([7, "cs", book.checksum()[1]])]
                super().__init__(script)
                inner.rollover = rollover
                inner.closed = False
            def recv_text(inner):
                if inner.messages:
                    return super().recv_text()
                if inner.rollover:
                    clock.now = ((clock.now // DAY_NS) + 1) * DAY_NS
                else:
                    stop.set()
                return fixtures._js([7, "hb"])
            def close(inner):
                inner.closed = True

        def connect():
            transport = Transport(rollover=not sockets)
            sockets.append(transport)
            return transport
        def sleep(seconds):
            clock.now += int(seconds * 1e9)
        rc = run_daily(self.base, clock_ns=clock, mono_ns=clock, sleep=sleep,
                       connect=connect, stop=stop.is_set, disk_free_probe=self._probe,
                       deprioritize=lambda: "posix_nice_10", max_days=2)
        self.assertEqual(rc, 0)
        second_day = utc_day(clock.now)
        self.assertNotEqual(first_day, second_day)
        self.assertEqual(len(sockets), 2)
        self.assertTrue(all(s.closed for s in sockets))
        rows, torn = read_journal(self.base / "runtime.journal")
        self.assertFalse(torn)
        self.assertEqual(len([r for r in rows if r["kind"] == "day_start"]), 2)
        final = [r for r in rows if r["kind"] == "coverage"]
        self.assertEqual(len(final), 2)
        for item in final:
            self.assertEqual(len(item["observed_minutes"]["trades_feed"]), 1)
            self.assertEqual(len(item["observed_minutes"]["valid_book_feed"]), 1)
            self.assertEqual(len(item["observed_minutes"]["data"]), 1)
            self.assertGreater(item["durable_count"], 0)
            self.assertFalse(item["sealed"])
        for day in (first_day, second_day):
            raw = fixtures._raw_rows(self.base / day)
            received = [r for r in raw if r["ts_recv_utc_ns"] > 0]
            self.assertTrue(received)
            self.assertTrue(all(utc_day(r["ts_recv_utc_ns"]) == day for r in received))
            self.assertTrue(any(r["channel"] == "trade" for r in raw))
        ends = [r for r in rows if r["kind"] == "day_end"]
        self.assertEqual([r["reason"] for r in ends], ["utc_rollover", "graceful_stop"])

    def test_runtime_forced_disconnect_reconnects_and_records_gap(self):
        clock = fixtures._Clock(step_ns=0)
        stop = threading.Event()
        connections = []
        class Transport(fixtures._ListTransport):
            def recv_text(inner):
                if not inner.messages and len(connections) >= 2:
                    stop.set()
                    return fixtures._js([7, "hb"])
                return super().recv_text()
            def close(inner):
                pass
        def connect():
            item = Transport([fixtures._BOOK_SUB, fixtures._SNAP])
            connections.append(item)
            return item
        def sleep(s):
            clock.now += int(s * 1e9)
        self.assertEqual(run_daily(self.base, clock_ns=clock, mono_ns=clock,
                                  sleep=sleep, connect=connect, stop=stop.is_set,
                                  disk_free_probe=self._probe,
                                  deprioritize=lambda: "posix_nice_10"), 0)
        self.assertEqual(len(connections), 2)
        raw = fixtures._raw_rows(self.base / utc_day(clock.now))
        gaps = [r["payload"] for r in raw if r["channel"] == "gap"]
        self.assertTrue(any(g["cause"] == "disconnect" and g["reconnect_reason"] == "disconnect" for g in gaps))

    def test_runtime_disk_failure_after_start_leaves_uncertain_latch_no_restart(self):
        def run(_path, _event):
            self.free = FREE_DISK_FLOOR_BYTES
            raise CaptureStopped("disk_floor", "disk floor")
        self.assertEqual(execute_daily(str(self.base), runner=run, disk_free_probe=self._probe), 75)
        self.free = 10**12
        with redirect_stderr(io.StringIO()):
            self.assertEqual(execute_daily(str(self.base), runner=lambda *_: self.fail("restarted"),
                                           disk_free_probe=self._probe), 78)

    def test_runtime_safety_scan_rejects_key_order_ai_plants(self):
        self.assertEqual(scan_package(), [])
        plant = self.base / "plant"
        plant.mkdir()
        for source in ("import openai\n", "client.create_order()\n", "import os\nx=os.environ\n",
                       "client.api_key\n", "import ccxt\n", "import anthropic\n"):
            (plant / "unsafe.py").write_text(source)
            self.assertTrue(scan_package(plant), source)

    def test_runtime_entrypoint_no_env_key_network_or_ai_access_with_fake_runner(self):
        import socket
        seen = []
        def fake(root, event):
            seen.append(root)
            self.assertFalse(event.is_set())
            return 0
        with patch.object(os, "getenv", side_effect=AssertionError("environment read")), \
             patch.object(socket, "create_connection", side_effect=AssertionError("socket opened")), \
             patch.object(socket, "socket", side_effect=AssertionError("socket opened")):
            self.assertEqual(entry.main(["daily", "--root", str(self.base)], runner=fake), 0)
        self.assertEqual(seen, [self.base])
        self.assertFalse(any(name.split(".")[0] in {"openai", "anthropic", "ccxt", "bfxapi"} for name in sys.modules))


    def test_runtime_idle_heartbeats_emit_status_without_claiming_data_coverage(self):
        clock = fixtures._Clock(step_ns=0)
        stop = threading.Event()
        class Transport(fixtures._ListTransport):
            def recv_text(inner):
                text = super().recv_text()
                if not inner.messages:
                    stop.set()
                if isinstance(json.loads(text), list):
                    clock.now += 15_000_000_000
                return text
            def close(inner):
                pass
        script = [fixtures._BOOK_SUB] + [fixtures._js([7, "hb"])] * 6
        self.assertEqual(run_daily(self.base, connect=lambda: Transport(script),
                                  clock_ns=clock, mono_ns=clock, stop=stop.is_set,
                                  disk_free_probe=self._probe,
                                  deprioritize=lambda: "posix_nice_10"), 0)
        rows, torn = read_journal(self.base / "runtime.journal")
        self.assertFalse(torn)
        beats = [r for r in rows if r["kind"] == "heartbeat"]
        self.assertGreaterEqual(len(beats), 3)
        self.assertTrue(all(r["observed_minutes"]["data"] == [] for r in beats))
        self.assertTrue(all(r["book_valid"] is False for r in beats))
        self.assertTrue(all(b["utc_ns"] - a["utc_ns"] <= 30_000_000_000 for a, b in zip(beats, beats[1:])))

    def test_runtime_same_day_resume_never_rewrites_old_segments(self):
        clock = fixtures._Clock(step_ns=0)
        def run():
            stop = threading.Event()
            class Transport(fixtures._ListTransport):
                def recv_text(inner):
                    if not inner.messages:
                        stop.set()
                        return fixtures._js([7, "hb"])
                    return super().recv_text()
                def close(inner):
                    pass
            return run_daily(self.base, connect=lambda: Transport([fixtures._BOOK_SUB, fixtures._SNAP]),
                             clock_ns=clock, mono_ns=clock, stop=stop.is_set,
                             disk_free_probe=self._probe,
                             deprioritize=lambda: "posix_nice_10")
        self.assertEqual(run(), 0)
        day = self.base / utc_day(clock.now)
        before = {p: p.read_bytes() for p in day.rglob("*.gz")}
        clock.now += 10_000_000_000
        self.assertEqual(run(), 0)
        self.assertTrue(all(p.read_bytes() == content for p, content in before.items()))
        self.assertGreater(len(list(day.rglob("*.gz"))), len(before))
        rows, torn = read_journal(day / "index.journal")
        self.assertFalse(torn)
        self.assertTrue(any(row["kind"] == "startup_gap" for row in rows))

    def test_runtime_configurable_floor_can_only_be_raised(self):
        with self.assertRaises(StartRefused):
            open_session(self.base / "raised", resume=False, repo_root=None, allow_root=self.base,
                         disk_free_probe=lambda _: 1024**3, free_disk_floor_bytes=2*1024**3)
        session = open_session(self.base / "default-floor", resume=False, repo_root=None,
                               allow_root=self.base, disk_free_probe=self._probe,
                               free_disk_floor_bytes=0)
        self.addCleanup(session.release)
        self.assertEqual(session.cfg.free_disk_floor_bytes, FREE_DISK_FLOOR_BYTES)



    def test_runtime_health_journal_also_honours_disk_floor(self):
        session = self._open("health-floor")
        self.addCleanup(session.release)
        self.free = FREE_DISK_FLOOR_BYTES
        with self.assertRaises(CaptureStopped):
            session.write_health(1)
        self.assertEqual((session.root / "health.journal").stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
