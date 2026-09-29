import tempfile
import unittest
from pathlib import Path

from research.policy_evidence_cache import PolicyEvidenceCache, cache_path
from research.policy_evidence_schema import generation_identity


def generation(revision="a"):
    return generation_identity(
        {"entry_hash":"manifest", "dataset_epoch":"epoch", "source_revision":"source", "deployed_revision":"deployed", "tile_config_signature":"tiles"},
        analyzer_revision=revision,
    )


class PolicyEvidenceCacheTests(unittest.TestCase):
    def test_cache_is_confined_to_generation_scoped_derived_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "canonical-research-data"
            cache = PolicyEvidenceCache(root, generation())
            self.assertEqual(cache.path, cache_path(root, generation()["generation_key"]))
            self.assertIn("derived", cache.path.parts)
        with self.assertRaisesRegex(ValueError, "CACHE_ROOT_NOT_CANONICAL"):
            cache_path("not-canonical", "g")

    def test_foreign_metadata_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "canonical-research-data"
            cache = PolicyEvidenceCache(root, generation())
            import sqlite3
            connection = sqlite3.connect(cache.path)
            try:
                connection.execute("UPDATE cache_meta SET value='wrong' WHERE key='epoch_id'")
                connection.commit()
            finally:
                connection.close()
            with self.assertRaisesRegex(ValueError, "STALE_OR_FOREIGN"):
                PolicyEvidenceCache(root, generation())


if __name__ == "__main__":
    unittest.main()
