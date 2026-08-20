import ast
from pathlib import Path
import unittest


BOT_PATH = Path(__file__).with_name("bot.py")


class ApiStateReadinessTimestampTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse(BOT_PATH.read_text(encoding="utf-8"))

    def _function(self, name):
        return next(
            node
            for node in self.tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        )

    def test_snapshot_recomputes_readiness_with_fresh_time_after_copy(self):
        target = self._function("_build_api_state_snapshot")
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

    def test_state_monitor_refreshes_clock_after_rest_io(self):
        target = self._function("state_monitor_loop")
        statements = list(ast.walk(target))
        refresh_line = next(
            node.lineno
            for node in statements
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "refresh_bbo_state"
        )
        recompute = next(
            node
            for node in statements
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_recompute_system_readiness"
        )
        fresh_clock_lines = [
            node.lineno
            for node in statements
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "now" for target in node.targets)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
            and isinstance(node.value.func.value, ast.Name)
            and node.value.func.value.id == "time"
            and node.value.func.attr == "time"
        ]
        self.assertTrue(any(refresh_line < line < recompute.lineno for line in fresh_clock_lines))


if __name__ == "__main__":
    unittest.main()
