"""Dashboard /api/data_size endpoint reports Fly runtime size + top files."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("FORCE_PAPER_MODE", "1")
os.environ.setdefault("RESEARCH_DATA_COLLECTION", "1")
os.environ.setdefault("SKIP_EXCHANGE_MARKET_LOAD", "1")

import bot


class _FakeCompleted:
    """Minimal CompletedProcess substitute.

    The endpoint reads ``out.stdout`` for both the text-mode ``du`` probes and
    the bytes-mode ``wc`` probe, so both kinds of output live on ``stdout``.
    """

    def __init__(self, stdout="", returncode=0, payload=b""):
        # When payload is provided it represents raw bytes (wc -l output); use
        # it directly so the endpoint's bytes-mode branch sees real content.
        self.stdout = payload if payload else stdout
        self.stderr = ""
        self.returncode = returncode


class DataSizeEndpointTests(unittest.TestCase):
    def setUp(self):
        self.original_bootstrap = bot._DASHBOARD_BOOTSTRAP_COMPLETE
        bot._DASHBOARD_BOOTSTRAP_COMPLETE = True

    def tearDown(self):
        bot._DASHBOARD_BOOTSTRAP_COMPLETE = self.original_bootstrap

    def _stub_subprocess(self, du_total_mb=480.0, du_files=None, wc_lines=None):
        """Build a deterministic subprocess.run fake returning known sizes."""
        if du_files is None:
            du_files = [
                (320.5, "/app/data/runtime/ai_reason_research.jsonl"),
                (110.0, "/app/data/runtime/signal_replay.jsonl"),
                (30.2, "/app/data/runtime/trades_3factor.csv"),
                (10.0, "/app/data/runtime/bot.log"),
                (5.0, "/app/data/runtime/debug.log"),
            ]
        if wc_lines is None:
            wc_lines = {
                "trades_3factor.csv": 1234,
                "ai_reason_research.jsonl": 9876,
                "signal_replay.jsonl": 4422,
            }

        du_total_stdout = "%.1f\t/app/data/runtime\n" % du_total_mb
        du_files_lines = "\n".join("%.1f\t%s" % (mb, p) for mb, p in du_files)
        wc_text = "\n".join(
            "%d\t/app/data/runtime/%s" % (cnt, fname) for fname, cnt in wc_lines.items()
        )

        def fake_run(cmd, *args, **kwargs):
            # First positional arg is the argv list. Branch on the leading tool.
            tool = cmd[0] if isinstance(cmd, (list, tuple)) and cmd else None
            if tool == "du":
                # `du -sm <runtime>` (total) vs `du -sm <runtime>/*` (files).
                # The file-list variant ends with `/*` on the only argument.
                arg = cmd[-1] if len(cmd) > 1 else ""
                if arg.endswith("/*"):
                    return _FakeCompleted(stdout=du_files_lines, returncode=0)
                return _FakeCompleted(stdout=du_total_stdout, returncode=0)
            if tool == "wc":
                return _FakeCompleted(stdout="", returncode=0, payload=wc_text.encode("utf-8"))
            return _FakeCompleted(stdout="", returncode=1)

        return fake_run

    def test_unauthenticated_request_is_rejected(self):
        # Force strict auth to fail by clearing both local-operator and token.
        with mock.patch.object(bot, "_admin_authed_strict", return_value=False):
            with bot.app.test_client() as client:
                resp = client.get("/api/data_size")
        self.assertEqual(resp.status_code, 401)
        body = resp.get_json()
        self.assertEqual(body["status"], "error")

    def test_authenticated_response_shape(self):
        # Strict auth passes so the endpoint runs the (stubbed) du/wc probes.
        # Top files now come from os.walk (not `du path/*`, which never expands).
        walk_files = [
            ("ai_reason_research.jsonl", 320.5 * 1024 * 1024),
            ("signal_replay.jsonl", 110.0 * 1024 * 1024),
            ("trades_3factor.csv", 30.2 * 1024 * 1024),
            ("bot.log", 10.0 * 1024 * 1024),
            ("debug.log", 5.0 * 1024 * 1024),
        ]

        def fake_walk(_root):
            yield ("/app/data/runtime", [], [name for name, _ in walk_files])

        def fake_getsize(path):
            name = os.path.basename(path)
            for fname, size in walk_files:
                if fname == name:
                    return int(size)
            raise OSError("missing")

        with mock.patch.object(bot, "_admin_authed_strict", return_value=True):
            with mock.patch.object(bot.subprocess, "run", side_effect=self._stub_subprocess()):
                with mock.patch.object(bot.os, "walk", side_effect=fake_walk):
                    with mock.patch.object(bot.os.path, "getsize", side_effect=fake_getsize):
                        with bot.app.test_client() as client:
                            resp = client.get("/api/data_size")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["volume_total_mb"], 1024.0)
        self.assertEqual(body["runtime_size_mb"], 480.0)
        # 480/1024*100 = 46.875 -> rounded to one decimal = 46.9
        self.assertAlmostEqual(body["volume_pct"], 46.9, delta=0.05)
        self.assertEqual(body["cleanup_status"], "ok")

        # Top-5 files are sorted descending by size and capped at 5.
        top = body["top_files"]
        self.assertEqual(len(top), 5)
        self.assertEqual(top[0]["name"], "ai_reason_research.jsonl")
        self.assertAlmostEqual(top[0]["size_mb"], 320.5, delta=0.05)
        # Sorted: 320.5, 110.0, 30.2, 10.0, 5.0
        self.assertGreaterEqual(top[0]["size_mb"], top[1]["size_mb"])

        # Line counts come from the wc stub.
        self.assertEqual(body["line_counts"]["trades_3factor.csv"], 1234)
        self.assertEqual(body["line_counts"]["signal_replay.jsonl"], 4422)
        self.assertEqual(body["line_counts"]["ai_reason_research.jsonl"], 9876)

    def test_critical_threshold_when_over_80_percent(self):
        # 900/1024*100 ~= 87.9 -> critical
        with mock.patch.object(bot, "_admin_authed_strict", return_value=True):
            with mock.patch.object(bot.subprocess, "run", side_effect=self._stub_subprocess(du_total_mb=900.0)):
                with bot.app.test_client() as client:
                    resp = client.get("/api/data_size")
        body = resp.get_json()
        self.assertGreater(body["volume_pct"], 80.0)
        self.assertEqual(body["cleanup_status"], "critical")

    def test_warn_threshold_between_60_and_80(self):
        # 700/1024*100 ~= 68.4 -> warn
        with mock.patch.object(bot, "_admin_authed_strict", return_value=True):
            with mock.patch.object(bot.subprocess, "run", side_effect=self._stub_subprocess(du_total_mb=700.0)):
                with bot.app.test_client() as client:
                    resp = client.get("/api/data_size")
        body = resp.get_json()
        self.assertGreater(body["volume_pct"], 60.0)
        self.assertLessEqual(body["volume_pct"], 80.0)
        self.assertEqual(body["cleanup_status"], "warn")


if __name__ == "__main__":
    unittest.main()
