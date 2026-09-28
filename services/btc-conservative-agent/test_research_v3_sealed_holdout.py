import json
import tempfile
import unittest
from pathlib import Path

from research_v3_sealed_holdout import (
    consume_seal,
    create_seal,
    load_seal,
    verify_evaluation_receipt,
)
from research_v3_validation import validate_policy


IDENTITY = {
    "dataset_epoch": "epoch-1",
    "source_revision": "source-abc",
    "deployed_revision": "deploy-abc",
    "tile_config_signature": "tiles-abc",
    "cohort_signature": "cohort-abc",
}
CANDIDATES = [{"policy_id": "p", "policy_signature": "policy-abc"}]


def episode(index, *, collected_at=210.0):
    return {
        **IDENTITY,
        "episode_id": f"e-{index}",
        "signal_ts": 200.0 + index,
        "required_end_ts": 220.0 + index,
        "evidence_collected_at": collected_at + index,
        "regime": ("BULL", "BEAR", "SIDEWAYS")[index % 3],
        "policy_outcomes": {"p": {"outcome_state": "FULL_FILL", "net_pnl_usd": 1.0}},
    }


class SealedHoldoutTests(unittest.TestCase):
    def make_seal(self, root):
        return create_seal(
            root, **IDENTITY, training_snapshot_hash="train-sha",
            training_completed_at=90, sealed_at=100, holdout_start_ts=200,
            policy_candidates=CANDIDATES,
        )

    def test_seal_is_content_addressed_immutable_and_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = self.make_seal(tmp)
            second = self.make_seal(tmp)
            self.assertEqual(first, second)
            self.assertEqual(load_seal(tmp, first["seal_id"]), first)
            path = Path(tmp) / "sealed_holdout" / "seals" / f"{first['seal_id']}.json"
            changed = json.loads(path.read_text())
            changed["dataset_epoch"] = "tampered"
            path.chmod(0o666)
            path.write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "MISMATCH"):
                load_seal(tmp, first["seal_id"])

    def test_post_seal_candidate_change_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            seal = self.make_seal(tmp)
            with self.assertRaisesRegex(ValueError, "POST_SEAL_POLICY_CANDIDATE_CHANGE"):
                consume_seal(
                    tmp, seal_id=seal["seal_id"],
                    policy_candidates=[{"policy_id": "p", "policy_signature": "changed"}],
                    holdout_episodes=[episode(0)], evaluation_started_at=400,
                )

    def test_historical_data_cannot_be_retroactively_sealed(self):
        with tempfile.TemporaryDirectory() as tmp:
            seal = self.make_seal(tmp)
            receipt = consume_seal(
                tmp, seal_id=seal["seal_id"], policy_candidates=CANDIDATES,
                holdout_episodes=[episode(0, collected_at=99)], evaluation_started_at=400,
            )
            self.assertFalse(receipt["passed"])
            self.assertIn("HISTORICAL_OR_PREINSPECTED_EVIDENCE:e-0", receipt["blockers"])
            self.assertFalse(verify_evaluation_receipt(receipt, policy_id="p", holdout_episodes=[episode(0, collected_at=99)]))

    def test_consumption_is_single_use_and_binds_all_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            seal = self.make_seal(tmp)
            receipt = consume_seal(
                tmp, seal_id=seal["seal_id"], policy_candidates=CANDIDATES,
                holdout_episodes=[episode(0), episode(1)], evaluation_started_at=400,
            )
            self.assertTrue(verify_evaluation_receipt(receipt, policy_id="p", holdout_episodes=[episode(0), episode(1)]))
            changed = [episode(0), episode(2)]
            with self.assertRaisesRegex(ValueError, "IMMUTABLE_RECEIPT_CONFLICT"):
                consume_seal(
                    tmp, seal_id=seal["seal_id"], policy_candidates=CANDIDATES,
                    holdout_episodes=changed, evaluation_started_at=401,
                )

    def test_qualification_rejects_boolean_and_accepts_verified_receipt_only(self):
        rows = [episode(index) for index in range(100)]
        kwargs = dict(
            policy_id="p", starting_equity_usd=1000, max_drawdown_usd=100,
            max_drawdown_pct=20, min_cvar95_usd=-10, policies_tested=1,
            conservative_execution=True, neighborhood_stable=True,
            liquidation_buffer_verified=True,
        )
        asserted = validate_policy(rows, sealed_holdout=True, **kwargs)
        self.assertFalse(asserted["gates"]["sealed_holdout_pass"])
        with tempfile.TemporaryDirectory() as tmp:
            seal = self.make_seal(tmp)
            receipt = consume_seal(
                tmp, seal_id=seal["seal_id"], policy_candidates=CANDIDATES,
                holdout_episodes=rows, evaluation_started_at=400,
            )
            verified = validate_policy(rows, sealed_holdout=receipt, **kwargs)
            self.assertTrue(verified["gates"]["sealed_holdout_pass"])
            different_rows = list(rows)
            different_rows[-1] = episode(999)
            self.assertFalse(validate_policy(
                different_rows, sealed_holdout=receipt, **kwargs,
            )["gates"]["sealed_holdout_pass"])
            tampered = dict(receipt)
            tampered["passed"] = False
            self.assertFalse(validate_policy(rows, sealed_holdout=tampered, **kwargs)["gates"]["sealed_holdout_pass"])


if __name__ == "__main__":
    unittest.main()
