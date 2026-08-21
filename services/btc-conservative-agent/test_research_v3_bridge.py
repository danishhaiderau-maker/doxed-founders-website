import json
import tempfile
import unittest

from research_v3_bridge import dual_write_v22_record
from research_v3_bridge import dual_write_provisional_source
from research_v3_bridge import dual_write_paper_fill, dual_write_paper_order_intent
from research_v3_store import V3EvidenceStore


def _event(event_id="cont-1", episode_id="episode-1"):
    return {
        "event_id": event_id,
        "event_episode_id": episode_id,
        "epoch_id": "epoch-v3-test",
        "envelope": {"signal_ts": 1000, "raw_direction": "LONG", "executed_direction": "LONG", "policy_signature": "policy-a", "policy_epoch_id": "pe-a"},
        "event_episode": {"shared_ai_call_id": "scan-1"},
        "feature_snapshot_at_signal": {"symbol": "tBTCF0:USTF0", "adx": 30},
        "pre_signal_context": {"1m": {"bars": 1}},
        "decision_tree_snapshot": {"AI": {"result": "PASS"}},
        "primary_outcome": "ACCEPTED_UNFILLED",
        "observation_status": "FUNNEL_COMPLETE",
        "ranking_eligible": True,
        "canonical_tape": {"path_1m": [{"t": 1000, "o": 100, "h": 101, "l": 99, "c": 100}], "canonical_tape_start": 1000, "canonical_tape_end": 1060, "coverage": {"complete": True}},
        "research_execution_basis": {"qty": 0.1},
        "research_chase_schedule": {"authoritative": True, "intervals": []},
        "entry_children": [{"id": 1}, {"id": 2}],
        "replay_eligibility": {"eligible": True},
    }


