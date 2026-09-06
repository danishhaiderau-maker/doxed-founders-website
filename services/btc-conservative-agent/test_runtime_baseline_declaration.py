import ast
from pathlib import Path
import unittest

from research.runtime_baseline_declaration import build_runtime_baseline_declaration
from research.quantity_execution import build_signed_quantity_constraints


class RuntimeDeclarationTests(unittest.TestCase):
    def inputs(self):
        return dict(context={"cycle_3m_universe": {"atr14_pct_3m": .2, "captured_ts": 100}},
            quantity_capture={"receipt": build_signed_quantity_constraints(symbol="tBTCUSD",
                quantity_step="0.000001", quantity_precision=6, min_lot="0.00001",
                min_notional="1", captured_at="1970-01-01T00:01:40+00:00",
                source_revision="rev", source="venue")}, symbol="tBTCUSD",
            source_revision="rev", captured_at_ts=101, margin_usd=.25, leverage=100,
            maker_fee_rate=.001, taker_fee_rate=.002)

    def test_preserves_observation_and_declares_assumptions(self):
        result = build_runtime_baseline_declaration(**self.inputs())
        declaration = result["declaration"]
        self.assertEqual(declaration["atr"]["observed_ts"], 100)
        self.assertEqual(declaration["atr"]["available_at_ts"], 101)
        self.assertAlmostEqual(declaration["input_fee_assumption_usd"], .075)
        self.assertFalse(declaration["qualification_eligible"])

    def test_missing_and_future_atr_never_backdated(self):
        for stamp in (None, 102):
            args = self.inputs()
            args["context"]["cycle_3m_universe"]["captured_ts"] = stamp
            self.assertIsNone(build_runtime_baseline_declaration(**args)["declaration"])

    def test_bad_quantity_hash_and_revision_fail(self):
        args = self.inputs()
        args["quantity_capture"]["receipt"]["quantity_step"] = ".5"
        self.assertIsNone(build_runtime_baseline_declaration(**args)["declaration"])

    def test_executable_lane_source_for_all_verdicts(self):
        tree = ast.parse(Path(__file__).with_name("bot.py").read_text(encoding="utf-8-sig"))
        fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_write_v3_shared_lane_decision")
        import copy
        from datetime import datetime
        captured = []
        namespace = dict(copy=copy, datetime=datetime, SYMBOL="tBTCUSD",
            _shared_ai_call_id=lambda **kw: "call", invert_signal_active=lambda: False,
            _v3_lane_policy_material=lambda lane: {}, _collector_v22_epoch_id=lambda: "epoch",
            os=__import__("os"),
            dual_write_lane_decision=lambda source, **kw: captured.append(source) or {
                "store_verification": {"passed": True}, "writes": [{"ledger": "pre_entry_features"}]})
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "bot.py", "exec"), namespace)
        declaration = build_runtime_baseline_declaration(**self.inputs())["declaration"]
        for verdict in ("APPROVE", "REJECT", "NO_TRADE"):
            self.assertTrue(namespace[fn.name]("lane", {"decision": verdict,
                "shared_ai_call_ts": "1970-01-01T00:02:00+00:00",
                "research_baseline_context_declaration": declaration}, {}, {},
                policy_decision=verdict, execution_disposition="NO_ORDER", exact_reason="test"))
            self.assertEqual(captured[-1]["research_baseline_context_declaration"], declaration)

    def test_actual_pre_api_capture_block_and_failure_isolation(self):
        tree = ast.parse(Path(__file__).with_name("bot.py").read_text(encoding="utf-8-sig"))
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "evaluate_signal_with_ai")
        body = next(n.body for n in ast.walk(fn) if isinstance(n, ast.Try)
            and any(isinstance(item, ast.ImportFrom) and item.module == "research.runtime_baseline_declaration"
                    for item in n.body))
        start = next(i for i, n in enumerate(body) if isinstance(n, ast.ImportFrom)
            and n.module == "research.runtime_baseline_declaration")
        end = next(i for i in range(start + 1, len(body)) if isinstance(body[i], ast.Try))
        block = body[start:end + 1]
        api_lines = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name) and n.func.id == "call_deepseek_api"]
        self.assertLess(block[-1].end_lineno, min(api_lines))
        inputs = self.inputs()
        inputs["quantity_capture"]["receipt"] = build_signed_quantity_constraints(
            symbol="TBTCUSD", quantity_step="0.000001", quantity_precision=6,
            min_lot="0.00001", min_notional="1", captured_at="1970-01-01T00:01:40Z",
            source_revision="rev", source="venue")
        from types import SimpleNamespace
        namespace = dict(ctx=inputs["context"],
            _capture_runtime_quantity_constraints=lambda **kw: inputs["quantity_capture"],
            get_trading_fee_rates=lambda: (.001, .002), BITFINEX_WS_SYMBOL="tBTCUSD",
            _runtime_git_rev_exact=lambda: "rev", time=SimpleNamespace(time=lambda: 101),
            FIXED_MARGIN_USDT=.25, _state_leverage=lambda: 100)
        code = compile(ast.Module(body=block, type_ignores=[]), "bot.py", "exec")
        exec(code, namespace)
        self.assertEqual(namespace["research_context_capture"]["status"], "DECLARED_DIAGNOSTIC")
        def fail(**kw):
            raise RuntimeError("private detail must not escape")
        namespace["_capture_runtime_quantity_constraints"] = fail
        exec(code, namespace)
        self.assertEqual(namespace["research_context_capture"]["reasons"],
            ["RUNTIME_CONTEXT_CAPTURE_FAILED:RuntimeError"])

    def test_producer_through_bridge_store_and_both_direction_consumer(self):
        import tempfile
        import json
        from research_v3_bridge import dual_write_lane_decision
        from research.baseline_execution_context import declared_directional_baseline_inputs
        from research_v3_store import _collection_provenance
        args = self.inputs()
        args["symbol"] = "TBTCUSD"
        revision = _collection_provenance()["source_revision"]
        args["source_revision"] = revision
        args["quantity_capture"]["receipt"] = build_signed_quantity_constraints(
            symbol="TBTCUSD", quantity_step="0.000001", quantity_precision=6,
            min_lot="0.00001", min_notional="1", captured_at="1970-01-01T00:01:40Z",
            source_revision=revision, source="venue")
        declaration = build_runtime_baseline_declaration(**args)["declaration"]
        with tempfile.TemporaryDirectory() as directory:
            dual_write_lane_decision({"shared_ai_call_id": "call", "shared_ai_call_ts_epoch": 120,
                "original_context_signal_ts": 95,
                "research_baseline_context_status": {"status": "DECLARED_DIAGNOSTIC"},
                "symbol": "tBTCUSD", "source_revision": "rev", "deployed_revision": "rev",
                "tile_config_signature": "tiles", "raw_ai_decision": "REJECT",
                "raw_direction": "NO_TRADE", "signal_price": 100,
                "signal_time_bbo": {"bid": 99, "ask": 101, "bid_qty": 1, "ask_qty": 1},
                "research_baseline_context_declaration": declaration}, lane="TEST",
                policy_decision="REJECT", execution_disposition="NO_ORDER", exact_reason="test",
                epoch_id="epoch", data_dir=directory)
            row = json.loads((Path(directory) / "v3/ledgers/opportunity.jsonl").read_text().splitlines()[0])
            self.assertEqual(row["original_context_signal_ts"], 95)
            self.assertEqual(row["signal_ts"], 120)
            self.assertEqual(row["research_baseline_context_status"]["status"], "DECLARED_DIAGNOSTIC")
            captures = row["baseline_schedule_snapshot"]["directional_schedules"]
            self.assertEqual(set(captures), {"LONG", "SHORT"})
            for capture in captures.values():
                self.assertEqual(capture["symbol"], declaration["signed_quantity_constraints"]["symbol"])
                self.assertEqual(capture["source_revision"], declaration["signed_quantity_constraints"]["source_revision"])
                baseline_id, envelope = next(iter(capture["schedules"].items()))
                result = declared_directional_baseline_inputs(capture,
                    {"baseline_id": baseline_id, "policy_signature": envelope["policy_signature"]})
                self.assertGreater(result["requested_qty"], 0)


if __name__ == "__main__":
    unittest.main()
