import json
import tempfile
import unittest

from research_v3_bridge import dual_write_v22_record
from research_v3_bridge import dual_write_provisional_source
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


if __name__ == "__main__":
    unittest.main()
