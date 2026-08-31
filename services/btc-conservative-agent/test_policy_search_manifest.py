import unittest

from policy_search_manifest import POLICY_SEARCH_MANIFEST, compact_search_receipt
from collector_v22 import build_research_event


class PolicySearchManifestTests(unittest.TestCase):
    def test_complete_dimensions_and_current_safety_values_are_present(self):
        dims = POLICY_SEARCH_MANIFEST["dimensions"]
        self.assertEqual(len(dims["entry_offset_pct"]), 30)
        self.assertIn(10, dims["entry_ttl_min"])
        self.assertIn(-12, dims["thesis_cut_margin_pct"])
        self.assertIn(30, dims["hard_stop_margin_pct"])
        self.assertIn("scenario_c_8", dims["ladder_id"])
        self.assertIn("AUTHENTICATED_ACTUAL", dims["fill_model"])

    def test_receipt_is_compact_and_signature_bound(self):
        receipt = compact_search_receipt()
        self.assertEqual(receipt["signature"], POLICY_SEARCH_MANIFEST["signature"])
        self.assertNotIn("dimensions", receipt)
        self.assertGreater(receipt["counts"]["naive_full_cartesian"], 1_000_000)

    def test_static_dynamic_contract_is_declared(self):
        protocol = POLICY_SEARCH_MANIFEST["search_protocol"]
        self.assertIn("regime classifier", protocol["dynamic_policy"])
        self.assertIn("never auto-activate", protocol["activation"])

    def test_research_entry_treatments_explicitly_cover_windows_and_terminal_modes(self):
        axes = POLICY_SEARCH_MANIFEST["entry_treatment_axes"]
        self.assertEqual(axes["chase_window_bucket"], list(range(6)))
        self.assertEqual(axes["chase_window_bucket_seconds"], 300)
        self.assertIn("MARKET_AT_SIGNAL", axes["execution_mode"])
        self.assertIn("NO_CHASE", axes["chase_mode"])
        self.assertIn("FINAL_MARKET_AFTER_EXPIRY", axes["expiry_action"])
        self.assertEqual(axes["execution_class"], "RESEARCH_ONLY")
        self.assertEqual(axes["missing_evidence_outcome"], "UNKNOWN")

    def test_event_binds_search_and_signal_time_features(self):
        signal_ts = 1_700_000_000.0
        bars = [[(signal_ts + i * 60) * 1000, 100.0, 101.0, 99.0, 100.0, 1.0] for i in range(181)]
        snapshot = {"schema": "research_feature_snapshot_v1", "cycle_3m_universe": {"adx14": 27.5}}
        event = build_research_event(
            trade_id="manifest-event", epoch_id="epoch-manifest", signal_ts=signal_ts,
            signal_price=100.0, candles_1m=bars, ticket_closed=True,
            feature_snapshot=snapshot,
        )
        self.assertEqual(event["feature_snapshot_at_signal"], snapshot)
        self.assertEqual(event["envelope"]["policy_search"]["signature"], POLICY_SEARCH_MANIFEST["signature"])


if __name__ == "__main__":
    unittest.main()
