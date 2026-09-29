import unittest

from research.policy_evidence_schema import generation_identity, normalize_query, stable_hash


class PolicyEvidenceSchemaTests(unittest.TestCase):
    def test_world_is_required_and_offset_point_ten_is_reachable(self):
        with self.assertRaisesRegex(ValueError, "EVIDENCE_WORLD_REQUIRED"):
            normalize_query({"entry_offset_pct": 0.10})
        query = normalize_query({
            "evidence_world": "conservative_bbo_depth_tape",
            "entry_offset_pct": 0.1,
        })
        self.assertEqual(query["entry_offset_pct"], "0.10")

    def test_query_normalization_and_hash_are_order_independent(self):
        a = normalize_query({"evidence_world": "AUTHENTICATED_ACTUAL", "family": ["b", "A"]})
        b = normalize_query({"family": ["a", "B"], "evidence_world": "AUTHENTICATED_ACTUAL"})
        self.assertEqual(a, b)
        self.assertEqual(stable_hash("q", a), stable_hash("q", b))

    def test_generation_is_manifest_and_revision_bound(self):
        manifest = {"entry_hash":"h", "dataset_epoch":"e", "source_revision":"s", "deployed_revision":"d1", "tile_config_signature":"t"}
        a = generation_identity(manifest, analyzer_revision="a1")
        b = generation_identity(manifest, analyzer_revision="a2")
        deployed = generation_identity({**manifest, "deployed_revision": "d2"}, analyzer_revision="a1")
        self.assertEqual(a["deployed_revision"], "d1")
        self.assertNotEqual(a["generation_key"], b["generation_key"])
        self.assertNotEqual(a["generation_key"], deployed["generation_key"])


if __name__ == "__main__":
    unittest.main()