class V3BridgeTests(unittest.TestCase):
    def test_paper_submit_and_fill_are_visible_before_terminal_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            signal = {
                "trade_id": "o29atr-1", "created_ts_ts": 1000,
                "raw_direction": "LONG", "final_direction": "LONG",
                "symbol": "tBTCF0:USTF0", "shared_ai_call_id": "scan-paper-1",
                "signal_price": 101.0, "research_lane": "OFFSET_029_ATR_TP_25",
                "paper_only": True, "relay_eligible": False,
                "policy_id": "OFFSET_029_ATR_TP_25", "policy_signature": "policy-paper",
                "policy_epoch_id": "policy-epoch-paper",
            }
            order = {
                "trade_id": "o29atr-1", "created_ts": 1001, "signal_dir": "LONG",
                "signal_price": 101.0, "limit_price": 100.7071, "qty": 0.2,
                "research_lane": "OFFSET_029_ATR_TP_25", "paper_only": True,
                "relay_eligible": False, "chase_schedule_authoritative": True,
                "research_chase_schedule": {"authoritative": True, "intervals": [{"step": 0}]},
            }
            submit = dual_write_paper_order_intent(order, signal, epoch_id="epoch-v3-test", data_dir=tmp)
            position = {"trade_id": "o29atr-1", "entry_ts": 1010, "entry": 100.7071, "qty": 0.2, "fill_model": "LIMIT_TOUCH"}
            fill = dual_write_paper_fill(order, signal, position, epoch_id="epoch-v3-test", data_dir=tmp)
            store = V3EvidenceStore(tmp, epoch_id="epoch-v3-test")
            self.assertTrue(submit["store_verification"]["passed"])
            self.assertTrue(fill["store_verification"]["passed"])
            intent = json.loads(store.ledger_path("order_intent").read_text().strip())
            execution = json.loads(store.ledger_path("execution").read_text().strip())
            self.assertEqual(intent["intent_kind"], "ACTUAL_PAPER_LIMIT_SUBMIT")
            self.assertEqual(intent["requested_qty"], 0.2)
            self.assertTrue(intent["chase_schedule_authoritative"])
            self.assertEqual(intent["policy_signature"], "policy-paper")
            self.assertEqual(intent["policy_epoch_id"], "policy-epoch-paper")
            self.assertEqual(execution["execution_world"], "SHOWCASE_PAPER_OBSERVED")
            self.assertFalse(execution["authenticated_exchange_actual"])
            self.assertEqual(execution["filled_qty"], 0.2)
            self.assertEqual(intent["episode_id"], execution["episode_id"])

    def test_paper_lifecycle_retry_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            signal = {"trade_id": "p-1", "created_ts_ts": 1000, "raw_direction": "SHORT", "final_direction": "SHORT", "shared_ai_call_id": "scan-1"}
            order = {"trade_id": "p-1", "created_ts": 1001, "signal_dir": "SHORT", "limit_price": 101, "qty": 0.1}
            position = {"trade_id": "p-1", "entry_ts": 1010, "entry": 101, "qty": 0.1}
            dual_write_paper_order_intent(order, signal, epoch_id="epoch-v3-test", data_dir=tmp)
            again = dual_write_paper_order_intent(order, signal, epoch_id="epoch-v3-test", data_dir=tmp)
            dual_write_paper_fill(order, signal, position, epoch_id="epoch-v3-test", data_dir=tmp)
            fill_again = dual_write_paper_fill(order, signal, position, epoch_id="epoch-v3-test", data_dir=tmp)
            self.assertTrue(all(row["duplicate"] for row in again["writes"]))
            self.assertTrue(all(row["duplicate"] for row in fill_again["writes"]))
            store = V3EvidenceStore(tmp, epoch_id="epoch-v3-test")
            self.assertEqual(store.verify()["ledger_counts"]["order_intent"], 1)
            self.assertEqual(store.verify()["ledger_counts"]["execution"], 1)

    def test_missing_policy_signature_gets_stable_explicit_paper_signature(self):
        with tempfile.TemporaryDirectory() as tmp:
            signal = {"trade_id": "p-2", "created_ts_ts": 1000, "raw_direction": "LONG", "shared_ai_call_id": "scan-2", "policy_id": "PATIENT", "research_lane": "PATIENT"}
            order = {"trade_id": "p-2", "created_ts": 1001, "signal_dir": "LONG", "limit_price": 99, "qty": 0.1}
            dual_write_paper_order_intent(order, signal, epoch_id="epoch-v3-test", data_dir=tmp)
            dual_write_paper_fill(order, signal, {"trade_id": "p-2", "entry_ts": 1010, "entry": 99, "qty": 0.1}, epoch_id="epoch-v3-test", data_dir=tmp)
            store = V3EvidenceStore(tmp, epoch_id="epoch-v3-test")
            intent = json.loads(store.ledger_path("order_intent").read_text().strip())
            execution = json.loads(store.ledger_path("execution").read_text().strip())
            self.assertTrue(intent["policy_signature"].startswith("paper-policy-"))
            self.assertTrue(intent["policy_epoch_id"].startswith("paper-policy-epoch-"))
            self.assertEqual(intent["policy_signature"], execution["policy_signature"])
            self.assertEqual(intent["policy_epoch_id"], execution["policy_epoch_id"])

    def test_provisional_without_stable_identity_is_deferred_without_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            receipt = dual_write_provisional_source("cont-pending", {
                "created_ts_ts": 1000, "final_direction": "LONG", "symbol": "BTCUSD",
            }, epoch_id="epoch-v3-test", data_dir=tmp)
            self.assertTrue(receipt["deferred"])
            self.assertEqual(receipt["reason"], "CAUSAL_IDENTITY_PENDING")
            store = V3EvidenceStore(tmp, epoch_id="epoch-v3-test")
            self.assertEqual(store.verify()["ledger_counts"]["opportunity"], 0)
            self.assertEqual(store.verify()["ledger_counts"]["lifecycle"], 0)

    def test_provisional_opportunity_is_available_before_terminal_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            receipt = dual_write_provisional_source("cont-early", {
                "created_ts_ts": 1000,
                "final_direction": "LONG",
                "symbol": "tBTCF0:USTF0",
                "shared_ai_call_id": "scan-early",
                "observation_status": "WAITING_120M",
            }, epoch_id="epoch-v3-test", data_dir=tmp)
            store = V3EvidenceStore(tmp, epoch_id="epoch-v3-test")
            self.assertTrue(receipt["store_verification"]["passed"])
            opportunity = json.loads(store.ledger_path("opportunity").read_text().strip())
            lifecycle = json.loads(store.ledger_path("lifecycle").read_text().strip())
            self.assertTrue(opportunity["first_observed_as_provisional"])
            self.assertFalse(lifecycle["terminal"])
            self.assertEqual(lifecycle["ranking_blocker"], "PATH_NOT_MATURED")

    def test_one_episode_many_branches_has_one_opportunity(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = dual_write_v22_record(_event("cont-1"), data_dir=tmp)
            b = dual_write_v22_record(_event("scan-1"), data_dir=tmp)
            store = V3EvidenceStore(tmp, epoch_id="epoch-v3-test")
            self.assertTrue(a["store_verification"]["passed"])
            self.assertTrue(b["store_verification"]["passed"])
            self.assertEqual(len(store.ledger_path("opportunity").read_text().splitlines()), 1)
            self.assertEqual(len(store.ledger_path("decision").read_text().splitlines()), 2)

    def test_large_market_path_is_stored_once_and_referenced(self):
        with tempfile.TemporaryDirectory() as tmp:
            event = _event()
            dual_write_v22_record(event, data_dir=tmp)
            dual_write_v22_record({**event, "event_id": "cont-2"}, data_dir=tmp)
            store = V3EvidenceStore(tmp, epoch_id="epoch-v3-test")
            self.assertEqual(store.verify()["market_segment_count"], 1)
            lifecycle = [json.loads(line) for line in store.ledger_path("lifecycle").read_text().splitlines()]
            self.assertEqual(lifecycle[0]["market_segment_refs"][0]["sha256"], lifecycle[1]["market_segment_refs"][0]["sha256"])

    def test_unfilled_is_not_realized_zero_pnl(self):
        with tempfile.TemporaryDirectory() as tmp:
            dual_write_v22_record(_event(), data_dir=tmp)
            store = V3EvidenceStore(tmp, epoch_id="epoch-v3-test")
            lifecycle = json.loads(store.ledger_path("lifecycle").read_text().strip())
            self.assertEqual(lifecycle["outcome_state"], "NO_FILL")
            self.assertNotEqual(lifecycle["outcome_state"], "REALIZED_ZERO_PNL")

    def test_order_intent_preserves_exact_entry_children_for_v3_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            event = _event()
            event["atr14_pct"] = 0.42
            event["envelope"]["signal_price"] = 100.0
            dual_write_v22_record(event, data_dir=tmp)
            store = V3EvidenceStore(tmp, epoch_id="epoch-v3-test")
            intent = json.loads(store.ledger_path("order_intent").read_text().strip())
            self.assertEqual(intent["entry_children"], event["entry_children"])
            self.assertEqual(intent["entry_children_count"], 2)
            self.assertEqual(intent["atr14_pct"], 0.42)
            self.assertEqual(intent["signal_price"], 100.0)
            self.assertEqual(intent["executed_direction"], "LONG")


if __name__ == "__main__":
    unittest.main()
