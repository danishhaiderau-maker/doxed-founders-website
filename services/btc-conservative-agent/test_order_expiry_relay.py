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

    def test_terminal_stamp_after_real_resting_limit_still_emits(self):
        predicate = load_predicate()
        self.assertTrue(predicate(
            {
                "entry_type": "LIMIT",
                "created_ts": 123.0,
                "order_created_ts": 130.0,
                "order_placed": True,
                "status": "EXPIRED",
            },
            "SIGNAL_TTL_EXPIRED",
            64000,
        ))

    def test_continuous_signal_uses_exact_preserved_submission_generation(self):
        predicate = load_predicate()
        source = {
            "trade_id": "cont-b357227a8961",
            "entry_mode": "AI_DIRECT_LIMIT",
            "created_ts": 123.0,
            "status": "EXPIRED",
            "_order_submission_accounted": True,
            "submitted_order_trade_id": "cont-b357227a8961",
            "submitted_order_entry_type": "SIM_LIMIT",
            "submitted_order_created_ts": 130.0,
            "submitted_order_event_seq": 2,
            "submitted_order_limit_price": 63259.82,
            "limit_chase_count": 2,
        }
        self.assertTrue(predicate(source, "SIGNAL_TTL_EXPIRED", 63259.82))
        self.assertFalse(predicate({**source, "limit_chase_count": 3}, "SIGNAL_TTL_EXPIRED", 63259.82))
        self.assertFalse(predicate({**source, "submitted_order_limit_price": 63260.82}, "SIGNAL_TTL_EXPIRED", 63259.82))

    def test_virtual_signal_cannot_forge_submission_with_partial_markers(self):
        predicate = load_predicate()
        self.assertFalse(predicate({
            "trade_id": "cont-virtual",
            "entry_mode": "AI_DIRECT_LIMIT",
            "created_ts": 123.0,
            "status": "EXPIRED",
            "_order_submission_accounted": True,
            "submitted_order_trade_id": "cont-virtual",
        }, "SIGNAL_TTL_EXPIRED", 64000))

    def test_terminal_pre_order_signal_cannot_emit(self):
        predicate = load_predicate()
        self.assertFalse(predicate(
            {
                "entry_type": "LIMIT",
                "created_ts": 123.0,
                "status": "EXPIRED",
            },
            "SIGNAL_TTL_EXPIRED",
            64000,
        ))

    def test_confirmed_cancelled_resting_order_still_emits(self):
        predicate = load_predicate()
        self.assertTrue(predicate(
            {
                "entry_type": "SIM_LIMIT",
                "created_ts": 123.0,
                "status": "CANCELLED",
                "cancel_confirmed": True,
            },
            "SIGNAL_TTL_EXPIRED",
            64000,
        ))

    def test_unconfirmed_cancelled_order_cannot_emit(self):
        predicate = load_predicate()
        self.assertFalse(predicate(
            {
                "entry_type": "SIM_LIMIT",
                "created_ts": 123.0,
                "status": "CANCELLED",
            },
            "SIGNAL_TTL_EXPIRED",
            64000,
        ))

    def test_real_expiry_waits_for_durable_platform_receipt(self):
        self.assertTrue(expiry_push_uses_durable_receipt())


if __name__ == "__main__":
    unittest.main()
