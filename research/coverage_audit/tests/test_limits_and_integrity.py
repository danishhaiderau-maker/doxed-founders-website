"""Resource-cap boundaries and input-pin integrity fixtures."""

from __future__ import annotations

import unittest

from research.coverage_audit.tests.test_synthetic_formats import run_group


class LimitTests(unittest.TestCase):
    def test_limits_and_integrity(self):
        run_group(self, "limits")

    def test_input_buffer_is_charged_before_read(self):
        from research.coverage_audit.tests.test_synthetic_formats import _rows_csv, _run_built, _with_profile

        built = _with_profile(_rows_csv((0, 60, 120)), memory_bytes=2048)
        report, extra = _run_built(built)
        self.assertIn("MEMORY_CAP", report["reasons"])
        self.assertEqual(extra["reads"], 0)
        self.assertNotEqual(report["status"], "COMPLETE")


if __name__ == "__main__":
    unittest.main()
