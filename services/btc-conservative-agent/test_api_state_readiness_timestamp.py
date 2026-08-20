import ast
from pathlib import Path
import unittest


BOT_PATH = Path(__file__).with_name("bot.py")


class ApiStateReadinessTimestampTests(unittest.TestCase):
    def test_snapshot_recomputes_readiness_with_fresh_time_after_copy(self):
        tree = ast.parse(BOT_PATH.read_text(encoding="utf-8"))
        target = next(
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_build_api_state_snapshot"
        )
        calls = [
            node
            for node in ast.walk(target)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_recompute_system_readiness"
        ]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].args, [])
        self.assertEqual(calls[0].keywords, [])


if __name__ == "__main__":
    unittest.main()
