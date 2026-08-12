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


def expiry_push_uses_durable_receipt():
    source = pathlib.Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
    module = ast.parse(source)
    recorder = next(
        node for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "_record_expired_order"
    )
    pushes = [
        node for node in ast.walk(recorder)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_push_showcase_relay_event"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "ORDER_EXPIRED"
    ]
    assert len(pushes) == 1
    return any(
        keyword.arg == "wait_for_durable_receipt"
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value is True
        for keyword in pushes[0].keywords
    )


class OrderExpiryRelayTests(unittest.TestCase):
    def test_pre_order_virtual_expiry_cannot_emit(self):
        predicate = load_predicate()
        self.assertFalse(predicate({"status": "AWAITING_DASHBOARD_CHASE"}, "SIGNAL_TTL_EXPIRED", 64000))

    def test_real_resting_limit_expiry_can_emit(self):
        predicate = load_predicate()
        for entry_type in ("LIMIT", "SIM_LIMIT"):
            with self.subTest(entry_type=entry_type):
                self.assertTrue(predicate(
                    {"entry_type": entry_type, "created_ts": 123.0, "status": "PENDING"},
                    "SIGNAL_TTL_EXPIRED",
                    64000,
                ))

    def test_non_pending_limit_expiry_cannot_emit(self):
        predicate = load_predicate()
        self.assertFalse(predicate(
            {"entry_type": "LIMIT", "created_ts": 123.0, "status": "ORDERED"},
            "SIGNAL_TTL_EXPIRED",
            64000,
        ))

    def test_real_expiry_waits_for_durable_platform_receipt(self):
        self.assertTrue(expiry_push_uses_durable_receipt())


if __name__ == "__main__":
    unittest.main()
