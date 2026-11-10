"""Offline Win32 binding/exit-78 regressions, not real Windows API tests."""
from __future__ import annotations

import ctypes
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from ctypes import wintypes
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from public_market_collector.config import StartRefused
from public_market_collector.daily import execute_daily, run_daily
from public_market_collector.framing import read_journal
from public_market_collector.ws_public import _windows_priority_kernel, deprioritize_process


class _Kernel:
    """Actual ctypes function objects backed by local callbacks, never Win32.

    Begin with undeclared/default-int interfaces. The production factory must
    replace them before calling. Keep the callbacks alive on this fixture.
    """
    def __init__(self, result):
        self.calls = []
        self.events = []

        def current():
            self.events.append("GetCurrentProcess")
            return wintypes.HANDLE(-1).value

        def priority(handle, value):
            self.events.append("SetPriorityClass")
            self.calls.append((handle, value))
            return result

        self.GetCurrentProcess = ctypes.CFUNCTYPE(wintypes.HANDLE)(current)
        self.SetPriorityClass = ctypes.CFUNCTYPE(
            wintypes.BOOL, wintypes.HANDLE, wintypes.DWORD
        )(priority)
        for function in (self.GetCurrentProcess, self.SetPriorityClass):
            function.argtypes = None
            function.restype = ctypes.c_int


