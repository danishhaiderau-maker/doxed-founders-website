"""Regression tests for truthful expired-order presentation."""

import ast
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path


BOT_PATH = Path(__file__).with_name("bot.py")
SOURCE = BOT_PATH.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE, filename=str(BOT_PATH))
FUNCTIONS = {
    node.name: node
    for node in TREE.body
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
}


def compile_functions(names, namespace):
    module = ast.Module(body=[FUNCTIONS[name] for name in names], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(BOT_PATH), "exec"), namespace)
    return namespace


class ExpiredOrderDashboardContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.namespace = {
            "datetime": datetime,
            "timezone": timezone,
            "MAX_EXPIRED_ORDERS": 20,
            "utc_iso": lambda value=None: datetime.fromtimestamp(
                value if isinstance(value, (int, float)) else time.time(),
                tz=timezone.utc,
            ).isoformat(),
            "_format_melbourne_hm": lambda value: str(value),
            "_normalize_order_side_to_dir": lambda value: str(value).upper(),
            "_enrich_melbourne_time_fields": lambda value: value,
        }
        compile_functions(
            (
                "parse_ts",
                "_expired_entry_created_ts",
                "_expired_order_api_row",
                "_dashboard_expired_order_rows",
            ),
            cls.namespace,
        )

    def test_iso_creation_time_preserves_real_thirty_minute_age(self):
        created_iso = "2026-08-01T14:27:01+00:00"
        actual = self.namespace["_expired_entry_created_ts"](
            {"created_ts": created_iso}
        )
        expected = datetime.fromisoformat(created_iso).timestamp()
        self.assertEqual(actual, expected)

    def test_ai_scan_coordinator_is_not_presented_as_expired_order(self):
        rows = [
            {
                "trade_id": "scan-no-order",
                "research_lane": "AI_SCAN",
                "reason": "SIGNAL_TTL_EXPIRED",
                "created_ts": 100.0,
                "expired_ts": 1900.0,
            },
            {
                "trade_id": "cont-real-candidate",
                "research_lane": "CONTINUOUS",
                "reason": "SIGNAL_TTL_EXPIRED",
                "created_ts": 100.0,
                "expired_ts": 1900.0,
                "age_min": 30,
            },
        ]

        visible = self.namespace["_dashboard_expired_order_rows"](rows, 5)

        self.assertEqual([row["trade_id"] for row in visible], ["cont-real-candidate"])
        self.assertEqual(visible[0]["age_min"], 30)

    def test_dashboard_limit_keeps_latest_five_real_rows(self):
        rows = [
            {"trade_id": f"cont-{idx}", "research_lane": "CONTINUOUS"}
            for idx in range(7)
        ]
        visible = self.namespace["_dashboard_expired_order_rows"](rows, 5)
        self.assertEqual(
            [row["trade_id"] for row in visible],
            ["cont-2", "cont-3", "cont-4", "cont-5", "cont-6"],
        )


if __name__ == "__main__":
    unittest.main()
