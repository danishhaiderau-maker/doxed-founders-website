"""Cadence, sequence, candle, and unverified-registry fixtures."""

from __future__ import annotations

import unittest

from research.coverage_audit.tests.test_synthetic_formats import run_group


class CadenceTests(unittest.TestCase):
    def test_cadence(self):
        run_group(self, "cadence")


if __name__ == "__main__":
    unittest.main()