class WindowsPriorityTests(unittest.TestCase):
    def test_factory_declares_prototypes_on_real_ctypes_function_objects(self):
        dll = _Kernel(1)
        kernel = _windows_priority_kernel(dll=dll)
        self.assertIs(kernel, dll)
        self.assertIsInstance(kernel.GetCurrentProcess, ctypes._CFuncPtr)
        self.assertIsInstance(kernel.SetPriorityClass, ctypes._CFuncPtr)
        self.assertEqual(kernel.GetCurrentProcess.argtypes, [])
        self.assertIs(kernel.GetCurrentProcess.restype, wintypes.HANDLE)
        self.assertEqual(kernel.SetPriorityClass.argtypes, [wintypes.HANDLE, wintypes.DWORD])
        self.assertIs(kernel.SetPriorityClass.restype, wintypes.BOOL)

    def test_windows_success_uses_typed_handle_and_last_error_loader(self):
        dll = _Kernel(1)
        with patch.object(ctypes, "WinDLL", return_value=dll, create=True) as load, \
             patch.object(ctypes, "get_last_error", create=True) as error:
            self.assertEqual(deprioritize_process(platform_name="nt"), "windows_below_normal")
        load.assert_called_once_with("kernel32", use_last_error=True)
        error.assert_not_called()
        self.assertEqual(dll.calls, [(wintypes.HANDLE(-1).value, 0x00004000)])

    def test_windows_failure_captures_numeric_error_immediately(self):
        for code in (6, 5, 0):
            with self.subTest(code=code):
                dll = _Kernel(0)
                def last_error():
                    dll.events.append("get_last_error")
                    return code
                with patch.object(ctypes, "WinDLL", return_value=dll, create=True), \
                     patch.object(ctypes, "get_last_error", side_effect=last_error, create=True) as error:
                    self.assertEqual(deprioritize_process(platform_name="nt"),
                                     f"priority_denied:winerror={code}")
                error.assert_called_once_with()
                self.assertEqual(dll.events, ["GetCurrentProcess", "SetPriorityClass", "get_last_error"])
                self.assertEqual(dll.calls, [(wintypes.HANDLE(-1).value, 0x00004000)])

    def test_daily_priority_refusal_surfaces_reason_exits_78_and_keeps_latch(self):
        dll = _Kernel(0)
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory(prefix="pmc-priority-refused-") as name:
            root = Path(name)
            def runner(path, event):
                return run_daily(path, stop=event.is_set,
                                 clock_ns=lambda: 1_700_000_000_000_000_000,
                                 disk_free_probe=lambda _: 10**12,
                                 deprioritize=lambda: deprioritize_process(platform_name="nt"))
            with patch.object(ctypes, "WinDLL", return_value=dll, create=True), \
                 patch.object(ctypes, "get_last_error", return_value=6, create=True), \
                 patch("public_market_collector.public_run.open_session") as session, \
                 patch("public_market_collector.public_run.open_public_socket") as connect, \
                 redirect_stderr(stderr):
                code = execute_daily(name, runner=runner, disk_free_probe=lambda _: 10**12,
                                     sleep=lambda _: self.fail("terminal refusal restarted"))
            self.assertEqual(code, 78)
            self.assertEqual(stderr.getvalue(), "REFUSED daily: PRIORITY_DENIED:priority_denied:winerror=6\n")
            session.assert_not_called()
            connect.assert_not_called()
            self.assertEqual(len(dll.calls), 1)
            rows, torn = read_journal(root / "launch.journal")
            self.assertFalse(torn)
            self.assertEqual([r["kind"] for r in rows], ["start", "exit"])
            self.assertEqual(rows[-1]["exit_code"], 78)
            runtime, torn = read_journal(root / "runtime.journal")
            self.assertFalse(torn)
            self.assertEqual([r["kind"] for r in runtime], ["day_start"])
            self.assertEqual({p.name for p in root.iterdir()},
                             {"launch.journal", "runtime.journal", "runtime.lock"})
            before = {p.name: p.read_bytes() for p in root.glob("*.journal")}
            with redirect_stderr(io.StringIO()) as latched:
                code = execute_daily(name, runner=lambda *_: self.fail("latch bypassed"),
                                     disk_free_probe=lambda _: 10**12)
            self.assertEqual(code, 78)
            self.assertIn("PREVIOUS_TERMINAL_EXIT_78", latched.getvalue())
            self.assertEqual(before, {p.name: p.read_bytes() for p in root.glob("*.journal")})

    def test_daily_other_start_refused_reason_is_exposed_and_terminal(self):
        def runner(*_):
            raise StartRefused("OUTPUT_EXISTS")
        with tempfile.TemporaryDirectory(prefix="pmc-other-refused-") as name, \
             redirect_stderr(io.StringIO()) as stderr:
            code = execute_daily(name, runner=runner, disk_free_probe=lambda _: 10**12,
                                 sleep=lambda _: self.fail("terminal refusal restarted"))
            self.assertEqual(code, 78)
            self.assertEqual(stderr.getvalue(), "REFUSED daily: OUTPUT_EXISTS\n")
            rows, torn = read_journal(Path(name) / "launch.journal")
            self.assertFalse(torn)
            self.assertEqual(rows[-1]["exit_code"], 78)

    def test_daily_windows_success_stays_zero_and_silent(self):
        dll = _Kernel(1)
        def runner(*_):
            self.assertEqual(deprioritize_process(platform_name="nt"), "windows_below_normal")
            return 0
        with tempfile.TemporaryDirectory(prefix="pmc-priority-success-") as name, \
             patch.object(ctypes, "WinDLL", return_value=dll, create=True), \
             patch.object(ctypes, "get_last_error", create=True) as error, \
             redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(execute_daily(name, runner=runner, disk_free_probe=lambda _: 10**12,
                                          sleep=lambda _: self.fail("success restarted")), 0)
            self.assertEqual(stderr.getvalue(), "")
            error.assert_not_called()
            rows, torn = read_journal(Path(name) / "launch.journal")
            self.assertFalse(torn)
            self.assertEqual([r["kind"] for r in rows], ["start", "exit"])
            self.assertEqual(rows[-1]["exit_code"], 0)

    def test_existing_windows_injected_seam_is_unchanged(self):
        calls = []
        with patch.object(ctypes, "WinDLL", create=True) as load:
            self.assertEqual(deprioritize_process(platform_name="nt", win_call=calls.append),
                             "windows_below_normal")
        self.assertEqual(calls, [0x00004000])
        load.assert_not_called()

    def test_posix_success_and_denial_are_unchanged(self):
        calls = []
        self.assertEqual(deprioritize_process(platform_name="posix", nice=calls.append), "posix_nice_10")
        self.assertEqual(calls, [10])
        def denied(_):
            raise OSError("denied")
        self.assertEqual(deprioritize_process(platform_name="posix", nice=denied), "nice_denied")


if __name__ == "__main__":
    unittest.main()
