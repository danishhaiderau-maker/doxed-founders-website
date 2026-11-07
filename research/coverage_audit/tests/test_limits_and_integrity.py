"""Resource-cap boundaries and input-pin integrity fixtures."""

from __future__ import annotations

import unittest

from research.coverage_audit.tests.test_synthetic_formats import run_group


class LimitTests(unittest.TestCase):
    def test_limits_and_integrity(self):
        run_group(self, "limits")


if __name__ == "__main__":
    unittest.main()
