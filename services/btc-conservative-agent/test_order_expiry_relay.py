import ast
import pathlib
import unittest


def load_predicate():
    source = pathlib.Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
    module = ast.parse(source)
    function = next(
        node for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "_is_executable_order_expiry"
    )
    namespace = {}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "bot.py", "exec"), namespace)
    return namespace["_is_executable_order_expiry"]


class OrderExpiryRelayTests(unittest.TestCase):
    def test_pre_order_virtual_expiry_cannot_emit(self):
        predicate = load_predicate()
        self.assertFalse(predicate({"status": "AWAITING_DASHBOARD_CHASE"}, "SIGNAL_TTL_EXPIRED", 64000))

    def test_real_resting_limit_expiry_can_emit(self):
        predicate = load_predicate()
        self.assertTrue(predicate(
            {"entry_type": "SIM_LIMIT", "created_ts": 123.0, "status": "PENDING"},
            "SIGNAL_TTL_EXPIRED",
            64000,
        ))


if __name__ == "__main__":
    unittest.main()
