"""Regression contract for the desktop analyzer's safe, reachable URL."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent
ANALYZER_SOURCES = (
    ROOT / "analyzer_research_engine_v62.py",
)


class AnalyzerLoopbackUrlContractTests(unittest.TestCase):
    def test_analyzer_sources_do_not_advertise_retired_lan_address(self):
        for source in ANALYZER_SOURCES:
            with self.subTest(source=source):
                text = source.read_text(encoding="utf-8")
                self.assertNotIn("10.0.0.102:9001", text)

    def test_analyzer_sources_default_to_loopback(self):
        for source in ANALYZER_SOURCES:
            with self.subTest(source=source):
                text = source.read_text(encoding="utf-8")
                self.assertIn("http://127.0.0.1:9001/", text)


if __name__ == "__main__":
    unittest.main()
